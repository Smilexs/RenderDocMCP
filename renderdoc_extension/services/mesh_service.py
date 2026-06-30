"""Mesh data extraction service. Reads IB + VBs at a given event_id and returns decoded vertices."""

import base64
import json
import os
import struct

import renderdoc as rd

from ..utils import Parsers


def _decode_attr(raw, offset, fmt_name, comp_count, comp_bytewidth, comp_type):
    """Decode a single vertex attribute from raw bytes given format info."""
    # We only support float / unorm8 / snorm8 / uint16 / uint32 / float16 here.
    if fmt_name.endswith("_FLOAT") and comp_bytewidth == 4:
        return list(struct.unpack_from("<%df" % comp_count, raw, offset))
    if fmt_name.endswith("_FLOAT") and comp_bytewidth == 2:
        import struct as _s
        # half float
        vals = _s.unpack_from("<%dH" % comp_count, raw, offset)
        out = []
        for h in vals:
            s = (h >> 15) & 0x1
            e = (h >> 10) & 0x1F
            f = h & 0x3FF
            if e == 0:
                v = (f / 1024.0) * (2 ** -14)
            elif e == 31:
                v = float("inf") if f == 0 else float("nan")
            else:
                v = (1.0 + f / 1024.0) * (2 ** (e - 15))
            out.append(-v if s else v)
        return out
    if fmt_name.endswith("_UNORM") and comp_bytewidth == 1:
        vals = struct.unpack_from("<%dB" % comp_count, raw, offset)
        return [v / 255.0 for v in vals]
    if fmt_name.endswith("_SNORM") and comp_bytewidth == 1:
        vals = struct.unpack_from("<%db" % comp_count, raw, offset)
        return [max(v / 127.0, -1.0) for v in vals]
    if fmt_name.endswith("_UINT") and comp_bytewidth == 4:
        return list(struct.unpack_from("<%dI" % comp_count, raw, offset))
    if fmt_name.endswith("_UINT") and comp_bytewidth == 2:
        return list(struct.unpack_from("<%dH" % comp_count, raw, offset))
    if fmt_name.endswith("_UINT") and comp_bytewidth == 1:
        return list(struct.unpack_from("<%dB" % comp_count, raw, offset))
    # Fallback: float32
    return list(struct.unpack_from("<%df" % comp_count, raw, offset))


def _rdc_auto_has_components(value, count):
    try:
        return value is not None and len(value) >= count
    except TypeError:
        return False


def _rdc_auto_values_have_components(values, count):
    return bool(values) and all(
        _rdc_auto_has_components(v, count) for v in values)


def _rdc_auto_vec2_list(values):
    if not _rdc_auto_values_have_components(values, 2):
        return None
    return [[v[0], v[1]] for v in values]


def _rdc_auto_attr_components(attr):
    try:
        return int(attr.get("components", 0))
    except Exception:
        pass
    values = attr.get("values") or []
    if values:
        try:
            return len(values[0])
        except TypeError:
            return 0
    return 0


def _rdc_auto_attr_text(attr):
    parts = [
        attr.get("name") or "",
        attr.get("semantic_name") or "",
        str(attr.get("semantic_index", "")),
    ]
    return " ".join(parts).upper()


def _rdc_auto_attr_matches(attr, keyword):
    if not keyword:
        return False
    text = _rdc_auto_attr_text(attr)
    keyword = keyword.upper()
    if keyword in text:
        return True
    if keyword.startswith("TEXCOORD"):
        semantic = (attr.get("semantic_name") or "").upper()
        if "TEXCOORD" not in semantic:
            return False
        try:
            expected = int(keyword[len("TEXCOORD"):])
        except ValueError:
            return False
        try:
            return int(attr.get("semantic_index", 0)) == expected
        except Exception:
            return False
    return False


def _get_cbuffer_bind(pipe, stage_enum, slot):
    """Return (resource_id, byte_offset, byte_size) for a constant buffer bind.

    The PipeState constant-buffer accessor differs across RenderDoc builds; probe
    the known method shapes in order and normalise the result. Returns None if no
    accessor works (caller can inspect _probe_cbuffer_api for diagnostics).
    """
    # Shape 1 (older): GetConstantBuffer(stage, slot, arrayIdx) -> BoundCBuffer
    fn = getattr(pipe, "GetConstantBuffer", None)
    if callable(fn):
        b = fn(stage_enum, slot, 0)
        rid = getattr(b, "resourceId", None)
        if rid is not None:
            return rid, getattr(b, "byteOffset", 0), getattr(b, "byteSize", 0)

    # Shape 2 (newer unified): GetConstantBlock(stage, slot, arrayIdx) -> UsedDescriptor
    fn = getattr(pipe, "GetConstantBlock", None)
    if callable(fn):
        d = fn(stage_enum, slot, 0)
        desc = getattr(d, "descriptor", d)
        rid = getattr(desc, "resource", None)
        if rid is not None:
            return rid, getattr(desc, "byteOffset", 0), getattr(desc, "byteSize", 0)

    # Shape 3: GetConstantBlocks(stage) -> list, index by slot
    fn = getattr(pipe, "GetConstantBlocks", None)
    if callable(fn):
        try:
            blocks = list(fn(stage_enum, False))
        except TypeError:
            blocks = list(fn(stage_enum))
        for blk in blocks:
            access = getattr(blk, "access", None)
            idx = getattr(access, "index", None) if access is not None else None
            if idx == slot or idx is None:
                desc = getattr(blk, "descriptor", blk)
                rid = getattr(desc, "resource", getattr(desc, "resourceId", None))
                if rid is not None:
                    return rid, getattr(desc, "byteOffset", 0), getattr(desc, "byteSize", 0)
    return None


def _probe_cbuffer_api(pipe):
    """List candidate constant-buffer methods on the pipe object for diagnostics."""
    return sorted(
        n for n in dir(pipe)
        if "onstant" in n.lower() or n.lower().startswith("getcb")
    )


def _mat_columns_from_floats(f16):
    """hlslcc stores a float4x4 as 4 consecutive float4 rows.

    For Unity's ObjectToWorld the VS computes:
        worldPos = m[0].xyz*vx + m[1].xyz*vy + m[2].xyz*vz + m[3].xyz
    so the 4 stored float4s are the COLUMNS of the standard ObjectToWorld matrix.
    Returns the 4 columns as [(x,y,z,w), ...].
    """
    return [tuple(f16[i * 4:i * 4 + 4]) for i in range(4)]


def _bake_position(col, v):
    """worldPos = c0*vx + c1*vy + c2*vz + c3 (c3.w == 1)."""
    c0, c1, c2, c3 = col
    return [
        c0[0] * v[0] + c1[0] * v[1] + c2[0] * v[2] + c3[0],
        c0[1] * v[0] + c1[1] * v[1] + c2[1] * v[2] + c3[1],
        c0[2] * v[0] + c1[2] * v[1] + c2[2] * v[2] + c3[2],
    ]


def _bake_dir_objecttoworld(col, v):
    """Direction (tangent) through ObjectToWorld columns, no translation, normalized."""
    c0, c1, c2, _ = col
    x = c0[0] * v[0] + c1[0] * v[1] + c2[0] * v[2]
    y = c0[1] * v[0] + c1[1] * v[1] + c2[1] * v[2]
    z = c0[2] * v[0] + c1[2] * v[1] + c2[2] * v[2]
    return _normalize3([x, y, z])


def _bake_normal_worldtoobject(rows, n):
    """worldNormal.i = dot(n, WorldToObject_stored_row_i), then normalize.

    Replicates the VS exactly (dp3 with WorldToObject rows = inverse-transpose).
    """
    r0, r1, r2, _ = rows
    x = n[0] * r0[0] + n[1] * r0[1] + n[2] * r0[2]
    y = n[0] * r1[0] + n[1] * r1[1] + n[2] * r1[2]
    z = n[0] * r2[0] + n[1] * r2[1] + n[2] * r2[2]
    return _normalize3([x, y, z])


def _normalize3(v):
    m = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
    if m < 1e-12:
        return [0.0, 0.0, 0.0]
    return [v[0] / m, v[1] / m, v[2] / m]


def _invert4x4(m):
    """Invert a 4x4 matrix (list of 4 rows of 4). Returns None if singular."""
    # Build augmented [m | I] and Gauss-Jordan eliminate.
    a = [list(m[r]) + [1.0 if c == r else 0.0 for c in range(4)] for r in range(4)]
    for col in range(4):
        # Pivot: largest abs in this column at/below diagonal.
        piv = max(range(col, 4), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            return None
        a[col], a[piv] = a[piv], a[col]
        pv = a[col][col]
        a[col] = [x / pv for x in a[col]]
        for r in range(4):
            if r == col:
                continue
            factor = a[r][col]
            if factor != 0.0:
                a[r] = [x - factor * y for x, y in zip(a[r], a[col])]
    return [row[4:] for row in a]


class MeshService:
    """Mesh data extraction service."""

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn

    # ------------------------------------------------------------------ #
    #  Internal: decode IB + VB attributes at an event into a dict        #
    # ------------------------------------------------------------------ #
    def _extract(self, controller, event_id):
        """Returns (data_dict, error_str). data_dict has indices + attributes."""
        try:
            controller.SetFrameEvent(int(event_id), True)
        except Exception as e:
            return None, "SetFrameEvent failed: %s" % e

        pipe = controller.GetPipelineState()

        action = self.ctx.GetAction(int(event_id))
        if action is None:
            return None, "No action at event_id=%d" % event_id
        num_indices = action.numIndices
        base_vertex = getattr(action, "baseVertex", 0)
        vertex_offset = getattr(action, "vertexOffset", 0)
        index_offset = getattr(action, "indexOffset", 0)

        try:
            ib = pipe.GetIBuffer()
        except Exception as e:
            return None, "GetIBuffer failed: %s" % e

        ib_stride = ib.byteStride if ib.byteStride else 2
        try:
            ib_raw = controller.GetBufferData(
                ib.resourceId,
                ib.byteOffset + index_offset * ib_stride,
                num_indices * ib_stride,
            )
        except Exception as e:
            return None, "GetBufferData(IB %s) failed: %s" % (str(ib.resourceId), e)

        if ib_stride == 2:
            indices = list(struct.unpack("<%dH" % num_indices, ib_raw))
        else:
            indices = list(struct.unpack("<%dI" % num_indices, ib_raw))

        if not indices:
            return None, "Index buffer returned empty (len=%d)" % len(ib_raw)

        if base_vertex:
            indices = [i + base_vertex for i in indices]
        max_idx = max(indices)
        min_idx = min(indices)
        vcount = max_idx + 1

        try:
            vinputs = pipe.GetVertexInputs()
            vbs = list(pipe.GetVBuffers())
        except Exception as e:
            return None, "GetVertexInputs/VBuffers failed: %s" % e

        vb_caches = {}
        for inp in vinputs:
            slot = inp.vertexBuffer
            if slot not in vb_caches:
                if slot >= len(vbs):
                    continue
                vb = vbs[slot]
                if vb.resourceId == rd.ResourceId.Null():
                    continue
                stride = vb.byteStride or 0
                if stride == 0:
                    continue
                bytes_needed = vcount * stride
                try:
                    vb_raw = controller.GetBufferData(
                        vb.resourceId,
                        vb.byteOffset + vertex_offset * stride,
                        bytes_needed,
                    )
                except Exception:
                    vb_raw = b""
                vb_caches[slot] = (vb_raw, stride)

        attributes = []
        for inp in vinputs:
            slot = inp.vertexBuffer
            if slot not in vb_caches:
                continue
            raw, stride = vb_caches[slot]
            fmt_name = str(inp.format.Name())
            comp_count = inp.format.compCount
            comp_bytewidth = inp.format.compByteWidth
            comp_type = str(inp.format.compType)
            values = []
            attr_offset = inp.byteOffset
            for vi in range(vcount):
                vert_start = vi * stride + attr_offset
                try:
                    v = _decode_attr(raw, vert_start, fmt_name, comp_count, comp_bytewidth, comp_type)
                except Exception:
                    v = [0.0] * comp_count
                values.append(v)
            attributes.append({
                "name": inp.name,
                "semantic_name": getattr(inp, "semanticName", ""),
                "semantic_index": getattr(inp, "semanticIndex", 0),
                "vertex_buffer_slot": slot,
                "byte_offset": attr_offset,
                "format": fmt_name,
                "components": comp_count,
                "values": values,
            })

        data = {
            "event_id": event_id,
            "topology": str(pipe.GetPrimitiveTopology()) if hasattr(pipe, "GetPrimitiveTopology") else "",
            "num_indices": num_indices,
            "num_vertices": vcount,
            "min_index": min_idx,
            "max_index": max_idx,
            "indices": indices,
            "attributes": attributes,
            "_diag": {
                "ib_resource": str(ib.resourceId),
                "ib_offset": ib.byteOffset,
                "ib_stride": ib_stride,
                "ib_raw_len": len(ib_raw),
                "vb_slot_to_resource": {
                    slot: str(vbs[slot].resourceId) for slot in vb_caches
                },
            },
        }
        return data, None

    # ------------------------------------------------------------------ #
    #  Internal: read VS $Globals matrices (ObjectToWorld / WorldToObject)#
    # ------------------------------------------------------------------ #
    def _read_unity_matrices(self, controller, pipe,
                             o2w_offset=32, w2o_offset=96):
        """Read the VS cb0 ($Globals) and pull the two Unity transform matrices.

        Offsets default to the layout observed for this shader family
        (ObjectToWorld @ byte 32, WorldToObject @ byte 96). Returns
        (object_to_world_columns, world_to_object_rows, diag) or (None, None, diag).
        """
        diag = {}
        stage = rd.ShaderStage.Vertex
        bind = _get_cbuffer_bind(pipe, stage, 0)
        if bind is None:
            diag["cbuffer_api"] = _probe_cbuffer_api(pipe)
            diag["error"] = "no usable constant-buffer accessor on PipeState"
            return None, None, diag
        rid, boff, bsize = bind
        diag["cb_resource"] = str(rid)
        diag["cb_offset"] = boff
        diag["cb_size"] = bsize
        need = max(o2w_offset, w2o_offset) + 64
        try:
            raw = controller.GetBufferData(rid, boff, max(bsize, need))
        except Exception as e:
            diag["error"] = "GetBufferData(cb) failed: %s" % e
            return None, None, diag
        if len(raw) < need:
            diag["error"] = "cb too small: got %d need %d" % (len(raw), need)
            return None, None, diag
        o2w = list(struct.unpack_from("<16f", raw, o2w_offset))
        w2o = list(struct.unpack_from("<16f", raw, w2o_offset))
        diag["object_to_world_raw"] = o2w
        diag["world_to_object_raw"] = w2o
        return _mat_columns_from_floats(o2w), _mat_columns_from_floats(w2o), diag

    # ------------------------------------------------------------------ #
    #  Public: original in-memory mesh data                               #
    # ------------------------------------------------------------------ #
    def get_mesh_data(self, event_id):
        """Read IB + VBs and decode vertex attributes for the given event_id."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            result["data"], result["error"] = self._extract(controller, event_id)

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    # ------------------------------------------------------------------ #
    #  Public: read the VS world matrices for an event                    #
    # ------------------------------------------------------------------ #
    def get_world_matrix(self, event_id, o2w_offset=32, w2o_offset=96):
        """Return Unity ObjectToWorld / WorldToObject for the draw at event_id.

        Reads the VS cb0 ($Globals). Returns a small dict (no vertex data):
            object_to_world_columns / world_to_object_rows : list[list[float]] (4x4)
            object_to_world_raw / world_to_object_raw       : flat 16-float arrays
            _diag : which cbuffer accessor / resource was used
        """
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                controller.SetFrameEvent(int(event_id), True)
            except Exception as e:
                result["error"] = "SetFrameEvent failed: %s" % e
                return
            pipe = controller.GetPipelineState()
            o2w, w2o, diag = self._read_unity_matrices(
                controller, pipe, o2w_offset, w2o_offset)
            if o2w is None:
                result["error"] = "matrix read failed: %s | diag=%s" % (
                    diag.get("error", "?"), diag)
                return
            result["data"] = {
                "event_id": event_id,
                "object_to_world_columns": [list(c) for c in o2w],
                "world_to_object_rows": [list(r) for r in w2o],
                "object_to_world_raw": diag.pop("object_to_world_raw", None),
                "world_to_object_raw": diag.pop("world_to_object_raw", None),
                "_diag": diag,
            }

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    # ------------------------------------------------------------------ #
    #  Internal: read MatrixVP from VS cb ($Globals), invert to VP^-1     #
    # ------------------------------------------------------------------ #
    def _read_matrix_vp(self, controller, event_id):
        """Locate hlslcc_mtx4x4unity_MatrixVP in a VS constant block and return it.

        CRITICAL: in OpenGL the VS $Globals block is a non-UBO loose-uniform
        "default block" (buffer_backed=False, byte_size=0). GetBufferData by byte
        offset CANNOT read it -- it misreads adjacent UBOs (cb0 ObjectToWorld). The
        only correct accessor is controller.GetCBufferVariableContents, the same API
        the get_cbuffer_contents tool uses. We mirror pipeline_service._get_cbuffer_info.

        Returns (vp_cols, diag): vp_cols is a list of 4 float4s. For hlslcc these are
        the COLUMNS of the matrix M where clip = M @ world (verified empirically: the
        w-component of cols 0..2 equals the camera forward vector). export_postvs_to_file
        rebuilds M as M[r][c] = vp_cols[c][r], then inverts. Or (None, diag) on failure.
        """
        diag = {}
        pipe = controller.GetPipelineState()
        stage = rd.ShaderStage.Vertex
        refl = pipe.GetShaderReflection(stage)
        if not refl:
            diag["error"] = "no VS reflection"
            return None, diag

        shader_id = refl.resourceId
        try:
            entry = pipe.GetShaderEntryPoint(stage)
        except Exception:
            entry = getattr(refl, "entryPoint", "")
        try:
            pipe_obj = pipe.GetGraphicsPipelineObject()
        except Exception:
            pipe_obj = rd.ResourceId.Null()

        blocks = list(getattr(refl, "constantBlocks", []) or [])
        diag["blocks"] = [getattr(b, "name", "") for b in blocks]

        def _find_named(vars_list, needle):
            """Recursively locate the variable whose name contains `needle`."""
            for v in vars_list:
                nm = getattr(v, "name", "") or ""
                if needle in nm:
                    return v
                mem = getattr(v, "members", None)
                if mem:
                    found = _find_named(list(mem), needle)
                    if found is not None:
                        return found
            return None

        def _find_camera_pos(vars_list):
            """Locate _WorldSpaceCameraPos (float3) for camera-relative VP rebuild."""
            for v in vars_list:
                nm = getattr(v, "name", "") or ""
                if "WorldSpaceCameraPos" in nm:
                    try:
                        return [float(x) for x in v.value.f32v[:3]]
                    except Exception:
                        return None
                mem = getattr(v, "members", None)
                if mem:
                    found = _find_camera_pos(list(mem))
                    if found is not None:
                        return found
            return None

        def _extract_4x4(var):
            """Pull 4 float4s out of a MatrixVP variable (member array or flat 16)."""
            members = list(getattr(var, "members", []) or [])
            if len(members) >= 4:
                rows = []
                for m in members[:4]:
                    try:
                        rows.append([float(x) for x in m.value.f32v[:4]])
                    except Exception:
                        return None
                return rows
            # No members: the variable itself may carry 16 floats (4x4 matrix var).
            try:
                flat = [float(x) for x in var.value.f32v[:16]]
                if len(flat) >= 16:
                    return [flat[i * 4:i * 4 + 4] for i in range(4)]
            except Exception:
                pass
            return None

        # Read every constant block's variables once, then search them.
        all_vars = []
        attempts = []
        for i, blk in enumerate(blocks):
            try:
                used = pipe.GetConstantBlock(stage, i, 0)
                desc = used.descriptor
                buf_id = desc.resource
                variables = controller.GetCBufferVariableContents(
                    pipe_obj,
                    shader_id,
                    stage,
                    entry,
                    i,
                    buf_id,
                    getattr(desc, "byteOffset", 0),
                    getattr(desc, "byteSize", 0),
                )
            except Exception as e:
                attempts.append((i, "GetCBufferVariableContents err: %s" % e))
                continue
            all_vars.append((i, blk, list(variables or [])))

        # Preferred: standard unity_MatrixVP (clip = MatrixVP @ world, no camera offset).
        for i, blk, variables in all_vars:
            target = _find_named(variables, "MatrixVP")
            if target is None:
                continue
            cols = _extract_4x4(target)
            if cols is None:
                attempts.append((i, "found MatrixVP but could not read 16 floats"))
                continue
            diag["block_index"] = i
            diag["block_name"] = getattr(blk, "name", "")
            diag["var_name"] = getattr(target, "name", "")
            diag["matrix_vp_raw"] = [x for row in cols for x in row]
            diag["camera_relative"] = False
            return cols, diag

        # Fallback: camera-relative VP (e.g. NSH ZeroViewProjMatrix).
        # The shader computes clip = ZeroViewProj @ (world - cameraPos), so the
        # caller must add cameraPos back after the inverse-transform. We surface
        # the camera position via diag["camera_offset"].
        for i, blk, variables in all_vars:
            target = _find_named(variables, "ZeroViewProjMatrix")
            if target is None:
                continue
            cols = _extract_4x4(target)
            if cols is None:
                attempts.append((i, "found ZeroViewProjMatrix but could not read 16 floats"))
                continue
            cam = None
            for _, _, vs in all_vars:
                cam = _find_camera_pos(vs)
                if cam is not None:
                    break
            diag["block_index"] = i
            diag["block_name"] = getattr(blk, "name", "")
            diag["var_name"] = getattr(target, "name", "")
            diag["matrix_vp_raw"] = [x for row in cols for x in row]
            diag["camera_relative"] = True
            diag["camera_offset"] = cam
            return cols, diag

        diag["error"] = "MatrixVP/ZeroViewProjMatrix not found via GetCBufferVariableContents"
        diag["attempts"] = attempts
        return None, diag

    # ------------------------------------------------------------------ #
    #  Internal: PostVS (post-vertex-shader / skinned) data extraction    #
    # ------------------------------------------------------------------ #
    def _extract_postvs(self, controller, event_id, instance=0, view=0):
        """Read VS-output (post-transform, post-skinning) vertices for a draw.

        Returns (data_dict, error). data_dict has indices + per-vertex clip-space
        SV_Position (and any float varyings). The heavy lifting of clip->world is
        done by the caller using VP^-1.
        """
        try:
            controller.SetFrameEvent(int(event_id), True)
        except Exception as e:
            return None, "SetFrameEvent failed: %s" % e

        action = self.ctx.GetAction(int(event_id))
        if action is None:
            return None, "No action at event_id=%d" % event_id

        try:
            postvs = controller.GetPostVSData(int(instance), int(view),
                                              rd.MeshDataStage.VSOut)
        except Exception as e:
            return None, "GetPostVSData failed: %s" % e

        vtx_rid = getattr(postvs, "vertexResourceId", None)
        if vtx_rid is None or vtx_rid == rd.ResourceId.Null():
            return None, "PostVS has no vertex data (vertexResourceId null)"

        vtx_stride = int(getattr(postvs, "vertexByteStride", 0))
        vtx_off = int(getattr(postvs, "vertexByteOffset", 0))
        disp_indices = int(getattr(postvs, "numIndices", 0))
        if disp_indices <= 0 or vtx_stride <= 0:
            return None, "PostVS empty (numIndices=%d stride=%d)" % (disp_indices, vtx_stride)

        # The PostVS format describes the first element (usually SV_Position float4).
        fmt = getattr(postvs, "format", None)
        pos_comp = getattr(fmt, "compCount", 4) if fmt else 4

        # ---- Read the PostVS index buffer FIRST. For an indexed draw the PostVS
        # vertex buffer holds only the UNIQUE post-transform vertices, indexed by
        # this IB; numIndices is the *display* index count (16962 here), NOT the
        # unique vertex count. Using it to size the VB read overruns the buffer.
        idx_rid = getattr(postvs, "indexResourceId", None)
        idx_stride = int(getattr(postvs, "indexByteStride", 0))
        idx_off = int(getattr(postvs, "indexByteOffset", 0))
        base_vtx = int(getattr(postvs, "baseVertex", 0))
        indices = []
        has_ib = (idx_rid is not None and idx_rid != rd.ResourceId.Null()
                  and idx_stride > 0)
        if has_ib:
            try:
                iraw = controller.GetBufferData(idx_rid, idx_off,
                                                disp_indices * idx_stride)
                cnt = len(iraw) // idx_stride
                if idx_stride == 2:
                    indices = list(struct.unpack_from("<%dH" % cnt, iraw, 0))
                else:
                    indices = list(struct.unpack_from("<%dI" % cnt, iraw, 0))
                if base_vtx:
                    indices = [ix + base_vtx for ix in indices]
            except Exception:
                indices = []

        # Derive the unique-vertex count: max index + 1 for indexed draws,
        # otherwise the display count (non-indexed: verts already expanded).
        if indices:
            num_verts = max(indices) + 1
        else:
            num_verts = disp_indices

        # Read the VB, then clamp to what the buffer actually contains so a stale
        # estimate can never overrun struct.unpack_from.
        try:
            vbytes = controller.GetBufferData(vtx_rid, vtx_off,
                                              num_verts * vtx_stride)
        except Exception as e:
            return None, "GetBufferData(PostVS vtx) failed: %s" % e
        avail = len(vbytes) // vtx_stride
        if avail < num_verts:
            num_verts = avail
        if num_verts <= 0:
            return None, "PostVS VB empty after read (bytes=%d stride=%d)" % (
                len(vbytes), vtx_stride)

        # Decode SV_Position (clip space) as the first compCount floats per vertex.
        positions = []
        for i in range(num_verts):
            base = i * vtx_stride
            comps = struct.unpack_from("<%df" % pos_comp, vbytes, base)
            if len(comps) < 4:
                comps = tuple(comps) + (1.0,) * (4 - len(comps))
            positions.append(list(comps[:4]))

        # Drop indices that fall outside the decoded vertex range (safety).
        if indices:
            indices = [ix for ix in indices if 0 <= ix < num_verts]
        else:
            indices = list(range(num_verts))

        return {
            "num_vertices": num_verts,
            "num_indices": len(indices),
            "indices": indices,
            "clip_positions": positions,
            "_diag": {
                "vtx_resource": str(vtx_rid),
                "vtx_stride": vtx_stride,
                "pos_comp": pos_comp,
                "disp_indices": disp_indices,
                "has_postvs_ib": has_ib,
            },
        }, None

    # ------------------------------------------------------------------ #
    #  Public: export PostVS (skinned) world-space mesh to JSON           #
    # ------------------------------------------------------------------ #
    def _graft_input_uvs(self, controller, event_id, num_verts,
                         uv0_slot, uv1_slot, color_slot):
        """Pull pose-invariant attrs (uv0/uv1/color) from the INPUT VB for PostVS.

        The PostVS fast path decodes only SV_Position; its other varyings lose their
        semantic names (generic interpolators), so we cannot reliably label them uv0/
        uv1. But UVs and vertex color are POSE-INVARIANT — skinning/vertex-anim moves
        position & normal, never texcoords. For an INDEXED draw the PostVS unique-
        vertex order matches the input VB 1:1 (same IB, num_verts = max(index)+1), so
        input-VB attribute i maps onto PostVS vertex i directly.

        Returns (graft_dict, diag). graft_dict may contain "uv0"/"uv1"/"color".
        Grafting is skipped (with a diag reason) if the input vertex count differs.
        """
        diag = {}
        data, err = self._extract(controller, event_id)
        if err:
            diag["skipped"] = "input _extract failed: %s" % err
            return {}, diag

        in_n = data["num_vertices"]
        diag["input_num_vertices"] = in_n
        if in_n != num_verts:
            # 1:1 correspondence no longer guaranteed (e.g. non-indexed draw or a
            # PostVS that re-emits vertices). Don't graft mismatched data.
            diag["skipped"] = "vertex count mismatch (input=%d postvs=%d)" % (
                in_n, num_verts)
            return {}, diag

        attrs_by_name = {(a.get("name") or "").upper(): a for a in data["attributes"]}
        slot_attrs = {}
        for a in data["attributes"]:
            slot_attrs.setdefault(a["vertex_buffer_slot"], []).append(a)
        has_named = any(
            tok in nm for nm in attrs_by_name
            for tok in ("POSITION", "NORMAL", "TANGENT", "TEXCOORD", "COLOR")
        )

        def by_name(keyword):
            if not keyword:
                return None
            for nm, a in attrs_by_name.items():
                if keyword in nm:
                    return a
            return None

        def by_slot(slot):
            here = slot_attrs.get(slot)
            return here[0] if here else None

        def resolve(slot, keyword):
            if has_named:
                return by_name(keyword)
            return by_slot(slot)

        graft = {}
        uv0 = resolve(uv0_slot, "TEXCOORD0")
        uv1 = resolve(uv1_slot, "TEXCOORD1")
        col = resolve(color_slot, "COLOR")
        if uv0 is not None:
            graft["uv0"] = [[v[0], v[1]] for v in uv0["values"]]
        if uv1 is not None:
            graft["uv1"] = [[v[0], v[1]] for v in uv1["values"]]
        if col is not None:
            graft["color"] = [list(v) for v in col["values"]]
        diag["resolved_by"] = "name" if has_named else "slot"
        diag["grafted"] = sorted(graft.keys())
        diag["available_names"] = sorted(attrs_by_name)
        return graft, diag

    def export_postvs_to_file(self, event_id, output_path, instance=0, view=0,
                              graft_uv=True, uv0_slot=3, uv1_slot=4, color_slot=1):
        """Extract VS-output (skinned, post-transform) vertices and write world-space JSON.

        For SKINNED meshes the input VB holds bind-pose vertices; the GPU skins them
        in the VS. RenderDoc captures the VS OUTPUT (PostVS) — the actual on-screen
        geometry. SV_Position is in CLIP space; we invert MatrixVP (clip = VP @ world)
        to recover WORLD-space positions that match the captured frame exactly.

        The output JSON matches export_mesh_to_file's schema (num_indices,
        num_vertices, indices, position) so RDCMeshBuilder can consume it directly.
        position is WORLD space (place the GameObject at Transform origin; align the
        RDC camera with absolute coords).

        UV0/UV1 (and vertex COLOR) are POSE-INVARIANT, so when graft_uv is True
        (default) they are copied from the INPUT VB onto the matching PostVS vertices
        (1:1 for indexed draws). This makes the PostVS .asset texture-mappable without
        a manual graft step. Normals/tangents are still omitted (bind-pose values are
        wrong for the skinned pose; Unity recomputes them). uv0_slot/uv1_slot/color_slot
        are used only when input attributes are generically named (DXBC/ANGLE); GL
        hlslcc captures resolve by semantic name automatically.
        """
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
          try:
            pv, err = self._extract_postvs(controller, event_id, instance, view)
            if err:
                result["error"] = err
                return
            vp_rows, vpdiag = self._read_matrix_vp(controller, event_id)
            if vp_rows is None:
                result["error"] = "MatrixVP read failed: %s | %s" % (
                    vpdiag.get("error", "?"), vpdiag)
                return

            # hlslcc stores the 4 float4s as COLUMNS of M where clip = M @ world.
            # Build M (row-major 4x4) then invert.
            cols = vp_rows  # each cols[i] is a column
            M = [[cols[c][r] for c in range(4)] for r in range(4)]
            Minv = _invert4x4(M)
            if Minv is None:
                result["error"] = "MatrixVP not invertible"
                return

            # Camera-relative VP (ZeroViewProjMatrix) reconstructs (world - cameraPos);
            # add cameraPos back to recover absolute world coordinates.
            cam = vpdiag.get("camera_offset") if vpdiag.get("camera_relative") else None
            cx, cy, cz = (cam if cam else (0.0, 0.0, 0.0))

            clip = pv["clip_positions"]
            out_pos = []
            bad = 0
            for cp in clip:
                # world = Minv @ clip (homogeneous), then divide by w.
                wx = Minv[0][0]*cp[0]+Minv[0][1]*cp[1]+Minv[0][2]*cp[2]+Minv[0][3]*cp[3]
                wy = Minv[1][0]*cp[0]+Minv[1][1]*cp[1]+Minv[1][2]*cp[2]+Minv[1][3]*cp[3]
                wz = Minv[2][0]*cp[0]+Minv[2][1]*cp[1]+Minv[2][2]*cp[2]+Minv[2][3]*cp[3]
                ww = Minv[3][0]*cp[0]+Minv[3][1]*cp[1]+Minv[3][2]*cp[2]+Minv[3][3]*cp[3]
                if abs(ww) < 1e-12:
                    bad += 1
                    out_pos.append([0.0, 0.0, 0.0])
                else:
                    out_pos.append([wx/ww + cx, wy/ww + cy, wz/ww + cz])

            raw_json = {
                "event_id": event_id,
                "baked_world": True,
                "source": "postvs",
                "num_indices": pv["num_indices"],
                "num_vertices": pv["num_vertices"],
                "indices": pv["indices"],
                "position": out_pos,
            }

            # Graft pose-invariant UV0/UV1/COLOR from the input VB (1:1 for indexed
            # draws). PostVS varyings lose semantic names, so this is the reliable
            # way to keep the skinned mesh texture-mappable.
            graft_diag = {}
            if graft_uv:
                try:
                    graft, graft_diag = self._graft_input_uvs(
                        controller, event_id, pv["num_vertices"],
                        uv0_slot, uv1_slot, color_slot)
                    raw_json.update(graft)
                except Exception as e:
                    import traceback
                    graft_diag = {"error": "%s\n%s" % (e, traceback.format_exc())}

            try:
                out_dir = os.path.dirname(output_path)
                if out_dir and not os.path.isdir(out_dir):
                    os.makedirs(out_dir)
                with open(output_path, "w", encoding="utf-8") as fh:
                    json.dump(raw_json, fh)
            except Exception as e:
                result["error"] = "write failed: %s" % e
                return

            def rng(arr, comp):
                if not arr:
                    return None
                return [min(r[comp] for r in arr), max(r[comp] for r in arr)]

            result["data"] = {
                "event_id": event_id,
                "output_path": output_path,
                "source": "postvs",
                "baked_world": True,
                "num_indices": pv["num_indices"],
                "num_vertices": pv["num_vertices"],
                "degenerate_w": bad,
                "position_bounds": {
                    "x": rng(out_pos, 0), "y": rng(out_pos, 1), "z": rng(out_pos, 2)
                },
                "has_uv0": "uv0" in raw_json,
                "has_uv1": "uv1" in raw_json,
                "has_color": "color" in raw_json,
                "matrix_vp_raw": vpdiag.get("matrix_vp_raw"),
                "_postvs_diag": pv.get("_diag"),
                "_graft_diag": graft_diag,
                "_vp_diag": {k: v for k, v in vpdiag.items() if k != "matrix_vp_raw"},
            }
          except Exception as e:
            import traceback
            result["error"] = "export_postvs_to_file error: %s\n%s" % (
                str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    # ------------------------------------------------------------------ #
    #  Public: extract mesh, optionally bake to world, write JSON to disk #
    # ------------------------------------------------------------------ #
    def export_mesh_to_file(self, event_id, output_path, bake_world=True,
                            pos_slot=-1, normal_slot=-1, tangent_slot=-1,
                            uv0_slot=-1, uv1_slot=-1, extra_slot=-1,
                            o2w_offset=32, w2o_offset=96):
        """Decode the mesh at event_id and write a compact raw JSON to disk.

        Avoids returning huge payloads through the MCP transport: the per-vertex
        arrays are written to *output_path* on the RenderDoc host, and only small
        metadata (counts, value ranges, the world matrix, output path) is returned.

        Negative attribute slots use automatic semantic/component inference.
        Non-negative slots are explicit caller overrides.

        When bake_world is True, position/normal/tangent.xyz are transformed into
        world space using the VS cb0 matrices, so the resulting GameObject can sit
        at the Transform origin and still match the captured frame.
        """
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
          try:
            data, err = self._extract(controller, event_id)
            if err:
                result["error"] = err
                return

            # Negative slots mean auto: use semantics when present, then fall back
            # to component counts in slot order. Non-negative slots remain explicit
            # caller overrides.
            attrs = list(data["attributes"])
            slot_attrs = {}
            for a in attrs:
                slot_attrs.setdefault(a["vertex_buffer_slot"], []).append(a)
            attrs_by_name = {
                (a.get("name") or "").upper(): a for a in attrs
            }
            claimed = set()
            auto_used = False

            def attr_id(a):
                try:
                    return attrs.index(a)
                except ValueError:
                    return id(a)

            def by_name(keyword, min_components=0, exact_components=None):
                if not keyword:
                    return None
                for a in attrs:
                    if attr_id(a) in claimed:
                        continue
                    comps = _rdc_auto_attr_components(a)
                    if min_components and comps < min_components:
                        continue
                    if exact_components is not None and comps != exact_components:
                        continue
                    if _rdc_auto_attr_matches(a, keyword):
                        return a
                return None

            has_named_semantics = any(
                _rdc_auto_attr_matches(a, tok)
                for a in attrs
                for tok in (
                    "POSITION", "NORMAL", "TANGENT",
                    "TEXCOORD0", "TEXCOORD1",
                )
            )

            def by_slot(slot, name_keyword=None):
                here = slot_attrs.get(slot)
                if not here:
                    return None
                if name_keyword:
                    for a in here:
                        if _rdc_auto_attr_matches(a, name_keyword):
                            return a
                return here[0]

            def claim(a):
                if a is not None:
                    claimed.add(attr_id(a))
                return a

            def first_by_components(min_components=0, exact_components=None):
                for a in attrs:
                    if attr_id(a) in claimed:
                        continue
                    comps = _rdc_auto_attr_components(a)
                    if min_components and comps < min_components:
                        continue
                    if exact_components is not None and comps != exact_components:
                        continue
                    return a
                return None

            def resolve(slot, name_keyword):
                nonlocal auto_used
                if slot is not None and slot >= 0:
                    return claim(by_slot(slot, name_keyword))

                auto_used = True
                if has_named_semantics:
                    min_components = 0
                    if name_keyword in ("POSITION", "NORMAL", "TANGENT"):
                        min_components = 3
                    elif name_keyword in ("TEXCOORD0", "TEXCOORD1"):
                        min_components = 2
                    a = by_name(name_keyword, min_components=min_components)
                    if a is not None:
                        return claim(a)

                if name_keyword == "POSITION":
                    return claim(first_by_components(min_components=3))
                if name_keyword == "NORMAL":
                    return claim(first_by_components(exact_components=3))
                if name_keyword == "TANGENT":
                    return claim(first_by_components(exact_components=4))
                if name_keyword in ("TEXCOORD0", "TEXCOORD1"):
                    return claim(first_by_components(exact_components=2))
                return None

            def vals_of(a):
                return a["values"] if a else None

            def resolved_slot(a):
                return a.get("vertex_buffer_slot") if a is not None else None

            pos_attr = resolve(pos_slot, "POSITION")
            nrm_attr = resolve(normal_slot, "NORMAL")
            tan_attr = resolve(tangent_slot, "TANGENT")
            uv0_attr = resolve(uv0_slot, "TEXCOORD0")
            uv1_attr = resolve(uv1_slot, "TEXCOORD1")
            extra_attr = resolve(extra_slot, None)

            pos = vals_of(pos_attr)
            nrm = vals_of(nrm_attr)
            tan = vals_of(tan_attr)
            uv0 = _rdc_auto_vec2_list(vals_of(uv0_attr))
            uv1 = _rdc_auto_vec2_list(vals_of(uv1_attr))
            extra = vals_of(extra_attr)

            if not _rdc_auto_values_have_components(pos, 3):
                pos = None
            if not _rdc_auto_values_have_components(nrm, 3):
                nrm = None
            if not _rdc_auto_values_have_components(tan, 3):
                tan = None

            if pos is None:
                result["error"] = "no POSITION at slot %d (slots=%s, names=%s)" % (
                    pos_slot, sorted(slot_attrs), sorted(attrs_by_name))
                return

            o2w = w2o = None
            mdiag = {}
            if bake_world:
                pipe = controller.GetPipelineState()
                o2w, w2o, mdiag = self._read_unity_matrices(
                    controller, pipe, o2w_offset, w2o_offset)
                if o2w is None:
                    result["error"] = "bake requested but matrix read failed: %s | diag=%s" % (
                        mdiag.get("error", "?"), mdiag)
                    return

            n = data["num_vertices"]
            out_pos, out_nrm, out_tan = [], [], []
            for i in range(n):
                p = pos[i]
                if bake_world:
                    out_pos.append(_bake_position(o2w, p))
                else:
                    out_pos.append([p[0], p[1], p[2]])

                if nrm is not None:
                    nv = nrm[i]
                    if bake_world:
                        out_nrm.append(_bake_normal_worldtoobject(w2o, nv))
                    else:
                        out_nrm.append(_normalize3([nv[0], nv[1], nv[2]]))

                if tan is not None:
                    tv = tan[i]
                    tw = tv[3] if len(tv) > 3 else 1.0
                    if bake_world:
                        d = _bake_dir_objecttoworld(o2w, tv)
                    else:
                        d = _normalize3([tv[0], tv[1], tv[2]])
                    out_tan.append([d[0], d[1], d[2], tw])

            raw_json = {
                "event_id": event_id,
                "baked_world": bool(bake_world),
                "num_indices": data["num_indices"],
                "num_vertices": n,
                "indices": data["indices"],
                "position": out_pos,
            }
            if out_nrm:
                raw_json["normal"] = out_nrm
            if out_tan:
                raw_json["tangent"] = out_tan
            if uv0 is not None:
                raw_json["uv0"] = [[u[0], u[1]] for u in uv0]
            if uv1 is not None:
                raw_json["uv1"] = [[u[0], u[1]] for u in uv1]
            if extra is not None:
                raw_json["uv2_extra"] = [list(e) for e in extra]

            try:
                out_dir = os.path.dirname(output_path)
                if out_dir and not os.path.isdir(out_dir):
                    os.makedirs(out_dir)
                with open(output_path, "w", encoding="utf-8") as fh:
                    json.dump(raw_json, fh)
            except Exception as e:
                result["error"] = "write failed: %s" % e
                return

            def rng(arr, comp):
                if not arr:
                    return None
                lo = min(r[comp] for r in arr)
                hi = max(r[comp] for r in arr)
                return [lo, hi]

            result["data"] = {
                "event_id": event_id,
                "output_path": output_path,
                "baked_world": bool(bake_world),
                "num_indices": data["num_indices"],
                "num_vertices": n,
                "has_normal": bool(out_nrm),
                "has_tangent": bool(out_tan),
                "has_uv0": uv0 is not None,
                "has_uv1": uv1 is not None,
                "has_extra": extra is not None,
                "position_bounds": {
                    "x": rng(out_pos, 0), "y": rng(out_pos, 1), "z": rng(out_pos, 2)
                },
                "object_to_world_columns": [list(c) for c in o2w] if o2w else None,
                "world_to_object_rows": [list(r) for r in w2o] if w2o else None,
                "slot_map": {
                    "position": resolved_slot(pos_attr),
                    "normal": resolved_slot(nrm_attr) if nrm is not None else None,
                    "tangent": resolved_slot(tan_attr) if tan is not None else None,
                    "uv0": resolved_slot(uv0_attr) if uv0 is not None else None,
                    "uv1": resolved_slot(uv1_attr) if uv1 is not None else None,
                    "extra": resolved_slot(extra_attr) if extra is not None else None,
                },
                "available_slots": sorted(slot_attrs),
                "attribute_names": sorted(attrs_by_name),
                "resolved_by": "auto" if auto_used else "slot",
                "_matrix_diag": mdiag,
            }
          except Exception as e:
            import traceback
            result["error"] = "export_mesh_to_file error: %s\n%s" % (
                str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

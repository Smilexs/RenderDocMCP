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
    #  Public: extract mesh, optionally bake to world, write JSON to disk #
    # ------------------------------------------------------------------ #
    def export_mesh_to_file(self, event_id, output_path, bake_world=True,
                            pos_slot=0, normal_slot=1, tangent_slot=2,
                            uv0_slot=3, uv1_slot=4, extra_slot=5,
                            o2w_offset=32, w2o_offset=96):
        """Decode the mesh at event_id and write a compact raw JSON to disk.

        Avoids returning huge payloads through the MCP transport: the per-vertex
        arrays are written to *output_path* on the RenderDoc host, and only small
        metadata (counts, value ranges, the world matrix, output path) is returned.

        Attribute slots map TEXCOORDn vertex-buffer slots to semantics. Defaults
        match the observed Unity character VS layout
        (v0=POSITION v1=NORMAL v2=TANGENT v3=UV0 v4=UV1 v5=extra/aniso).

        When bake_world is True, position/normal/tangent.xyz are transformed into
        world space using the VS cb0 matrices, so the resulting GameObject can sit
        at the Transform origin and still match the captured frame.
        """
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            data, err = self._extract(controller, event_id)
            if err:
                result["error"] = err
                return

            attrs_by_slot = {a["vertex_buffer_slot"]: a for a in data["attributes"]}

            def vals(slot):
                a = attrs_by_slot.get(slot)
                return a["values"] if a else None

            pos = vals(pos_slot)
            nrm = vals(normal_slot)
            tan = vals(tangent_slot)
            uv0 = vals(uv0_slot)
            uv1 = vals(uv1_slot)
            extra = vals(extra_slot)

            if pos is None:
                result["error"] = "no POSITION at slot %d (slots=%s)" % (
                    pos_slot, sorted(attrs_by_slot))
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
                    "position": pos_slot, "normal": normal_slot, "tangent": tangent_slot,
                    "uv0": uv0_slot, "uv1": uv1_slot, "extra": extra_slot,
                },
                "available_slots": sorted(attrs_by_slot),
                "_matrix_diag": mdiag,
            }

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

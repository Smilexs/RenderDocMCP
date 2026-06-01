"""Mesh data extraction service. Reads IB + VBs at a given event_id and returns decoded vertices."""

import base64
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


class MeshService:
    """Mesh data extraction service."""

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn

    def get_mesh_data(self, event_id):
        """Read IB + VBs and decode vertex attributes for the given event_id.

        Returns a dict with:
            - num_indices, num_vertices, topology
            - indices: list[int]
            - attributes: list of {name, semantic_name, fmt, components, values: list[list]}
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

            # Get the action info to know index count
            action = self.ctx.GetAction(int(event_id))
            if action is None:
                result["error"] = "No action at event_id=%d" % event_id
                return
            num_indices = action.numIndices
            num_instances = max(1, action.numInstances)
            base_vertex = getattr(action, "baseVertex", 0)
            vertex_offset = getattr(action, "vertexOffset", 0)
            index_offset = getattr(action, "indexOffset", 0)

            # Index buffer
            try:
                ib = pipe.GetIBuffer()
            except Exception as e:
                result["error"] = "GetIBuffer failed: %s" % e
                return

            ib_stride = ib.byteStride if ib.byteStride else 2
            try:
                ib_raw = controller.GetBufferData(
                    ib.resourceId,
                    ib.byteOffset + index_offset * ib_stride,
                    num_indices * ib_stride,
                )
            except Exception as e:
                result["error"] = "GetBufferData(IB %s) failed: %s" % (str(ib.resourceId), e)
                return

            if ib_stride == 2:
                indices = list(struct.unpack("<%dH" % num_indices, ib_raw))
            else:
                indices = list(struct.unpack("<%dI" % num_indices, ib_raw))

            if not indices:
                result["error"] = "Index buffer returned empty (len=%d)" % len(ib_raw)
                return

            # Apply base_vertex
            if base_vertex:
                indices = [i + base_vertex for i in indices]
            max_idx = max(indices)
            min_idx = min(indices)
            vcount = max_idx + 1  # we read 0..max_idx so indexing matches

            # Vertex inputs + buffers
            try:
                vinputs = pipe.GetVertexInputs()
                vbs = list(pipe.GetVBuffers())
            except Exception as e:
                result["error"] = "GetVertexInputs/VBuffers failed: %s" % e
                return

            # Cache per-vb raw bytes
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
                    except Exception as e:
                        vb_raw = b""
                    vb_caches[slot] = (vb_raw, stride)

            # Decode all attributes for all vertices in [0..max_idx]
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

            result["data"] = {
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

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

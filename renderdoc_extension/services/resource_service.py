"""
Resource information service for RenderDoc.
"""

import base64

import renderdoc as rd

from ..utils import Parsers


class ResourceService:
    """Resource information service"""

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn

    def _find_texture_by_id(self, controller, resource_id):
        """Find texture by resource ID"""
        target_id = Parsers.extract_numeric_id(resource_id)
        for tex in controller.GetTextures():
            tex_id_str = str(tex.resourceId)
            tex_id = Parsers.extract_numeric_id(tex_id_str)
            if tex_id == target_id:
                return tex
        return None

    def _resource_id_int(self, resource_id):
        """Convert a RenderDoc ResourceId to a stable integer when possible."""
        try:
            return int(resource_id)
        except Exception:
            try:
                return Parsers.extract_numeric_id(str(resource_id))
            except Exception:
                return 0

    def _resource_name_lookup(self, controller):
        """Build {numeric_resource_id: name} from RenderDoc resources."""
        lookup = {}
        try:
            for res in controller.GetResources():
                rid = self._resource_id_int(res.resourceId)
                lookup[rid] = getattr(res, "name", "")
        except Exception:
            pass
        return lookup

    def _resource_type_lookup(self, controller):
        """Build {numeric_resource_id: resource type string}."""
        lookup = {}
        try:
            for res in controller.GetResources():
                rid = self._resource_id_int(res.resourceId)
                lookup[rid] = str(getattr(res, "type", "resource"))
        except Exception:
            pass
        return lookup

    def _find_buffer_by_id(self, controller, resource_id):
        """Find buffer by resource ID."""
        target_id = Parsers.extract_numeric_id(resource_id)
        for buf in controller.GetBuffers():
            if self._resource_id_int(buf.resourceId) == target_id:
                return buf
        return None

    def _find_resource_desc_by_id(self, controller, resource_id):
        """Find ResourceDescription by resource ID."""
        target_id = Parsers.extract_numeric_id(resource_id)
        try:
            for res in controller.GetResources():
                if self._resource_id_int(res.resourceId) == target_id:
                    return res
        except Exception:
            pass
        return None

    def _format_name(self, fmt):
        """Return a readable RenderDoc ResourceFormat name."""
        try:
            return str(fmt.Name())
        except Exception:
            try:
                return str(fmt.name)
            except Exception:
                return str(fmt)

    def _make_subresource(self, mip=0, slice=0, sample=0):
        """Create a RenderDoc Subresource compatibly across API versions."""
        try:
            return rd.Subresource(int(mip), int(slice), int(sample))
        except TypeError:
            pass
        sub = rd.Subresource()
        sub.mip = int(mip)
        sub.slice = int(slice)
        sub.sample = int(sample)
        return sub

    def _pixel_value_dict(self, value):
        """Serialize RenderDoc PixelValue-like objects."""
        data = {}
        for attr, key in (
            ("floatValue", "float"),
            ("uintValue", "uint"),
            ("sintValue", "sint"),
            ("unormValue", "unorm"),
            ("snormValue", "snorm"),
        ):
            try:
                channel_values = list(getattr(value, attr)[:4])
                data[key] = {
                    "r": channel_values[0],
                    "g": channel_values[1],
                    "b": channel_values[2],
                    "a": channel_values[3],
                }
            except Exception:
                pass
        if not data:
            data["raw"] = str(value)
        return data

    def _save_texture_result_ok(self, save_result):
        """Normalize RenderDoc SaveTexture return shapes."""
        if isinstance(save_result, bool):
            return save_result
        try:
            return save_result.code == rd.ResultCode.Succeeded
        except Exception:
            return bool(save_result)

    def _apply_texture_save_subresource(self, texsave, mip=0, slice=0, sample=0):
        """Set mip/slice/sample on TextureSave across RenderDoc versions."""
        try:
            texsave.mip = int(mip)
        except Exception:
            pass
        try:
            texsave.slice = self._make_subresource(mip, slice, sample)
        except Exception:
            try:
                texsave.slice.sliceIndex = int(slice)
            except Exception:
                pass
        try:
            texsave.sample.sampleIndex = int(sample)
        except Exception:
            pass

    def _build_texture_save(self, tex_desc, dest_type, alpha_enum,
                            mip=0, slice=0, sample=0):
        """Create a configured TextureSave object."""
        texsave = rd.TextureSave()
        texsave.resourceId = tex_desc.resourceId
        texsave.destType = dest_type
        texsave.alpha = alpha_enum
        self._apply_texture_save_subresource(texsave, mip, slice, sample)

        # Typeless formats (e.g. R16_TYPELESS depth/shadow) need an explicit
        # typecast so SaveTexture can interpret the bits.
        try:
            fmt_name = str(tex_desc.format.Name())
            if "TYPELESS" in fmt_name.upper():
                texsave.typeCast = rd.CompType.UNorm
        except Exception:
            pass
        return texsave

    def get_textures(self):
        """List all texture resources alive in the current capture."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"textures": None, "error": None}

        def callback(controller):
            try:
                name_lookup = self._resource_name_lookup(controller)
                textures = []
                for tex in controller.GetTextures():
                    rid = self._resource_id_int(tex.resourceId)
                    textures.append({
                        "resource_id": str(tex.resourceId),
                        "id": rid,
                        "name": name_lookup.get(rid, ""),
                        "width": getattr(tex, "width", 0),
                        "height": getattr(tex, "height", 0),
                        "depth": getattr(tex, "depth", 0),
                        "array_size": getattr(tex, "arraysize", 0),
                        "mip_levels": getattr(tex, "mips", 0),
                        "format": self._format_name(getattr(tex, "format", "")),
                        "dimension": str(getattr(tex, "type", "")),
                        "msaa_samples": getattr(tex, "msSamp", 0),
                        "byte_size": getattr(tex, "byteSize", 0),
                        "cubemap": bool(getattr(tex, "cubemap", False)),
                    })
                result["textures"] = {
                    "count": len(textures),
                    "textures": textures,
                }
            except Exception as e:
                import traceback
                result["error"] = "get_textures error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["textures"]

    def get_buffers(self):
        """List all buffer resources alive in the current capture."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"buffers": None, "error": None}

        def callback(controller):
            try:
                name_lookup = self._resource_name_lookup(controller)
                buffers = []
                for buf in controller.GetBuffers():
                    rid = self._resource_id_int(buf.resourceId)
                    buffers.append({
                        "resource_id": str(buf.resourceId),
                        "id": rid,
                        "name": name_lookup.get(rid, ""),
                        "length": getattr(buf, "length", 0),
                        "creation_flags": str(getattr(buf, "creationFlags", "")),
                        "byte_stride": getattr(buf, "byteStride", 0),
                        "structure_byte_stride": getattr(buf, "structureByteStride", 0),
                    })
                result["buffers"] = {
                    "count": len(buffers),
                    "buffers": buffers,
                }
            except Exception as e:
                import traceback
                result["error"] = "get_buffers error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["buffers"]

    def get_resources(self):
        """List all RenderDoc resources with inferred broad resource type."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"resources": None, "error": None}

        def callback(controller):
            try:
                texture_ids = set()
                buffer_ids = set()
                for tex in controller.GetTextures():
                    texture_ids.add(self._resource_id_int(tex.resourceId))
                for buf in controller.GetBuffers():
                    buffer_ids.add(self._resource_id_int(buf.resourceId))

                resources = []
                for res in controller.GetResources():
                    rid = self._resource_id_int(res.resourceId)
                    if rid in texture_ids:
                        res_type = "texture"
                    elif rid in buffer_ids:
                        res_type = "buffer"
                    else:
                        res_type = str(getattr(res, "type", "resource"))
                    resources.append({
                        "resource_id": str(res.resourceId),
                        "id": rid,
                        "name": getattr(res, "name", ""),
                        "type": res_type,
                    })

                result["resources"] = {
                    "count": len(resources),
                    "resources": resources,
                }
            except Exception as e:
                import traceback
                result["error"] = "get_resources error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["resources"]

    def get_resource_info(self, resource_id):
        """Get detailed metadata for a texture, buffer, or generic resource."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                rid_obj = Parsers.parse_resource_id(resource_id)
                rid_int = self._resource_id_int(rid_obj)
                res_desc = self._find_resource_desc_by_id(controller, resource_id)
                tex_desc = self._find_texture_by_id(controller, resource_id)
                buf_desc = self._find_buffer_by_id(controller, resource_id)

                if res_desc is None and tex_desc is None and buf_desc is None:
                    result["error"] = "Resource not found: %s" % resource_id
                    return

                data = {
                    "resource_id": str(getattr(res_desc, "resourceId", rid_obj)),
                    "id": rid_int,
                    "name": getattr(res_desc, "name", ""),
                    "type": str(getattr(res_desc, "type", "resource")),
                }

                if tex_desc is not None:
                    data.update({
                        "type": "texture" if "Swapchain" not in data["type"] else data["type"],
                        "width": getattr(tex_desc, "width", 0),
                        "height": getattr(tex_desc, "height", 0),
                        "depth": getattr(tex_desc, "depth", 0),
                        "array_size": getattr(tex_desc, "arraysize", 0),
                        "mip_levels": getattr(tex_desc, "mips", 0),
                        "format": self._format_name(getattr(tex_desc, "format", "")),
                        "dimension": str(getattr(tex_desc, "type", "")),
                        "msaa_samples": getattr(tex_desc, "msSamp", 0),
                        "byte_size": getattr(tex_desc, "byteSize", 0),
                        "cubemap": bool(getattr(tex_desc, "cubemap", False)),
                    })
                    fmt = getattr(tex_desc, "format", None)
                    if fmt is not None:
                        bgra_order = getattr(fmt, "BGRAOrder", False)
                        if callable(bgra_order):
                            bgra_order = bgra_order()
                        data["format_details"] = {
                            "name": self._format_name(fmt),
                            "component_count": getattr(fmt, "compCount", 0),
                            "component_byte_width": getattr(fmt, "compByteWidth", 0),
                            "component_type": str(getattr(fmt, "compType", "")),
                            "bgra_order": bool(bgra_order),
                        }

                if buf_desc is not None:
                    data.update({
                        "type": "buffer",
                        "length": getattr(buf_desc, "length", 0),
                        "byte_stride": getattr(buf_desc, "byteStride", 0),
                        "structure_byte_stride": getattr(buf_desc, "structureByteStride", 0),
                        "creation_flags": str(getattr(buf_desc, "creationFlags", "")),
                    })
                    try:
                        data["gpu_address"] = getattr(buf_desc, "gpuAddress", 0)
                    except Exception:
                        pass

                result["data"] = data
            except Exception as e:
                import traceback
                result["error"] = "get_resource_info error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def get_resource_usage(self, resource_id):
        """Get frame usage history for one resource."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                rid = Parsers.parse_resource_id(resource_id)
                structured_file = controller.GetStructuredFile()
                action_names = {}

                def collect(actions):
                    for action in actions:
                        try:
                            action_names[int(action.eventId)] = action.GetName(structured_file)
                        except Exception:
                            pass
                        try:
                            collect(action.children)
                        except Exception:
                            pass

                collect(controller.GetRootActions())

                entries = []
                for usage in controller.GetUsage(rid):
                    event_id = int(getattr(usage, "eventId", getattr(usage, "eventID", 0)))
                    usage_name = str(getattr(usage, "usage", ""))
                    entries.append({
                        "event_id": event_id,
                        "name": action_names.get(event_id, ""),
                        "usage": usage_name,
                        "access": self._usage_access(usage_name),
                    })

                result["data"] = {
                    "resource_id": resource_id,
                    "count": len(entries),
                    "usages": entries,
                }
            except Exception as e:
                import traceback
                result["error"] = "get_resource_usage error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def _usage_access(self, usage_name):
        """Classify a RenderDoc ResourceUsage string as read/write/other."""
        write_hints = (
            "ColorTarget", "DepthStencilTarget", "CopyDst", "Clear",
            "GenMips", "ResolveDst", "RWResource", "CPUWrite",
        )
        read_hints = (
            "VertexBuffer", "IndexBuffer", "Constants", "Resource",
            "InputTarget", "CopySrc", "ResolveSrc", "Indirect",
        )
        is_write = any(hint in usage_name for hint in write_hints)
        is_read = any(hint in usage_name for hint in read_hints) or "RWResource" in usage_name
        if is_read and is_write:
            return "read_write"
        if is_write:
            return "write"
        if is_read:
            return "read"
        return "other"

    def get_buffer_contents(self, resource_id, offset=0, length=0, event_id=None):
        """Get buffer data. Optionally set frame event first for transient buffers."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            # Optionally set frame event so transient buffers are valid
            if event_id is not None:
                try:
                    controller.SetFrameEvent(int(event_id), True)
                except Exception:
                    pass

            # Parse resource ID
            try:
                rid = Parsers.parse_resource_id(resource_id)
            except Exception:
                result["error"] = "Invalid resource ID: %s" % resource_id
                return

            # Find buffer (may not exist in GetBuffers() for transient/internal buffers)
            buf_desc = None
            try:
                for buf in controller.GetBuffers():
                    if buf.resourceId == rid:
                        buf_desc = buf
                        break
            except Exception:
                pass

            actual_length = length if length > 0 else (buf_desc.length if buf_desc else 0)

            try:
                data = controller.GetBufferData(rid, offset, actual_length)
            except Exception as e:
                result["error"] = "GetBufferData failed for %s: %s" % (resource_id, str(e))
                return

            # Diagnostic: list buffer count
            try:
                bufs_count = len(controller.GetBuffers())
            except Exception:
                bufs_count = -1

            result["data"] = {
                "resource_id": resource_id,
                "length": len(data),
                "total_size": buf_desc.length if buf_desc else len(data),
                "offset": offset,
                "content_base64": base64.b64encode(data).decode("ascii"),
                "_diag_buffers_count": bufs_count,
                "_diag_rid_id": rid.id if hasattr(rid, 'id') else -1,
                "_diag_buf_desc_found": buf_desc is not None,
            }

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def pick_pixel(self, resource_id, x, y, mip=0, slice=0, sample=0):
        """Read one pixel value from a texture or render target."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                tex_desc = self._find_texture_by_id(controller, resource_id)
                if not tex_desc:
                    result["error"] = "Texture not found: %s" % resource_id
                    return

                sub = self._make_subresource(mip, slice, sample)
                comp_type = getattr(rd.CompType, "Typeless", rd.CompType.Float)
                value = controller.PickPixel(
                    tex_desc.resourceId, int(x), int(y), sub, comp_type)

                result["data"] = {
                    "resource_id": resource_id,
                    "x": int(x),
                    "y": int(y),
                    "mip": int(mip),
                    "slice": int(slice),
                    "sample": int(sample),
                    "format": self._format_name(tex_desc.format),
                    "value": self._pixel_value_dict(value),
                }
            except Exception as e:
                import traceback
                result["error"] = "pick_pixel error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def pixel_history(self, resource_id, x, y, mip=0, slice=0, sample=0):
        """Get the modification history for one pixel across the frame."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                tex_desc = self._find_texture_by_id(controller, resource_id)
                if not tex_desc:
                    result["error"] = "Texture not found: %s" % resource_id
                    return

                sub = self._make_subresource(mip, slice, sample)
                comp_type = getattr(rd.CompType, "Typeless", rd.CompType.Float)
                modifications = controller.PixelHistory(
                    tex_desc.resourceId, int(x), int(y), sub, comp_type)

                history = []
                for mod in modifications:
                    entry = {
                        "event_id": getattr(mod, "eventId", 0),
                    }
                    for attr, key in (
                        ("preMod", "pre"),
                        ("postMod", "post"),
                        ("shaderOut", "shader_out"),
                    ):
                        try:
                            value = getattr(mod, attr)
                            if value:
                                col = getattr(value, "col", value)
                                entry[key] = self._pixel_value_dict(col)
                        except Exception:
                            pass
                    try:
                        entry["primitive_id"] = getattr(mod, "primitiveID")
                    except Exception:
                        pass
                    try:
                        entry["passed"] = bool(getattr(mod, "passed"))
                    except Exception:
                        pass
                    history.append(entry)

                result["data"] = {
                    "resource_id": resource_id,
                    "x": int(x),
                    "y": int(y),
                    "mip": int(mip),
                    "slice": int(slice),
                    "sample": int(sample),
                    "format": self._format_name(tex_desc.format),
                    "count": len(history),
                    "history": history,
                }
            except Exception as e:
                import traceback
                result["error"] = "pixel_history error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def export_texture_to_file(self, resource_id, output_path, file_type="PNG",
                               mip=0, slice=0, sample=0, alpha="Preserve",
                               event_id=None):
        """Save a texture to an image file ON THE RENDERDOC HOST via controller.SaveTexture.

        Avoids returning multi-MB base64 through the MCP transport (which overflows /
        truncates for 1024^2+ textures). Returns only small metadata.

        file_type: one of PNG, JPG, BMP, TGA, HDR, EXR, DDS (case-insensitive).
        alpha: Preserve | Discard | BlendToColor | BlendToCheckerboard.
        event_id: optional frame event to set first (needed for transient render targets).
        """
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                tex_desc = self._find_texture_by_id(controller, resource_id)
                if not tex_desc:
                    result["error"] = "Texture not found: %s" % resource_id
                    return

                if event_id is not None:
                    try:
                        controller.SetFrameEvent(int(event_id), True)
                    except Exception:
                        pass

                # Resolve file type enum
                ft_name = str(file_type).upper()
                ft_map = {
                    "PNG": rd.FileType.PNG,
                    "JPG": rd.FileType.JPG,
                    "JPEG": rd.FileType.JPG,
                    "BMP": rd.FileType.BMP,
                    "TGA": rd.FileType.TGA,
                    "HDR": rd.FileType.HDR,
                    "EXR": rd.FileType.EXR,
                    "DDS": rd.FileType.DDS,
                }
                if ft_name not in ft_map:
                    result["error"] = "Unsupported file_type '%s' (use PNG/JPG/BMP/TGA/HDR/EXR/DDS)" % file_type
                    return

                # Resolve alpha mapping enum (handle US/UK spelling variations defensively)
                alpha_name = str(alpha)
                blend_color = getattr(rd.AlphaMapping, "BlendToColour",
                                      getattr(rd.AlphaMapping, "BlendToColor", rd.AlphaMapping.Preserve))
                alpha_map = {
                    "Preserve": rd.AlphaMapping.Preserve,
                    "Discard": rd.AlphaMapping.Discard,
                    "BlendToColor": blend_color,
                    "BlendToColour": blend_color,
                    "BlendToCheckerboard": rd.AlphaMapping.BlendToCheckerboard,
                }
                alpha_enum = alpha_map.get(alpha_name, rd.AlphaMapping.Preserve)

                # Validate mip / slice
                use_mip = mip
                if ft_name != "DDS":
                    if use_mip < 0 or use_mip >= tex_desc.mips:
                        result["error"] = "Invalid mip %d (texture has %d mips)" % (use_mip, tex_desc.mips)
                        return

                # Ensure destination directory exists on the host
                try:
                    import os as _os
                    _dir = _os.path.dirname(output_path)
                    if _dir and not _os.path.isdir(_dir):
                        _os.makedirs(_dir, exist_ok=True)
                except Exception:
                    pass

                is_cubemap = bool(getattr(tex_desc, "cubemap", False)) or getattr(tex_desc, "arraysize", 0) == 6
                face_names = ["posX", "negX", "posY", "negY", "posZ", "negZ"]
                face_labels = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]

                if is_cubemap and int(slice) == -1:
                    if ft_name == "DDS":
                        texsave = self._build_texture_save(
                            tex_desc, ft_map[ft_name], alpha_enum, use_mip, 0, sample)
                        save_result = controller.SaveTexture(texsave, output_path)
                        if not self._save_texture_result_ok(save_result):
                            result["error"] = "SaveTexture failed for cubemap %s -> %s: %s" % (
                                resource_id, output_path, str(save_result))
                            return
                        result["data"] = {
                            "resource_id": resource_id,
                            "output_path": output_path,
                            "file_type": ft_name,
                            "width": tex_desc.width,
                            "height": tex_desc.height,
                            "mip": use_mip,
                            "slice": slice,
                            "sample": sample,
                            "format": self._format_name(tex_desc.format),
                            "mip_levels": tex_desc.mips,
                            "cubemap": True,
                            "faces": 6,
                            "mode": "cubemap_dds",
                        }
                        return

                    import os as _os
                    base, ext = _os.path.splitext(output_path)
                    saved_faces = []
                    errors = []
                    for face_idx in range(6):
                        face_path = "%s_face%d_%s%s" % (
                            base, face_idx, face_names[face_idx], ext)
                        texsave = self._build_texture_save(
                            tex_desc, ft_map[ft_name], alpha_enum,
                            use_mip, face_idx, sample)
                        save_result = controller.SaveTexture(texsave, face_path)
                        if self._save_texture_result_ok(save_result):
                            saved_faces.append({
                                "path": face_path,
                                "face": face_idx,
                                "name": face_names[face_idx],
                                "label": face_labels[face_idx],
                            })
                        else:
                            errors.append({
                                "face": face_idx,
                                "name": face_names[face_idx],
                                "error": str(save_result),
                            })

                    if not saved_faces:
                        result["error"] = "SaveTexture failed for all cubemap faces: %s" % errors
                        return
                    result["data"] = {
                        "resource_id": resource_id,
                        "output_path": output_path,
                        "file_type": ft_name,
                        "width": tex_desc.width,
                        "height": tex_desc.height,
                        "mip": use_mip,
                        "slice": slice,
                        "sample": sample,
                        "format": self._format_name(tex_desc.format),
                        "mip_levels": tex_desc.mips,
                        "cubemap": True,
                        "faces": len(saved_faces),
                        "saved_faces": saved_faces,
                        "errors": errors,
                        "mode": "cubemap_faces",
                    }
                    return

                if int(slice) < 0:
                    result["error"] = "slice=-1 is only valid for cubemap all-face export"
                    return

                if is_cubemap and int(slice) >= 6:
                    result["error"] = "Invalid cubemap face %d (valid faces: 0..5, or -1 for all)" % int(slice)
                    return

                texsave = self._build_texture_save(
                    tex_desc, ft_map[ft_name], alpha_enum, use_mip, slice, sample)
                save_result = controller.SaveTexture(texsave, output_path)
                if not self._save_texture_result_ok(save_result):
                    result["error"] = "SaveTexture failed for %s -> %s: %s" % (
                        resource_id, output_path, str(save_result))
                    return

                result["data"] = {
                    "resource_id": resource_id,
                    "output_path": output_path,
                    "file_type": ft_name,
                    "width": tex_desc.width,
                    "height": tex_desc.height,
                    "mip": use_mip,
                    "slice": slice,
                    "sample": sample,
                    "format": self._format_name(tex_desc.format),
                    "mip_levels": tex_desc.mips,
                    "cubemap": is_cubemap,
                }
            except Exception as e:
                import traceback
                result["error"] = "export_texture_to_file error: %s\n%s" % (str(e), traceback.format_exc())

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def get_texture_info(self, resource_id):
        """Get texture metadata"""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"texture": None, "error": None}

        def callback(controller):
            try:
                tex_desc = self._find_texture_by_id(controller, resource_id)

                if not tex_desc:
                    result["error"] = "Texture not found: %s" % resource_id
                    return

                result["texture"] = {
                    "resource_id": resource_id,
                    "width": tex_desc.width,
                    "height": tex_desc.height,
                    "depth": tex_desc.depth,
                    "array_size": tex_desc.arraysize,
                    "mip_levels": tex_desc.mips,
                    "format": str(tex_desc.format.Name()),
                    "dimension": str(tex_desc.type),
                    "msaa_samples": tex_desc.msSamp,
                    "byte_size": tex_desc.byteSize,
                }
            except Exception as e:
                import traceback
                result["error"] = "Error: %s\n%s" % (str(e), traceback.format_exc())

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["texture"]

    def get_texture_data(self, resource_id, mip=0, slice=0, sample=0, depth_slice=None):
        """Get texture pixel data."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            tex_desc = self._find_texture_by_id(controller, resource_id)

            if not tex_desc:
                result["error"] = "Texture not found: %s" % resource_id
                return

            # Validate mip level
            if mip < 0 or mip >= tex_desc.mips:
                result["error"] = "Invalid mip level %d (texture has %d mips)" % (
                    mip,
                    tex_desc.mips,
                )
                return

            # Validate slice for array/cube textures
            max_slices = tex_desc.arraysize
            if tex_desc.cubemap:
                max_slices = tex_desc.arraysize * 6
            if slice < 0 or (max_slices > 1 and slice >= max_slices):
                result["error"] = "Invalid slice %d (texture has %d slices)" % (
                    slice,
                    max_slices,
                )
                return

            # Validate sample for MSAA
            if sample < 0 or (tex_desc.msSamp > 1 and sample >= tex_desc.msSamp):
                result["error"] = "Invalid sample %d (texture has %d samples)" % (
                    sample,
                    tex_desc.msSamp,
                )
                return

            # Calculate dimensions at this mip level
            mip_width = max(1, tex_desc.width >> mip)
            mip_height = max(1, tex_desc.height >> mip)
            mip_depth = max(1, tex_desc.depth >> mip)

            # Validate depth_slice for 3D textures
            is_3d = tex_desc.depth > 1
            if depth_slice is not None:
                if not is_3d:
                    result["error"] = "depth_slice can only be used with 3D textures"
                    return
                if depth_slice < 0 or depth_slice >= mip_depth:
                    result["error"] = "Invalid depth_slice %d (texture has %d depth at mip %d)" % (
                        depth_slice,
                        mip_depth,
                        mip,
                    )
                    return

            # Create subresource specification
            sub = self._make_subresource(mip, slice, sample)

            # Get texture data
            try:
                data = controller.GetTextureData(tex_desc.resourceId, sub)
            except Exception as e:
                result["error"] = "Failed to get texture data: %s" % str(e)
                return

            # Extract depth slice for 3D textures if requested
            output_depth = mip_depth
            if is_3d and depth_slice is not None:
                total_size = len(data)
                bytes_per_slice = total_size // mip_depth
                slice_start = depth_slice * bytes_per_slice
                slice_end = slice_start + bytes_per_slice
                data = data[slice_start:slice_end]
                output_depth = 1

            result["data"] = {
                "resource_id": resource_id,
                "width": mip_width,
                "height": mip_height,
                "depth": output_depth,
                "mip": mip,
                "slice": slice,
                "sample": sample,
                "depth_slice": depth_slice,
                "format": str(tex_desc.format.Name()),
                "dimension": str(tex_desc.type),
                "is_3d": is_3d,
                "total_depth": mip_depth if is_3d else 1,
                "data_length": len(data),
                "content_base64": base64.b64encode(data).decode("ascii"),
            }

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

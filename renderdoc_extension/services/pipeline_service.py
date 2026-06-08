"""
Pipeline state service for RenderDoc.
"""

import base64

import renderdoc as rd

from ..utils import Parsers, Serializers, Helpers


class PipelineService:
    """Pipeline state service"""

    _ROLE_PATTERNS = (
        (("albedo", "basecolor", "base_color", "diffuse", "maintex", "base map"), "albedo"),
        (("normal", "bump", "nrm", "nor_"), "normal"),
        (("roughness", "rough", "gloss", "smoothness"), "roughness"),
        (("metallic", "metalness", "metal"), "metallic"),
        (("ambientocclusion", "ambient_occlusion", "occlusion", "_ao", " ao"), "ao"),
        (("emissive", "emission", "glow"), "emissive"),
        (("opacity", "alpha", "transparent", "translucency"), "opacity"),
        (("height", "displacement", "parallax"), "height"),
        (("specular", "spec"), "specular"),
        (("shadow", "shadowmap"), "shadow"),
        (("environment", "reflection", "cubemap", "cube", "ibl", "sky"), "environment"),
        (("depth", "zbuffer", "z_buffer"), "depth"),
    )

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn

    def _resource_id_int(self, resource_id):
        """Convert a RenderDoc ResourceId to a stable integer when possible."""
        try:
            return int(resource_id)
        except Exception:
            try:
                return Parsers.extract_numeric_id(str(resource_id))
            except Exception:
                return 0

    def _format_name(self, fmt):
        """Return a readable RenderDoc ResourceFormat name."""
        try:
            return str(fmt.Name())
        except Exception:
            try:
                return str(fmt.name)
            except Exception:
                return str(fmt)

    def _resource_name_lookup(self, controller):
        """Build {numeric_resource_id: name} from RenderDoc resources."""
        lookup = {}
        try:
            for res in controller.GetResources():
                lookup[self._resource_id_int(res.resourceId)] = getattr(res, "name", "")
        except Exception:
            pass
        return lookup

    def _texture_lookup(self, controller):
        """Build {numeric_resource_id: TextureDescription}."""
        lookup = {}
        try:
            for tex in controller.GetTextures():
                lookup[self._resource_id_int(tex.resourceId)] = tex
        except Exception:
            pass
        return lookup

    def _extract_binding_resource(self, binding):
        """Return (ResourceId-like object, numeric id) from a bound resource shape."""
        if binding is None:
            return None, 0

        candidates = []
        descriptor = getattr(binding, "descriptor", None)
        if descriptor is not None:
            candidates.append(descriptor)
        candidates.append(binding)

        resources = getattr(binding, "resources", None)
        if resources:
            candidates.extend(resources)

        for obj in candidates:
            for attr in ("resource", "resourceId", "view", "viewResourceId", "imageView"):
                if hasattr(obj, attr):
                    try:
                        rid = getattr(obj, attr)
                        rid_int = self._resource_id_int(rid)
                        if rid_int != 0:
                            return rid, rid_int
                    except Exception:
                        pass
        return None, 0

    def _resolve_bound_resource(self, bindings, index):
        """Resolve a resource binding by access index or list position."""
        if not bindings:
            return None, 0

        ordered = []
        try:
            for binding in bindings:
                access = getattr(binding, "access", None)
                access_index = getattr(access, "index", None) if access is not None else None
                if access_index == index:
                    ordered.append(binding)
        except Exception:
            pass

        try:
            if index is not None and index >= 0 and index < len(bindings):
                ordered.append(bindings[index])
        except Exception:
            pass

        for binding in ordered:
            rid, rid_int = self._extract_binding_resource(binding)
            if rid_int != 0:
                return rid, rid_int
        return None, 0

    def _resolve_vulkan_descriptors(self, controller, pipe, stage, event_id):
        """Best-effort fallback for Vulkan descriptor-set resource resolution."""
        resolved = {}

        # Some RenderDoc builds expose descriptor accesses on the controller.
        try:
            accesses = controller.GetDescriptorAccess()
            for access in accesses:
                bind = getattr(access, "index", getattr(access, "binding", -1))
                rid, rid_int = self._extract_binding_resource(access)
                if rid_int != 0 and bind is not None and int(bind) >= 0:
                    resolved[int(bind)] = rid_int
            if resolved:
                return resolved
        except Exception:
            pass

        # Vulkan pipeline state carries descriptor sets/bindings in API-specific
        # structures. Attribute names vary across RenderDoc versions, so this is
        # deliberately defensive.
        try:
            vk_state = None
            if hasattr(pipe, "vulkan"):
                vk_state = pipe.vulkan
            elif hasattr(pipe, "GetVulkanPipelineState"):
                vk_state = pipe.GetVulkanPipelineState()

            descriptor_sets = []
            for root_name in ("graphics", "compute"):
                root = getattr(vk_state, root_name, None) if vk_state else None
                descriptor_sets.extend(list(getattr(root, "descriptorSets", []) or []))

            for desc_set in descriptor_sets:
                bindings = getattr(desc_set, "bindings", []) or []
                for binding in bindings:
                    bind_num = getattr(
                        binding, "binding", getattr(binding, "descriptorIndex", -1))
                    descriptors = (
                        getattr(binding, "binds", None)
                        or getattr(binding, "descriptors", None)
                        or []
                    )
                    for descriptor in descriptors:
                        rid, rid_int = self._extract_binding_resource(descriptor)
                        if rid_int == 0:
                            image_info = getattr(descriptor, "imageInfo", None)
                            if image_info is not None:
                                rid, rid_int = self._extract_binding_resource(image_info)
                        if rid_int != 0 and bind_num is not None and int(bind_num) >= 0:
                            resolved[int(bind_num)] = rid_int
                            break
            if resolved:
                return resolved
        except Exception:
            pass

        # Last resort: resource usage can reveal sampled/read textures at the
        # target event even when descriptor slot metadata is unavailable.
        try:
            next_slot = 0
            for tex in controller.GetTextures():
                try:
                    usages = controller.GetUsage(tex.resourceId)
                except Exception:
                    continue
                for usage in usages:
                    used_event = getattr(usage, "eventId", getattr(usage, "eventID", 0))
                    if int(used_event) != int(event_id):
                        continue
                    usage_type = str(getattr(usage, "usage", "")).lower()
                    if (
                        "read" in usage_type
                        or "sample" in usage_type
                        or "input" in usage_type
                        or "fs" in usage_type
                        or "ps" in usage_type
                    ):
                        rid_int = self._resource_id_int(tex.resourceId)
                        if rid_int not in resolved.values():
                            resolved[next_slot] = rid_int
                            next_slot += 1
                        break
        except Exception:
            pass

        return resolved

    def _infer_texture_role(self, variable_name, texture_name, texture_format):
        """Infer a material texture role from shader variable/name/format hints."""
        combined = ("%s %s" % (variable_name or "", texture_name or "")).lower()
        for keywords, role in self._ROLE_PATTERNS:
            for keyword in keywords:
                if keyword in combined:
                    return role

        fmt = (texture_format or "").lower()
        if "bc5" in fmt:
            return "normal"
        if "bc6" in fmt:
            return "environment"
        if "depth" in fmt or "d24" in fmt or "d32" in fmt:
            return "depth"
        return "unknown"

    def get_bound_textures(self, event_id, stage="pixel"):
        """Get textures bound to a shader stage at an event with role inference."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                controller.SetFrameEvent(int(event_id), True)
                pipe = controller.GetPipelineState()
                stage_enum = Parsers.parse_stage(stage)

                shader = pipe.GetShader(stage_enum)
                if shader == rd.ResourceId.Null():
                    result["data"] = {
                        "event_id": event_id,
                        "stage": stage,
                        "count": 0,
                        "textures": [],
                        "note": "No shader bound at this stage",
                    }
                    return

                reflection = None
                try:
                    reflection = pipe.GetShaderReflection(stage_enum)
                except Exception:
                    pass

                try:
                    raw_bindings = pipe.GetReadOnlyResources(stage_enum, False)
                except TypeError:
                    try:
                        raw_bindings = pipe.GetReadOnlyResources(stage_enum)
                    except Exception:
                        raw_bindings = []
                except Exception:
                    raw_bindings = []
                bindings = list(raw_bindings or [])
                api_name = ""
                try:
                    api_name = str(controller.GetAPIProperties().pipelineType)
                except Exception:
                    pass
                vulkan_descriptors = {}
                if "vulkan" in api_name.lower():
                    vulkan_descriptors = self._resolve_vulkan_descriptors(
                        controller, pipe, stage_enum, event_id)

                texture_lookup = self._texture_lookup(controller)
                name_lookup = self._resource_name_lookup(controller)
                textures = []

                readonly_resources = []
                if reflection:
                    readonly_resources = list(getattr(reflection, "readOnlyResources", []) or [])

                for idx, ro in enumerate(readonly_resources):
                    is_texture = getattr(ro, "isTexture", None)
                    res_type = str(getattr(ro, "resType", "")).lower()
                    if is_texture is False:
                        continue
                    if is_texture is None and res_type and "texture" not in res_type:
                        continue

                    variable_name = getattr(ro, "name", "texture_%d" % idx)
                    bind_index = getattr(ro, "fixedBindNumber", idx)
                    rid, rid_int = self._resolve_bound_resource(bindings, bind_index)
                    binding_source = "pipeline_binding" if rid_int != 0 else ""
                    if rid_int == 0 and bind_index != idx:
                        rid, rid_int = self._resolve_bound_resource(bindings, idx)
                        binding_source = "pipeline_binding" if rid_int != 0 else ""
                    if rid_int == 0:
                        for key in (bind_index, idx):
                            if key in vulkan_descriptors:
                                rid_int = vulkan_descriptors[key]
                                binding_source = "vulkan_descriptor_fallback"
                                break

                    entry = {
                        "slot": idx,
                        "binding": bind_index,
                        "shader_name": variable_name,
                        "resource_id": str(rid) if rid is not None else str(rid_int) if rid_int else "",
                        "id": rid_int,
                        "binding_source": binding_source,
                    }

                    tex = texture_lookup.get(rid_int)
                    tex_name = name_lookup.get(rid_int, "")
                    fmt_name = ""
                    if tex is not None:
                        fmt_name = self._format_name(getattr(tex, "format", ""))
                        entry["resource_id"] = str(tex.resourceId)
                        entry.update({
                            "texture_name": tex_name,
                            "width": getattr(tex, "width", 0),
                            "height": getattr(tex, "height", 0),
                            "depth": getattr(tex, "depth", 0),
                            "array_size": getattr(tex, "arraysize", 0),
                            "mip_levels": getattr(tex, "mips", 0),
                            "format": fmt_name,
                            "dimension": str(getattr(tex, "type", "")),
                            "msaa_samples": getattr(tex, "msSamp", 0),
                            "cubemap": bool(getattr(tex, "cubemap", False)),
                        })
                    else:
                        entry["texture_name"] = tex_name

                    entry["role"] = self._infer_texture_role(
                        variable_name, tex_name, fmt_name)
                    textures.append(entry)

                # Fallback for captures where reflection lacks readOnlyResources.
                if not textures and bindings:
                    for idx, binding in enumerate(bindings):
                        rid, rid_int = self._extract_binding_resource(binding)
                        if rid_int == 0:
                            continue
                        tex = texture_lookup.get(rid_int)
                        if tex is None:
                            continue
                        tex_name = name_lookup.get(rid_int, "")
                        fmt_name = self._format_name(getattr(tex, "format", ""))
                        textures.append({
                            "slot": idx,
                            "binding": idx,
                            "shader_name": "",
                            "resource_id": str(rid),
                            "id": rid_int,
                            "binding_source": "pipeline_binding_no_reflection",
                            "texture_name": tex_name,
                            "width": getattr(tex, "width", 0),
                            "height": getattr(tex, "height", 0),
                            "depth": getattr(tex, "depth", 0),
                            "array_size": getattr(tex, "arraysize", 0),
                            "mip_levels": getattr(tex, "mips", 0),
                            "format": fmt_name,
                            "dimension": str(getattr(tex, "type", "")),
                            "msaa_samples": getattr(tex, "msSamp", 0),
                            "cubemap": bool(getattr(tex, "cubemap", False)),
                            "role": self._infer_texture_role("", tex_name, fmt_name),
                        })

                if not textures and vulkan_descriptors:
                    for bind_index in sorted(vulkan_descriptors):
                        rid_int = vulkan_descriptors[bind_index]
                        tex = texture_lookup.get(rid_int)
                        if tex is None:
                            continue
                        tex_name = name_lookup.get(rid_int, "")
                        fmt_name = self._format_name(getattr(tex, "format", ""))
                        textures.append({
                            "slot": bind_index,
                            "binding": bind_index,
                            "shader_name": "",
                            "resource_id": str(tex.resourceId),
                            "id": rid_int,
                            "binding_source": "vulkan_descriptor_fallback_no_reflection",
                            "texture_name": tex_name,
                            "width": getattr(tex, "width", 0),
                            "height": getattr(tex, "height", 0),
                            "depth": getattr(tex, "depth", 0),
                            "array_size": getattr(tex, "arraysize", 0),
                            "mip_levels": getattr(tex, "mips", 0),
                            "format": fmt_name,
                            "dimension": str(getattr(tex, "type", "")),
                            "msaa_samples": getattr(tex, "msSamp", 0),
                            "cubemap": bool(getattr(tex, "cubemap", False)),
                            "role": self._infer_texture_role("", tex_name, fmt_name),
                        })

                result["data"] = {
                    "event_id": event_id,
                    "stage": stage,
                    "shader_resource_id": str(shader),
                    "count": len(textures),
                    "textures": textures,
                }
            except Exception as e:
                import traceback
                result["error"] = "get_bound_textures error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def get_shader_info(self, event_id, stage, disassembly_target=None,
                        include_bytecode=False):
        """Get shader information for a specific stage.

        Args:
            event_id: event to inspect.
            stage: shader stage name.
            disassembly_target: optional substring (case-insensitive) to pick a
                disassembly target other than the default. e.g. "HLSL" selects
                the "HLSL (DXBC_2_HLSL)" target on D3D11/12 if the plugin is
                present. If None, uses the first (default ISA) target.
            include_bytecode: if True, also return the raw shader bytecode
                (DXBC on D3D11/12, SPIR-V on Vulkan) base64-encoded, so callers
                can decompile it externally (e.g. cmd_Decompiler.exe).
        """
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"shader": None, "error": None}

        def callback(controller):
            controller.SetFrameEvent(event_id, True)

            pipe = controller.GetPipelineState()
            stage_enum = Parsers.parse_stage(stage)

            shader = pipe.GetShader(stage_enum)
            if shader == rd.ResourceId.Null():
                result["error"] = "No %s shader bound" % stage
                return

            entry = pipe.GetShaderEntryPoint(stage_enum)
            reflection = pipe.GetShaderReflection(stage_enum)

            shader_info = {
                "resource_id": str(shader),
                "entry_point": entry,
                "stage": stage,
            }

            # Get disassembly
            try:
                targets = controller.GetDisassemblyTargets(True)
                # Expose all available targets so callers can discover e.g.
                # "HLSL (DXBC_2_HLSL)" without trial and error.
                shader_info["available_disassembly_targets"] = list(targets)
                chosen = None
                if targets:
                    if disassembly_target:
                        needle = disassembly_target.lower()
                        for t in targets:
                            if needle in str(t).lower():
                                chosen = t
                                break
                        if chosen is None:
                            shader_info["disassembly_target_error"] = (
                                "No target matched '%s'. Available: %s"
                                % (disassembly_target, list(targets))
                            )
                    if chosen is None:
                        chosen = targets[0]
                    disasm = controller.DisassembleShader(
                        pipe.GetGraphicsPipelineObject(), reflection, chosen
                    )
                    shader_info["disassembly"] = disasm
                    shader_info["disassembly_target"] = str(chosen)
            except Exception as e:
                shader_info["disassembly_error"] = str(e)

            # Optionally return raw bytecode (DXBC / SPIR-V) for external
            # decompilation. reflection.rawBytes are the original compiled bytes.
            if include_bytecode and reflection is not None:
                try:
                    raw = bytes(reflection.rawBytes)
                    shader_info["bytecode_base64"] = base64.b64encode(raw).decode("ascii")
                    shader_info["bytecode_length"] = len(raw)
                    try:
                        shader_info["bytecode_encoding"] = str(reflection.encoding)
                    except Exception:
                        pass
                except Exception as e:
                    shader_info["bytecode_error"] = str(e)

            # Get constant buffer info
            if reflection:
                shader_info["constant_buffers"] = self._get_cbuffer_info(
                    controller, pipe, reflection, stage_enum
                )
                shader_info["resources"] = self._get_resource_bindings(reflection)

            result["shader"] = shader_info

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["shader"]

    def get_pipeline_state(self, event_id):
        """Get full pipeline state at an event"""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"pipeline": None, "error": None}

        def callback(controller):
            controller.SetFrameEvent(event_id, True)

            pipe = controller.GetPipelineState()
            api = controller.GetAPIProperties().pipelineType

            pipeline_info = {
                "event_id": event_id,
                "api": str(api),
            }

            # Shader stages with detailed bindings
            stages = {}
            stage_list = Helpers.get_all_shader_stages()
            for stage in stage_list:
                shader = pipe.GetShader(stage)
                if shader != rd.ResourceId.Null():
                    stage_info = {
                        "resource_id": str(shader),
                        "entry_point": pipe.GetShaderEntryPoint(stage),
                    }

                    reflection = pipe.GetShaderReflection(stage)

                    stage_info["resources"] = self._get_stage_resources(
                        controller, pipe, stage, reflection
                    )
                    stage_info["uavs"] = self._get_stage_uavs(
                        controller, pipe, stage, reflection
                    )
                    stage_info["samplers"] = self._get_stage_samplers(
                        pipe, stage, reflection
                    )
                    stage_info["constant_buffers"] = self._get_stage_cbuffers(
                        controller, pipe, stage, reflection
                    )

                    stages[str(stage)] = stage_info

            pipeline_info["shaders"] = stages

            # Viewport and scissor
            try:
                vp_scissor = pipe.GetViewportScissor()
                if vp_scissor:
                    viewports = []
                    for v in vp_scissor.viewports:
                        viewports.append(
                            {
                                "x": v.x,
                                "y": v.y,
                                "width": v.width,
                                "height": v.height,
                                "min_depth": v.minDepth,
                                "max_depth": v.maxDepth,
                            }
                        )
                    pipeline_info["viewports"] = viewports
            except Exception:
                pass

            # Render targets
            try:
                om = pipe.GetOutputMerger()
                if om:
                    rts = []
                    for i, rt in enumerate(om.renderTargets):
                        if rt.resourceId != rd.ResourceId.Null():
                            rts.append({"index": i, "resource_id": str(rt.resourceId)})
                    pipeline_info["render_targets"] = rts

                    if om.depthTarget.resourceId != rd.ResourceId.Null():
                        pipeline_info["depth_target"] = str(om.depthTarget.resourceId)
            except Exception:
                pass

            # Input assembly (extended: layout + vertex buffers + index buffer)
            try:
                ia_info = {"topology": ""}
                try:
                    topo = pipe.GetPrimitiveTopology()
                    ia_info["topology"] = str(topo)
                except Exception:
                    pass

                # Vertex input layout (attributes / elements)
                attributes = []
                try:
                    vinputs = pipe.GetVertexInputs()
                    for vi in vinputs:
                        attr = {
                            "name": getattr(vi, "name", ""),
                            "semantic_name": getattr(vi, "semanticName", ""),
                            "semantic_index": getattr(vi, "semanticIndex", 0),
                            "vertex_buffer_slot": getattr(vi, "vertexBuffer", 0),
                            "byte_offset": getattr(vi, "byteOffset", 0),
                            "per_instance": bool(getattr(vi, "perInstance", False)),
                            "instance_rate": getattr(vi, "instanceRate", 0),
                            "format_name": str(vi.format.Name()) if hasattr(vi, "format") else "",
                            "format_byte_width": getattr(vi.format, "ElementSize", lambda: 0)() if hasattr(vi, "format") else 0,
                            "format_component_count": getattr(vi.format, "compCount", 0) if hasattr(vi, "format") else 0,
                            "format_component_type": str(vi.format.compType) if hasattr(vi, "format") else "",
                            "format_component_bytewidth": getattr(vi.format, "compByteWidth", 0) if hasattr(vi, "format") else 0,
                            "format_bgra_order": bool(getattr(vi.format, "BGRAOrder", lambda: False)()) if hasattr(vi, "format") else False,
                        }
                        attributes.append(attr)
                except Exception as e:
                    ia_info["vertex_inputs_error"] = str(e)
                ia_info["vertex_inputs"] = attributes

                # Vertex buffer bindings
                vbuffers = []
                try:
                    vbs = pipe.GetVBuffers()
                    for idx, vb in enumerate(vbs):
                        if vb.resourceId == rd.ResourceId.Null():
                            continue
                        vbuffers.append({
                            "slot": idx,
                            "resource_id": str(vb.resourceId),
                            "byte_offset": getattr(vb, "byteOffset", 0),
                            "byte_stride": getattr(vb, "byteStride", 0),
                            "byte_size": getattr(vb, "byteSize", 0),
                        })
                except Exception as e:
                    ia_info["vertex_buffers_error"] = str(e)
                ia_info["vertex_buffers"] = vbuffers

                # Index buffer
                try:
                    ib = pipe.GetIBuffer()
                    if ib and ib.resourceId != rd.ResourceId.Null():
                        ia_info["index_buffer"] = {
                            "resource_id": str(ib.resourceId),
                            "byte_offset": getattr(ib, "byteOffset", 0),
                            "byte_stride": getattr(ib, "byteStride", 0),
                            "byte_size": getattr(ib, "byteSize", 0),
                        }
                except Exception as e:
                    ia_info["index_buffer_error"] = str(e)

                pipeline_info["input_assembly"] = ia_info
            except Exception as e:
                pipeline_info["input_assembly"] = {"error": str(e)}

            result["pipeline"] = pipeline_info

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["pipeline"]

    def _get_stage_resources(self, controller, pipe, stage, reflection):
        """Get shader resource views (SRVs) for a stage"""
        resources = []
        try:
            srvs = pipe.GetReadOnlyResources(stage, False)

            name_map = {}
            if reflection:
                for res in reflection.readOnlyResources:
                    name_map[res.fixedBindNumber] = res.name

            for srv in srvs:
                if srv.descriptor.resource == rd.ResourceId.Null():
                    continue

                slot = srv.access.index
                res_info = {
                    "slot": slot,
                    "name": name_map.get(slot, ""),
                    "resource_id": str(srv.descriptor.resource),
                }

                res_info.update(
                    self._get_resource_details(controller, srv.descriptor.resource)
                )

                res_info["first_mip"] = srv.descriptor.firstMip
                res_info["num_mips"] = srv.descriptor.numMips
                res_info["first_slice"] = srv.descriptor.firstSlice
                res_info["num_slices"] = srv.descriptor.numSlices

                resources.append(res_info)
        except Exception as e:
            resources.append({"error": str(e)})

        return resources

    def _get_stage_uavs(self, controller, pipe, stage, reflection):
        """Get unordered access views (UAVs) for a stage"""
        uavs = []
        try:
            uav_list = pipe.GetReadWriteResources(stage, False)

            name_map = {}
            if reflection:
                for res in reflection.readWriteResources:
                    name_map[res.fixedBindNumber] = res.name

            for uav in uav_list:
                if uav.descriptor.resource == rd.ResourceId.Null():
                    continue

                slot = uav.access.index
                uav_info = {
                    "slot": slot,
                    "name": name_map.get(slot, ""),
                    "resource_id": str(uav.descriptor.resource),
                }

                uav_info.update(
                    self._get_resource_details(controller, uav.descriptor.resource)
                )

                uav_info["first_element"] = uav.descriptor.firstMip
                uav_info["num_elements"] = uav.descriptor.numMips

                uavs.append(uav_info)
        except Exception as e:
            uavs.append({"error": str(e)})

        return uavs

    def _get_stage_samplers(self, pipe, stage, reflection):
        """Get samplers for a stage"""
        samplers = []
        try:
            sampler_list = pipe.GetSamplers(stage, False)

            name_map = {}
            if reflection:
                for samp in reflection.samplers:
                    name_map[samp.fixedBindNumber] = samp.name

            for samp in sampler_list:
                slot = samp.access.index
                samp_info = {
                    "slot": slot,
                    "name": name_map.get(slot, ""),
                }

                desc = samp.descriptor
                try:
                    samp_info["address_u"] = str(desc.addressU)
                    samp_info["address_v"] = str(desc.addressV)
                    samp_info["address_w"] = str(desc.addressW)
                except AttributeError:
                    pass

                try:
                    samp_info["filter"] = str(desc.filter)
                except AttributeError:
                    pass

                try:
                    samp_info["max_anisotropy"] = desc.maxAnisotropy
                except AttributeError:
                    pass

                try:
                    samp_info["min_lod"] = desc.minLOD
                    samp_info["max_lod"] = desc.maxLOD
                    samp_info["mip_lod_bias"] = desc.mipLODBias
                except AttributeError:
                    pass

                try:
                    samp_info["border_color"] = [
                        desc.borderColor[0],
                        desc.borderColor[1],
                        desc.borderColor[2],
                        desc.borderColor[3],
                    ]
                except (AttributeError, TypeError):
                    pass

                try:
                    samp_info["compare_function"] = str(desc.compareFunction)
                except AttributeError:
                    pass

                samplers.append(samp_info)
        except Exception as e:
            samplers.append({"error": str(e)})

        return samplers

    def _get_stage_cbuffers(self, controller, pipe, stage, reflection):
        """Get constant buffers for a stage with variable values (方案A)"""
        cbuffers = []
        if not reflection:
            return cbuffers

        shader_id = reflection.resourceId
        try:
            entry = pipe.GetShaderEntryPoint(stage)
        except Exception:
            entry = reflection.entryPoint if hasattr(reflection, "entryPoint") else ""
        try:
            pipe_obj = pipe.GetGraphicsPipelineObject()
        except Exception:
            pipe_obj = rd.ResourceId.Null()

        for i, cb in enumerate(reflection.constantBlocks):
            slot = cb.bindPoint if hasattr(cb, 'bindPoint') else cb.fixedBindNumber
            cb_info = {
                "slot": slot,
                "name": cb.name,
                "byte_size": cb.byteSize,
                "variable_count": len(cb.variables) if cb.variables else 0,
                "variables": [],
            }

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
                cb_info["variables"] = Serializers.serialize_variables(variables)
            except Exception as e:
                import traceback
                cb_info["error"] = "%s | %s" % (str(e), traceback.format_exc().splitlines()[-1])
                if cb.variables:
                    cb_info["variables"] = [{
                        "name": var.name,
                        "byte_offset": var.byteOffset,
                        "type": str(var.type.name) if var.type else "",
                    } for var in cb.variables]

            cbuffers.append(cb_info)

        return cbuffers

    def _get_resource_details(self, controller, resource_id):
        """Get details about a resource (texture or buffer)"""
        details = {}

        try:
            resource_name = self.ctx.GetResourceName(resource_id)
            if resource_name:
                details["resource_name"] = resource_name
        except Exception:
            pass

        for tex in controller.GetTextures():
            if tex.resourceId == resource_id:
                details["type"] = "texture"
                details["width"] = tex.width
                details["height"] = tex.height
                details["depth"] = tex.depth
                details["array_size"] = tex.arraysize
                details["mip_levels"] = tex.mips
                details["format"] = str(tex.format.Name())
                details["dimension"] = str(tex.type)
                details["msaa_samples"] = tex.msSamp
                return details

        for buf in controller.GetBuffers():
            if buf.resourceId == resource_id:
                details["type"] = "buffer"
                details["length"] = buf.length
                return details

        return details

    def _get_cbuffer_info(self, controller, pipe, reflection, stage):
        """Get constant buffer information and values"""
        cbuffers = []
        if not reflection:
            return cbuffers

        shader_id = reflection.resourceId
        try:
            entry = pipe.GetShaderEntryPoint(stage)
        except Exception:
            entry = reflection.entryPoint if hasattr(reflection, "entryPoint") else ""
        try:
            pipe_obj = pipe.GetGraphicsPipelineObject()
        except Exception:
            pipe_obj = rd.ResourceId.Null()

        for i, cb in enumerate(reflection.constantBlocks):
            cb_info = {
                "name": cb.name,
                "slot": i,
                "size": cb.byteSize,
                "variables": [],
            }

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
                cb_info["variables"] = Serializers.serialize_variables(variables)
            except Exception as e:
                import traceback
                cb_info["error"] = "%s | %s" % (str(e), traceback.format_exc().splitlines()[-1])

            cbuffers.append(cb_info)

        return cbuffers

    def _get_resource_bindings(self, reflection):
        """Get shader resource bindings"""
        resources = []

        try:
            for res in reflection.readOnlyResources:
                resources.append(
                    {
                        "name": res.name,
                        "type": str(res.resType),
                        "binding": res.fixedBindNumber,
                        "access": "ReadOnly",
                    }
                )
        except Exception:
            pass

        try:
            for res in reflection.readWriteResources:
                resources.append(
                    {
                        "name": res.name,
                        "type": str(res.resType),
                        "binding": res.fixedBindNumber,
                        "access": "ReadWrite",
                    }
                )
        except Exception:
            pass

        return resources

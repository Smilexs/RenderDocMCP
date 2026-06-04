"""
Pipeline state service for RenderDoc.
"""

import base64

import renderdoc as rd

from ..utils import Parsers, Serializers, Helpers


class PipelineService:
    """Pipeline state service"""

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn

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

"""
Request Handler for RenderDoc MCP Bridge
Routes incoming requests to appropriate facade methods.
"""

import traceback


class RequestHandler:
    """Handles incoming MCP bridge requests"""

    def __init__(self, facade):
        self.facade = facade
        self._methods = {
            "ping": self._handle_ping,
            "get_capture_status": self._handle_get_capture_status,
            "get_draw_calls": self._handle_get_draw_calls,
            "get_frame_summary": self._handle_get_frame_summary,
            "find_draws_by_shader": self._handle_find_draws_by_shader,
            "find_draws_by_texture": self._handle_find_draws_by_texture,
            "find_draws_by_resource": self._handle_find_draws_by_resource,
            "get_draw_call_details": self._handle_get_draw_call_details,
            "get_action_timings": self._handle_get_action_timings,
            "enumerate_counters": self._handle_enumerate_counters,
            "fetch_counters": self._handle_fetch_counters,
            "get_debug_messages": self._handle_get_debug_messages,
            "debug_pixel": self._handle_debug_pixel,
            "debug_vertex": self._handle_debug_vertex,
            "get_shader_info": self._handle_get_shader_info,
            "get_bound_textures": self._handle_get_bound_textures,
            "get_buffer_contents": self._handle_get_buffer_contents,
            "get_textures": self._handle_get_textures,
            "get_buffers": self._handle_get_buffers,
            "get_resources": self._handle_get_resources,
            "get_texture_info": self._handle_get_texture_info,
            "get_texture_data": self._handle_get_texture_data,
            "pick_pixel": self._handle_pick_pixel,
            "pixel_history": self._handle_pixel_history,
            "export_texture_to_file": self._handle_export_texture_to_file,
            "get_pipeline_state": self._handle_get_pipeline_state,
            "get_mesh_data": self._handle_get_mesh_data,
            "get_world_matrix": self._handle_get_world_matrix,
            "export_mesh_to_file": self._handle_export_mesh_to_file,
            "export_postvs_to_file": self._handle_export_postvs_to_file,
            "list_captures": self._handle_list_captures,
            "open_capture": self._handle_open_capture,
            "capture_frame": self._handle_capture_frame,
            "launch_application": self._handle_launch_application,
            "connect_running_target": self._handle_connect_running_target,
            "list_running_targets": self._handle_list_running_targets,
            "get_target_status": self._handle_get_target_status,
            "trigger_capture": self._handle_trigger_capture,
            "close_target": self._handle_close_target,
            "get_resource_info": self._handle_get_resource_info,
            "get_resource_usage": self._handle_get_resource_usage,
            "list_cbuffers": self._handle_list_cbuffers,
            "get_cbuffer_contents": self._handle_get_cbuffer_contents,
            "list_shaders": self._handle_list_shaders,
            "search_shaders": self._handle_search_shaders,
            "list_passes": self._handle_list_passes,
            "get_pass_info": self._handle_get_pass_info,
            "get_pass_attachments": self._handle_get_pass_attachments,
            "get_pass_statistics": self._handle_get_pass_statistics,
            "get_pass_deps": self._handle_get_pass_deps,
            "find_unused_targets": self._handle_find_unused_targets,
        }

    def handle(self, request):
        """Handle a request and return response"""
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        try:
            if method not in self._methods:
                return self._error_response(
                    request_id, -32601, "Method not found: %s" % method
                )

            result = self._methods[method](params)
            return {"id": request_id, "result": result}

        except ValueError as e:
            return self._error_response(request_id, -32602, str(e))
        except Exception as e:
            traceback.print_exc()
            return self._error_response(request_id, -32000, str(e))

    def _error_response(self, request_id, code, message):
        """Create an error response"""
        return {"id": request_id, "error": {"code": code, "message": message}}

    def _handle_ping(self, params):
        """Handle ping request"""
        return {"status": "ok", "message": "pong"}

    def _handle_get_capture_status(self, params):
        """Handle get_capture_status request"""
        return self.facade.get_capture_status()

    def _handle_get_draw_calls(self, params):
        """Handle get_draw_calls request"""
        include_children = params.get("include_children", True)
        marker_filter = params.get("marker_filter")
        exclude_markers = params.get("exclude_markers")
        event_id_min = params.get("event_id_min")
        event_id_max = params.get("event_id_max")
        only_actions = params.get("only_actions", False)
        flags_filter = params.get("flags_filter")
        return self.facade.get_draw_calls(
            include_children=include_children,
            marker_filter=marker_filter,
            exclude_markers=exclude_markers,
            event_id_min=event_id_min,
            event_id_max=event_id_max,
            only_actions=only_actions,
            flags_filter=flags_filter,
        )

    def _handle_get_frame_summary(self, params):
        """Handle get_frame_summary request"""
        return self.facade.get_frame_summary()

    def _handle_find_draws_by_shader(self, params):
        """Handle find_draws_by_shader request"""
        shader_name = params.get("shader_name")
        if shader_name is None:
            raise ValueError("shader_name is required")
        stage = params.get("stage")
        return self.facade.find_draws_by_shader(shader_name, stage)

    def _handle_find_draws_by_texture(self, params):
        """Handle find_draws_by_texture request"""
        texture_name = params.get("texture_name")
        if texture_name is None:
            raise ValueError("texture_name is required")
        return self.facade.find_draws_by_texture(texture_name)

    def _handle_find_draws_by_resource(self, params):
        """Handle find_draws_by_resource request"""
        resource_id = params.get("resource_id")
        if resource_id is None:
            raise ValueError("resource_id is required")
        return self.facade.find_draws_by_resource(resource_id)

    def _handle_get_draw_call_details(self, params):
        """Handle get_draw_call_details request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        return self.facade.get_draw_call_details(int(event_id))

    def _handle_get_action_timings(self, params):
        """Handle get_action_timings request"""
        event_ids = params.get("event_ids")
        marker_filter = params.get("marker_filter")
        exclude_markers = params.get("exclude_markers")
        return self.facade.get_action_timings(
            event_ids=event_ids,
            marker_filter=marker_filter,
            exclude_markers=exclude_markers,
        )

    def _handle_enumerate_counters(self, params):
        """Handle enumerate_counters request"""
        return self.facade.enumerate_counters()

    def _handle_fetch_counters(self, params):
        """Handle fetch_counters request"""
        counter_ids = params.get("counter_ids")
        if not counter_ids:
            raise ValueError("counter_ids is required")
        if isinstance(counter_ids, str):
            counter_ids = [int(x.strip()) for x in counter_ids.split(",") if x.strip()]
        else:
            counter_ids = [int(x) for x in counter_ids]
        return self.facade.fetch_counters(counter_ids)

    def _handle_get_debug_messages(self, params):
        """Handle get_debug_messages request"""
        return self.facade.get_debug_messages()

    def _handle_debug_pixel(self, params):
        """Handle debug_pixel request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        x = params.get("x")
        y = params.get("y")
        if x is None or y is None:
            raise ValueError("x and y are required")
        return self.facade.debug_pixel(
            int(event_id),
            int(x),
            int(y),
            int(params.get("sample", 0)),
            int(params.get("primitive", -1)),
            int(params.get("max_steps", 50)),
            int(params.get("max_vars_per_step", 10)),
        )

    def _handle_debug_vertex(self, params):
        """Handle debug_vertex request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        vertex_id = params.get("vertex_id")
        if vertex_id is None:
            raise ValueError("vertex_id is required")
        return self.facade.debug_vertex(
            int(event_id),
            int(vertex_id),
            int(params.get("instance_id", 0)),
            int(params.get("index", 0)),
            int(params.get("view", 0)),
            int(params.get("max_steps", 50)),
            int(params.get("max_vars_per_step", 10)),
        )

    def _handle_get_shader_info(self, params):
        """Handle get_shader_info request"""
        event_id = params.get("event_id")
        stage = params.get("stage")
        if event_id is None:
            raise ValueError("event_id is required")
        if stage is None:
            raise ValueError("stage is required")
        disassembly_target = params.get("disassembly_target")
        include_bytecode = params.get("include_bytecode", False)
        return self.facade.get_shader_info(
            int(event_id), stage, disassembly_target, include_bytecode
        )

    def _handle_get_bound_textures(self, params):
        """Handle get_bound_textures request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        stage = params.get("stage", "pixel")
        return self.facade.get_bound_textures(int(event_id), stage)

    def _handle_list_cbuffers(self, params):
        """Handle list_cbuffers request"""
        stage = params.get("stage")
        if stage is None:
            raise ValueError("stage is required")
        event_id = params.get("event_id", params.get("eventId"))
        return self.facade.list_cbuffers(
            stage, None if event_id is None else int(event_id))

    def _handle_get_cbuffer_contents(self, params):
        """Handle get_cbuffer_contents request"""
        stage = params.get("stage")
        if stage is None:
            raise ValueError("stage is required")
        index = params.get("index")
        if index is None:
            raise ValueError("index is required")
        event_id = params.get("event_id", params.get("eventId"))
        return self.facade.get_cbuffer_contents(
            stage, int(index), None if event_id is None else int(event_id))

    def _handle_list_shaders(self, params):
        """Handle list_shaders request"""
        return self.facade.list_shaders(
            int(params.get("max_events", params.get("maxEvents", 10000))),
            int(params.get("max_shaders", params.get("maxShaders", 200))),
        )

    def _handle_search_shaders(self, params):
        """Handle search_shaders request"""
        pattern = params.get("pattern")
        if not pattern:
            raise ValueError("pattern is required")
        return self.facade.search_shaders(
            pattern,
            params.get("stage"),
            int(params.get("limit", 50)),
            int(params.get("max_events", params.get("maxEvents", 10000))),
            params.get("disassembly_target", params.get("disassemblyTarget")),
        )

    def _handle_get_buffer_contents(self, params):
        """Handle get_buffer_contents request"""
        resource_id = params.get("resource_id")
        if resource_id is None:
            raise ValueError("resource_id is required")
        offset = params.get("offset", 0)
        length = params.get("length", 0)
        event_id = params.get("event_id")
        return self.facade.get_buffer_contents(resource_id, offset, length, event_id)

    def _handle_get_resource_info(self, params):
        """Handle get_resource_info request"""
        resource_id = params.get("resource_id", params.get("resourceId"))
        if resource_id is None:
            raise ValueError("resource_id is required")
        return self.facade.get_resource_info(resource_id)

    def _handle_get_resource_usage(self, params):
        """Handle get_resource_usage request"""
        resource_id = params.get("resource_id", params.get("resourceId"))
        if resource_id is None:
            raise ValueError("resource_id is required")
        return self.facade.get_resource_usage(resource_id)

    def _handle_get_textures(self, params):
        """Handle get_textures request"""
        return self.facade.get_textures()

    def _handle_get_buffers(self, params):
        """Handle get_buffers request"""
        return self.facade.get_buffers()

    def _handle_get_resources(self, params):
        """Handle get_resources request"""
        return self.facade.get_resources()

    def _handle_get_texture_info(self, params):
        """Handle get_texture_info request"""
        resource_id = params.get("resource_id")
        if resource_id is None:
            raise ValueError("resource_id is required")
        return self.facade.get_texture_info(resource_id)

    def _handle_get_texture_data(self, params):
        """Handle get_texture_data request"""
        resource_id = params.get("resource_id")
        if resource_id is None:
            raise ValueError("resource_id is required")
        mip = params.get("mip", 0)
        slice_idx = params.get("slice", 0)
        sample = params.get("sample", 0)
        depth_slice = params.get("depth_slice")  # None = full volume
        return self.facade.get_texture_data(resource_id, mip, slice_idx, sample, depth_slice)

    def _handle_pick_pixel(self, params):
        """Handle pick_pixel request"""
        resource_id = params.get("resource_id")
        if resource_id is None:
            raise ValueError("resource_id is required")
        x = params.get("x")
        y = params.get("y")
        if x is None or y is None:
            raise ValueError("x and y are required")
        return self.facade.pick_pixel(
            resource_id,
            int(x),
            int(y),
            int(params.get("mip", 0)),
            int(params.get("slice", 0)),
            int(params.get("sample", 0)),
        )

    def _handle_pixel_history(self, params):
        """Handle pixel_history request"""
        resource_id = params.get("resource_id")
        if resource_id is None:
            raise ValueError("resource_id is required")
        x = params.get("x")
        y = params.get("y")
        if x is None or y is None:
            raise ValueError("x and y are required")
        return self.facade.pixel_history(
            resource_id,
            int(x),
            int(y),
            int(params.get("mip", 0)),
            int(params.get("slice", 0)),
            int(params.get("sample", 0)),
        )

    def _handle_export_texture_to_file(self, params):
        """Handle export_texture_to_file request"""
        resource_id = params.get("resource_id")
        if resource_id is None:
            raise ValueError("resource_id is required")
        output_path = params.get("output_path")
        if not output_path:
            raise ValueError("output_path is required")
        event_id = params.get("event_id")
        return self.facade.export_texture_to_file(
            resource_id,
            output_path,
            params.get("file_type", "PNG"),
            int(params.get("mip", 0)),
            int(params.get("slice", 0)),
            int(params.get("sample", 0)),
            params.get("alpha", "Preserve"),
            None if event_id is None else int(event_id),
        )

    def _handle_get_pipeline_state(self, params):
        """Handle get_pipeline_state request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        return self.facade.get_pipeline_state(int(event_id))

    def _handle_get_mesh_data(self, params):
        """Handle get_mesh_data request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        return self.facade.get_mesh_data(int(event_id))

    def _handle_get_world_matrix(self, params):
        """Handle get_world_matrix request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        o2w_offset = params.get("o2w_offset", 32)
        w2o_offset = params.get("w2o_offset", 96)
        return self.facade.get_world_matrix(int(event_id), int(o2w_offset), int(w2o_offset))

    def _handle_export_mesh_to_file(self, params):
        """Handle export_mesh_to_file request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        output_path = params.get("output_path")
        if not output_path:
            raise ValueError("output_path is required")

        def param(snake_name, camel_name, default):
            return params.get(snake_name, params.get(camel_name, default))

        return self.facade.export_mesh_to_file(
            int(event_id),
            output_path,
            bool(param("bake_world", "bakeWorld", True)),
            int(param("pos_slot", "posSlot", -1)),
            int(param("normal_slot", "normalSlot", -1)),
            int(param("tangent_slot", "tangentSlot", -1)),
            int(param("uv0_slot", "uv0Slot", -1)),
            int(param("uv1_slot", "uv1Slot", -1)),
            int(param("extra_slot", "extraSlot", -1)),
            int(param("o2w_offset", "o2wOffset", 32)),
            int(param("w2o_offset", "w2oOffset", 96)),
        )

    def _handle_export_postvs_to_file(self, params):
        """Handle export_postvs_to_file request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        output_path = params.get("output_path")
        if not output_path:
            raise ValueError("output_path is required")
        return self.facade.export_postvs_to_file(
            int(event_id),
            output_path,
            int(params.get("instance", 0)),
            int(params.get("view", 0)),
            bool(params.get("graft_uv", True)),
            int(params.get("uv0_slot", 3)),
            int(params.get("uv1_slot", 4)),
            int(params.get("color_slot", 1)),
        )

    def _handle_list_captures(self, params):
        """Handle list_captures request"""
        directory = params.get("directory")
        if directory is None:
            raise ValueError("directory is required")
        return self.facade.list_captures(directory)

    def _handle_open_capture(self, params):
        """Handle open_capture request"""
        capture_path = params.get("capture_path")
        if capture_path is None:
            raise ValueError("capture_path is required")
        return self.facade.open_capture(capture_path)

    def _handle_capture_frame(self, params):
        """Handle capture_frame request"""
        exe_path = params.get("exe_path", params.get("exePath"))
        if not exe_path:
            raise ValueError("exe_path is required")
        return self.facade.capture_frame(
            exe_path,
            params.get("working_dir", params.get("workingDir", "")),
            params.get("cmd_line", params.get("cmdLine", "")),
            int(params.get("delay_frames", params.get("delayFrames", 100))),
            params.get("output_path", params.get("outputPath", "")),
            int(params.get("timeout_seconds", params.get("timeoutSeconds", 60))),
        )

    def _handle_launch_application(self, params):
        """Handle launch_application request"""
        exe_path = params.get("exe_path", params.get("exePath"))
        if not exe_path:
            raise ValueError("exe_path is required")
        return self.facade.launch_application(
            exe_path,
            params.get("working_dir", params.get("workingDir", "")),
            params.get("cmd_line", params.get("cmdLine", "")),
            params.get("graphics_api", params.get("graphicsApi", "auto")),
            params.get(
                "target_process_name",
                params.get("targetProcessName", params.get("target_name", params.get("targetName", ""))),
            ),
            params.get("connect_target", params.get("connectTarget", True)),
        )

    def _handle_connect_running_target(self, params):
        """Handle connect_running_target request"""
        return self.facade.connect_running_target(
            params.get(
                "target_process_name",
                params.get("targetProcessName", params.get("target_name", params.get("targetName", ""))),
            ),
            params.get("graphics_api", params.get("graphicsApi", "auto")),
            int(params.get("timeout_seconds", params.get("timeoutSeconds", 60))),
        )

    def _handle_list_running_targets(self, params):
        """Handle list_running_targets request"""
        return self.facade.list_running_targets()

    def _handle_get_target_status(self, params):
        """Handle get_target_status request"""
        session_id = params.get("session_id", params.get("sessionId"))
        if not session_id:
            raise ValueError("session_id is required")
        return self.facade.get_target_status(session_id)

    def _handle_trigger_capture(self, params):
        """Handle trigger_capture request"""
        session_id = params.get("session_id", params.get("sessionId"))
        if not session_id:
            raise ValueError("session_id is required")
        return self.facade.trigger_capture(
            session_id,
            params.get("output_path", params.get("outputPath", "")),
            int(params.get("timeout_seconds", params.get("timeoutSeconds", 60))),
        )

    def _handle_close_target(self, params):
        """Handle close_target request"""
        session_id = params.get("session_id", params.get("sessionId"))
        if not session_id:
            raise ValueError("session_id is required")
        return self.facade.close_target(session_id)

    def _handle_list_passes(self, params):
        """Handle list_passes request"""
        return self.facade.list_passes()

    def _handle_get_pass_info(self, params):
        """Handle get_pass_info request"""
        event_id = params.get("event_id", params.get("eventId"))
        if event_id is None:
            raise ValueError("event_id is required")
        return self.facade.get_pass_info(int(event_id))

    def _handle_get_pass_attachments(self, params):
        """Handle get_pass_attachments request"""
        event_id = params.get("event_id", params.get("eventId"))
        if event_id is None:
            raise ValueError("event_id is required")
        return self.facade.get_pass_attachments(int(event_id))

    def _handle_get_pass_statistics(self, params):
        """Handle get_pass_statistics request"""
        return self.facade.get_pass_statistics()

    def _handle_get_pass_deps(self, params):
        """Handle get_pass_deps request"""
        return self.facade.get_pass_deps()

    def _handle_find_unused_targets(self, params):
        """Handle find_unused_targets request"""
        return self.facade.find_unused_targets()

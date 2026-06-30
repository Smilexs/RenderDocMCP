"""
RenderDoc API Facade
Provides thread-safe access to RenderDoc's ReplayController and CaptureContext.
Uses BlockInvoke to marshal calls to the replay thread.
"""

import traceback

from .services import (
    CaptureManager,
    ActionService,
    SearchService,
    ResourceService,
    PipelineService,
    MeshService,
    PassService,
)


class RenderDocFacade:
    """
    Facade for RenderDoc API access.

    This class delegates all operations to specialized service classes:
    - CaptureManager: Capture management (status, list, open)
    - ActionService: Draw call / action operations
    - SearchService: Reverse lookup searches
    - ResourceService: Texture and buffer data
    - PipelineService: Pipeline state and shader info
    """

    def __init__(self, ctx):
        """
        Initialize facade with CaptureContext.

        Args:
            ctx: The pyrenderdoc CaptureContext from register()
        """
        self.ctx = ctx

        # Initialize service classes
        self._capture = CaptureManager(ctx, self._invoke)
        self._action = ActionService(ctx, self._invoke)
        self._search = SearchService(ctx, self._invoke)
        self._resource = ResourceService(ctx, self._invoke)
        self._pipeline = PipelineService(ctx, self._invoke)
        self._mesh = MeshService(ctx, self._invoke)
        self._pass = PassService(ctx, self._invoke)

    def _invoke(self, callback):
        """Invoke callback on replay thread via BlockInvoke"""
        callback_error = {}

        def guarded_callback(controller):
            try:
                callback(controller)
            except Exception as e:
                callback_error["message"] = str(e)
                callback_error["traceback"] = traceback.format_exc()

        self.ctx.Replay().BlockInvoke(guarded_callback)
        if callback_error:
            raise RuntimeError(
                "Replay callback failed: %s\n%s" % (
                    callback_error["message"],
                    callback_error["traceback"],
                )
            )

    # ==================== Capture Management ====================

    def get_capture_status(self):
        """Check if a capture is loaded and get API info"""
        return self._capture.get_capture_status()

    def list_captures(self, directory):
        """List all .rdc files in the specified directory"""
        return self._capture.list_captures(directory)

    def open_capture(self, capture_path):
        """Open a capture file in RenderDoc"""
        return self._capture.open_capture(capture_path)

    def capture_frame(self, exe_path, working_dir="", cmd_line="",
                      delay_frames=100, output_path="", timeout_seconds=60):
        """Launch a target app through RenderDoc, capture one frame, and open it"""
        return self._capture.capture_frame(
            exe_path, working_dir, cmd_line, delay_frames,
            output_path, timeout_seconds,
        )

    def launch_application(self, exe_path, working_dir="", cmd_line="",
                           graphics_api="auto", target_process_name="", connect_target=True):
        """Launch a target app through RenderDoc and keep TargetControl open"""
        return self._capture.launch_application(
            exe_path, working_dir, cmd_line, graphics_api,
            target_process_name=target_process_name,
            connect_target=connect_target)

    def connect_running_target(self, target_process_name="", graphics_api="auto",
                               timeout_seconds=60):
        """Connect TargetControl to an already-running RenderDoc target"""
        return self._capture.connect_running_target(
            target_process_name, graphics_api, timeout_seconds)

    def list_running_targets(self):
        """List active RenderDoc targets visible from localhost"""
        return self._capture.list_running_targets()

    def get_target_status(self, session_id):
        """Check whether a launched target session is still controllable"""
        return self._capture.get_target_status(session_id)

    def trigger_capture(self, session_id, output_path="", timeout_seconds=60):
        """Trigger a capture on a launched target session and save it"""
        return self._capture.trigger_capture(
            session_id, output_path, timeout_seconds)

    def close_target(self, session_id):
        """Close a launched target session"""
        return self._capture.close_target(session_id)

    # ==================== Draw Call / Action Operations ====================

    def get_draw_calls(
        self,
        include_children=True,
        marker_filter=None,
        exclude_markers=None,
        event_id_min=None,
        event_id_max=None,
        only_actions=False,
        flags_filter=None,
    ):
        """Get all draw calls/actions in the capture with optional filtering"""
        return self._action.get_draw_calls(
            include_children=include_children,
            marker_filter=marker_filter,
            exclude_markers=exclude_markers,
            event_id_min=event_id_min,
            event_id_max=event_id_max,
            only_actions=only_actions,
            flags_filter=flags_filter,
        )

    def get_frame_summary(self):
        """Get a summary of the current capture frame"""
        return self._action.get_frame_summary()

    def get_draw_call_details(self, event_id):
        """Get detailed information about a specific draw call"""
        return self._action.get_draw_call_details(event_id)

    def get_action_timings(self, event_ids=None, marker_filter=None, exclude_markers=None):
        """Get GPU timing information for actions"""
        return self._action.get_action_timings(
            event_ids=event_ids,
            marker_filter=marker_filter,
            exclude_markers=exclude_markers,
        )

    def enumerate_counters(self):
        """List available GPU performance counters"""
        return self._action.enumerate_counters()

    def fetch_counters(self, counter_ids):
        """Fetch GPU performance counter values"""
        return self._action.fetch_counters(counter_ids)

    def get_debug_messages(self):
        """Get API validation/debug messages"""
        return self._action.get_debug_messages()

    def debug_pixel(self, event_id, x, y, sample=0, primitive=-1,
                    max_steps=50, max_vars_per_step=10):
        """Debug pixel shader execution for a pixel at an event"""
        return self._action.debug_pixel(
            event_id, x, y, sample, primitive, max_steps, max_vars_per_step
        )

    def debug_vertex(self, event_id, vertex_id, instance_id=0, index=0,
                     view=0, max_steps=50, max_vars_per_step=10):
        """Debug vertex shader execution for a vertex at an event"""
        return self._action.debug_vertex(
            event_id, vertex_id, instance_id, index, view,
            max_steps, max_vars_per_step
        )

    # ==================== Search Operations ====================

    def find_draws_by_shader(self, shader_name, stage=None):
        """Find all draw calls using a shader with the given name (partial match)"""
        return self._search.find_draws_by_shader(shader_name, stage)

    def find_draws_by_texture(self, texture_name):
        """Find all draw calls using a texture with the given name (partial match)"""
        return self._search.find_draws_by_texture(texture_name)

    def find_draws_by_resource(self, resource_id):
        """Find all draw calls using a specific resource ID (exact match)"""
        return self._search.find_draws_by_resource(resource_id)

    # ==================== Resource Operations ====================

    def get_buffer_contents(self, resource_id, offset=0, length=0, event_id=None):
        """Get buffer data"""
        return self._resource.get_buffer_contents(resource_id, offset, length, event_id)

    def get_resource_info(self, resource_id):
        """Get detailed metadata for any RenderDoc resource"""
        return self._resource.get_resource_info(resource_id)

    def get_resource_usage(self, resource_id):
        """Get a resource's frame usage history"""
        return self._resource.get_resource_usage(resource_id)

    def get_textures(self):
        """List texture resources"""
        return self._resource.get_textures()

    def get_buffers(self):
        """List buffer resources"""
        return self._resource.get_buffers()

    def get_resources(self):
        """List all resources"""
        return self._resource.get_resources()

    def get_texture_info(self, resource_id):
        """Get texture metadata"""
        return self._resource.get_texture_info(resource_id)

    def get_texture_data(self, resource_id, mip=0, slice=0, sample=0, depth_slice=None):
        """Get texture pixel data"""
        return self._resource.get_texture_data(resource_id, mip, slice, sample, depth_slice)

    def pick_pixel(self, resource_id, x, y, mip=0, slice=0, sample=0):
        """Read one pixel from a texture/render target"""
        return self._resource.pick_pixel(resource_id, x, y, mip, slice, sample)

    def pixel_history(self, resource_id, x, y, mip=0, slice=0, sample=0):
        """Get modification history for one pixel"""
        return self._resource.pixel_history(resource_id, x, y, mip, slice, sample)

    def export_texture_to_file(self, resource_id, output_path, file_type="PNG",
                               mip=0, slice=0, sample=0, alpha="Preserve",
                               event_id=None):
        """Save a texture to an image file on the host via controller.SaveTexture"""
        return self._resource.export_texture_to_file(
            resource_id, output_path, file_type, mip, slice, sample, alpha, event_id
        )

    # ==================== Pipeline Operations ====================

    def get_shader_info(self, event_id, stage, disassembly_target=None,
                        include_bytecode=False):
        """Get shader information for a specific stage"""
        return self._pipeline.get_shader_info(
            event_id, stage, disassembly_target, include_bytecode
        )

    def get_pipeline_state(self, event_id):
        """Get full pipeline state at an event"""
        return self._pipeline.get_pipeline_state(event_id)

    def get_bound_textures(self, event_id, stage="pixel"):
        """Get shader-stage texture bindings with role inference"""
        return self._pipeline.get_bound_textures(event_id, stage)

    def list_cbuffers(self, stage, event_id=None):
        """List constant buffers bound to a shader stage"""
        return self._pipeline.list_cbuffers(stage, event_id)

    def get_cbuffer_contents(self, stage, index, event_id=None):
        """Read variables from one constant buffer by stage and index"""
        return self._pipeline.get_cbuffer_contents(stage, index, event_id)

    def list_shaders(self, max_events=10000, max_shaders=200):
        """List unique shaders used across draw/dispatch events"""
        return self._pipeline.list_shaders(max_events, max_shaders)

    def search_shaders(self, pattern, stage=None, limit=50,
                       max_events=10000, disassembly_target=None):
        """Search shader disassembly text across unique shaders"""
        return self._pipeline.search_shaders(
            pattern, stage, limit, max_events, disassembly_target,
        )

    # ==================== Pass / Frame Structure Operations ====================

    def list_passes(self):
        """List marker-based and synthetic render pass ranges"""
        return self._pass.list_passes()

    def get_pass_info(self, event_id):
        """Get details for the pass containing an event"""
        return self._pass.get_pass_info(event_id)

    def get_pass_attachments(self, event_id):
        """Get color/depth attachments for a pass"""
        return self._pass.get_pass_attachments(event_id)

    def get_pass_statistics(self):
        """Get per-pass aggregate statistics"""
        return self._pass.get_pass_statistics()

    def get_pass_deps(self):
        """Build inter-pass resource dependencies"""
        return self._pass.get_pass_deps()

    def find_unused_targets(self):
        """Find written targets not contributing to visible output"""
        return self._pass.find_unused_targets()

    # ==================== Mesh Operations ====================

    def get_mesh_data(self, event_id):
        """Extract decoded mesh data (IB + VB attributes) at an event"""
        return self._mesh.get_mesh_data(event_id)

    def get_world_matrix(self, event_id, o2w_offset=32, w2o_offset=96):
        """Read VS cb0 Unity ObjectToWorld / WorldToObject matrices at an event"""
        return self._mesh.get_world_matrix(event_id, o2w_offset, w2o_offset)

    def export_mesh_to_file(self, event_id, output_path, bake_world=True,
                            pos_slot=-1, normal_slot=-1, tangent_slot=-1,
                            uv0_slot=-1, uv1_slot=-1, extra_slot=-1,
                            o2w_offset=32, w2o_offset=96):
        """Decode mesh at an event, optionally bake to world space, write JSON to disk"""
        return self._mesh.export_mesh_to_file(
            event_id, output_path, bake_world,
            pos_slot, normal_slot, tangent_slot,
            uv0_slot, uv1_slot, extra_slot,
            o2w_offset, w2o_offset,
        )

    def export_postvs_to_file(self, event_id, output_path, instance=0, view=0,
                              graft_uv=True, uv0_slot=3, uv1_slot=4, color_slot=1):
        """Extract VS-output (skinned) world-space vertices, write JSON to disk.

        graft_uv (default True) copies pose-invariant UV0/UV1/COLOR from the input VB
        onto the matching PostVS vertices so the skinned mesh stays texture-mappable.
        """
        return self._mesh.export_postvs_to_file(
            event_id, output_path, instance, view,
            graft_uv, uv0_slot, uv1_slot, color_slot)

"""
RenderDoc MCP Server
FastMCP 2.0 server providing access to RenderDoc capture data.
"""

import os
import shutil
import subprocess
import time
from typing import Literal

from fastmcp import FastMCP

from .bridge.client import RenderDocBridge, RenderDocBridgeError
from .config import settings

# Initialize FastMCP server
mcp = FastMCP(
    name="RenderDoc MCP Server",
)

# RenderDoc bridge client
bridge = RenderDocBridge(host=settings.renderdoc_host, port=settings.renderdoc_port)


@mcp.tool
def ping() -> dict:
    """
    Check whether the RenderDoc MCP bridge extension is reachable.

    Returns status information without raising for a missing bridge, so clients can
    use it as a lightweight health check before running heavier RenderDoc tools.
    """
    try:
        return bridge.call("ping", timeout=3.0)
    except RenderDocBridgeError as e:
        return {"status": "error", "message": str(e)}


@mcp.tool
def get_capture_status() -> dict:
    """
    Check if a capture is currently loaded in RenderDoc.
    Returns the capture status and API type if loaded.
    """
    return bridge.call("get_capture_status")


@mcp.tool
def get_draw_calls(
    include_children: bool = True,
    marker_filter: str | None = None,
    exclude_markers: list[str] | None = None,
    event_id_min: int | None = None,
    event_id_max: int | None = None,
    only_actions: bool = False,
    flags_filter: list[str] | None = None,
) -> dict:
    """
    Get the list of all draw calls and actions in the current capture.

    Args:
        include_children: Include child actions in the hierarchy (default: True)
        marker_filter: Only include actions under markers containing this string (partial match)
        exclude_markers: Exclude actions under markers containing these strings (list of partial matches)
        event_id_min: Only include actions with event_id >= this value
        event_id_max: Only include actions with event_id <= this value
        only_actions: If True, exclude marker actions (PushMarker/PopMarker/SetMarker)
        flags_filter: Only include actions with these flags (list of flag names, e.g. ["Drawcall", "Dispatch"])

    Returns a hierarchical tree of actions including markers, draw calls,
    dispatches, and other GPU events.
    """
    params: dict[str, object] = {"include_children": include_children}
    if marker_filter is not None:
        params["marker_filter"] = marker_filter
    if exclude_markers is not None:
        params["exclude_markers"] = exclude_markers
    if event_id_min is not None:
        params["event_id_min"] = event_id_min
    if event_id_max is not None:
        params["event_id_max"] = event_id_max
    if only_actions:
        params["only_actions"] = only_actions
    if flags_filter is not None:
        params["flags_filter"] = flags_filter
    return bridge.call("get_draw_calls", params)


@mcp.tool
def get_frame_summary() -> dict:
    """
    Get a summary of the current capture frame.

    Returns statistics about the frame including:
    - API type (D3D11, D3D12, Vulkan, etc.)
    - Total action count
    - Statistics: draw calls, dispatches, clears, copies, presents, markers
    - Top-level markers with event IDs and child counts
    - Resource counts: textures, buffers
    """
    return bridge.call("get_frame_summary")


@mcp.tool
def list_passes() -> dict:
    """
    List marker-based and synthetic render pass ranges in the current frame.

    Returns pass names, event ranges, first draw/dispatch events, and whether
    each pass was inferred from render-target changes.
    """
    return bridge.call("list_passes")


@mcp.tool
def get_pass_info(event_id: int) -> dict:
    """
    Get the render pass containing a given event.

    Args:
        event_id: Any event ID inside the pass

    Returns pass range metadata plus draw/dispatch actions in that pass.
    """
    return bridge.call("get_pass_info", {"event_id": event_id})


@mcp.tool
def get_pass_attachments(event_id: int) -> dict:
    """
    Get color and depth attachments for the pass containing an event.

    Args:
        event_id: Any event ID inside the pass
    """
    return bridge.call("get_pass_attachments", {"event_id": event_id})


@mcp.tool
def get_pass_statistics() -> dict:
    """
    Get aggregate statistics for each render pass.

    Returns draw/dispatch counts, approximate triangle totals, and attachment
    counts/dimensions where available.
    """
    return bridge.call("get_pass_statistics")


@mcp.tool
def get_pass_deps() -> dict:
    """
    Build an inter-pass resource dependency graph.

    Returns edges where a resource written in one pass is read by a later pass.
    """
    return bridge.call("get_pass_deps", timeout=90.0)


@mcp.tool
def find_unused_targets() -> dict:
    """
    Find render targets/resources written during the frame but not consumed by
    the final visible output according to usage-history heuristics.
    """
    return bridge.call("find_unused_targets", timeout=90.0)


@mcp.tool
def find_draws_by_shader(
    shader_name: str,
    stage: Literal["vertex", "hull", "domain", "geometry", "pixel", "compute"] | None = None,
) -> dict:
    """
    Find all draw calls using a shader with the given name (partial match).

    Args:
        shader_name: Partial name to search for in shader names or entry points
        stage: Optional shader stage to search (if not specified, searches all stages)

    Returns a list of matching draw calls with event IDs and match reasons.
    """
    params: dict[str, object] = {"shader_name": shader_name}
    if stage is not None:
        params["stage"] = stage
    return bridge.call("find_draws_by_shader", params)


@mcp.tool
def find_draws_by_texture(texture_name: str) -> dict:
    """
    Find all draw calls using a texture with the given name (partial match).

    Args:
        texture_name: Partial name to search for in texture resource names

    Returns a list of matching draw calls with event IDs and match reasons.
    Searches SRVs, UAVs, and render targets.
    """
    return bridge.call("find_draws_by_texture", {"texture_name": texture_name})


@mcp.tool
def find_draws_by_resource(resource_id: str) -> dict:
    """
    Find all draw calls using a specific resource ID (exact match).

    Args:
        resource_id: Resource ID to search for (e.g. "ResourceId::12345" or "12345")

    Returns a list of matching draw calls with event IDs and match reasons.
    Searches shaders, SRVs, UAVs, render targets, and depth targets.
    """
    return bridge.call("find_draws_by_resource", {"resource_id": resource_id})


@mcp.tool
def get_draw_call_details(event_id: int) -> dict:
    """
    Get detailed information about a specific draw call.

    Args:
        event_id: The event ID of the draw call to inspect

    Includes vertex/index counts, resource outputs, and other metadata.
    """
    return bridge.call("get_draw_call_details", {"event_id": event_id})


@mcp.tool
def get_action_timings(
    event_ids: list[int] | None = None,
    marker_filter: str | None = None,
    exclude_markers: list[str] | None = None,
) -> dict:
    """
    Get GPU timing information for actions (draw calls, dispatches, etc.).

    Args:
        event_ids: Optional list of specific event IDs to get timings for.
                   If not specified, returns timings for all actions.
        marker_filter: Only include actions under markers containing this string (partial match).
        exclude_markers: Exclude actions under markers containing these strings.

    Returns timing data including:
    - available: Whether GPU timing counters are supported
    - unit: Time unit (typically "seconds")
    - timings: List of {event_id, name, duration_seconds, duration_ms}
    - total_duration_ms: Sum of all durations
    - count: Number of timing entries

    Note: GPU timing counters may not be available on all hardware/drivers.
    """
    params: dict[str, object] = {}
    if event_ids is not None:
        params["event_ids"] = event_ids
    if marker_filter is not None:
        params["marker_filter"] = marker_filter
    if exclude_markers is not None:
        params["exclude_markers"] = exclude_markers
    return bridge.call("get_action_timings", params)


@mcp.tool
def enumerate_counters() -> dict:
    """
    List GPU performance counters available for the current capture.

    Returns counter IDs, names, descriptions, units, and result types where the
    active driver exposes them.
    """
    return bridge.call("enumerate_counters")


@mcp.tool
def fetch_counters(counter_ids: list[int]) -> dict:
    """
    Fetch values for specific GPU performance counters.

    Args:
        counter_ids: Counter IDs returned by enumerate_counters, for example [1].

    Returns counter results grouped by event ID, including counter metadata.
    """
    return bridge.call("fetch_counters", {"counter_ids": counter_ids}, timeout=60.0)


@mcp.tool
def get_debug_messages() -> dict:
    """
    Get graphics API validation/debug messages recorded in the capture.

    Returns messages with event IDs, severity, category/source when available, and
    the driver/validation-layer description text.
    """
    return bridge.call("get_debug_messages")


@mcp.tool
def debug_pixel(
    event_id: int,
    x: int,
    y: int,
    sample: int = 0,
    primitive: int = -1,
    max_steps: int = 50,
    max_vars_per_step: int = 10,
) -> dict:
    """
    Debug pixel shader execution for a screen pixel at a specific draw event.

    Args:
        event_id: Draw event to debug
        x: Pixel X coordinate in the current render target
        y: Pixel Y coordinate in the current render target
        sample: MSAA sample index (default 0)
        primitive: Primitive ID, -1 lets RenderDoc choose (default)
        max_steps: Maximum debug steps to return
        max_vars_per_step: Maximum source variables per step

    Returns a bounded shader debug trace. Availability depends on capture/API,
    shader debug support, and driver capabilities.
    """
    return bridge.call(
        "debug_pixel",
        {
            "event_id": event_id,
            "x": x,
            "y": y,
            "sample": sample,
            "primitive": primitive,
            "max_steps": max_steps,
            "max_vars_per_step": max_vars_per_step,
        },
        timeout=60.0,
    )


@mcp.tool
def debug_vertex(
    event_id: int,
    vertex_id: int,
    instance_id: int = 0,
    index: int = 0,
    view: int = 0,
    max_steps: int = 50,
    max_vars_per_step: int = 10,
) -> dict:
    """
    Debug vertex shader execution for a vertex at a specific draw event.

    Args:
        event_id: Draw event to debug
        vertex_id: Vertex ID to debug
        instance_id: Instance ID (default 0)
        index: Index within the draw (default 0)
        view: Multiview/view index (default 0)
        max_steps: Maximum debug steps to return
        max_vars_per_step: Maximum source variables per step

    Returns a bounded shader debug trace. Availability depends on capture/API,
    shader debug support, and driver capabilities.
    """
    return bridge.call(
        "debug_vertex",
        {
            "event_id": event_id,
            "vertex_id": vertex_id,
            "instance_id": instance_id,
            "index": index,
            "view": view,
            "max_steps": max_steps,
            "max_vars_per_step": max_vars_per_step,
        },
        timeout=60.0,
    )


@mcp.tool
def get_shader_info(
    event_id: int,
    stage: Literal["vertex", "hull", "domain", "geometry", "pixel", "compute"],
    disassembly_target: str | None = None,
    include_bytecode: bool = False,
) -> dict:
    """
    Get shader information for a specific stage at a given event.

    Args:
        event_id: The event ID to inspect the shader at
        stage: The shader stage (vertex, hull, domain, geometry, pixel, compute)
        disassembly_target: Optional case-insensitive substring to select a
            disassembly target other than the default ISA. On D3D11/D3D12 pass
            "HLSL" to get the "HLSL (DXBC_2_HLSL)" decompiled output (keeps the
            original variable names r0..rN / textures2D_N_ / vN semantics) when
            the build ships that target. The full list is always returned in
            "available_disassembly_targets" so you can discover valid names.
        include_bytecode: If True, also return the raw compiled bytecode
            base64-encoded in "bytecode_base64" (DXBC on D3D11/12, SPIR-V on
            Vulkan) plus "bytecode_encoding"/"bytecode_length", so it can be
            decompiled externally (e.g. cmd_Decompiler.exe -D file.dxbc).

    Returns shader disassembly, available disassembly targets, the chosen
    target, constant buffer values, resource bindings, and optionally raw
    bytecode. Use disassembly_target="HLSL" first; if it is missing from
    available_disassembly_targets, fall back to include_bytecode=True and
    decompile the DXBC with the skill's bundled tool.
    """
    params = {"event_id": event_id, "stage": stage}
    if disassembly_target is not None:
        params["disassembly_target"] = disassembly_target
    if include_bytecode:
        params["include_bytecode"] = True
    return bridge.call("get_shader_info", params)


@mcp.tool
def get_bound_textures(
    event_id: int,
    stage: Literal["vertex", "hull", "domain", "geometry", "pixel", "fragment", "compute"] = "pixel",
) -> dict:
    """
    Get textures bound to a shader stage at an event, with inferred material roles.

    Args:
        event_id: The event ID to inspect
        stage: Shader stage. "fragment" is accepted as an alias for "pixel".

    Returns texture bindings with shader variable names, resource IDs, texture
    metadata, and role guesses such as albedo, normal, roughness, metallic, AO,
    emissive, shadow, or environment.
    """
    return bridge.call("get_bound_textures", {"event_id": event_id, "stage": stage})


@mcp.tool
def list_cbuffers(
    stage: Literal["vs", "hs", "ds", "gs", "ps", "cs", "vertex", "hull", "domain", "geometry", "pixel", "compute"],
    event_id: int | None = None,
) -> dict:
    """
    List constant buffers bound to a shader stage.

    Args:
        stage: Shader stage ("vs"/"ps" aliases and full names are accepted)
        event_id: Optional event to inspect. If omitted, uses RenderDoc's current event.
    """
    params: dict[str, object] = {"stage": stage}
    if event_id is not None:
        params["event_id"] = event_id
    return bridge.call("list_cbuffers", params)


@mcp.tool
def get_cbuffer_contents(
    stage: Literal["vs", "hs", "ds", "gs", "ps", "cs", "vertex", "hull", "domain", "geometry", "pixel", "compute"],
    index: int,
    event_id: int | None = None,
) -> dict:
    """
    Read all variables from one constant buffer.

    Args:
        stage: Shader stage ("vs"/"ps" aliases and full names are accepted)
        index: Constant buffer index from list_cbuffers
        event_id: Optional event to inspect. If omitted, uses RenderDoc's current event.
    """
    params: dict[str, object] = {"stage": stage, "index": index}
    if event_id is not None:
        params["event_id"] = event_id
    return bridge.call("get_cbuffer_contents", params)


@mcp.tool
def list_shaders(max_events: int = 10000, max_shaders: int = 200) -> dict:
    """
    List unique shaders used by draw/dispatch events in the capture.

    Args:
        max_events: Maximum draw/dispatch events to scan
        max_shaders: Maximum unique shaders to return
    """
    return bridge.call(
        "list_shaders",
        {"max_events": max_events, "max_shaders": max_shaders},
        timeout=60.0,
    )


@mcp.tool
def search_shaders(
    pattern: str,
    stage: Literal["vs", "hs", "ds", "gs", "ps", "cs", "vertex", "hull", "domain", "geometry", "pixel", "compute"] | None = None,
    limit: int = 50,
    max_events: int = 10000,
    disassembly_target: str | None = None,
) -> dict:
    """
    Search shader disassembly text across unique shaders.

    Args:
        pattern: Case-insensitive text to search for
        stage: Optional shader stage filter
        limit: Maximum matching shaders to return
        max_events: Maximum draw/dispatch events to scan
        disassembly_target: Optional disassembly target substring, e.g. "HLSL"
    """
    params: dict[str, object] = {
        "pattern": pattern,
        "limit": limit,
        "max_events": max_events,
    }
    if stage is not None:
        params["stage"] = stage
    if disassembly_target is not None:
        params["disassembly_target"] = disassembly_target
    return bridge.call("search_shaders", params, timeout=90.0)


@mcp.tool
def get_buffer_contents(
    resource_id: str,
    offset: int = 0,
    length: int = 0,
    event_id: int | None = None,
) -> dict:
    """
    Read the contents of a buffer resource.

    Args:
        resource_id: The resource ID of the buffer to read
        offset: Byte offset to start reading from (default: 0)
        length: Number of bytes to read, 0 for entire buffer (default: 0)
        event_id: Optional event ID to set as the current frame event before reading.
                  Required for transient/internal buffers that only exist at a specific
                  draw call (e.g. constant buffer uploads, scratch UAVs). Omit for
                  persistent resources listed in GetBuffers().

    Returns buffer data as base64-encoded bytes along with metadata.
    """
    params = {"resource_id": resource_id, "offset": offset, "length": length}
    if event_id is not None:
        params["event_id"] = event_id
    return bridge.call("get_buffer_contents", params)


@mcp.tool
def get_textures() -> dict:
    """
    List all texture resources alive in the current capture.

    Returns resource IDs, names, dimensions, formats, mip counts, array sizes,
    MSAA sample counts, and byte sizes when available.
    """
    return bridge.call("get_textures")


@mcp.tool
def get_buffers() -> dict:
    """
    List all buffer resources alive in the current capture.

    Returns resource IDs, names, byte lengths, creation flags, and usage metadata
    when RenderDoc exposes it.
    """
    return bridge.call("get_buffers")


@mcp.tool
def get_resources() -> dict:
    """
    List all resources in the current capture.

    Returns RenderDoc resources with names and inferred type labels (texture,
    buffer, or resource).
    """
    return bridge.call("get_resources")


@mcp.tool
def get_resource_info(resource_id: str) -> dict:
    """
    Get detailed metadata for any RenderDoc resource.

    Args:
        resource_id: Resource ID from get_resources/get_textures/get_buffers

    Returns type-specific metadata for textures and buffers when available.
    """
    return bridge.call("get_resource_info", {"resource_id": resource_id})


@mcp.tool
def get_resource_usage(resource_id: str) -> dict:
    """
    Get frame usage history for one RenderDoc resource.

    Args:
        resource_id: Resource ID from get_resources/get_textures/get_buffers

    Returns usage events with action names and read/write classification.
    """
    return bridge.call("get_resource_usage", {"resource_id": resource_id})


@mcp.tool
def get_texture_info(resource_id: str) -> dict:
    """
    Get metadata about a texture resource.

    Args:
        resource_id: The resource ID of the texture

    Includes dimensions, format, mip levels, and other properties.
    """
    return bridge.call("get_texture_info", {"resource_id": resource_id})


@mcp.tool
def get_texture_data(
    resource_id: str,
    mip: int = 0,
    slice: int = 0,
    sample: int = 0,
    depth_slice: int | None = None,
) -> dict:
    """
    Read the pixel data of a texture resource.

    Args:
        resource_id: The resource ID of the texture to read
        mip: Mip level to retrieve (default: 0)
        slice: Array slice or cube face index (default: 0)
               For cube maps: 0=X+, 1=X-, 2=Y+, 3=Y-, 4=Z+, 5=Z-
        sample: MSAA sample index (default: 0)
        depth_slice: For 3D textures only, extract a specific depth slice (default: None = full volume)
                     When specified, returns only the 2D slice at that depth index

    Returns texture pixel data as base64-encoded bytes along with metadata
    including dimensions at the requested mip level and format information.
    """
    params = {"resource_id": resource_id, "mip": mip, "slice": slice, "sample": sample}
    if depth_slice is not None:
        params["depth_slice"] = depth_slice
    return bridge.call("get_texture_data", params)


@mcp.tool
def pick_pixel(
    resource_id: str,
    x: int,
    y: int,
    mip: int = 0,
    slice: int = 0,
    sample: int = 0,
) -> dict:
    """
    Read one pixel value from a texture or render target.

    Args:
        resource_id: Texture resource ID
        x: Pixel X coordinate
        y: Pixel Y coordinate
        mip: Mip level (default 0)
        slice: Array slice or cube face (default 0)
        sample: MSAA sample index (default 0)

    Returns RGBA values as floats when RenderDoc can decode the format.
    """
    return bridge.call(
        "pick_pixel",
        {
            "resource_id": resource_id,
            "x": x,
            "y": y,
            "mip": mip,
            "slice": slice,
            "sample": sample,
        },
    )


@mcp.tool
def pixel_history(
    resource_id: str,
    x: int,
    y: int,
    mip: int = 0,
    slice: int = 0,
    sample: int = 0,
) -> dict:
    """
    Get the modification history for one pixel across the frame.

    Args:
        resource_id: Render target texture resource ID
        x: Pixel X coordinate
        y: Pixel Y coordinate
        mip: Mip level (default 0)
        slice: Array slice or cube face (default 0)
        sample: MSAA sample index (default 0)

    Returns per-event pre/post values when RenderDoc can provide pixel history.
    """
    return bridge.call(
        "pixel_history",
        {
            "resource_id": resource_id,
            "x": x,
            "y": y,
            "mip": mip,
            "slice": slice,
            "sample": sample,
        },
        timeout=60.0,
    )


@mcp.tool
def export_texture_to_file(
    resource_id: str,
    output_path: str,
    file_type: str = "PNG",
    mip: int = 0,
    slice: int = 0,
    sample: int = 0,
    alpha: str = "Preserve",
    event_id: int | None = None,
) -> dict:
    """
    Save a texture to an image file ON THE RENDERDOC HOST, returning only metadata.

    Use this instead of get_texture_data for real textures: a 1024x1024 RGBA8 texture
    is 4 MB, whose base64 (~5.6M chars) overflows / truncates the MCP transport and
    the agent context. SaveTexture decodes + encodes the image on the host side and
    writes it straight to disk, so nothing heavy crosses the wire.

    The host performs format conversion (incl. block-compressed / typeless decode) and
    the correct vertical orientation for the file format, so the written PNG is already
    upright (no manual flip needed regardless of graphics API).

    Args:
        resource_id: Texture resource ID (e.g. "11059" or "ResourceId::11059").
        output_path: Absolute path on the RenderDoc host to write to (extension should
            match file_type, e.g. ...\\T_albedo.png).
        file_type: PNG (default), JPG, BMP, TGA, HDR, EXR, or DDS. PNG/BMP/TGA/DDS keep
            alpha; JPG/HDR drop it. DDS preserves all mips/slices and exact format.
        mip: Mip level to save (default 0). Ignored for DDS (saves all).
        slice: Array slice or cube face (default 0). For cubemaps, pass -1 to
            export all faces; DDS writes a single native cubemap, other formats
            write one file per face with _faceN suffixes.
        sample: MSAA sample index (default 0).
        alpha: Preserve | Discard | BlendToColor | BlendToCheckerboard (default Preserve).
        event_id: Optional frame event to set first (needed for transient render targets;
            not needed for persistent material textures).

    Returns metadata: output_path, file_type, width, height, mip, slice, sample, format,
    mip_levels.
    """
    params = {
        "resource_id": resource_id,
        "output_path": output_path,
        "file_type": file_type,
        "mip": mip,
        "slice": slice,
        "sample": sample,
        "alpha": alpha,
    }
    if event_id is not None:
        params["event_id"] = event_id
    return bridge.call("export_texture_to_file", params)


@mcp.tool
def get_pipeline_state(event_id: int) -> dict:
    """
    Get the full graphics pipeline state at a specific event.

    Args:
        event_id: The event ID to get pipeline state at

    Returns detailed pipeline state including:
    - Bound shaders with entry points for each stage
    - Shader resources (SRVs): textures and buffers with dimensions, format, slot, name
    - UAVs (RWTextures/RWBuffers): resource details with dimensions and format
    - Samplers: addressing modes, filter settings, LOD parameters
    - Constant buffers: slot, size, variable count
    - Render targets and depth target
    - Viewports and input assembly state
    """
    return bridge.call("get_pipeline_state", {"event_id": event_id})


@mcp.tool
def get_mesh_data(event_id: int) -> dict:
    """
    Extract decoded mesh data (index buffer + vertex attributes) for a draw call.

    Reads the index buffer and bound vertex buffers at the given event, then decodes
    each vertex attribute (POSITION, NORMAL, TEXCOORD, COLOR, etc.) according to the
    input layout. Useful for inspecting geometry, exporting meshes, or analyzing
    skinning / morph targets.

    Args:
        event_id: The event ID of the draw call to extract mesh data from

    Returns a dict with:
    - event_id, topology, num_indices, num_vertices, min_index, max_index
    - indices: list of vertex indices (with baseVertex applied)
    - attributes: list of {name, semantic_name, vertex_buffer_slot, byte_offset,
                          format, components, values}
      where values is a list[list[float|int]] of length num_vertices

    Supports formats: float32, float16 (half), unorm8, snorm8, uint8/16/32.
    """
    return bridge.call("get_mesh_data", {"event_id": event_id})


@mcp.tool
def get_world_matrix(
    event_id: int,
    o2w_offset: int = 32,
    w2o_offset: int = 96,
) -> dict:
    """
    Read the Unity world transform matrices (unity_ObjectToWorld / unity_WorldToObject)
    from the vertex shader constant buffer cb0 ($Globals) at a draw call.

    get_mesh_data returns OBJECT-space vertices. To place an extracted model at the
    same world position as in the captured frame, you need this matrix. Use it to
    bake vertices to world space, or to derive a GameObject Transform.

    Args:
        event_id: The event ID of the draw call.
        o2w_offset: Byte offset of unity_ObjectToWorld inside cb0 (default 32; read it
                    from get_pipeline_state -> VS $Globals variable byte_offset if it differs).
        w2o_offset: Byte offset of unity_WorldToObject inside cb0 (default 96).

    Returns:
    - object_to_world_columns: 4x4 as 4 stored float4s (these are matrix COLUMNS;
      worldPos = c0*x + c1*y + c2*z + c3).
    - world_to_object_rows: 4x4 stored float4s (used as dp3 rows for normals).
    - object_to_world_raw / world_to_object_raw: flat 16-float arrays.
    - _diag: which cbuffer accessor + resource were used (for debugging).
    """
    return bridge.call("get_world_matrix", {
        "event_id": event_id,
        "o2w_offset": o2w_offset,
        "w2o_offset": w2o_offset,
    })


@mcp.tool
def export_mesh_to_file(
    event_id: int,
    output_path: str,
    bake_world: bool = True,
    pos_slot: int = -1,
    normal_slot: int = -1,
    tangent_slot: int = -1,
    uv0_slot: int = -1,
    uv1_slot: int = -1,
    extra_slot: int = -1,
    o2w_offset: int = 32,
    w2o_offset: int = 96,
) -> dict:
    """
    Extract the mesh at a draw call and WRITE the per-vertex data to a JSON file on
    the RenderDoc host machine, returning only small metadata.

    Use this instead of get_mesh_data for real models: large meshes (thousands of
    vertices) produce JSON too big to return through the MCP transport, which fails
    or gets truncated. This writes the heavy arrays straight to disk.

    The output JSON has: num_indices, num_vertices, indices, position, and (when
    present) normal, tangent (xyzw), uv0, uv1, uv2_extra. Field names are by SEMANTIC
    (mapped from the vertex-buffer slots below), ready for a Unity mesh builder.

    Args:
        event_id: The draw call event ID.
        output_path: Absolute path on the RenderDoc host to write the JSON to.
        bake_world: If True (default), transform position/normal/tangent into WORLD
            space using the VS cb0 matrices (replicating the shader). The resulting
            GameObject sits at Transform origin and matches the captured frame, so
            multiple extracted draws line up with each other automatically.
        pos_slot/normal_slot/tangent_slot/uv0_slot/uv1_slot/extra_slot: which
            vertex-buffer slot carries each semantic. Negative values (the default)
            let the RenderDoc extension infer slots from semantics and component
            counts. Use non-negative values for explicit overrides.
        o2w_offset/w2o_offset: byte offsets of the matrices in cb0 (see get_world_matrix).

    Returns metadata: output_path, counts, which channels were written, position
    world-space bounds, the world matrix used, and the slot map actually applied.
    """
    return bridge.call("export_mesh_to_file", {
        "event_id": event_id,
        "output_path": output_path,
        "bake_world": bake_world,
        "pos_slot": pos_slot,
        "normal_slot": normal_slot,
        "tangent_slot": tangent_slot,
        "uv0_slot": uv0_slot,
        "uv1_slot": uv1_slot,
        "extra_slot": extra_slot,
        "o2w_offset": o2w_offset,
        "w2o_offset": w2o_offset,
    })


@mcp.tool
def export_postvs_to_file(
    event_id: int,
    output_path: str,
    instance: int = 0,
    view: int = 0,
    graft_uv: bool = True,
    uv0_slot: int = 3,
    uv1_slot: int = 4,
    color_slot: int = 1,
) -> dict:
    """
    Extract VS-OUTPUT (post-skinning / post-transform) vertices for a draw and write
    WORLD-space JSON to disk, returning only small metadata.

    USE THIS FOR SKINNED MESHES. export_mesh_to_file reads the INPUT vertex buffer,
    which for a skinned character holds BIND-POSE (T-pose-ish) vertices — they do NOT
    match the animated pose seen in the captured frame. RenderDoc also captures the VS
    OUTPUT (the "PostVS" / VSOut stage): the actual on-screen, skinned geometry.

    SV_Position in PostVS is CLIP space. This tool reads MatrixVP from the VS constant
    buffer ($Globals / UnityPerFrame), inverts it (clip = VP @ world), and recovers
    WORLD-space positions — so the result lines up pixel-accurately with the frame and
    with an RDC-absolute-positioned camera.

    Output JSON matches export_mesh_to_file's schema (num_indices, num_vertices,
    indices, position) so RDCMeshBuilder consumes it directly. position is WORLD space:
    place the GameObject at Transform origin (identity), and align the RDC camera using
    absolute world coords.

    UV0/UV1 (and vertex COLOR) are POSE-INVARIANT (skinning moves position/normal, never
    texcoords). When graft_uv is True (default) they are copied from the INPUT VB onto
    the matching PostVS vertices (1:1 for indexed draws), so the PostVS mesh is texture-
    mappable out of the box — no manual graft step needed. Normals/tangents are still
    omitted (bind-pose values are wrong for the skinned pose; recompute them in Unity).

    Args:
        event_id: The draw call event ID (a skinned mesh draw).
        output_path: Absolute path on the RenderDoc host to write the JSON to.
        instance: Instance index for instanced draws (default 0).
        view: Multiview index (default 0).
        graft_uv: Copy UV0/UV1/COLOR from the input VB (default True). Set False for the
            old position-only fast path.
        uv0_slot/uv1_slot/color_slot: Input-VB slot fallback used ONLY when input
            attributes are generically named (DXBC/ANGLE). GL hlslcc captures resolve by
            semantic name (in_TEXCOORD0/1, in_COLOR0) automatically and ignore these.

    Returns metadata: output_path, counts, world-space position bounds, has_uv0/has_uv1/
    has_color flags, the MatrixVP used, and diagnostics (incl. _graft_diag).
    """
    return bridge.call("export_postvs_to_file", {
        "event_id": event_id,
        "output_path": output_path,
        "instance": instance,
        "view": view,
        "graft_uv": graft_uv,
        "uv0_slot": uv0_slot,
        "uv1_slot": uv1_slot,
        "color_slot": color_slot,
    })


@mcp.tool
def list_captures(directory: str) -> dict:
    """
    List all RenderDoc capture files (.rdc) in the specified directory.

    Args:
        directory: The directory path to search for capture files

    Returns a list of capture files with their metadata including:
    - filename: The capture file name
    - path: Full path to the file
    - size_bytes: File size in bytes
    - modified_time: Last modified timestamp (ISO format)
    """
    return bridge.call("list_captures", {"directory": directory})


@mcp.tool
def open_capture(capture_path: str) -> dict:
    """
    Open a RenderDoc capture file (.rdc).

    Args:
        capture_path: Full path to the capture file to open

    Returns success status and information about the opened capture.
    Note: This will close any currently open capture.
    """
    return bridge.call("open_capture", {"capture_path": capture_path})


@mcp.tool
def capture_frame(
    exe_path: str,
    working_dir: str = "",
    cmd_line: str = "",
    delay_frames: int = 100,
    output_path: str = "",
    timeout_seconds: int = 60,
) -> dict:
    """
    Launch an application through RenderDoc, capture one frame, then open it.

    Args:
        exe_path: Absolute path to the executable to launch
        working_dir: Optional working directory; defaults to the executable folder
        cmd_line: Optional command-line arguments for the target process
        delay_frames: Approximate number of frames to wait before triggering capture
        output_path: Optional .rdc path to write; defaults to a temp capture path
        timeout_seconds: Seconds to wait for target control and capture completion

    The RenderDoc MCP bridge must already be loaded in qrenderdoc for this tool.
    """
    params: dict[str, object] = {
        "exe_path": exe_path,
        "working_dir": working_dir,
        "cmd_line": cmd_line,
        "delay_frames": delay_frames,
        "output_path": output_path,
        "timeout_seconds": timeout_seconds,
    }
    return bridge.call("capture_frame", params, timeout=float(timeout_seconds) + 120.0)


@mcp.tool
def launch_application(
    exe_path: str,
    working_dir: str = "",
    cmd_line: str = "",
    graphics_api: Literal["auto", "vulkan", "d3d11", "d3d12", "opengl", "gles"] = "auto",
) -> dict:
    """
    Launch an application through RenderDoc and keep its target control session open.

    Args:
        exe_path: Absolute path to the executable to launch, e.g. MuMu12's emulator exe
        working_dir: Optional working directory; defaults to the executable folder
        cmd_line: Optional command-line arguments for the target process
        graphics_api: API hint for launch environment setup. Use auto, vulkan,
            d3d11, d3d12, opengl, or gles. The target process still decides which
            graphics API it actually creates.

    Returns a session_id and pid. Use the session_id with get_target_status,
    trigger_capture, and close_target.
    """
    return bridge.call(
        "launch_application",
        {
            "exe_path": exe_path,
            "working_dir": working_dir,
            "cmd_line": cmd_line,
            "graphics_api": graphics_api,
        },
        timeout=90.0,
    )


@mcp.tool
def get_target_status(session_id: str) -> dict:
    """
    Check whether a RenderDoc-launched target session is still controllable.

    Args:
        session_id: Session ID returned by launch_application
    """
    return bridge.call(
        "get_target_status",
        {"session_id": session_id},
        timeout=10.0,
    )


@mcp.tool
def trigger_capture(
    session_id: str,
    output_path: str = "",
    timeout_seconds: int = 60,
) -> dict:
    """
    Trigger one capture on a previously launched target session and save it.

    Args:
        session_id: Session ID returned by launch_application
        output_path: Optional .rdc path to write; defaults to a temp capture path
        timeout_seconds: Seconds to wait for capture completion
    """
    return bridge.call(
        "trigger_capture",
        {
            "session_id": session_id,
            "output_path": output_path,
            "timeout_seconds": timeout_seconds,
        },
        timeout=float(timeout_seconds) + 120.0,
    )


@mcp.tool
def close_target(session_id: str) -> dict:
    """
    Close a RenderDoc target control session created by launch_application.

    Args:
        session_id: Session ID returned by launch_application
    """
    return bridge.call(
        "close_target",
        {"session_id": session_id},
        timeout=10.0,
    )


@mcp.tool
def launch_renderdoc(capture_path: str, renderdoc_path: str = "") -> dict:
    """
    Launch qrenderdoc with a capture file and wait for the MCP bridge to respond.

    If an existing RenderDoc instance already has the bridge online, this reuses it
    and calls open_capture instead of spawning another process.

    Args:
        capture_path: Absolute path to the .rdc capture file
        renderdoc_path: Optional qrenderdoc executable path or installation directory

    Returns launch metadata including executable path, process ID, file size, and
    whether the bridge became ready before the wait timeout.
    """
    if not capture_path:
        return {"error": "capture_path is required"}
    if not os.path.isfile(capture_path):
        return {"error": "File not found: %s" % capture_path}
    if not capture_path.lower().endswith(".rdc"):
        return {"error": "Not an .rdc file: %s" % capture_path}

    if bridge.is_bridge_alive():
        result = bridge.call("open_capture", {"capture_path": capture_path}, timeout=60.0)
        if isinstance(result, dict):
            result["method"] = "open_capture"
            result["bridge_already_running"] = True
        return result

    exe = _find_renderdoc_exe(renderdoc_path)
    if not exe:
        return {
            "error": "Could not find qrenderdoc executable",
            "searched_paths": _get_renderdoc_search_paths(),
        }

    popen_kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        process = subprocess.Popen([exe, capture_path], **popen_kwargs)
    except Exception as e:
        return {"error": "Failed to launch RenderDoc: %s" % str(e), "exe": exe}

    file_size_mb = os.path.getsize(capture_path) / (1024 * 1024)
    if file_size_mb > 200:
        max_wait = 90.0
    elif file_size_mb > 50:
        max_wait = 60.0
    else:
        max_wait = 40.0

    elapsed = 0.0
    poll_interval = 2.0
    bridge_ready = False
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        if bridge.is_bridge_alive():
            bridge_ready = True
            break

    return {
        "launched": True,
        "exe": exe,
        "capture_path": capture_path,
        "pid": process.pid,
        "bridge_ready": bridge_ready,
        "wait_seconds": elapsed if bridge_ready else max_wait,
        "file_size_mb": round(file_size_mb, 1),
        "status": (
            "RenderDoc launched and MCP bridge is ready"
            if bridge_ready
            else "RenderDoc launched; capture may still be loading. Use ping to check readiness."
        ),
    }


def _find_renderdoc_exe(user_path: str = "") -> str:
    """Find qrenderdoc from an explicit path, env vars, PATH, or common installs."""
    if user_path:
        if os.path.isfile(user_path):
            return user_path
        for name in ("qrenderdoc.exe", "qrenderdoc"):
            candidate = os.path.join(user_path, name)
            if os.path.isfile(candidate):
                return candidate

    env_path = os.environ.get("RENDERDOC_PATH", "") or os.environ.get("RENDERDOC_MODULE_PATH", "")
    if env_path:
        if os.path.isfile(env_path):
            return env_path
        for name in ("qrenderdoc.exe", "qrenderdoc"):
            candidate = os.path.join(env_path, name)
            if os.path.isfile(candidate):
                return candidate

    path_exe = shutil.which("qrenderdoc")
    if path_exe:
        return path_exe

    for candidate in _get_renderdoc_search_paths():
        if os.path.isfile(candidate):
            return candidate
    return ""


def _get_renderdoc_search_paths() -> list[str]:
    """Return common qrenderdoc install paths for diagnostics."""
    paths: list[str] = []
    for drive in ("C", "D"):
        paths.append("%s:\\Program Files\\RenderDoc\\qrenderdoc.exe" % drive)
        paths.append("%s:\\Program Files (x86)\\RenderDoc\\qrenderdoc.exe" % drive)

    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        paths.append(os.path.join(local_appdata, "RenderDoc", "qrenderdoc.exe"))

    paths.extend([
        "/usr/bin/qrenderdoc",
        "/usr/local/bin/qrenderdoc",
        "/opt/renderdoc/bin/qrenderdoc",
        "/Applications/RenderDoc.app/Contents/MacOS/qrenderdoc",
    ])
    return paths


def main():
    """Run the MCP server"""
    mcp.run()


if __name__ == "__main__":
    main()

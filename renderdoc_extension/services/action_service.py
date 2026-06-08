"""
Draw call / action operations service for RenderDoc.
"""

import renderdoc as rd

from ..utils import Serializers, Helpers


class ActionService:
    """Draw call / action operations service"""

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn

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
        """
        Get all draw calls/actions in the capture with optional filtering.
        """
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"actions": []}

        def callback(controller):
            root_actions = controller.GetRootActions()
            structured_file = controller.GetStructuredFile()
            result["actions"] = Serializers.serialize_actions(
                root_actions,
                structured_file,
                include_children,
                marker_filter=marker_filter,
                exclude_markers=exclude_markers,
                event_id_min=event_id_min,
                event_id_max=event_id_max,
                only_actions=only_actions,
                flags_filter=flags_filter,
            )

        self._invoke(callback)
        return result

    def get_frame_summary(self):
        """
        Get a summary of the current capture frame.
        """
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"summary": None}

        def callback(controller):
            root_actions = controller.GetRootActions()
            structured_file = controller.GetStructuredFile()
            api = controller.GetAPIProperties().pipelineType

            # Statistics counters
            stats = {
                "draw_calls": 0,
                "dispatches": 0,
                "clears": 0,
                "copies": 0,
                "presents": 0,
                "markers": 0,
            }
            total_actions = [0]

            def count_actions(actions):
                for action in actions:
                    total_actions[0] += 1
                    flags = action.flags

                    if flags & rd.ActionFlags.Drawcall:
                        stats["draw_calls"] += 1
                    if flags & rd.ActionFlags.Dispatch:
                        stats["dispatches"] += 1
                    if flags & rd.ActionFlags.Clear:
                        stats["clears"] += 1
                    if flags & rd.ActionFlags.Copy:
                        stats["copies"] += 1
                    if flags & rd.ActionFlags.Present:
                        stats["presents"] += 1
                    if flags & (rd.ActionFlags.PushMarker | rd.ActionFlags.SetMarker):
                        stats["markers"] += 1

                    if action.children:
                        count_actions(action.children)

            count_actions(root_actions)

            # Top-level markers
            top_markers = []
            for action in root_actions:
                if action.flags & rd.ActionFlags.PushMarker:
                    child_count = Helpers.count_children(action)
                    top_markers.append({
                        "name": action.GetName(structured_file),
                        "event_id": action.eventId,
                        "child_count": child_count,
                    })

            # Resource counts
            textures = controller.GetTextures()
            buffers = controller.GetBuffers()

            result["summary"] = {
                "api": str(api),
                "total_actions": total_actions[0],
                "statistics": stats,
                "top_level_markers": top_markers,
                "resource_counts": {
                    "textures": len(textures),
                    "buffers": len(buffers),
                },
            }

        self._invoke(callback)
        return result["summary"]

    def get_draw_call_details(self, event_id):
        """Get detailed information about a specific draw call"""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"details": None, "error": None}

        def callback(controller):
            # Move to the event
            controller.SetFrameEvent(event_id, True)

            action = self.ctx.GetAction(event_id)
            if not action:
                result["error"] = "No action at event %d" % event_id
                return

            structured_file = controller.GetStructuredFile()

            details = {
                "event_id": action.eventId,
                "action_id": action.actionId,
                "name": action.GetName(structured_file),
                "flags": Serializers.serialize_flags(action.flags),
                "num_indices": action.numIndices,
                "num_instances": action.numInstances,
                "base_vertex": action.baseVertex,
                "vertex_offset": action.vertexOffset,
                "instance_offset": action.instanceOffset,
                "index_offset": action.indexOffset,
            }

            # Output resources
            outputs = []
            for i, output in enumerate(action.outputs):
                if output != rd.ResourceId.Null():
                    outputs.append({"index": i, "resource_id": str(output)})
            details["outputs"] = outputs

            if action.depthOut != rd.ResourceId.Null():
                details["depth_output"] = str(action.depthOut)

            result["details"] = details

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["details"]

    def get_action_timings(
        self,
        event_ids=None,
        marker_filter=None,
        exclude_markers=None,
    ):
        """
        Get GPU timing information for actions.

        Args:
            event_ids: Optional list of specific event IDs to get timings for.
                      If None, returns timings for all actions.
            marker_filter: Only include actions under markers containing this string.
            exclude_markers: Exclude actions under markers containing these strings.

        Returns:
            Dictionary with:
            - available: Whether GPU timing counters are supported
            - unit: Time unit (typically "seconds")
            - timings: List of {event_id, name, duration_seconds, duration_ms}
            - total_duration_ms: Sum of all durations
        """
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            # Check if EventGPUDuration counter is available
            counters = controller.EnumerateCounters()
            if rd.GPUCounter.EventGPUDuration not in counters:
                result["data"] = {
                    "available": False,
                    "error": "GPU timing counters not supported on this capture",
                }
                return

            # Get counter description
            counter_desc = controller.DescribeCounter(rd.GPUCounter.EventGPUDuration)

            # Fetch timing data
            counter_results = controller.FetchCounters([rd.GPUCounter.EventGPUDuration])

            # Build event_id to timing map
            timing_map = {}
            target_counter = int(rd.GPUCounter.EventGPUDuration)
            for r in counter_results:
                if r.counter == target_counter:
                    # EventGPUDuration typically returns double
                    # Try to get the value in the most appropriate way
                    val = r.value.d  # double is the standard for duration
                    timing_map[r.eventId] = val

            # Get structured file for action names
            structured_file = controller.GetStructuredFile()
            root_actions = controller.GetRootActions()

            # Collect actions to report timings for
            timings = []
            total_duration = [0.0]

            def collect_timings(actions, parent_markers=None):
                if parent_markers is None:
                    parent_markers = []

                for action in actions:
                    action_name = action.GetName(structured_file)
                    current_markers = parent_markers[:]

                    # Track marker hierarchy
                    is_marker = bool(action.flags & (rd.ActionFlags.PushMarker | rd.ActionFlags.SetMarker))
                    if is_marker:
                        current_markers.append(action_name)

                    # Apply marker filter
                    if marker_filter:
                        marker_path = "/".join(current_markers)
                        if marker_filter.lower() not in marker_path.lower():
                            # Still recurse into children
                            if action.children:
                                collect_timings(action.children, current_markers)
                            continue

                    # Apply exclude filter
                    if exclude_markers:
                        skip = False
                        for exclude in exclude_markers:
                            for m in current_markers:
                                if exclude.lower() in m.lower():
                                    skip = True
                                    break
                            if skip:
                                break
                        if skip:
                            if action.children:
                                collect_timings(action.children, current_markers)
                            continue

                    # Check if we should include this event
                    event_id = action.eventId
                    include = True
                    if event_ids is not None:
                        include = event_id in event_ids

                    if include and event_id in timing_map:
                        duration_sec = timing_map[event_id]
                        duration_ms = duration_sec * 1000.0
                        timings.append({
                            "event_id": event_id,
                            "name": action_name,
                            "duration_seconds": duration_sec,
                            "duration_ms": duration_ms,
                        })
                        total_duration[0] += duration_ms

                    # Recurse into children
                    if action.children:
                        collect_timings(action.children, current_markers)

            collect_timings(root_actions)

            # Sort by event_id
            timings.sort(key=lambda x: x["event_id"])

            result["data"] = {
                "available": True,
                "unit": str(counter_desc.unit),
                "timings": timings,
                "total_duration_ms": total_duration[0],
                "count": len(timings),
            }

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def enumerate_counters(self):
        """List GPU counters available for the current capture."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                counters = []
                for counter in controller.EnumerateCounters():
                    try:
                        desc = controller.DescribeCounter(counter)
                    except Exception:
                        desc = None
                    counters.append({
                        "id": int(counter),
                        "name": getattr(desc, "name", str(counter)) if desc else str(counter),
                        "description": getattr(desc, "description", "") if desc else "",
                        "unit": str(getattr(desc, "unit", "")) if desc else "",
                        "result_type": str(getattr(desc, "resultType", "")) if desc else "",
                    })
                result["data"] = {
                    "count": len(counters),
                    "counters": counters,
                }
            except Exception as e:
                import traceback
                result["error"] = "enumerate_counters error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def fetch_counters(self, counter_ids):
        """Fetch GPU counter values for specific counter IDs."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")
        if not counter_ids:
            raise ValueError("counter_ids is required")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                available = {}
                try:
                    for counter in controller.EnumerateCounters():
                        available[int(counter)] = counter
                except Exception:
                    pass

                counters = []
                for counter_id in counter_ids:
                    numeric_id = int(counter_id)
                    if numeric_id in available:
                        counters.append(available[numeric_id])
                    else:
                        counters.append(rd.GPUCounter(numeric_id))

                descriptions = {}
                for counter in counters:
                    try:
                        descriptions[int(counter)] = controller.DescribeCounter(counter)
                    except Exception:
                        descriptions[int(counter)] = None

                counter_results = controller.FetchCounters(counters)
                values = []
                for item in counter_results:
                    counter_id = int(item.counter)
                    desc = descriptions.get(counter_id)
                    values.append({
                        "event_id": getattr(item, "eventId", 0),
                        "counter_id": counter_id,
                        "counter_name": getattr(desc, "name", str(counter_id)) if desc else str(counter_id),
                        "unit": str(getattr(desc, "unit", "")) if desc else "",
                        "value": self._counter_result_value(
                            getattr(item, "value", None), desc),
                    })

                result["data"] = {
                    "counter_ids": [int(c) for c in counter_ids],
                    "count": len(values),
                    "values": values,
                }
            except Exception as e:
                import traceback
                result["error"] = "fetch_counters error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def get_debug_messages(self):
        """Return API validation/debug messages recorded in the capture."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                messages = []
                for msg in controller.GetDebugMessages():
                    messages.append({
                        "event_id": getattr(msg, "eventId", 0),
                        "message_id": getattr(msg, "messageID", getattr(msg, "messageId", "")),
                        "category": str(getattr(msg, "category", "")),
                        "severity": str(getattr(msg, "severity", "")),
                        "source": str(getattr(msg, "source", "")),
                        "description": getattr(msg, "description", ""),
                    })
                result["data"] = {
                    "count": len(messages),
                    "messages": messages,
                }
            except Exception as e:
                import traceback
                result["error"] = "get_debug_messages error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def debug_pixel(self, event_id, x, y, sample=0, primitive=-1,
                    max_steps=50, max_vars_per_step=10):
        """Debug pixel shader execution for a screen pixel at an event."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            trace = None
            try:
                controller.SetFrameEvent(int(event_id), True)
                inputs = rd.DebugPixelInputs()
                inputs.sample = int(sample)
                inputs.primitive = 0xFFFFFFFF if int(primitive) < 0 else int(primitive)

                trace = controller.DebugPixel(int(x), int(y), inputs)
                debugger = getattr(trace, "debugger", None) if trace else None
                if not debugger:
                    result["data"] = {
                        "available": False,
                        "event_id": int(event_id),
                        "x": int(x),
                        "y": int(y),
                        "error": "No pixel debug trace returned",
                    }
                    return

                steps = self._collect_debug_steps(
                    controller, debugger, int(max_steps), int(max_vars_per_step))
                result["data"] = {
                    "available": True,
                    "event_id": int(event_id),
                    "x": int(x),
                    "y": int(y),
                    "sample": int(sample),
                    "primitive": int(primitive),
                    "step_count": len(steps),
                    "trace": steps,
                }
            except Exception as e:
                import traceback
                result["error"] = "debug_pixel error: %s\n%s" % (
                    str(e), traceback.format_exc())
            finally:
                if trace is not None:
                    try:
                        controller.FreeTrace(trace)
                    except Exception:
                        pass

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def debug_vertex(self, event_id, vertex_id, instance_id=0, index=0,
                     view=0, max_steps=50, max_vars_per_step=10):
        """Debug vertex shader execution for a vertex at an event."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            trace = None
            try:
                controller.SetFrameEvent(int(event_id), True)
                trace = controller.DebugVertex(
                    int(vertex_id), int(instance_id), int(index), int(view))
                debugger = getattr(trace, "debugger", None) if trace else None
                if not debugger:
                    result["data"] = {
                        "available": False,
                        "event_id": int(event_id),
                        "vertex_id": int(vertex_id),
                        "instance_id": int(instance_id),
                        "error": "No vertex debug trace returned",
                    }
                    return

                steps = self._collect_debug_steps(
                    controller, debugger, int(max_steps), int(max_vars_per_step))
                result["data"] = {
                    "available": True,
                    "event_id": int(event_id),
                    "vertex_id": int(vertex_id),
                    "instance_id": int(instance_id),
                    "index": int(index),
                    "view": int(view),
                    "step_count": len(steps),
                    "trace": steps,
                }
            except Exception as e:
                import traceback
                result["error"] = "debug_vertex error: %s\n%s" % (
                    str(e), traceback.format_exc())
            finally:
                if trace is not None:
                    try:
                        controller.FreeTrace(trace)
                    except Exception:
                        pass

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def _collect_debug_steps(self, controller, debugger, max_steps, max_vars_per_step):
        """Collect a bounded shader debugger trace."""
        steps = []
        while len(steps) < max_steps:
            batch = controller.ContinueDebug(debugger)
            if not batch:
                break
            for state in batch:
                step = {
                    "step": getattr(state, "stepIndex", len(steps)),
                }
                source_vars = getattr(state, "sourceVars", None)
                if source_vars:
                    step["vars"] = [
                        self._serialize_debug_var(var)
                        for var in list(source_vars)[:max_vars_per_step]
                    ]
                steps.append(step)
                if len(steps) >= max_steps:
                    break
        return steps

    def _serialize_debug_var(self, var):
        """Serialize one shader debugger source variable."""
        data = {
            "name": getattr(var, "name", ""),
        }
        try:
            data["value"] = str(getattr(var, "value", ""))
        except Exception:
            pass
        try:
            data["type"] = str(getattr(var, "type", ""))
        except Exception:
            pass
        return data

    def _counter_result_value(self, value, desc=None):
        """Decode RenderDoc CounterValue using the described result type first."""
        if value is None:
            return None

        result_type = str(getattr(desc, "resultType", "")) if desc else ""
        preferred = []
        if "Double" in result_type:
            preferred = ["d"]
        elif "Float" in result_type:
            preferred = ["f", "d"]
        elif "UInt64" in result_type or "Uint64" in result_type:
            preferred = ["u64", "u32"]
        elif "SInt64" in result_type or "Int64" in result_type:
            preferred = ["s64", "s32"]
        elif "UInt" in result_type or "Uint" in result_type:
            preferred = ["u32", "u64"]
        elif "SInt" in result_type or "Int" in result_type:
            preferred = ["s32", "s64"]

        for attr in preferred + ["d", "f", "u64", "s64", "u32", "s32"]:
            try:
                return getattr(value, attr)
            except Exception:
                pass
        return str(value)

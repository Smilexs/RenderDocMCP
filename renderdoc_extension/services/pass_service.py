"""
Render pass and frame-structure analysis service.
"""

import renderdoc as rd

from ..utils import Parsers, Serializers


class PassService:
    """Render pass analysis service."""

    _WRITE_USAGE_HINTS = (
        "ColorTarget",
        "DepthStencilTarget",
        "CopyDst",
        "Clear",
        "GenMips",
        "ResolveDst",
        "RWResource",
        "CPUWrite",
    )
    _READ_USAGE_HINTS = (
        "VertexBuffer",
        "IndexBuffer",
        "Constants",
        "Resource",
        "InputTarget",
        "CopySrc",
        "ResolveSrc",
        "Indirect",
    )

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn

    def list_passes(self):
        """List marker-based and synthetic render pass ranges."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                passes = self._enumerate_pass_ranges(controller)
                result["data"] = {
                    "count": len(passes),
                    "passes": passes,
                }
            except Exception as e:
                import traceback
                result["error"] = "list_passes error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def get_pass_info(self, event_id):
        """Get details and draw/dispatches for the pass containing event_id."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                passes = self._enumerate_pass_ranges(controller)
                pr = self._find_pass(passes, int(event_id))
                if pr is None:
                    result["error"] = "Event %s does not belong to any pass" % event_id
                    return

                structured_file = controller.GetStructuredFile()
                actions = []
                self._collect_actions_in_range(
                    controller.GetRootActions(),
                    structured_file,
                    pr["begin_event_id"],
                    pr["end_event_id"],
                    actions,
                )

                draw_count = 0
                dispatch_count = 0
                for action in actions:
                    if "Drawcall" in action["flags"]:
                        draw_count += 1
                    if "Dispatch" in action["flags"]:
                        dispatch_count += 1

                info = dict(pr)
                info.update({
                    "draw_count": draw_count,
                    "dispatch_count": dispatch_count,
                    "action_count": len(actions),
                    "actions": actions,
                })
                result["data"] = info
            except Exception as e:
                import traceback
                result["error"] = "get_pass_info error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def get_pass_attachments(self, event_id):
        """Get color/depth attachments for the pass containing event_id."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                passes = self._enumerate_pass_ranges(controller)
                pr = self._find_pass(passes, int(event_id))
                if pr is None:
                    result["error"] = "Event %s does not belong to any pass" % event_id
                    return
                if not pr.get("first_draw_event_id"):
                    result["error"] = "Pass '%s' has no draw/dispatch event" % pr["name"]
                    return

                attachments = self._attachments_for_event(
                    controller, pr["first_draw_event_id"])
                attachments.update({
                    "pass_name": pr["name"],
                    "event_id": pr["begin_event_id"],
                    "requested_event_id": int(event_id),
                    "synthetic": pr["synthetic"],
                    "first_draw_event_id": pr["first_draw_event_id"],
                })
                result["data"] = attachments
            except Exception as e:
                import traceback
                result["error"] = "get_pass_attachments error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def get_pass_statistics(self):
        """Return per-pass aggregate statistics."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                root_actions = controller.GetRootActions()
                passes = self._enumerate_pass_ranges(controller)
                stats = []
                for pr in passes:
                    draw_count, dispatch_count, triangles = self._count_actions_in_range(
                        root_actions, pr["begin_event_id"], pr["end_event_id"])
                    row = dict(pr)
                    row.update({
                        "draw_count": draw_count,
                        "dispatch_count": dispatch_count,
                        "total_triangles": triangles,
                        "attachment_count": 0,
                    })
                    if pr.get("first_draw_event_id"):
                        try:
                            attachments = self._attachments_for_event(
                                controller, pr["first_draw_event_id"])
                            row["attachment_count"] = (
                                len(attachments.get("color_targets", []))
                                + (1 if attachments.get("depth_target") else 0)
                            )
                            if attachments.get("color_targets"):
                                first_rt = attachments["color_targets"][0]
                                row["rt_width"] = first_rt.get("width", 0)
                                row["rt_height"] = first_rt.get("height", 0)
                        except Exception as e:
                            row["attachment_error"] = str(e)
                    stats.append(row)

                result["data"] = {
                    "count": len(stats),
                    "passes": stats,
                }
            except Exception as e:
                import traceback
                result["error"] = "get_pass_statistics error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def get_pass_deps(self):
        """Build an inter-pass resource dependency graph."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                passes = self._enumerate_pass_ranges(controller)
                resources = self._all_resource_refs(controller)
                writers = {}
                readers = {}

                for rid, rid_key, name in resources:
                    for usage in self._get_usage(controller, rid):
                        pass_index = self._find_pass_index(
                            passes, self._usage_event_id(usage))
                        if pass_index < 0:
                            continue
                        usage_name = self._usage_name(usage)
                        if self._is_write_usage(usage_name):
                            writers.setdefault(rid_key, {
                                "name": name,
                                "passes": set(),
                                "usages": set(),
                            })
                            writers[rid_key]["passes"].add(pass_index)
                            writers[rid_key]["usages"].add(usage_name)
                        if self._is_read_usage(usage_name):
                            readers.setdefault(rid_key, {
                                "name": name,
                                "passes": set(),
                                "usages": set(),
                            })
                            readers[rid_key]["passes"].add(pass_index)
                            readers[rid_key]["usages"].add(usage_name)

                edge_map = {}
                for rid_key, writer in writers.items():
                    reader = readers.get(rid_key)
                    if not reader:
                        continue
                    for src_index in writer["passes"]:
                        for dst_index in reader["passes"]:
                            if src_index >= dst_index:
                                continue
                            edge_key = (src_index, dst_index)
                            edge_map.setdefault(edge_key, []).append({
                                "resource_id": rid_key,
                                "name": writer["name"],
                                "write_usages": sorted(writer["usages"]),
                                "read_usages": sorted(reader["usages"]),
                            })

                edges = []
                for (src_index, dst_index), shared in sorted(edge_map.items()):
                    edges.append({
                        "src_pass": passes[src_index]["name"],
                        "src_event_id": passes[src_index]["begin_event_id"],
                        "dst_pass": passes[dst_index]["name"],
                        "dst_event_id": passes[dst_index]["begin_event_id"],
                        "shared_resources": shared,
                    })

                result["data"] = {
                    "pass_count": len(passes),
                    "edge_count": len(edges),
                    "edges": edges,
                }
            except Exception as e:
                import traceback
                result["error"] = "get_pass_deps error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def find_unused_targets(self):
        """Find resources written by passes but not consumed by visible output."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            try:
                passes = self._enumerate_pass_ranges(controller)
                resources = self._all_resource_refs(controller)

                write_targets = {}
                readers = {}
                live = set()

                resource_types = self._resource_type_lookup(controller)
                for rid, rid_key, name in resources:
                    if "swapchain" in resource_types.get(rid_key, "").lower():
                        live.add(rid_key)

                    for usage in self._get_usage(controller, rid):
                        pass_index = self._find_pass_index(
                            passes, self._usage_event_id(usage))
                        if pass_index < 0:
                            continue
                        usage_name = self._usage_name(usage)
                        if self._is_write_usage(usage_name):
                            target = write_targets.setdefault(rid_key, {
                                "name": name,
                                "written_by": set(),
                                "usages": set(),
                            })
                            target["written_by"].add(pass_index)
                            target["usages"].add(usage_name)
                        if self._is_read_usage(usage_name):
                            readers.setdefault(rid_key, set()).add(pass_index)

                # Some captures do not label the swapchain. Treat final pass
                # outputs as live so the last visible framebuffer is not flagged.
                if not live and passes:
                    last_index = len(passes) - 1
                    for rid_key, data in write_targets.items():
                        if last_index in data["written_by"]:
                            live.add(rid_key)

                changed = True
                while changed:
                    changed = False
                    for pass_index in range(len(passes) - 1, -1, -1):
                        writes_live = any(
                            pass_index in data["written_by"] and rid_key in live
                            for rid_key, data in write_targets.items())
                        if not writes_live:
                            continue
                        for rid_key, pass_set in readers.items():
                            if pass_index in pass_set and rid_key not in live:
                                live.add(rid_key)
                                changed = True

                unused_keys = {
                    rid_key for rid_key in write_targets
                    if rid_key not in live
                }
                wave_map = self._assign_unused_waves(
                    unused_keys, write_targets, readers)

                unused = []
                for rid_key in sorted(unused_keys):
                    data = write_targets[rid_key]
                    unused.append({
                        "resource_id": rid_key,
                        "name": data["name"],
                        "written_by": [
                            passes[i]["name"] for i in sorted(data["written_by"])
                        ],
                        "written_by_event_ids": [
                            passes[i]["begin_event_id"] for i in sorted(data["written_by"])
                        ],
                        "write_usages": sorted(data["usages"]),
                        "wave": wave_map.get(rid_key, 1),
                    })

                result["data"] = {
                    "total_targets": len(write_targets),
                    "unused_count": len(unused),
                    "unused": unused,
                }
            except Exception as e:
                import traceback
                result["error"] = "find_unused_targets error: %s\n%s" % (
                    str(e), traceback.format_exc())

        self._invoke(callback)
        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def _enumerate_pass_ranges(self, controller):
        structured_file = controller.GetStructuredFile()
        root_actions = controller.GetRootActions()

        marker_passes = []
        for action in root_actions:
            children = self._children(action)
            if not children or not self._has_draws_or_dispatches(children):
                continue
            flat_children = []
            self._flatten_events(children, flat_children)
            first_draw = 0
            for event in flat_children:
                if event["is_draw_or_dispatch"]:
                    first_draw = event["event_id"]
                    break
            marker_passes.append({
                "name": self._action_name(action, structured_file),
                "begin_event_id": int(getattr(action, "eventId", 0)),
                "end_event_id": self._last_event_id(action),
                "first_draw_event_id": first_draw,
                "synthetic": False,
            })

        all_events = []
        self._flatten_events(root_actions, all_events)
        all_events.sort(key=lambda item: item["event_id"])

        if not marker_passes:
            return self._build_synthetic_ranges(all_events)

        uncovered = []
        for event in all_events:
            covered = False
            for pr in marker_passes:
                if pr["begin_event_id"] <= event["event_id"] <= pr["end_event_id"]:
                    covered = True
                    break
            if not covered:
                uncovered.append(event)

        passes = marker_passes + self._build_synthetic_ranges(uncovered)
        passes.sort(key=lambda item: item["begin_event_id"])
        return passes

    def _build_synthetic_ranges(self, events):
        if not events:
            return []

        result = []
        current_key = events[0]["rt_key"]
        begin = events[0]["event_id"]
        end = events[0]["event_id"]
        has_draw = events[0]["is_draw_or_dispatch"]
        first_draw = events[0]["event_id"] if has_draw else 0

        def emit():
            if not has_draw:
                return
            result.append({
                "name": self._synthetic_pass_name(current_key),
                "begin_event_id": begin,
                "end_event_id": end,
                "first_draw_event_id": first_draw,
                "synthetic": True,
            })

        for event in events[1:]:
            boundary = event["is_boundary"]
            if event["rt_key"] != current_key or boundary:
                emit()
                current_key = event["rt_key"]
                begin = event["event_id"]
                has_draw = False
                first_draw = 0

            if event["is_draw_or_dispatch"]:
                has_draw = True
                if first_draw == 0:
                    first_draw = event["event_id"]
            end = event["event_id"]

        emit()
        return result

    def _flatten_events(self, actions, out):
        for action in actions:
            flags = getattr(action, "flags", 0)
            is_draw_or_dispatch = (
                self._has_flag(flags, "Drawcall")
                or self._has_flag(flags, "Dispatch")
            )
            is_relevant = (
                is_draw_or_dispatch
                or self._has_flag(flags, "Clear")
                or self._has_flag(flags, "Copy")
            )
            if is_relevant:
                out.append({
                    "event_id": int(getattr(action, "eventId", 0)),
                    "rt_key": self._rt_key(action),
                    "is_draw_or_dispatch": is_draw_or_dispatch,
                    "is_boundary": (
                        self._has_flag(flags, "Clear")
                        or self._has_flag(flags, "Copy")
                    ),
                })
            children = self._children(action)
            if children:
                self._flatten_events(children, out)

    def _collect_actions_in_range(self, actions, structured_file, begin, end, out):
        for action in actions:
            event_id = int(getattr(action, "eventId", 0))
            flags = getattr(action, "flags", 0)
            if begin <= event_id <= end and (
                self._has_flag(flags, "Drawcall")
                or self._has_flag(flags, "Dispatch")
                or self._has_flag(flags, "Clear")
                or self._has_flag(flags, "Copy")
            ):
                out.append(self._serialize_action(action, structured_file))
            children = self._children(action)
            if children:
                self._collect_actions_in_range(
                    children, structured_file, begin, end, out)

    def _serialize_action(self, action, structured_file):
        outputs = []
        try:
            for i, rid in enumerate(action.outputs):
                if not self._is_null_resource(rid):
                    outputs.append({
                        "index": i,
                        "resource_id": str(rid),
                    })
        except Exception:
            pass

        item = {
            "event_id": int(getattr(action, "eventId", 0)),
            "action_id": int(getattr(action, "actionId", 0)),
            "name": self._action_name(action, structured_file),
            "flags": Serializers.serialize_flags(getattr(action, "flags", 0)),
            "num_indices": int(getattr(action, "numIndices", 0)),
            "num_instances": int(getattr(action, "numInstances", 0)),
            "draw_index": int(getattr(action, "drawIndex", 0)),
        }
        if outputs:
            item["outputs"] = outputs
        try:
            depth = action.depthOut
            if not self._is_null_resource(depth):
                item["depth_output"] = str(depth)
        except Exception:
            pass
        return item

    def _count_actions_in_range(self, actions, begin, end):
        draws = 0
        dispatches = 0
        triangles = 0
        for action in actions:
            event_id = int(getattr(action, "eventId", 0))
            if begin <= event_id <= end:
                flags = getattr(action, "flags", 0)
                if self._has_flag(flags, "Drawcall"):
                    draws += 1
                    num_indices = int(getattr(action, "numIndices", 0))
                    instances = max(int(getattr(action, "numInstances", 1)), 1)
                    triangles += (num_indices * instances) // 3
                if self._has_flag(flags, "Dispatch"):
                    dispatches += 1
            children = self._children(action)
            if children:
                c_draws, c_dispatches, c_tris = self._count_actions_in_range(
                    children, begin, end)
                draws += c_draws
                dispatches += c_dispatches
                triangles += c_tris
        return draws, dispatches, triangles

    def _attachments_for_event(self, controller, event_id):
        controller.SetFrameEvent(int(event_id), True)
        pipe = controller.GetPipelineState()
        color_targets = []
        depth_target = None

        try:
            om = pipe.GetOutputMerger()
        except Exception:
            om = None

        if om is not None:
            for i, rt in enumerate(getattr(om, "renderTargets", []) or []):
                rid = getattr(rt, "resourceId", None)
                if self._is_null_resource(rid):
                    continue
                info = self._describe_resource(controller, rid)
                info["index"] = i
                color_targets.append(info)

            try:
                depth = om.depthTarget
                depth_rid = getattr(depth, "resourceId", None)
                if not self._is_null_resource(depth_rid):
                    depth_target = self._describe_resource(controller, depth_rid)
            except Exception:
                pass

        return {
            "sample_event_id": int(event_id),
            "color_targets": color_targets,
            "depth_target": depth_target,
        }

    def _describe_resource(self, controller, rid):
        data = {
            "resource_id": str(rid),
            "id": self._resource_id_int(rid),
            "name": "",
            "type": "resource",
        }
        try:
            name = self.ctx.GetResourceName(rid)
            if name:
                data["name"] = name
        except Exception:
            pass

        for tex in controller.GetTextures():
            if tex.resourceId == rid:
                data.update({
                    "type": "texture",
                    "width": getattr(tex, "width", 0),
                    "height": getattr(tex, "height", 0),
                    "depth": getattr(tex, "depth", 0),
                    "array_size": getattr(tex, "arraysize", 0),
                    "mip_levels": getattr(tex, "mips", 0),
                    "format": self._format_name(getattr(tex, "format", "")),
                    "dimension": str(getattr(tex, "type", "")),
                    "msaa_samples": getattr(tex, "msSamp", 0),
                })
                return data

        for buf in controller.GetBuffers():
            if buf.resourceId == rid:
                data.update({
                    "type": "buffer",
                    "length": getattr(buf, "length", 0),
                })
                return data

        return data

    def _all_resource_refs(self, controller):
        names = {}
        try:
            for res in controller.GetResources():
                names[str(res.resourceId)] = getattr(res, "name", "")
        except Exception:
            pass

        refs = []
        seen = set()
        for seq in (controller.GetTextures(), controller.GetBuffers()):
            for item in seq:
                rid = item.resourceId
                key = str(rid)
                if key in seen:
                    continue
                seen.add(key)
                refs.append((rid, key, names.get(key, "")))
        return refs

    def _resource_type_lookup(self, controller):
        lookup = {}
        try:
            for res in controller.GetResources():
                lookup[str(res.resourceId)] = str(getattr(res, "type", ""))
        except Exception:
            pass
        return lookup

    def _get_usage(self, controller, rid):
        try:
            return list(controller.GetUsage(rid))
        except Exception:
            return []

    def _find_pass(self, passes, event_id):
        for pr in passes:
            if pr["begin_event_id"] <= event_id <= pr["end_event_id"]:
                return pr
        return None

    def _find_pass_index(self, passes, event_id):
        for i, pr in enumerate(passes):
            if pr["begin_event_id"] <= event_id <= pr["end_event_id"]:
                return i
        return -1

    def _assign_unused_waves(self, unused_keys, write_targets, readers):
        remaining = set(unused_keys)
        waves = {}
        wave = 1
        while remaining:
            this_wave = set()
            for rid_key in list(remaining):
                has_remaining_consumer = False
                for pass_index in readers.get(rid_key, set()):
                    for other_key, data in write_targets.items():
                        if (
                            other_key != rid_key
                            and other_key in remaining
                            and pass_index in data["written_by"]
                        ):
                            has_remaining_consumer = True
                            break
                    if has_remaining_consumer:
                        break
                if not has_remaining_consumer:
                    this_wave.add(rid_key)

            if not this_wave:
                this_wave = set(remaining)

            for rid_key in this_wave:
                waves[rid_key] = wave
                remaining.discard(rid_key)
            wave += 1
        return waves

    def _has_draws_or_dispatches(self, actions):
        for action in actions:
            flags = getattr(action, "flags", 0)
            if self._has_flag(flags, "Drawcall") or self._has_flag(flags, "Dispatch"):
                return True
            children = self._children(action)
            if children and self._has_draws_or_dispatches(children):
                return True
        return False

    def _last_event_id(self, action):
        children = self._children(action)
        if children:
            return self._last_event_id(children[-1])
        return int(getattr(action, "eventId", 0))

    def _rt_key(self, action):
        colors = []
        try:
            for rid in action.outputs:
                if not self._is_null_resource(rid):
                    colors.append(str(rid))
        except Exception:
            pass

        depth = ""
        try:
            if not self._is_null_resource(action.depthOut):
                depth = str(action.depthOut)
        except Exception:
            pass
        return (tuple(colors), depth)

    def _synthetic_pass_name(self, rt_key):
        colors, depth = rt_key
        if not colors and not depth:
            return "No-RT"
        parts = ["RT%d" % i for i in range(len(colors))]
        if depth:
            parts.append("Depth")
        return "+".join(parts)

    def _is_write_usage(self, usage_name):
        return any(hint in usage_name for hint in self._WRITE_USAGE_HINTS)

    def _is_read_usage(self, usage_name):
        if "RWResource" in usage_name:
            return True
        return any(hint in usage_name for hint in self._READ_USAGE_HINTS)

    def _usage_event_id(self, usage):
        return int(getattr(usage, "eventId", getattr(usage, "eventID", 0)))

    def _usage_name(self, usage):
        return str(getattr(usage, "usage", ""))

    def _children(self, action):
        try:
            return list(action.children)
        except Exception:
            return []

    def _has_flag(self, flags, name):
        try:
            return bool(flags & getattr(rd.ActionFlags, name))
        except Exception:
            return False

    def _is_null_resource(self, rid):
        if rid is None:
            return True
        try:
            if rid == rd.ResourceId.Null():
                return True
        except Exception:
            pass
        try:
            return self._resource_id_int(rid) == 0
        except Exception:
            return False

    def _resource_id_int(self, resource_id):
        try:
            return int(resource_id)
        except Exception:
            try:
                return Parsers.extract_numeric_id(str(resource_id))
            except Exception:
                return 0

    def _format_name(self, fmt):
        try:
            return str(fmt.Name())
        except Exception:
            return str(fmt)

    def _action_name(self, action, structured_file):
        try:
            return action.GetName(structured_file)
        except Exception:
            return getattr(action, "customName", "")

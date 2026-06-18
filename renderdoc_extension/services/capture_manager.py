"""
Capture management service for RenderDoc.
"""

import datetime
import os
import shutil
import sys
import tempfile
import time
import uuid

import renderdoc as rd


class CaptureManager:
    """Capture management service"""

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn
        self._target_sessions = {}

    def get_capture_status(self):
        """Check if a capture is loaded and get API info"""
        if not self.ctx.IsCaptureLoaded():
            return {"loaded": False}

        result = {"loaded": True, "api": None, "filename": None}

        try:
            result["filename"] = self.ctx.GetCaptureFilename()
        except Exception:
            pass

        # Get API type via replay
        def callback(controller):
            try:
                props = controller.GetAPIProperties()
                result["api"] = str(props.pipelineType)
            except Exception:
                pass

        self._invoke(callback)
        return result

    def list_captures(self, directory):
        """
        List all .rdc files in the specified directory.

        Args:
            directory: Directory path to search

        Returns:
            dict with 'captures' list containing file info
        """
        import os
        import datetime

        # Validate directory exists
        if not os.path.isdir(directory):
            raise ValueError("Directory not found: %s" % directory)

        captures = []

        try:
            for filename in os.listdir(directory):
                if filename.lower().endswith(".rdc"):
                    filepath = os.path.join(directory, filename)
                    if os.path.isfile(filepath):
                        stat = os.stat(filepath)
                        # Format timestamp as ISO 8601
                        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
                        captures.append({
                            "filename": filename,
                            "path": filepath,
                            "size_bytes": stat.st_size,
                            "modified_time": mtime.isoformat(),
                        })
        except Exception as e:
            raise ValueError("Failed to list directory: %s" % str(e))

        # Sort by modified time (newest first)
        captures.sort(key=lambda x: x["modified_time"], reverse=True)

        return {
            "directory": directory,
            "count": len(captures),
            "captures": captures,
        }

    def open_capture(self, capture_path):
        """
        Open a capture file in RenderDoc.

        Args:
            capture_path: Full path to the .rdc file

        Returns:
            dict with success status and capture info
        """
        import os

        # Validate file exists
        if not os.path.isfile(capture_path):
            raise ValueError("Capture file not found: %s" % capture_path)

        # Validate extension
        if not capture_path.lower().endswith(".rdc"):
            raise ValueError("Invalid file type. Expected .rdc file: %s" % capture_path)

        # Create ReplayOptions with defaults
        opts = rd.ReplayOptions()

        # Open the capture
        # LoadCapture will automatically close any existing capture
        try:
            self.ctx.LoadCapture(
                capture_path,   # captureFile
                opts,           # ReplayOptions
                capture_path,   # origFilename (same as capture path)
                False,          # temporary (False = permanent load)
                True,           # local (True = local file)
            )
        except Exception as e:
            raise ValueError("Failed to open capture: %s" % str(e))

        # Verify the capture was loaded
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("Failed to load capture (unknown error)")

        # Get capture info
        result = {
            "success": True,
            "capture_path": capture_path,
            "filename": os.path.basename(capture_path),
        }

        # Get API type if possible (may require replay thread)
        try:
            api_result = {"api": None}

            def callback(controller):
                try:
                    props = controller.GetAPIProperties()
                    api_result["api"] = str(props.pipelineType)
                except Exception:
                    pass

            self._invoke(callback)
            if api_result["api"]:
                result["api"] = api_result["api"]
        except Exception:
            pass

        return result

    def capture_frame(self, exe_path, working_dir="", cmd_line="",
                      delay_frames=100, output_path="", timeout_seconds=60):
        """
        Launch an application through RenderDoc, capture one frame, then open it.

        This uses the RenderDoc Python API from the already-running qrenderdoc
        process, so the MCP bridge extension must be loaded before this call.
        """
        output_path = output_path or self._default_capture_path(exe_path)
        session_id = ""
        pid = 0
        try:
            launched = self.launch_application(
                exe_path,
                working_dir,
                cmd_line,
                "auto",
                timeout_seconds,
                output_path,
            )
            session_id = launched.get("session_id", "")
            pid = int(launched.get("pid", 0) or 0)
            target = self._target_sessions[session_id]["target"]

            wait_ms = max(int(delay_frames) * 16, 2000)
            wait_until = time.time() + (wait_ms / 1000.0)
            while time.time() < wait_until:
                if self._target_disconnected(target):
                    raise ValueError("Target process disconnected before capture")
                msg = self._receive_message(target)
                if "Disconnected" in self._message_type_name(msg):
                    raise ValueError("Target process disconnected before capture")
                time.sleep(0.05)

            capture_result = self.trigger_capture(
                session_id, output_path, timeout_seconds)
            found_capture = capture_result.get("capture_path", "")
        finally:
            if session_id:
                self.close_target(session_id)

        if not found_capture or not os.path.isfile(found_capture):
            raise ValueError("Capture completed but no .rdc file was found")

        info = self.open_capture(output_path)
        info.update({
            "capture_path": output_path,
            "path": output_path,
            "pid": pid,
            "method": "capture_frame",
        })
        return info

    def launch_application(self, exe_path, working_dir="", cmd_line="",
                           graphics_api="auto", timeout_seconds=60,
                           output_path="", target_process_name="", connect_target=True):
        """
        Launch an application through RenderDoc and keep its TargetControl open.

        Returns a session ID that can be used by get_target_status,
        trigger_capture, and close_target.
        """
        exe_path, working_dir = self._validate_launch_paths(exe_path, working_dir)
        graphics_api = self._normalize_graphics_api(graphics_api)
        execute_and_inject, create_target_control = self._get_capture_entrypoints(
            "launch_application")

        output_path = output_path or self._default_capture_path(exe_path)
        output_dir = os.path.dirname(os.path.abspath(output_path))
        if output_dir and not os.path.isdir(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        opts = self._make_capture_options()
        env_mods = self._make_capture_env_mods(graphics_api)
        capture_template = (
            output_path[:-4] if output_path.lower().endswith(".rdc") else output_path)

        started_at = time.time()
        try:
            exec_result = execute_and_inject(
                exe_path,
                working_dir,
                cmd_line or "",
                env_mods,
                capture_template,
                opts,
                False,
            )
        except Exception as e:
            raise ValueError("Failed to launch and inject: %s" % str(e))

        if not self._execute_result_ok(exec_result):
            raise ValueError(
                "Failed to launch and inject: %s"
                % self._execute_result_message(exec_result))

        ident = int(getattr(exec_result, "ident", 0))
        if not self._as_bool(connect_target):
            return {
                "session_id": "",
                "pid": 0,
                "ident": ident,
                "exe_path": exe_path,
                "working_dir": working_dir,
                "cmd_line": cmd_line or "",
                "graphics_api": graphics_api,
                "status": "launched",
                "controllable": False,
                "connected": False,
            }

        target = self._connect_target(
            create_target_control, ident, timeout_seconds,
            target_process_name, graphics_api)
        if target is None:
            raise ValueError("Failed to connect to injected target process")

        pid = self._get_target_pid(target)
        target_name = self._get_target_name(target)
        target_api = self._get_target_api(target)
        session_id = uuid.uuid4().hex
        self._target_sessions[session_id] = {
            "session_id": session_id,
            "target": target,
            "pid": pid,
            "ident": ident,
            "target_name": target_name,
            "target_api": target_api,
            "exe_path": exe_path,
            "working_dir": working_dir,
            "cmd_line": cmd_line or "",
            "graphics_api": graphics_api,
            "capture_template": capture_template,
            "started_at": started_at,
            "last_capture_path": "",
        }

        return {
            "session_id": session_id,
            "pid": pid,
            "ident": ident,
            "target_name": target_name,
            "target_api": target_api,
            "exe_path": exe_path,
            "working_dir": working_dir,
            "cmd_line": cmd_line or "",
            "graphics_api": graphics_api,
            "status": "running",
            "controllable": not self._target_disconnected(target),
        }

    def connect_running_target(self, target_process_name="", graphics_api="auto",
                               timeout_seconds=60):
        """Connect TargetControl to an already-running RenderDoc target."""
        create_target_control = self._get_target_control_entrypoint(
            "connect_running_target")
        expected_name = (target_process_name or "").strip()
        expected_api = self._normalize_graphics_api(graphics_api)
        deadline = time.time() + max(float(timeout_seconds), 5.0)
        last_targets = []

        while time.time() < deadline:
            last_targets = []
            for ident in self._candidate_target_idents(0, expected_name):
                target = self._connect_target_candidate(create_target_control, ident)
                if target is None:
                    continue

                info = self._describe_target(target, ident)
                last_targets.append(info)
                if expected_name and not self._target_name_matches(
                        info.get("target_name", ""), expected_name):
                    self._shutdown_target(target)
                    continue
                if not self._target_api_matches(info.get("target_api", ""), expected_api):
                    self._shutdown_target(target)
                    continue

                session_id = uuid.uuid4().hex
                self._target_sessions[session_id] = {
                    "session_id": session_id,
                    "target": target,
                    "pid": info.get("pid", 0),
                    "ident": ident,
                    "target_name": info.get("target_name", ""),
                    "target_api": info.get("target_api", ""),
                    "exe_path": info.get("target_name", "") or expected_name,
                    "working_dir": "",
                    "cmd_line": "",
                    "graphics_api": expected_api,
                    "started_at": time.time(),
                    "last_capture_path": "",
                }
                info.update({
                    "session_id": session_id,
                    "status": "running",
                    "controllable": not self._target_disconnected(target),
                    "connected": not self._target_disconnected(target),
                })
                return info

            time.sleep(1.0)

        raise ValueError(
            "No running RenderDoc target matched %s with API %s. Visible targets: %s"
            % (expected_name or "<any>", expected_api, self._format_targets(last_targets)))

    def list_running_targets(self):
        """List active RenderDoc targets visible from localhost."""
        create_target_control = self._get_target_control_entrypoint(
            "list_running_targets")
        targets = []
        for ident in self._candidate_target_idents(0, ""):
            target = self._connect_target_candidate(create_target_control, ident)
            if target is None:
                continue
            targets.append(self._describe_target(target, ident))
            self._shutdown_target(target)
        return {"targets": targets, "count": len(targets)}

    def _get_target_control_entrypoint(self, tool_name):
        create_target_control = (
            getattr(rd, "CreateTargetControl", None)
            or getattr(rd, "RENDERDOC_CreateTargetControl", None)
        )
        if create_target_control is None:
            raise ValueError(
                "This RenderDoc Python build does not expose CreateTargetControl; "
                "%s is unavailable" % tool_name)
        return create_target_control

    def _describe_target(self, target, ident):
        return {
            "ident": int(ident),
            "pid": self._get_target_pid(target),
            "target_name": self._get_target_name(target),
            "target_api": self._get_target_api(target),
            "connected": not self._target_disconnected(target),
        }

    def _format_targets(self, targets):
        if not targets:
            return "none"
        parts = []
        for target in targets:
            parts.append(
                "ident=%s pid=%s name=%s api=%s connected=%s"
                % (
                    target.get("ident", ""),
                    target.get("pid", ""),
                    target.get("target_name", ""),
                    target.get("target_api", ""),
                    target.get("connected", ""),
                )
            )
        return "; ".join(parts)

    def _as_bool(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in ("0", "false", "no", "off")
        return bool(value)

    def get_target_status(self, session_id):
        """Return whether a launched TargetControl session is still usable."""
        session = self._target_sessions.get(session_id)
        if session is None:
            return {
                "session_id": session_id,
                "exists": False,
                "controllable": False,
                "status": "not_found",
            }

        target = session["target"]
        controllable = not self._target_disconnected(target)
        return {
            "session_id": session_id,
            "exists": True,
            "controllable": controllable,
            "connected": controllable,
            "status": "running" if controllable else "disconnected",
            "pid": session.get("pid", 0),
            "ident": session.get("ident", 0),
            "target_name": self._get_target_name(target) or session.get("target_name", ""),
            "target_api": self._get_target_api(target) or session.get("target_api", ""),
            "exe_path": session.get("exe_path", ""),
            "working_dir": session.get("working_dir", ""),
            "cmd_line": session.get("cmd_line", ""),
            "graphics_api": session.get("graphics_api", "auto"),
            "last_capture_path": session.get("last_capture_path", ""),
            "uptime_seconds": max(0.0, time.time() - session.get("started_at", time.time())),
        }

    def trigger_capture(self, session_id, output_path="", timeout_seconds=60):
        """Trigger a capture on a previously launched target and save it."""
        session = self._target_sessions.get(session_id)
        if session is None:
            raise ValueError("Unknown target session: %s" % session_id)

        target = session["target"]
        if self._target_disconnected(target):
            raise ValueError("Target session is disconnected: %s" % session_id)

        exe_path = session.get("exe_path", "")
        output_path = output_path or self._default_capture_path(exe_path)
        output_dir = os.path.dirname(os.path.abspath(output_path))
        if output_dir and not os.path.isdir(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        capture_started_at = time.time()
        try:
            target.TriggerCapture(1)
        except Exception:
            try:
                target.QueueCapture(0, 1)
            except Exception as e:
                raise ValueError("Failed to trigger capture: %s" % str(e))

        found_capture = self._wait_for_capture_file(
            target,
            exe_path,
            session.get("capture_template", output_path),
            output_path,
            timeout_seconds,
            capture_started_at,
        )
        if not found_capture or not os.path.isfile(found_capture):
            raise ValueError("Capture completed but no .rdc file was found")

        if os.path.abspath(found_capture) != os.path.abspath(output_path):
            shutil.copyfile(found_capture, output_path)

        session["last_capture_path"] = output_path
        return {
            "success": True,
            "session_id": session_id,
            "pid": session.get("pid", 0),
            "capture_path": output_path,
            "path": output_path,
            "source_capture_path": found_capture,
            "status": "captured",
        }

    def close_target(self, session_id):
        """Close and forget a TargetControl session."""
        session = self._target_sessions.pop(session_id, None)
        if session is None:
            return {
                "success": False,
                "session_id": session_id,
                "exists": False,
                "status": "not_found",
            }

        target = session.get("target")
        shutdown_error = ""
        try:
            target.Shutdown()
        except Exception as e:
            shutdown_error = str(e)

        result = {
            "success": shutdown_error == "",
            "session_id": session_id,
            "exists": True,
            "pid": session.get("pid", 0),
            "status": "closed" if shutdown_error == "" else "close_error",
        }
        if shutdown_error:
            result["error"] = shutdown_error
        return result

    def _validate_launch_paths(self, exe_path, working_dir=""):
        if not exe_path:
            raise ValueError("exe_path is required")
        if not os.path.isfile(exe_path):
            raise ValueError("Target executable not found: %s" % exe_path)

        working_dir = working_dir or os.path.dirname(os.path.abspath(exe_path))
        if not os.path.isdir(working_dir):
            raise ValueError("Working directory not found: %s" % working_dir)
        return exe_path, working_dir

    def _get_capture_entrypoints(self, tool_name):
        execute_and_inject = (
            getattr(rd, "ExecuteAndInject", None)
            or getattr(rd, "RENDERDOC_ExecuteAndInject", None)
        )
        create_target_control = (
            getattr(rd, "CreateTargetControl", None)
            or getattr(rd, "RENDERDOC_CreateTargetControl", None)
        )
        if execute_and_inject is None or create_target_control is None:
            raise ValueError(
                "This RenderDoc Python build does not expose ExecuteAndInject/"
                "CreateTargetControl; %s is unavailable" % tool_name)
        return execute_and_inject, create_target_control

    def _normalize_graphics_api(self, graphics_api):
        value = (graphics_api or "auto").strip().lower()
        aliases = {
            "": "auto",
            "default": "auto",
            "dx11": "d3d11",
            "direct3d11": "d3d11",
            "directx11": "d3d11",
            "dx12": "d3d12",
            "direct3d12": "d3d12",
            "directx12": "d3d12",
            "gl": "opengl",
            "gles2": "gles",
            "gles3": "gles",
            "opengles": "gles",
        }
        value = aliases.get(value, value)
        allowed = ("auto", "vulkan", "d3d11", "d3d12", "opengl", "gles")
        if value not in allowed:
            raise ValueError(
                "Unsupported graphics_api '%s'. Expected one of: %s"
                % (graphics_api, ", ".join(allowed)))
        return value

    def _get_target_pid(self, target):
        try:
            return int(target.GetPID())
        except Exception:
            return 0

    def _default_capture_path(self, exe_path):
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        exe_name = os.path.splitext(os.path.basename(exe_path))[0]
        directory = os.path.join(tempfile.gettempdir(), "renderdoc_mcp_captures")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, "%s_%s.rdc" % (exe_name, stamp))

    def _make_capture_options(self):
        try:
            opts = rd.CaptureOptions()
        except Exception:
            return None

        defaults = {
            "allowVSync": True,
            "allowFullscreen": True,
            "apiValidation": False,
            "captureCallstacks": False,
            "captureCallstacksOnlyActions": False,
            "delayForDebugger": 0,
            "verifyBufferAccess": False,
            "hookIntoChildren": True,
            "refAllResources": True,
            "captureAllCmdLists": False,
            "debugOutputMute": True,
            "softMemoryLimit": 0,
        }
        for name, value in defaults.items():
            try:
                setattr(opts, name, value)
            except Exception:
                pass
        return opts

    def _make_capture_env_mods(self, graphics_api="auto"):
        mods = []

        def append_env(name, value):
            try:
                mod = rd.EnvironmentModification()
                mod.mod = rd.EnvMod.Set
                mod.sep = rd.EnvSep.NoSep
                mod.name = name
                mod.value = value
                mods.append(mod)
            except Exception:
                pass

        if graphics_api in ("auto", "vulkan"):
            append_env("ENABLE_VULKAN_RENDERDOC_CAPTURE", "1")

        runtime_dir = self._renderdoc_runtime_dir()
        if runtime_dir and graphics_api in ("auto", "vulkan"):
            append_env("VK_IMPLICIT_LAYER_PATH", runtime_dir)

        return mods

    def _renderdoc_runtime_dir(self):
        candidates = []
        module_path = getattr(rd, "__file__", "")
        if module_path:
            candidates.append(os.path.dirname(os.path.abspath(module_path)))
        try:
            candidates.append(os.path.dirname(os.path.abspath(sys.executable)))
        except Exception:
            pass

        for candidate in candidates:
            if candidate and os.path.isdir(candidate):
                return candidate
        return ""

    def _execute_result_ok(self, exec_result):
        try:
            result = getattr(exec_result, "result", None)
            code = getattr(result, "code", None)
            if code is not None:
                return code == rd.ResultCode.Succeeded
        except Exception:
            pass
        try:
            status = getattr(exec_result, "status", None)
            if status is not None:
                return "Succeeded" in str(status)
        except Exception:
            pass
        return int(getattr(exec_result, "ident", 0)) > 0

    def _execute_result_message(self, exec_result):
        try:
            result = getattr(exec_result, "result", None)
            message = result.Message()
            return str(message)
        except Exception:
            return str(exec_result)

    def _connect_target(self, create_target_control, ident, timeout_seconds,
                        target_process_name="", graphics_api="auto"):
        expected_name = (target_process_name or "").strip()
        expected_api = self._normalize_graphics_api(graphics_api)
        fallback_target = None
        fallback_ident = 0
        deadline = time.time() + max(float(timeout_seconds), 5.0)

        while time.time() < deadline:
            candidate_idents = self._candidate_target_idents(ident, expected_name)
            for candidate in candidate_idents:
                if candidate <= 0:
                    continue
                if fallback_target is not None and candidate == fallback_ident:
                    continue

                target = self._connect_target_candidate(create_target_control, candidate)
                if target is None:
                    continue

                target_name = self._get_target_name(target)
                target_api = self._get_target_api(target)
                if expected_name and not self._target_name_matches(target_name, expected_name):
                    self._shutdown_target(target)
                    continue

                if not expected_name:
                    return target

                if self._target_api_matches(target_api, expected_api):
                    if fallback_target is not None:
                        self._shutdown_target(fallback_target)
                    return target

                if fallback_target is None:
                    fallback_target = target
                    fallback_ident = candidate
                else:
                    self._shutdown_target(target)

            if fallback_target is not None:
                if self._target_disconnected(fallback_target):
                    fallback_target = None
                    fallback_ident = 0
                elif self._target_api_matches(
                        self._get_target_api(fallback_target), expected_api):
                    return fallback_target
                else:
                    self._receive_message(fallback_target)

            time.sleep(1.0)

        return fallback_target

    def _candidate_target_idents(self, ident, expected_name=""):
        fallback = []
        for candidate in (ident + 1, ident + 2, ident, ident - 1):
            if candidate > 0 and candidate not in fallback:
                fallback.append(candidate)
        for offset in range(3, 11):
            for candidate in (ident + offset, ident - offset):
                if candidate > 0 and candidate not in fallback:
                    fallback.append(candidate)

        enumerated = self._enumerate_target_idents()
        candidates = enumerated + fallback if expected_name else fallback + enumerated
        result = []
        for candidate in candidates:
            if candidate > 0 and candidate not in result:
                result.append(candidate)
        return result

    def _enumerate_target_idents(self):
        enumerate_targets = (
            getattr(rd, "EnumerateRemoteTargets", None)
            or getattr(rd, "RENDERDOC_EnumerateRemoteTargets", None)
        )
        if enumerate_targets is None:
            return []

        idents = []
        next_ident = 0
        for _ in range(128):
            try:
                ident = int(enumerate_targets("", next_ident))
            except Exception:
                break
            if ident <= 0 or ident in idents:
                break
            idents.append(ident)
            next_ident = ident
        return idents

    def _connect_target_candidate(self, create_target_control, ident):
        try:
            return create_target_control("", int(ident), "renderdoc-mcp", True)
        except Exception:
            return None

    def _target_name_matches(self, target_name, expected_name):
        target = (target_name or "").lower()
        expected = (expected_name or "").lower()
        if not expected:
            return True
        names = [expected]
        if expected.endswith(".exe"):
            names.append(expected[:-4])
        else:
            names.append(expected + ".exe")
        return any(name and name in target for name in names)

    def _target_api_matches(self, target_api, expected_api):
        api = (target_api or "").lower()
        expected = (expected_api or "auto").lower()
        if not api:
            return False
        if expected in ("", "auto"):
            return True
        return expected in api

    def _get_target_name(self, target):
        try:
            return str(target.GetTarget())
        except Exception:
            return ""

    def _get_target_api(self, target):
        try:
            return str(target.GetAPI())
        except Exception:
            return ""

    def _shutdown_target(self, target):
        try:
            target.Shutdown()
        except Exception:
            pass

    def _wait_for_capture_file(self, target, exe_path, capture_template,
                               output_path, timeout_seconds, min_mtime=0):
        deadline = time.time() + max(float(timeout_seconds), 5.0)
        capture_path = ""
        capture_id = None

        while time.time() < deadline:
            if self._target_disconnected(target):
                break
            msg = self._receive_message(target)
            msg_type = self._message_type_name(msg)
            if "NewCapture" in msg_type:
                new_capture = getattr(msg, "newCapture", None)
                try:
                    capture_path = str(new_capture.path)
                except Exception:
                    capture_path = ""
                try:
                    capture_id = new_capture.ID
                except Exception:
                    try:
                        capture_id = new_capture.id
                    except Exception:
                        capture_id = None
                if capture_path and os.path.isfile(capture_path):
                    return capture_path
                if capture_id is not None:
                    copied = self._copy_capture(target, capture_id, output_path)
                    if copied:
                        return copied
            if "Disconnected" in msg_type:
                break
            scanned = self._find_newest_capture(
                exe_path, capture_template, min_mtime)
            if scanned:
                return scanned
            time.sleep(0.1)

        return self._find_newest_capture(exe_path, capture_template, min_mtime)

    def _copy_capture(self, target, capture_id, output_path):
        try:
            target.CopyCapture(capture_id, output_path)
        except Exception:
            return ""

        deadline = time.time() + 30.0
        while time.time() < deadline:
            if os.path.isfile(output_path):
                return output_path
            self._receive_message(target)
            time.sleep(0.1)
        return output_path if os.path.isfile(output_path) else ""

    def _find_newest_capture(self, exe_path, capture_template, min_mtime=0):
        exe_name = os.path.splitext(os.path.basename(exe_path))[0].lower()
        search_dirs = [
            os.path.dirname(os.path.abspath(capture_template)),
            os.path.join(tempfile.gettempdir(), "RenderDoc"),
            os.path.join(tempfile.gettempdir(), "renderdoc_mcp_captures"),
        ]

        newest = ""
        newest_time = 0
        for directory in search_dirs:
            if not directory or not os.path.isdir(directory):
                continue
            try:
                for filename in os.listdir(directory):
                    if not filename.lower().endswith(".rdc"):
                        continue
                    lower_name = filename.lower()
                    if exe_name not in lower_name:
                        continue
                    path = os.path.join(directory, filename)
                    mtime = os.path.getmtime(path)
                    if min_mtime and mtime < float(min_mtime) - 1.0:
                        continue
                    if mtime > newest_time:
                        newest = path
                        newest_time = mtime
            except Exception:
                pass
        return newest

    def _receive_message(self, target):
        try:
            return target.ReceiveMessage(None)
        except TypeError:
            try:
                return target.ReceiveMessage()
            except Exception:
                return None
        except Exception:
            return None

    def _target_disconnected(self, target):
        try:
            return not bool(target.Connected())
        except Exception:
            return False

    def _message_type_name(self, msg):
        if msg is None:
            return ""
        try:
            return str(msg.type)
        except Exception:
            return ""

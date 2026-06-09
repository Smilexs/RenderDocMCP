import os
import sys
import tempfile
import types
import unittest


class FakeResult:
    def __init__(self, code):
        self.code = code

    def Message(self):
        return "ok"


class FakeExecuteResult:
    def __init__(self, ident, code):
        self.ident = ident
        self.result = FakeResult(code)


class FakeCaptureOptions:
    pass


class FakeEnvironmentModification:
    pass


class FakeNewCapture:
    def __init__(self, path, capture_id=7):
        self.path = path
        self.ID = capture_id


class FakeTargetMessage:
    def __init__(self, capture_path):
        self.type = "NewCapture"
        self.newCapture = FakeNewCapture(capture_path)


class FakeTarget:
    def __init__(self, pid=1234, capture_messages=None):
        self.pid = pid
        self.connected = True
        self.shutdown_called = False
        self.trigger_count = 0
        self.capture_messages = list(capture_messages or [])

    def GetPID(self):
        return self.pid

    def Connected(self):
        return self.connected

    def TriggerCapture(self, count):
        self.trigger_count += int(count)

    def QueueCapture(self, delay, count):
        self.trigger_count += int(count)

    def ReceiveMessage(self, _arg=None):
        if self.capture_messages:
            return self.capture_messages.pop(0)
        return None

    def Shutdown(self):
        self.shutdown_called = True
        self.connected = False


class FakeRenderDoc(types.SimpleNamespace):
    def __init__(self, target):
        super().__init__()
        self.ResultCode = types.SimpleNamespace(Succeeded="Succeeded")
        self.CaptureOptions = FakeCaptureOptions
        self.EnvironmentModification = FakeEnvironmentModification
        self.EnvMod = types.SimpleNamespace(Set="Set")
        self.EnvSep = types.SimpleNamespace(NoSep="NoSep")
        self._target = target
        self.execute_calls = []
        self.target_control_calls = []

    def ExecuteAndInject(self, exe_path, working_dir, cmd_line, env_mods,
                         capture_template, opts, wait_for_exit):
        self.execute_calls.append({
            "exe_path": exe_path,
            "working_dir": working_dir,
            "cmd_line": cmd_line,
            "env_mods": env_mods,
            "capture_template": capture_template,
            "opts": opts,
            "wait_for_exit": wait_for_exit,
        })
        return FakeExecuteResult(100, self.ResultCode.Succeeded)

    def CreateTargetControl(self, host, ident, client_name, force_connection):
        self.target_control_calls.append((host, ident, client_name, force_connection))
        if ident == 101:
            return self._target
        return None


def import_capture_manager_with_fake_rd(fake_rd):
    sys.modules["renderdoc"] = fake_rd
    sys.modules.pop("renderdoc_extension.services.capture_manager", None)
    module = __import__(
        "renderdoc_extension.services.capture_manager",
        fromlist=["CaptureManager"],
    )
    module.rd = fake_rd
    return module.CaptureManager


class CaptureManagerTargetSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.exe_path = os.path.join(self.temp.name, "MuMuPlayer.exe")
        with open(self.exe_path, "wb") as f:
            f.write(b"fake exe")

    def make_manager(self, target):
        fake_rd = FakeRenderDoc(target)
        CaptureManager = import_capture_manager_with_fake_rd(fake_rd)
        return CaptureManager(ctx=None, invoke_fn=lambda callback: None), fake_rd

    def test_launch_application_returns_session_and_pid(self):
        target = FakeTarget(pid=4321)
        manager, fake_rd = self.make_manager(target)

        result = manager.launch_application(
            self.exe_path,
            self.temp.name,
            "--start",
            "vulkan",
        )

        self.assertTrue(result["session_id"])
        self.assertEqual(4321, result["pid"])
        self.assertEqual("running", result["status"])
        self.assertEqual("vulkan", result["graphics_api"])
        self.assertEqual(self.exe_path, result["exe_path"])
        self.assertFalse(target.shutdown_called)
        self.assertEqual("--start", fake_rd.execute_calls[0]["cmd_line"])

    def test_get_target_status_reflects_controllability(self):
        target = FakeTarget(pid=4321)
        manager, _fake_rd = self.make_manager(target)
        session_id = manager.launch_application(self.exe_path)["session_id"]

        status = manager.get_target_status(session_id)
        self.assertTrue(status["exists"])
        self.assertTrue(status["controllable"])
        self.assertEqual(4321, status["pid"])

        target.connected = False
        disconnected = manager.get_target_status(session_id)
        self.assertTrue(disconnected["exists"])
        self.assertFalse(disconnected["controllable"])

    def test_trigger_capture_saves_output_without_closing_target(self):
        source_capture = os.path.join(self.temp.name, "source.rdc")
        output_capture = os.path.join(self.temp.name, "captures", "saved.rdc")
        with open(source_capture, "wb") as f:
            f.write(b"rdc data")
        target = FakeTarget(
            pid=4321,
            capture_messages=[FakeTargetMessage(source_capture)],
        )
        manager, _fake_rd = self.make_manager(target)
        session_id = manager.launch_application(self.exe_path)["session_id"]

        result = manager.trigger_capture(session_id, output_capture, timeout_seconds=5)

        self.assertTrue(result["success"])
        self.assertEqual(session_id, result["session_id"])
        self.assertEqual(output_capture, result["capture_path"])
        self.assertTrue(os.path.isfile(output_capture))
        self.assertEqual(1, target.trigger_count)
        self.assertTrue(target.Connected())

    def test_close_target_shutdowns_and_removes_session(self):
        target = FakeTarget(pid=4321)
        manager, _fake_rd = self.make_manager(target)
        session_id = manager.launch_application(self.exe_path)["session_id"]

        result = manager.close_target(session_id)

        self.assertTrue(result["success"])
        self.assertTrue(target.shutdown_called)
        self.assertFalse(manager.get_target_status(session_id)["exists"])


if __name__ == "__main__":
    unittest.main()

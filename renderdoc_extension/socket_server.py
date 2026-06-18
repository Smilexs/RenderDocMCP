"""
File-based IPC Server for RenderDoc MCP Bridge
Uses file polling with a background thread – no PySide2 / Qt dependency required.
"""

import json
import os
import traceback
import tempfile
import threading
import time


# IPC directory
IPC_DIR = os.path.join(tempfile.gettempdir(), "renderdoc_mcp")
REQUEST_FILE = os.path.join(IPC_DIR, "request.json")
RESPONSE_FILE = os.path.join(IPC_DIR, "response.json")
LOCK_FILE = os.path.join(IPC_DIR, "lock")


try:
    from PySide2 import QtCore, QtWidgets
except Exception:
    QtCore = None
    QtWidgets = None


if QtCore is not None:
    class QtMainThreadDispatcher(QtCore.QObject):
        run_signal = QtCore.Signal(object)

        def __init__(self):
            super(QtMainThreadDispatcher, self).__init__()
            self.run_signal.connect(self._run, QtCore.Qt.QueuedConnection)

        def call(self, fn, *args, **kwargs):
            app = QtWidgets.QApplication.instance() if QtWidgets is not None else None
            if app is not None and QtCore.QThread.currentThread() == app.thread():
                return fn(*args, **kwargs)

            request = {
                "fn": fn,
                "args": args,
                "kwargs": kwargs,
                "event": threading.Event(),
                "result": None,
                "error": None,
            }
            self.run_signal.emit(request)
            if not request["event"].wait(300.0):
                raise TimeoutError("Timed out waiting for RenderDoc UI thread")
            if request["error"] is not None:
                raise request["error"]
            return request["result"]

        @QtCore.Slot(object)
        def _run(self, request):
            try:
                request["result"] = request["fn"](*request["args"], **request["kwargs"])
            except Exception as e:
                request["error"] = e
            finally:
                request["event"].set()
else:
    class QtMainThreadDispatcher(object):
        def call(self, fn, *args, **kwargs):
            return fn(*args, **kwargs)


class MCPBridgeServer(object):
    """File-based IPC server for MCP bridge communication"""

    def __init__(self, host, port, handler, dispatcher=None):
        self.handler = handler
        self.dispatcher = dispatcher
        self._thread = None
        self._running = False

        # Create IPC directory
        if not os.path.exists(IPC_DIR):
            os.makedirs(IPC_DIR)

    def start(self):
        """Start the server with polling"""
        self._running = True

        # Clean up old files
        self._cleanup_files()

        # Start polling in a daemon thread (check every 100ms)
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

        print("[MCP Bridge] File-based IPC server started")
        print("[MCP Bridge] IPC directory: %s" % IPC_DIR)
        return True

    def stop(self):
        """Stop the server"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        self._cleanup_files()
        print("[MCP Bridge] Server stopped")

    def _poll_loop(self):
        """Background loop that polls for requests every 100ms"""
        while self._running:
            self._poll_request()
            time.sleep(0.1)

    def is_running(self):
        """Check if server is running"""
        return self._running

    def _cleanup_files(self):
        """Remove IPC files"""
        for f in [REQUEST_FILE, RESPONSE_FILE, LOCK_FILE]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass

    def _poll_request(self):
        """Check for incoming request"""
        if not self._running:
            return

        # Check if request file exists
        if not os.path.exists(REQUEST_FILE):
            return

        # Check if lock file exists (client is still writing)
        if os.path.exists(LOCK_FILE):
            return

        try:
            # Read request
            with open(REQUEST_FILE, "r", encoding="utf-8") as f:
                request = json.load(f)

            # Remove request file
            os.remove(REQUEST_FILE)

            # Process request
            try:
                if self.dispatcher is not None:
                    response = self.dispatcher.call(self.handler.handle, request)
                else:
                    response = self.handler.handle(request)
            except Exception as e:
                traceback.print_exc()
                response = {
                    "id": request.get("id"),
                    "error": {"code": -32603, "message": str(e)}
                }

            # Write response
            with open(RESPONSE_FILE, "w", encoding="utf-8") as f:
                json.dump(response, f)

        except Exception as e:
            print("[MCP Bridge] Error processing request: %s" % str(e))
            traceback.print_exc()

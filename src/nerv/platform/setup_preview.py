"""Operator-only camera preview process with explicit handoff before robot connection."""
import json
import queue
import subprocess
import threading

from .runtime_management import environment

PREVIEW_TIMEOUT_S = 15


class SetupPreview:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.output = queue.Queue()

    def close(self):
        with self.lock:
            if self.process:
                self.process.stdin.close()
                try:
                    self.process.wait(timeout=PREVIEW_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=PREVIEW_TIMEOUT_S)
                self.process = None
        return {"ok": True}

    def request(self, python, operation, cameras=None, *, module):
        with self.lock:
            if operation == "close":
                return self.close()
            if not self.process:
                if operation != "open":
                    raise ValueError("Open camera preview first")
                self.output = queue.Queue()
                self.process = subprocess.Popen([python, "-m", module],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", env=environment())
                proc, output = self.process, self.output
                def read():
                    for line in proc.stdout:
                        try:
                            output.put(json.loads(line))
                        except ValueError:
                            continue
                    output.put({"ok": False, "message": "Camera preview process exited"})
                threading.Thread(target=read, daemon=True, name="camera-preview").start()
            self.process.stdin.write(json.dumps({"operation": operation, "cameras": cameras}) + "\n")
            self.process.stdin.flush()
            try:
                return self.output.get(timeout=PREVIEW_TIMEOUT_S)
            except queue.Empty:
                self.close()
                raise ValueError("Camera preview timed out; check permissions and device ownership")


preview = SetupPreview()

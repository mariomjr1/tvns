"""
runner.py — Thread-safe subprocess runner for the TVNS BIDS Pipeline GUI.

Usage:
    runner = ScriptRunner(root_widget)
    runner.run(cmd=['bash', '-c', '...'], cwd='/some/dir',
               on_line=lambda line: ..., on_done=lambda rc: ...)

Output lines and the final return code are dispatched on the Tk main thread
via a queue polled every 50 ms, so callbacks can safely touch widgets.
"""

import queue
import subprocess
import threading


class ScriptRunner:
    def __init__(self, root):
        self._root = root
        self._q = queue.Queue()
        self._busy = False
        self._poll()

    @property
    def busy(self):
        return self._busy

    def run(self, cmd, cwd, on_line, on_done):
        self._busy = True
        threading.Thread(
            target=self._worker,
            args=(cmd, cwd, on_line, on_done),
            daemon=True,
        ).start()

    def _worker(self, cmd, cwd, on_line, on_done):
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for raw in proc.stdout:
                self._q.put((on_line, raw.rstrip("\n")))
            proc.wait()
            self._q.put((on_done, proc.returncode))
        except Exception as exc:
            self._q.put((on_line, f"[runner] ERROR: {exc}"))
            self._q.put((on_done, 1))
        finally:
            self._busy = False

    def _poll(self):
        try:
            while True:
                cb, arg = self._q.get_nowait()
                cb(arg)
        except queue.Empty:
            pass
        self._root.after(50, self._poll)

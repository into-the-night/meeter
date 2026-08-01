from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable


class McpServiceManager:
    """Own exactly one local MCP child process without affecting app health."""

    def __init__(self, data_dir: Path, settings: Callable[[], dict[str, Any]], port: int = 4318):
        self.data_dir = data_dir
        self.settings = settings
        self.port = port
        self._process: subprocess.Popen[bytes] | None = None
        self._state = "stopped"
        self._error: str | None = None
        self._desired = False
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is not None:
                self._process = None
                if self._state != "stopped":
                    self._state = "error"
                    self._error = f"MCP service exited with code {process.returncode}"
            return {"state": self._state, "error": self._error, "url": f"http://127.0.0.1:{self.port}/mcp"}

    def start_async(self) -> None:
        with self._lock:
            if self._process is not None or self._state == "starting":
                return
            self._desired = True
            self._state, self._error = "starting", None
        threading.Thread(target=self._start, daemon=True, name="meeter-mcp-start").start()

    def _start(self) -> None:
        try:
            config = self.settings()
            if not config.get("enabled", False):
                with self._lock:
                    self._state = "stopped"
                return
            command = [
                sys.executable, str(Path(__file__).resolve().parents[1] / "mcp_server.py"),
                "--transport", "streamable-http", "--host", "127.0.0.1", "--port", str(self.port),
                "--parent-pid", str(os.getpid()), "--data-dir", str(self.data_dir),
                "--privacy", str(config["privacy"]), "--redact-pii", str(bool(config["redact_pii"])).lower(),
            ]
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with self._lock:
                if not self._desired:
                    process.terminate()
                    return
                self._process = process
                self._state = "running"
            threading.Thread(target=self._watch, args=(process,), daemon=True, name="meeter-mcp-watch").start()
        except Exception as exc:
            with self._lock:
                self._process = None
                self._state, self._error = "error", str(exc)

    def _watch(self, process: subprocess.Popen[bytes]) -> None:
        code = process.wait()
        with self._lock:
            if self._process is process:
                self._process = None
                if self._state != "stopped":
                    self._state, self._error = "error", f"MCP service exited with code {code}"

    def stop(self) -> None:
        with self._lock:
            self._desired = False
            process, self._process = self._process, None
            self._state, self._error = "stopped", None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def apply(self, previous: dict[str, Any], current: dict[str, Any]) -> None:
        if not current.get("enabled", False):
            self.stop()
        elif not previous.get("enabled", False):
            self.start_async()
        elif any(previous.get(key) != current.get(key) for key in ("privacy", "redact_pii")):
            self.stop()
            self.start_async()

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

from meeter.demo import demo_meeting
from meeter.local_models import ModelNotReady
from meeter.pipeline import MeetingPipeline
from meeter.storage import LocalStore


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
MAX_UPLOAD_BYTES = 750 * 1024 * 1024


class JobManager:
    def __init__(self, pipeline: MeetingPipeline):
        self.pipeline = pipeline
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def submit(self, audio_path: Path, source_name: str) -> dict[str, Any]:
        job_id = f"job_{uuid4().hex[:12]}"
        with self._lock:
            self._jobs[job_id] = {"id": job_id, "state": "queued", "step": "Preparing", "progress": 2}
        threading.Thread(target=self._run, args=(job_id, audio_path, source_name), daemon=True).start()
        return self.get(job_id) or {}

    def _run(self, job_id: str, audio_path: Path, source_name: str) -> None:
        def update(step: str, progress: int) -> None:
            with self._lock:
                self._jobs[job_id].update({"state": "processing", "step": step, "progress": progress})

        try:
            meeting = self.pipeline.process(audio_path, source_name, update)
            with self._lock:
                self._jobs[job_id].update({"state": "complete", "step": "Ready", "progress": 100, "meeting_id": meeting["id"]})
        except ModelNotReady as exc:
            with self._lock:
                self._jobs[job_id].update({"state": "error", "code": "MODEL_NOT_READY", "error": str(exc), "step": "Setup needed"})
        except Exception as exc:  # keeps details local and avoids killing the HTTP worker
            with self._lock:
                self._jobs[job_id].update({"state": "error", "code": "PROCESSING_FAILED", "error": str(exc), "step": "Processing failed"})
        finally:
            if os.environ.get("MEETER_KEEP_AUDIO", "0") != "1":
                audio_path.unlink(missing_ok=True)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None


class MeeterServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: LocalStore):
        self.store = store
        self.pipeline = MeetingPipeline(store)
        self.jobs = JobManager(self.pipeline)
        super().__init__(address, RequestHandler)


class RequestHandler(BaseHTTPRequestHandler):
    server: MeeterServer
    protocol_version = "HTTP/1.1"
    server_version = "Meeter"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _secure_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), display-capture=(self), geolocation=(), microphone=(self)")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

    def _begin(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self._secure_headers()
        self.end_headers()

    def _json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._begin(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _read_json(self, max_bytes: int = 1024 * 1024) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > max_bytes:
            raise ValueError("Invalid request size")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def _is_loopback(self) -> bool:
        return self.client_address[0] in {"127.0.0.1", "::1"}

    def _guard(self) -> bool:
        if self._is_loopback():
            return True
        self._json({"error": "Meeter accepts local connections only"}, HTTPStatus.FORBIDDEN)
        return False

    def do_GET(self) -> None:
        if not self._guard():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self._json({"ok": True, "version": "0.1.0", "readiness": self.server.pipeline.readiness()})
            return
        if path == "/api/meetings":
            self._json({"meetings": self.server.store.list_meetings()})
            return
        if path.startswith("/api/meetings/"):
            meeting = self.server.store.get_meeting(path.rsplit("/", 1)[-1])
            self._json(meeting if meeting else {"error": "Meeting not found"}, 200 if meeting else 404)
            return
        if path.startswith("/api/jobs/"):
            job = self.server.jobs.get(path.rsplit("/", 1)[-1])
            self._json(job if job else {"error": "Job not found"}, 200 if job else 404)
            return
        if path == "/api/speakers":
            profiles = [{"id": item.get("id"), "name": item.get("name")} for item in self.server.store.load_speakers()]
            self._json({"speakers": profiles})
            return
        self._serve_static(path)

    def do_HEAD(self) -> None:
        if not self._guard():
            return
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            self._json({"error": "HEAD is not available for API resources"}, 405)
            return
        self._serve_static(path, head_only=True)

    def _serve_static(self, url_path: str, head_only: bool = False) -> None:
        relative = "index.html" if url_path in {"", "/"} else url_path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT not in target.parents and target != WEB_ROOT:
            self._json({"error": "Not found"}, 404)
            return
        if not target.is_file():
            target = WEB_ROOT / "index.html"
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in {".html", ".css", ".js"}:
            content_type += "; charset=utf-8"
        self._begin(200, content_type, len(body))
        if not head_only:
            self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._guard():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/meetings/demo":
                meeting = demo_meeting().to_dict()
                self.server.store.save_meeting(meeting)
                self._json(meeting, 201)
                return
            if path == "/api/meetings/process":
                self._process_upload()
                return
            if path == "/api/speakers/enroll":
                self._enroll_speaker(parsed)
                return
            if path.endswith("/speaker-name") and path.startswith("/api/meetings/"):
                self._rename_speaker(path.split("/")[3])
                return
            if path.endswith("/action-state") and path.startswith("/api/meetings/"):
                self._set_action_state(path.split("/")[3])
                return
            self._json({"error": "Not found"}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except ModelNotReady as exc:
            self._json({"error": str(exc), "code": "MODEL_NOT_READY"}, 409)

    def _read_upload(self) -> tuple[str, bytes]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("The recording is empty")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("Recording exceeds the 750 MB local limit")
        filename = urllib.parse.unquote(self.headers.get("X-Filename", "recording.webm"))
        return filename, self.rfile.read(length)

    def _process_upload(self) -> None:
        filename, body = self._read_upload()
        audio_path = self.server.store.save_upload(filename, body)
        job = self.server.jobs.submit(audio_path, filename)
        self._json(job, 202)

    def _enroll_speaker(self, parsed: urllib.parse.ParseResult) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        name = query.get("name", [""])[0].strip()
        if len(name) < 2 or len(name) > 80:
            raise ValueError("Provide a speaker name between 2 and 80 characters")
        filename, body = self._read_upload()
        audio_path = self.server.store.save_upload(filename, body)
        try:
            self._json(self.server.pipeline.enroll(audio_path, name), 201)
        finally:
            audio_path.unlink(missing_ok=True)

    def _rename_speaker(self, meeting_id: str) -> None:
        payload = self._read_json()
        old_name = str(payload.get("old_name", "")).strip()
        new_name = str(payload.get("new_name", "")).strip()
        if not old_name or not (2 <= len(new_name) <= 80):
            raise ValueError("Invalid speaker names")
        meeting = self.server.store.get_meeting(meeting_id)
        if meeting is None:
            self._json({"error": "Meeting not found"}, 404)
            return
        for turn in meeting.get("transcript", []):
            if turn.get("speaker") == old_name:
                turn["speaker"] = new_name
        for participant in meeting.get("participants", []):
            if participant.get("name") == old_name:
                participant["name"] = new_name
        for action in meeting.get("actions", []):
            if action.get("owner") == old_name:
                action["owner"] = new_name
        self.server.store.save_meeting(meeting)
        self._json(meeting)

    def _set_action_state(self, meeting_id: str) -> None:
        payload = self._read_json()
        meeting = self.server.store.get_meeting(meeting_id)
        if meeting is None:
            self._json({"error": "Meeting not found"}, 404)
            return
        found = False
        for action in meeting.get("actions", []):
            if action.get("id") == payload.get("action_id"):
                action["completed"] = bool(payload.get("completed"))
                found = True
        if not found:
            raise ValueError("Action not found")
        self.server.store.save_meeting(meeting)
        self._json({"ok": True})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local-only Meeter app")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MEETER_PORT", "4317")))
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args()
    store = LocalStore(args.data_dir)
    server = MeeterServer(("127.0.0.1", args.port), store)
    print(f"Meeter is running locally at http://127.0.0.1:{args.port}")
    print(f"Data directory: {store.root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Meeter")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

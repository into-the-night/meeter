#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from meeter.demo import demo_meeting
from meeter.local_models import ModelNotReady
from meeter.mcp_access import PrivacyLevel
from meeter.mcp_manager import McpServiceManager
from meeter.pipeline import MeetingPipeline
from meeter.storage import LocalStore


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
MAX_UPLOAD_BYTES = 750 * 1024 * 1024
STREAM_CHUNK_BYTES = 64 * 1024


def normalize_media_type(value: str | None, filename: str) -> str:
    supplied = (value or "").split(";", 1)[0].strip().lower()
    if supplied.startswith("audio/") or supplied in {"video/mp4", "video/webm"}:
        return supplied
    guessed = (mimetypes.guess_type(filename)[0] or "").lower()
    if guessed.startswith("audio/") or guessed in {"video/mp4", "video/webm"}:
        return guessed
    return "application/octet-stream"


def parse_managed_bool(name: str, *, strict: bool = False) -> bool | None:
    if name not in os.environ:
        return None
    value = os.environ[name].strip().lower()
    if strict and value not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise ValueError(f"{name} must be true or false")
    return value not in {"", "0", "false", "no", "off"}


class JobManager:
    def __init__(self, pipeline: MeetingPipeline):
        self.pipeline = pipeline
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.model_lock = threading.RLock()
        for job in self.pipeline.store.recover_jobs():
            self._jobs[str(job["id"])] = job

    def _update(self, job_id: str, values: dict[str, Any]) -> None:
        with self._lock:
            self._jobs[job_id].update(values)
            self.pipeline.store.save_job(self._jobs[job_id])

    def submit(self, audio_path: Path, source_name: str, mime_type: str, retain_audio: bool) -> dict[str, Any]:
        job_id = f"job_{uuid4().hex[:12]}"
        with self._lock:
            self._jobs[job_id] = {"id": job_id, "state": "queued", "step": "Preparing", "progress": 2, "created_at": time.time()}
            self.pipeline.store.save_job(self._jobs[job_id])
        threading.Thread(
            target=self._run,
            args=(job_id, audio_path, source_name, mime_type, retain_audio),
            daemon=True,
        ).start()
        return self.get(job_id) or {}

    def submit_stream(
        self,
        audio_path: Path,
        source_name: str,
        mime_type: str,
        retain_audio: bool,
        duration: float,
        chunk_results: Callable[[], list[dict[str, Any]] | None],
    ) -> dict[str, Any]:
        job_id = f"job_{uuid4().hex[:12]}"
        with self._lock:
            self._jobs[job_id] = {"id": job_id, "state": "queued", "step": "Finishing queued batches", "progress": 62, "created_at": time.time()}
            self.pipeline.store.save_job(self._jobs[job_id])
        threading.Thread(
            target=self._run_stream,
            args=(job_id, audio_path, source_name, mime_type, retain_audio, duration, chunk_results),
            daemon=True,
        ).start()
        return self.get(job_id) or {}

    def _run(self, job_id: str, audio_path: Path, source_name: str, mime_type: str, retain_audio: bool) -> None:
        def update(step: str, progress: int) -> None:
            self._update(job_id, {"state": "processing", "step": step, "progress": progress, "updated_at": time.time()})

        def build() -> dict[str, Any]:
            with self.model_lock:
                return self.pipeline.process(audio_path, source_name, update)

        self._publish(job_id, audio_path, source_name, mime_type, retain_audio, build)

    def _run_stream(
        self,
        job_id: str,
        audio_path: Path,
        source_name: str,
        mime_type: str,
        retain_audio: bool,
        duration: float,
        chunk_results: Callable[[], list[dict[str, Any]] | None],
    ) -> None:
        def update(step: str, progress: int) -> None:
            self._update(job_id, {"state": "processing", "step": step, "progress": progress, "updated_at": time.time()})

        def build() -> dict[str, Any]:
            chunks = chunk_results()
            with self.model_lock:
                if chunks:
                    return self.pipeline.finalize_stream_chunks(chunks, source_name, duration, update)
                update("A batch failed; safely processing the complete recording", 8)
                return self.pipeline.process(audio_path, source_name, update)

        self._publish(job_id, audio_path, source_name, mime_type, retain_audio, build)

    def _publish(
        self,
        job_id: str,
        audio_path: Path,
        source_name: str,
        mime_type: str,
        retain_audio: bool,
        build: Callable[[], dict[str, Any]],
    ) -> None:
        promoted_audio = False
        meeting_id: str | None = None
        try:
            meeting = build()
            meeting_id = str(meeting["id"])
            if retain_audio:
                meeting["audio"] = self.pipeline.store.save_meeting_audio(meeting_id, audio_path, mime_type)
                promoted_audio = True
            else:
                meeting["audio"] = None
            self.pipeline.store.save_meeting(meeting)
            self._update(job_id, {"state": "complete", "step": "Ready", "progress": 100, "meeting_id": meeting_id, "timings": meeting.get("processing", {}), "updated_at": time.time()})
        except ModelNotReady as exc:
            self._update(job_id, {"state": "error", "code": "MODEL_NOT_READY", "error": str(exc), "step": "Setup needed", "updated_at": time.time()})
        except Exception as exc:  # keeps details local and avoids killing the HTTP worker
            if promoted_audio and meeting_id:
                self.pipeline.store.delete_meeting_audio(meeting_id)
            self._update(job_id, {"state": "error", "code": "PROCESSING_FAILED", "error": str(exc), "step": "Processing failed", "updated_at": time.time()})
        finally:
            audio_path.unlink(missing_ok=True)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id) or self.pipeline.store.get_job(job_id)
            return dict(job) if job else None


class RecordingSessionManager:
    """Queue independently decodable audio batches while capture continues."""

    def __init__(self, pipeline: MeetingPipeline, jobs: JobManager):
        self.pipeline = pipeline
        self.jobs = jobs
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._queue: queue.Queue[tuple[str, int, Path, float, float]] = queue.Queue()
        threading.Thread(target=self._worker, name="meeter-recording-batches", daemon=True).start()

    def create(self, source_name: str, mime_type: str, retain_audio: bool) -> dict[str, Any]:
        session_id = f"recording_{uuid4().hex[:12]}"
        with self._lock:
            self._sessions[session_id] = {
                "id": session_id,
                "state": "recording",
                "source_name": source_name,
                "mime_type": mime_type,
                "retain_audio": retain_audio,
                "chunks": {},
                "job_id": None,
                "error": None,
                "cancelled": False,
            }
        return self.get(session_id) or {}

    def add_chunk(self, session_id: str, index: int, path: Path, start: float, end: float) -> dict[str, Any]:
        if index < 0 or start < 0 or end <= start:
            path.unlink(missing_ok=True)
            raise ValueError("Invalid recording batch metadata")
        with self._condition:
            session = self._sessions.get(session_id)
            if session is None or session["cancelled"]:
                path.unlink(missing_ok=True)
                raise ValueError("Recording session not found")
            if session["state"] != "recording":
                path.unlink(missing_ok=True)
                raise ValueError("Recording session is no longer accepting batches")
            if index in session["chunks"]:
                path.unlink(missing_ok=True)
                raise ValueError("Recording batch index was already received")
            session["chunks"][index] = {"status": "queued", "path": path, "start": start, "end": end}
            self._queue.put((session_id, index, path, start, end))
            self._condition.notify_all()
        return self.get(session_id) or {}

    def finalize(self, session_id: str, audio_path: Path, source_name: str, mime_type: str, duration: float) -> dict[str, Any]:
        with self._condition:
            session = self._sessions.get(session_id)
            if session is None or session["cancelled"]:
                audio_path.unlink(missing_ok=True)
                raise ValueError("Recording session not found")
            if session["job_id"]:
                audio_path.unlink(missing_ok=True)
                raise ValueError("Recording session was already finalized")
            session["state"] = "finalizing"
            job = self.jobs.submit_stream(
                audio_path,
                source_name,
                mime_type,
                bool(session["retain_audio"]),
                duration,
                lambda: self._wait_results(session_id),
            )
            session["job_id"] = job["id"]
            self._condition.notify_all()
            return job

    def cancel(self, session_id: str) -> bool:
        with self._condition:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session["cancelled"] = True
            session["state"] = "cancelled"
            for chunk in session["chunks"].values():
                if chunk["status"] == "queued":
                    Path(chunk["path"]).unlink(missing_ok=True)
            self._condition.notify_all()
        return True

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            chunks = list(session["chunks"].values())
            return {
                "id": session_id,
                "state": session["state"],
                "received_chunks": len(chunks),
                "processed_chunks": sum(chunk["status"] == "complete" for chunk in chunks),
                "failed_chunks": sum(chunk["status"] == "error" for chunk in chunks),
                "job_id": session["job_id"],
                "error": session["error"],
            }

    def _worker(self) -> None:
        while True:
            session_id, index, path, start, end = self._queue.get()
            try:
                with self._condition:
                    session = self._sessions.get(session_id)
                    if session is None or session["cancelled"]:
                        path.unlink(missing_ok=True)
                        continue
                    session["chunks"][index]["status"] = "processing"
                with self.jobs.model_lock:
                    result = self.pipeline.process_stream_chunk(path, index, start, end)
                with self._condition:
                    chunk = self._sessions[session_id]["chunks"][index]
                    chunk.update({"status": "complete", "result": result})
                    self._condition.notify_all()
            except Exception as exc:
                with self._condition:
                    session = self._sessions.get(session_id)
                    if session is not None:
                        session["chunks"][index]["status"] = "error"
                        session["error"] = str(exc)
                        self._condition.notify_all()
            finally:
                path.unlink(missing_ok=True)
                self._queue.task_done()

    def _wait_results(self, session_id: str) -> list[dict[str, Any]] | None:
        with self._condition:
            while True:
                session = self._sessions.get(session_id)
                if session is None or session["cancelled"]:
                    return None
                chunks = list(session["chunks"].values())
                pending = any(chunk["status"] in {"queued", "processing"} for chunk in chunks)
                if not pending:
                    session["state"] = "processing"
                    if not chunks or any(chunk["status"] == "error" for chunk in chunks):
                        self._sessions.pop(session_id, None)
                        return None
                    results = [session["chunks"][index]["result"] for index in sorted(session["chunks"])]
                    self._sessions.pop(session_id, None)
                    return results
                self._condition.wait(timeout=1)


class MeeterServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: LocalStore, *, start_mcp: bool = True):
        self.store = store
        self.pipeline = MeetingPipeline(store)
        self.jobs = JobManager(self.pipeline)
        self.recordings = RecordingSessionManager(self.pipeline, self.jobs)
        super().__init__(address, RequestHandler)
        threading.Thread(target=self._warm_models, name="meeter-model-warmup", daemon=True).start()
        self.mcp = McpServiceManager(store.root, self.mcp_settings, int(os.environ.get("MEETER_MCP_PORT", "4318")))
        if start_mcp and self.mcp_settings()["enabled"]:
            self.mcp.start_async()

    def _warm_models(self) -> None:
        try:
            with self.jobs.model_lock:
                self.pipeline.warmup()
        except Exception as exc:
            print(f"[warmup] {exc}")

    def server_close(self) -> None:
        if hasattr(self, "mcp"):
            self.mcp.stop()
        super().server_close()

    def audio_settings(self) -> dict[str, Any]:
        settings, settings_error = self.store.load_settings_state()
        saved = settings.get("audio", {})
        configured_value = saved.get("retain_recordings", False) if isinstance(saved, dict) else False
        configured = configured_value if isinstance(configured_value, bool) else False
        if not isinstance(configured_value, bool) and settings_error is None:
            settings_error = "Audio retention setting is invalid and has been disabled."
        managed = parse_managed_bool("MEETER_KEEP_AUDIO")
        return {
            "retain_recordings": configured if managed is None else managed,
            "managed": managed is not None,
            "managed_by": "MEETER_KEEP_AUDIO" if managed is not None else None,
            "error": settings_error,
        }

    def mcp_settings(self) -> dict[str, Any]:
        settings, settings_error = self.store.load_settings_state()
        saved = settings.get("mcp", {})
        enabled = saved.get("enabled", False) if isinstance(saved, dict) else False
        privacy = saved.get("privacy", "insights") if isinstance(saved, dict) else "insights"
        redact_pii = saved.get("redact_pii", True) if isinstance(saved, dict) else True
        if not isinstance(enabled, bool) or privacy not in {"insights", "excerpts", "full"} or not isinstance(redact_pii, bool):
            enabled, privacy, redact_pii = False, "insights", True
            settings_error = settings_error or "MCP settings are invalid and have been disabled."
        privacy_managed = "MEETER_MCP_PRIVACY" in os.environ
        pii_managed = "MEETER_MCP_REDACT_PII" in os.environ
        if privacy_managed:
            try:
                privacy = PrivacyLevel.parse(os.environ["MEETER_MCP_PRIVACY"]).name.lower()
            except ValueError:
                enabled, privacy = False, "insights"
                settings_error = "MEETER_MCP_PRIVACY is invalid and MCP has been disabled."
        if pii_managed:
            try:
                redact_pii = bool(parse_managed_bool("MEETER_MCP_REDACT_PII", strict=True))
            except ValueError:
                enabled, redact_pii = False, True
                settings_error = "MEETER_MCP_REDACT_PII is invalid and MCP has been disabled."
        status = self.mcp.status() if hasattr(self, "mcp") else {"state": "stopped", "error": None, "url": f"http://127.0.0.1:{int(os.environ.get('MEETER_MCP_PORT', '4318'))}/mcp"}
        return {
            "enabled": enabled, "privacy": privacy, "redact_pii": redact_pii,
            "managed": {"privacy": privacy_managed, "redact_pii": pii_managed, "allowlist": "MEETER_MCP_ALLOWED_MEETINGS" in os.environ},
            "error": settings_error or status["error"], "status": status["state"], "url": status["url"],
        }


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

    def _begin(self, status: int, content_type: str, length: int, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self._secure_headers()
        self.end_headers()

    def _json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._begin(status, "application/json; charset=utf-8", len(body))
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

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
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "meetings"] and parts[3] == "audio":
            self._serve_meeting_audio(parts[2])
            return
        if len(parts) == 3 and parts[:2] == ["api", "meetings"]:
            meeting = self.server.store.get_meeting(parts[2])
            self._json(meeting if meeting else {"error": "Meeting not found"}, 200 if meeting else 404)
            return
        if path.startswith("/api/jobs/"):
            job = self.server.jobs.get(path.rsplit("/", 1)[-1])
            self._json(job if job else {"error": "Job not found"}, 200 if job else 404)
            return
        if path.startswith("/api/recording-sessions/"):
            session = self.server.recordings.get(path.rsplit("/", 1)[-1])
            self._json(session if session else {"error": "Recording session not found"}, 200 if session else 404)
            return
        if path == "/api/speakers":
            profiles = [{"id": item.get("id"), "name": item.get("name")} for item in self.server.store.load_speakers()]
            self._json({"speakers": profiles})
            return
        if path == "/api/settings/audio":
            self._json(self.server.audio_settings())
            return
        if path == "/api/settings/mcp":
            self._json(self.server.mcp_settings())
            return
        self._serve_static(path)

    def do_HEAD(self) -> None:
        if not self._guard():
            return
        path = urllib.parse.urlparse(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "meetings"] and parts[3] == "audio":
            self._serve_meeting_audio(parts[2], head_only=True)
            return
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

    @staticmethod
    def _parse_byte_range(value: str, size: int) -> tuple[int, int] | None:
        if not value.startswith("bytes=") or "," in value or size <= 0:
            return None
        spec = value[6:].strip()
        if "-" not in spec:
            return None
        start_text, end_text = spec.split("-", 1)
        try:
            if not start_text:
                suffix = int(end_text)
                if suffix <= 0:
                    return None
                return max(0, size - suffix), size - 1
            start = int(start_text)
            if start < 0 or start >= size:
                return None
            end = size - 1 if not end_text else int(end_text)
            if end < start:
                return None
            return start, min(end, size - 1)
        except ValueError:
            return None

    def _serve_meeting_audio(self, meeting_id: str, head_only: bool = False) -> None:
        meeting = self.server.store.get_meeting(meeting_id)
        metadata = meeting.get("audio") if isinstance(meeting, dict) else None
        target = self.server.store.get_meeting_audio(meeting_id) if isinstance(metadata, dict) else None
        if target is None:
            if head_only:
                self._begin(404, "application/json; charset=utf-8", 0, {"Cache-Control": "private, no-store"})
            else:
                body = json.dumps({"error": "Meeting audio not found"}).encode("utf-8")
                self._begin(404, "application/json; charset=utf-8", len(body), {"Cache-Control": "private, no-store"})
                self.wfile.write(body)
            return
        size = target.stat().st_size
        mime_type = str(metadata.get("mime_type") or "application/octet-stream")
        common_headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}
        range_value = self.headers.get("Range")
        start, end, status = 0, max(0, size - 1), 200
        if range_value:
            selected = self._parse_byte_range(range_value, size)
            if selected is None:
                self._begin(416, "application/octet-stream", 0, {**common_headers, "Content-Range": f"bytes */{size}"})
                return
            start, end = selected
            status = 206
            common_headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        length = 0 if size == 0 else end - start + 1
        self._begin(status, mime_type, length, common_headers)
        if head_only or length == 0:
            return
        try:
            with target.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_PUT(self) -> None:
        if not self._guard():
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/settings/audio":
                payload = self._read_json()
                if not isinstance(payload.get("retain_recordings"), bool):
                    raise ValueError("retain_recordings must be a boolean")
                if parse_managed_bool("MEETER_KEEP_AUDIO") is not None:
                    self._json({"error": "Audio retention is managed by MEETER_KEEP_AUDIO"}, 409)
                    return
                self.server.store.update_settings_section(
                    "audio", {"retain_recordings": payload["retain_recordings"]}
                )
                self._json(self.server.audio_settings())
                return
            if path == "/api/settings/mcp":
                payload = self._read_json()
                allowed = {"enabled", "privacy", "redact_pii"}
                if not payload or set(payload) - allowed:
                    raise ValueError("Expected enabled, privacy, or redact_pii")
                before = self.server.mcp_settings()
                if "enabled" in payload and not isinstance(payload["enabled"], bool):
                    raise ValueError("enabled must be a boolean")
                if "privacy" in payload:
                    if before["managed"]["privacy"]:
                        self._json({"error": "MCP privacy is managed by MEETER_MCP_PRIVACY"}, 409); return
                    if not isinstance(payload["privacy"], str):
                        raise ValueError("privacy must be insights, excerpts, or full")
                    PrivacyLevel.parse(payload["privacy"])
                if "redact_pii" in payload:
                    if not isinstance(payload["redact_pii"], bool):
                        raise ValueError("redact_pii must be a boolean")
                    if before["managed"]["redact_pii"]:
                        self._json({"error": "PII redaction is managed by MEETER_MCP_REDACT_PII"}, 409); return
                self.server.store.update_settings_section("mcp", payload)
                after = self.server.mcp_settings()
                self.server.mcp.apply(before, after)
                self._json(self.server.mcp_settings())
                return
            self._json({"error": "Not found"}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)

    def do_PATCH(self) -> None:
        if not self._guard():
            return
        path = urllib.parse.urlparse(self.path).path
        parts = path.strip("/").split("/")
        try:
            if len(parts) == 3 and parts[:2] == ["api", "meetings"]:
                payload = self._read_json()
                meeting = self.server.store.rename_meeting(parts[2], payload.get("title"))
                self._json(meeting if meeting else {"error": "Meeting not found"}, 200 if meeting else 404)
                return
            self._json({"error": "Not found"}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)

    def do_DELETE(self) -> None:
        if not self._guard():
            return
        path = urllib.parse.urlparse(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "recording-sessions"]:
            if self.server.recordings.cancel(parts[2]):
                self._json({"ok": True, "recording_session_id": parts[2]})
            else:
                self._json({"error": "Recording session not found"}, 404)
            return
        if len(parts) == 3 and parts[:2] == ["api", "meetings"]:
            meeting_id = parts[2]
            if self.server.store.delete_meeting(meeting_id):
                self._json({"ok": True, "meeting_id": meeting_id})
            else:
                self._json({"error": "Meeting not found"}, 404)
            return
        self._json({"error": "Not found"}, 404)

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
            if path == "/api/recording-sessions":
                self._create_recording_session()
                return
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "recording-sessions"] and parts[3] == "chunks":
                self._add_recording_chunk(parts[2])
                return
            if len(parts) == 4 and parts[:2] == ["api", "recording-sessions"] and parts[3] == "finalize":
                self._finalize_recording_session(parts[2])
                return
            if path == "/api/speakers/enroll":
                self._enroll_speaker(parsed)
                return
            if path.endswith("/speaker-name") and path.startswith("/api/meetings/"):
                self._rename_speaker(path.split("/")[3])
                return
            if path.endswith("/merge-speakers") and path.startswith("/api/meetings/"):
                self._merge_speakers(path.split("/")[3])
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
        mime_type = normalize_media_type(self.headers.get("Content-Type"), filename)
        retain_audio = self.server.audio_settings()["retain_recordings"]
        job = self.server.jobs.submit(audio_path, filename, mime_type, retain_audio)
        self._json(job, 202)

    def _create_recording_session(self) -> None:
        payload = self._read_json()
        source_name = str(payload.get("source_name", "Live recording")).strip()[:180] or "Live recording"
        mime_type = normalize_media_type(str(payload.get("mime_type", "")), source_name)
        retain_audio = self.server.audio_settings()["retain_recordings"]
        self._json(self.server.recordings.create(source_name, mime_type, retain_audio), 201)

    def _add_recording_chunk(self, session_id: str) -> None:
        filename, body = self._read_upload()
        try:
            index = int(self.headers.get("X-Chunk-Index", "-1"))
            start = float(self.headers.get("X-Chunk-Start-Ms", "-1")) / 1000
            end = float(self.headers.get("X-Chunk-End-Ms", "-1")) / 1000
        except ValueError as exc:
            raise ValueError("Invalid recording batch metadata") from exc
        audio_path = self.server.store.save_upload(filename, body)
        session = self.server.recordings.add_chunk(session_id, index, audio_path, start, end)
        self._json(session, 202)

    def _finalize_recording_session(self, session_id: str) -> None:
        filename, body = self._read_upload()
        try:
            duration = float(self.headers.get("X-Recording-Duration-Ms", "0")) / 1000
        except ValueError as exc:
            raise ValueError("Invalid recording duration") from exc
        if duration <= 0:
            raise ValueError("Invalid recording duration")
        audio_path = self.server.store.save_upload(filename, body)
        mime_type = normalize_media_type(self.headers.get("Content-Type"), filename)
        job = self.server.recordings.finalize(session_id, audio_path, filename, mime_type, duration)
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

    def _merge_speakers(self, meeting_id: str) -> None:
        payload = self._read_json()
        source_name = str(payload.get("source_name", "")).strip()
        target_name = str(payload.get("target_name", "")).strip()
        if not source_name or not target_name or source_name == target_name:
            raise ValueError("Choose two different speakers to merge")
        meeting = self.server.store.get_meeting(meeting_id)
        if meeting is None:
            self._json({"error": "Meeting not found"}, 404)
            return
        participants = meeting.get("participants", [])
        source = next((item for item in participants if item.get("name") == source_name), None)
        target = next((item for item in participants if item.get("name") == target_name), None)
        if source is None or target is None:
            raise ValueError("Speaker not found in this meeting")

        target_id = target.get("id")
        for turn in meeting.get("transcript", []):
            if turn.get("speaker") == source_name:
                turn["speaker"] = target_name
                if target_id:
                    turn["participant_key"] = target_id
                if target.get("known") and target_id:
                    turn["speaker_id"] = target_id
                else:
                    turn.pop("speaker_id", None)
        for action in meeting.get("actions", []):
            if action.get("owner") == source_name:
                action["owner"] = target_name
        meeting["participants"] = [item for item in participants if item is not source]
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

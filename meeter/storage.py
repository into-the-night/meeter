from __future__ import annotations

import json
import os
import platform
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any


def default_data_dir() -> Path:
    override = os.environ.get("MEETER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Meeter"
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Meeter"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "meeter"


class LocalStore:
    def __init__(self, root: Path | None = None):
        self.root = (root or default_data_dir()).resolve()
        self.meetings_dir = self.root / "meetings"
        self.audio_dir = self.root / "audio"
        self._lock = RLock()
        self.meetings_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)

    def _atomic_json(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)

    def save_meeting(self, meeting: dict[str, Any]) -> dict[str, Any]:
        meeting_id = str(meeting["id"])
        if not meeting_id.startswith("meeting_") or not meeting_id.replace("_", "").isalnum():
            raise ValueError("Invalid meeting id")
        with self._lock:
            self._atomic_json(self.meetings_dir / f"{meeting_id}.json", meeting)
        return meeting

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        if not meeting_id.startswith("meeting_") or not meeting_id.replace("_", "").isalnum():
            return None
        path = self.meetings_dir / f"{meeting_id}.json"
        if not path.exists():
            return None
        with self._lock, path.open(encoding="utf-8") as handle:
            return json.load(handle)

    def list_meetings(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with self._lock:
            for path in self.meetings_dir.glob("meeting_*.json"):
                try:
                    with path.open(encoding="utf-8") as handle:
                        item = json.load(handle)
                    records.append({
                        "id": item["id"],
                        "title": item.get("title", "Untitled meeting"),
                        "created_at": item.get("created_at"),
                        "duration": item.get("duration", 0),
                        "participants": item.get("participants", []),
                        "action_count": len(item.get("actions", [])),
                    })
                except (OSError, json.JSONDecodeError, KeyError):
                    continue
        return sorted(records, key=lambda item: item.get("created_at") or "", reverse=True)

    def save_upload(self, filename: str, body: bytes) -> Path:
        safe_name = "".join(ch for ch in Path(filename).name if ch.isalnum() or ch in ".-_ ").strip()
        if not safe_name:
            safe_name = "recording.webm"
        target = self.audio_dir / f"{next(tempfile._get_candidate_names())}_{safe_name}"
        target.write_bytes(body)
        return target

    @property
    def speakers_path(self) -> Path:
        return self.root / "speakers.json"

    def load_speakers(self) -> list[dict[str, Any]]:
        if not self.speakers_path.exists():
            return []
        with self._lock, self.speakers_path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, list) else []

    def save_speakers(self, speakers: list[dict[str, Any]]) -> None:
        with self._lock:
            self._atomic_json(self.speakers_path, speakers)


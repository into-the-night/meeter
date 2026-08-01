from __future__ import annotations

import json
import os
import platform
import tempfile
import unicodedata
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "audio": {"retain_recordings": True},
    "mcp": {"enabled": True, "privacy": "insights", "redact_pii": True},
}


def validate_meeting_title(value: Any) -> str:
    """Return a normalized meeting title or raise a user-safe validation error."""
    if not isinstance(value, str):
        raise ValueError("Title must be a string")
    title = value.strip()
    if not title:
        raise ValueError("Title must not be empty")
    if len(title) > 120:
        raise ValueError("Title must be 120 characters or fewer")
    if any(unicodedata.category(character) == "Cc" for character in title):
        raise ValueError("Title must not contain control characters")
    return title


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

    @staticmethod
    def _valid_meeting_id(meeting_id: str) -> bool:
        return meeting_id.startswith("meeting_") and meeting_id.replace("_", "").isalnum()

    def save_meeting(self, meeting: dict[str, Any]) -> dict[str, Any]:
        meeting_id = str(meeting["id"])
        if not self._valid_meeting_id(meeting_id):
            raise ValueError("Invalid meeting id")
        with self._lock:
            self._atomic_json(self.meetings_dir / f"{meeting_id}.json", meeting)
        return meeting

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        if not self._valid_meeting_id(meeting_id):
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

    def rename_meeting(self, meeting_id: str, title: Any) -> dict[str, Any] | None:
        normalized = validate_meeting_title(title)
        if not self._valid_meeting_id(meeting_id):
            return None
        path = self.meetings_dir / f"{meeting_id}.json"
        with self._lock:
            if not path.is_file():
                return None
            with path.open(encoding="utf-8") as handle:
                meeting = json.load(handle)
            meeting["title"] = normalized
            self._atomic_json(path, meeting)
        return meeting

    def delete_meeting(self, meeting_id: str) -> bool:
        """Permanently delete one meeting, removing its owned audio first."""
        if not self._valid_meeting_id(meeting_id):
            return False
        meeting_path = self.meetings_dir / f"{meeting_id}.json"
        with self._lock:
            if not meeting_path.is_file():
                return False
            audio_path = self.meeting_audio_path(meeting_id)
            if audio_path is not None:
                audio_path.unlink(missing_ok=True)
            meeting_path.unlink()
        return True

    def save_upload(self, filename: str, body: bytes) -> Path:
        safe_name = "".join(ch for ch in Path(filename).name if ch.isalnum() or ch in ".-_ ").strip()
        if not safe_name:
            safe_name = "recording.webm"
        target = self.audio_dir / f"{next(tempfile._get_candidate_names())}_{safe_name}"
        target.write_bytes(body)
        return target

    def meeting_audio_path(self, meeting_id: str) -> Path | None:
        if not self._valid_meeting_id(meeting_id):
            return None
        return self.audio_dir / f"{meeting_id}.source"

    def save_meeting_audio(self, meeting_id: str, source: Path, mime_type: str) -> dict[str, Any]:
        target = self.meeting_audio_path(meeting_id)
        if target is None:
            raise ValueError("Invalid meeting id")
        with self._lock:
            os.replace(source, target)
            size = target.stat().st_size
        return {"mime_type": mime_type, "size_bytes": size}

    def get_meeting_audio(self, meeting_id: str) -> Path | None:
        target = self.meeting_audio_path(meeting_id)
        return target if target is not None and target.is_file() else None

    def delete_meeting_audio(self, meeting_id: str) -> None:
        target = self.meeting_audio_path(meeting_id)
        if target is not None:
            with self._lock:
                target.unlink(missing_ok=True)

    @property
    def settings_path(self) -> Path:
        return self.root / "settings.json"

    def load_settings_state(self) -> tuple[dict[str, Any], str | None]:
        settings = deepcopy(DEFAULT_SETTINGS)
        if not self.settings_path.is_file():
            return settings, None
        try:
            with self._lock, self.settings_path.open(encoding="utf-8") as handle:
                saved = json.load(handle)
        except (OSError, json.JSONDecodeError):
            settings["audio"]["retain_recordings"] = False
            settings["mcp"]["enabled"] = False
            return settings, "Settings could not be read; audio retention and MCP are disabled until they are saved again."
        if not isinstance(saved, dict):
            settings["audio"]["retain_recordings"] = False
            settings["mcp"]["enabled"] = False
            return settings, "Settings are invalid; audio retention and MCP are disabled until they are saved again."
        for section, value in saved.items():
            if isinstance(value, dict) and isinstance(settings.get(section), dict):
                settings[section].update(value)
            else:
                settings[section] = value
        return settings, None

    def load_settings(self) -> dict[str, Any]:
        settings, _ = self.load_settings_state()
        return settings

    def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(settings, dict):
            raise ValueError("Settings must be a JSON object")
        with self._lock:
            self._atomic_json(self.settings_path, settings)
        return settings

    def update_settings_section(self, section: str, values: dict[str, Any]) -> dict[str, Any]:
        if not section or not isinstance(values, dict):
            raise ValueError("Invalid settings section")
        with self._lock:
            settings = self.load_settings()
            current = settings.get(section)
            settings[section] = {**(current if isinstance(current, dict) else {}), **values}
            self._atomic_json(self.settings_path, settings)
        return settings[section]

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

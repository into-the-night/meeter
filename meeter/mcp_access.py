from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, time, timezone
from enum import IntEnum
from pathlib import Path
from threading import RLock
from typing import Any

from .storage import default_data_dir


class PrivacyLevel(IntEnum):
    INSIGHTS = 1
    EXCERPTS = 2
    FULL = 3

    @classmethod
    def parse(cls, value: str) -> "PrivacyLevel":
        try:
            return cls[value.strip().upper()]
        except KeyError as exc:
            choices = ", ".join(item.name.lower() for item in cls)
            raise ValueError(f"Invalid privacy level {value!r}; choose one of: {choices}") from exc


@dataclass(frozen=True, slots=True)
class McpPrivacyConfig:
    level: PrivacyLevel = PrivacyLevel.INSIGHTS
    redact_pii: bool = True
    allowed_meeting_ids: frozenset[str] | None = None
    max_results: int = 50
    max_excerpts: int = 8

    def permits(self, meeting_id: str) -> bool:
        return self.allowed_meeting_ids is None or meeting_id in self.allowed_meeting_ids


class ReadOnlyMeetingStore:
    """Meeting reader that never creates directories or writes application data."""

    def __init__(self, root: Path | None = None):
        self.root = (root or default_data_dir()).expanduser().resolve()
        self.meetings_dir = self.root / "meetings"
        self._lock = RLock()

    @staticmethod
    def _valid_id(meeting_id: str) -> bool:
        return meeting_id.startswith("meeting_") and meeting_id.replace("_", "").isalnum()

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        if not self._valid_id(meeting_id):
            return None
        path = self.meetings_dir / f"{meeting_id}.json"
        if not path.is_file():
            return None
        try:
            with self._lock, path.open(encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def iter_meetings(self) -> list[dict[str, Any]]:
        if not self.meetings_dir.is_dir():
            return []
        records: list[dict[str, Any]] = []
        with self._lock:
            for path in self.meetings_dir.glob("meeting_*.json"):
                try:
                    with path.open(encoding="utf-8") as handle:
                        value = json.load(handle)
                    if isinstance(value, dict) and self._valid_id(str(value.get("id", ""))):
                        records.append(value)
                except (OSError, json.JSONDecodeError):
                    continue
        return sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True)


_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(
    r"(?<![\w-])(?!\d{4}-\d{2}-\d{2}\b)(?:\+?\d[\d ().-]{7,}\d)(?![\w-])"
)


def _redact_text(value: str) -> str:
    value = _EMAIL.sub("[redacted email]", value)
    return _PHONE.sub("[redacted phone]", value)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    return value


def _parse_datetime(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        if len(value) == 10:
            parsed_date = datetime.fromisoformat(value).date()
            parsed = datetime.combine(parsed_date, time.max if end_of_day else time.min)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO date or datetime: {value}") from exc


class MeetingContextService:
    """Privacy-filtered, read-only query layer used by the MCP transport."""

    def __init__(self, store: ReadOnlyMeetingStore, config: McpPrivacyConfig):
        self.store = store
        self.config = config

    def _privacy(self) -> dict[str, Any]:
        return {
            "level": self.config.level.name.lower(),
            "pii_redaction": self.config.redact_pii,
            "transcript_access": (
                "none"
                if self.config.level == PrivacyLevel.INSIGHTS
                else "matching excerpts"
                if self.config.level == PrivacyLevel.EXCERPTS
                else "full"
            ),
            "read_only": True,
        }

    def _finish(self, value: dict[str, Any]) -> dict[str, Any]:
        value["privacy"] = self._privacy()
        return _redact(value) if self.config.redact_pii else value

    def _meetings(self) -> list[dict[str, Any]]:
        return [
            meeting
            for meeting in self.store.iter_meetings()
            if self.config.permits(str(meeting.get("id", "")))
        ]

    def _get(self, meeting_id: str) -> dict[str, Any]:
        if not self.config.permits(meeting_id):
            raise LookupError("Meeting is not available to this MCP server")
        meeting = self.store.get_meeting(meeting_id)
        if meeting is None:
            raise LookupError("Meeting not found")
        return meeting

    @staticmethod
    def _participants(meeting: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"name": item.get("name", "Unknown"), "known": bool(item.get("known", False))}
            for item in meeting.get("participants", [])
            if isinstance(item, dict)
        ]

    @classmethod
    def _actions(cls, meeting: dict[str, Any]) -> list[dict[str, Any]]:
        fields = ("id", "text", "owner", "due", "priority", "context", "completed")
        return [
            {field: item.get(field) for field in fields}
            for item in meeting.get("actions", [])
            if isinstance(item, dict)
        ]

    @classmethod
    def _discussion(cls, meeting: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"title": item.get("title", ""), "detail": item.get("detail", "")}
            for item in meeting.get("discussion", [])
            if isinstance(item, dict)
        ]

    @classmethod
    def _insights(cls, meeting: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": meeting.get("id"),
            "title": meeting.get("title", "Untitled meeting"),
            "created_at": meeting.get("created_at"),
            "duration_seconds": meeting.get("duration", 0),
            "language": meeting.get("language", "en"),
            "participants": cls._participants(meeting),
            "summary": meeting.get("summary", ""),
            "decisions": meeting.get("decisions", []),
            "actions": cls._actions(meeting),
            "discussion": cls._discussion(meeting),
            "risks": meeting.get("risks", []),
        }

    def list_meetings(
        self,
        query: str = "",
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        start = _parse_datetime(date_from)
        end = _parse_datetime(date_to, end_of_day=True)
        needle = query.casefold().strip()
        result: list[dict[str, Any]] = []
        for meeting in self._meetings():
            created = _parse_datetime(str(meeting.get("created_at") or ""))
            if start and (created is None or created < start):
                continue
            if end and (created is None or created > end):
                continue
            participants = self._participants(meeting)
            searchable = " ".join(
                [
                    str(meeting.get("title", "")),
                    *(str(item.get("name", "")) for item in participants),
                ]
            ).casefold()
            if needle and needle not in searchable:
                continue
            result.append(
                {
                    "id": meeting.get("id"),
                    "title": meeting.get("title", "Untitled meeting"),
                    "created_at": meeting.get("created_at"),
                    "duration_seconds": meeting.get("duration", 0),
                    "participants": participants,
                    "action_count": len(meeting.get("actions", [])),
                }
            )
        safe_limit = max(1, min(limit, self.config.max_results))
        return self._finish({"meetings": result[:safe_limit], "total_matches": len(result)})

    def get_meeting_insights(self, meeting_id: str) -> dict[str, Any]:
        return self._finish({"meeting": self._insights(self._get(meeting_id))})

    @staticmethod
    def _insight_sections(meeting: dict[str, Any]) -> list[tuple[str, str]]:
        sections = [
            ("title", str(meeting.get("title", ""))),
            ("summary", str(meeting.get("summary", ""))),
        ]
        for key in ("decisions", "risks"):
            sections.extend((key, str(item)) for item in meeting.get(key, []))
        sections.extend(
            ("actions", " ".join(str(item.get(field, "")) for field in ("text", "owner", "context")))
            for item in meeting.get("actions", [])
            if isinstance(item, dict)
        )
        sections.extend(
            ("discussion", " ".join(str(item.get(field, "")) for field in ("title", "detail")))
            for item in meeting.get("discussion", [])
            if isinstance(item, dict)
        )
        return sections

    def search_meetings(self, query: str, limit: int = 10) -> dict[str, Any]:
        needle = query.casefold().strip()
        if len(needle) < 2:
            raise ValueError("Search query must contain at least 2 characters")
        matches: list[dict[str, Any]] = []
        for meeting in self._meetings():
            insight_hits = [
                {"section": section, "text": text}
                for section, text in self._insight_sections(meeting)
                if needle in text.casefold()
            ]
            transcript_hits: list[dict[str, Any]] = []
            if self.config.level >= PrivacyLevel.EXCERPTS:
                for turn in meeting.get("transcript", []):
                    if not isinstance(turn, dict) or needle not in str(turn.get("text", "")).casefold():
                        continue
                    transcript_hits.append(self._transcript_turn(turn))
                    if len(transcript_hits) >= self.config.max_excerpts:
                        break
            if insight_hits or transcript_hits:
                matches.append(
                    {
                        "meeting_id": meeting.get("id"),
                        "title": meeting.get("title", "Untitled meeting"),
                        "created_at": meeting.get("created_at"),
                        "insight_matches": insight_hits,
                        **({"transcript_excerpts": transcript_hits} if transcript_hits else {}),
                    }
                )
        safe_limit = max(1, min(limit, self.config.max_results))
        return self._finish({"query": query, "matches": matches[:safe_limit], "total_matches": len(matches)})

    def get_action_items(
        self,
        owner: str = "",
        include_completed: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        owner_needle = owner.casefold().strip()
        actions: list[dict[str, Any]] = []
        for meeting in self._meetings():
            for action in meeting.get("actions", []):
                if not isinstance(action, dict):
                    continue
                if not include_completed and bool(action.get("completed", False)):
                    continue
                if owner_needle and owner_needle not in str(action.get("owner", "")).casefold():
                    continue
                projected = self._actions({"actions": [action]})[0]
                actions.append(
                    {
                        **projected,
                        "meeting_id": meeting.get("id"),
                        "meeting_title": meeting.get("title", "Untitled meeting"),
                        "meeting_created_at": meeting.get("created_at"),
                    }
                )
        safe_limit = max(1, min(limit, self.config.max_results))
        return self._finish({"actions": actions[:safe_limit], "total_matches": len(actions)})

    @staticmethod
    def _transcript_turn(turn: dict[str, Any]) -> dict[str, Any]:
        return {
            "start_seconds": turn.get("start"),
            "end_seconds": turn.get("end"),
            "speaker": turn.get("speaker", "Unknown"),
            "text": turn.get("text", ""),
        }

    def get_transcript_excerpts(
        self,
        meeting_id: str,
        query: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        if self.config.level < PrivacyLevel.EXCERPTS:
            raise PermissionError("Transcript excerpts are disabled by the MCP privacy level")
        needle = query.casefold().strip()
        if len(needle) < 2:
            raise ValueError("Excerpt query must contain at least 2 characters")
        meeting = self._get(meeting_id)
        safe_limit = max(1, min(limit, self.config.max_excerpts))
        excerpts = [
            self._transcript_turn(turn)
            for turn in meeting.get("transcript", [])
            if isinstance(turn, dict) and needle in str(turn.get("text", "")).casefold()
        ][:safe_limit]
        return self._finish(
            {
                "meeting_id": meeting_id,
                "title": meeting.get("title", "Untitled meeting"),
                "query": query,
                "excerpts": excerpts,
            }
        )

    def get_meeting_transcript(self, meeting_id: str) -> dict[str, Any]:
        if self.config.level < PrivacyLevel.FULL:
            raise PermissionError("Full transcripts are disabled by the MCP privacy level")
        meeting = self._get(meeting_id)
        return self._finish(
            {
                "meeting_id": meeting_id,
                "title": meeting.get("title", "Untitled meeting"),
                "transcript": [
                    self._transcript_turn(turn)
                    for turn in meeting.get("transcript", [])
                    if isinstance(turn, dict)
                ],
            }
        )

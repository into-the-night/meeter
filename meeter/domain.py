from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class TranscriptTurn:
    start: float
    end: float
    speaker: str
    text: str
    speaker_id: str | None = None
    confidence: float | None = None


@dataclass(slots=True)
class ActionItem:
    text: str
    owner: str | None = None
    due: str | None = None
    priority: str = "medium"
    context: str = ""
    id: str = field(default_factory=lambda: f"action_{uuid4().hex[:10]}")
    completed: bool = False


@dataclass(slots=True)
class Meeting:
    title: str
    duration: float
    transcript: list[TranscriptTurn]
    summary: str
    decisions: list[str]
    actions: list[ActionItem]
    discussion: list[dict[str, str]]
    risks: list[str] = field(default_factory=list)
    participants: list[dict[str, Any]] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"meeting_{uuid4().hex[:12]}")
    created_at: str = field(default_factory=now_iso)
    source_name: str = "Recording"
    language: str = "en"
    model_note: str = "Processed locally"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


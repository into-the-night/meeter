from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .domain import ActionItem, Meeting, TranscriptTurn
from .local_models import DiarizationAdapter, DiarizationTurn, EmbeddingAdapter, LlamaCppAdapter, ModelNotReady, WhisperAdapter, cosine_similarity
from .storage import LocalStore
from .summarizer import summarize


Progress = Callable[[str, int], None]


def _overlap(start: float, end: float, turn: DiarizationTurn) -> float:
    return max(0.0, min(end, turn.end) - max(start, turn.start))


def assign_diarization(segments: list[dict[str, Any]], diarization: list[DiarizationTurn]) -> list[dict[str, Any]]:
    ordered_labels: list[str] = []
    output = []
    for segment in segments:
        candidates = [(turn, _overlap(float(segment["start"]), float(segment["end"]), turn)) for turn in diarization]
        best = max(candidates, key=lambda pair: pair[1], default=(None, 0.0))[0]
        raw_label = best.label if best else "UNKNOWN"
        if raw_label not in ordered_labels:
            ordered_labels.append(raw_label)
        output.append({**segment, "cluster": raw_label})
    for item in output:
        item["speaker"] = f"Unknown speaker {ordered_labels.index(item['cluster']) + 1}"
    return output


def recognize_clusters(
    rows: list[dict[str, Any]],
    cluster_embeddings: dict[str, list[float]],
    profiles: list[dict[str, Any]],
    threshold: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    threshold = threshold if threshold is not None else float(os.environ.get("MEETER_SPEAKER_THRESHOLD", "0.72"))
    cluster_names: dict[str, tuple[str, str | None, float | None]] = {}
    unknown_index = 0
    for cluster in dict.fromkeys(row["cluster"] for row in rows):
        embedding = cluster_embeddings.get(cluster)
        scored = [(profile, cosine_similarity(embedding or [], profile.get("embedding", []))) for profile in profiles]
        best_profile, best_score = max(scored, key=lambda pair: pair[1], default=(None, -1.0))
        if best_profile and best_score >= threshold:
            cluster_names[cluster] = (str(best_profile["name"]), str(best_profile.get("id")), round(best_score, 3))
        else:
            unknown_index += 1
            cluster_names[cluster] = (f"Unknown speaker {unknown_index}", None, None)
    participant_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        name, speaker_id, confidence = cluster_names[row["cluster"]]
        row.update({"speaker": name, "speaker_id": speaker_id, "confidence": confidence})
        key = speaker_id or row["cluster"]
        participant_map[key] = {"id": key, "name": name, "known": speaker_id is not None}
    return rows, list(participant_map.values())


class MeetingPipeline:
    def __init__(self, store: LocalStore):
        self.store = store
        self.whisper = WhisperAdapter()
        self.diarizer = DiarizationAdapter()
        self.embedder = EmbeddingAdapter()
        self.llm = LlamaCppAdapter()

    def readiness(self) -> dict[str, Any]:
        keys = {
            "transcription": "MEETER_WHISPER_MODEL",
            "diarization": "MEETER_DIARIZATION_MODEL",
            "recognition": "MEETER_EMBEDDING_MODEL",
            "summarization": "MEETER_LLM_MODEL",
        }
        configured = {name: bool(os.environ.get(env, "").strip()) for name, env in keys.items()}
        return {"ready": configured["transcription"] and configured["diarization"], "components": configured, "network": "loopback-only"}

    def process(self, audio_path: Path, source_name: str, progress: Progress) -> dict[str, Any]:
        progress("Transcribing locally", 12)
        segments, language, duration = self.whisper.transcribe(audio_path)
        progress("Separating speakers", 42)
        diarization = self.diarizer.diarize(audio_path)
        rows = assign_diarization(segments, diarization)
        progress("Recognizing known voices", 61)
        profiles = self.store.load_speakers()
        try:
            embeddings = self.embedder.embed_clusters(audio_path, diarization)
        except ModelNotReady:
            embeddings = {}
        rows, participants = recognize_clusters(rows, embeddings, profiles)
        progress("Writing minutes and actions", 76)
        summary, model_note = summarize(rows, self.llm if self.llm.configured else None)
        meeting = Meeting(
            title=summary["title"],
            duration=duration,
            transcript=[TranscriptTurn(
                start=row["start"], end=row["end"], speaker=row["speaker"], text=row["text"],
                speaker_id=row.get("speaker_id"), confidence=row.get("confidence"),
            ) for row in rows],
            summary=summary["summary"],
            decisions=summary["decisions"],
            actions=[ActionItem(**item) for item in summary["actions"]],
            discussion=summary["discussion"],
            risks=summary["risks"],
            participants=participants,
            source_name=source_name,
            language=language,
            model_note=model_note,
        )
        progress("Saving on this device", 94)
        return self.store.save_meeting(meeting.to_dict())

    def enroll(self, audio_path: Path, name: str) -> dict[str, Any]:
        embedding = self.embedder.embed_file(audio_path)
        profiles = self.store.load_speakers()
        profile_id = "speaker_" + "".join(ch.lower() for ch in name if ch.isalnum())[:24]
        profile = {"id": profile_id, "name": name.strip(), "embedding": embedding}
        profiles = [item for item in profiles if item.get("id") != profile_id]
        profiles.append(profile)
        self.store.save_speakers(profiles)
        return {"id": profile_id, "name": name.strip()}


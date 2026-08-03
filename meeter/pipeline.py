from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .domain import ActionItem, Meeting, TranscriptTurn
from .local_models import DiarizationAdapter, DiarizationTurn, EmbeddingAdapter, LlamaCppAdapter, ModelNotReady, QwenASRAdapter, WhisperAdapter, cosine_similarity
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


def select_voice_snippets(
    diarization: list[DiarizationTurn],
    rows: list[dict[str, Any]],
    participants: list[dict[str, Any]],
    minimum_seconds: float = 1.5,
    maximum_seconds: float = 6.0,
) -> list[dict[str, Any]]:
    """Attach the best short diarization interval for each recognized participant."""
    cluster_keys: dict[str, str] = {}
    for row in rows:
        cluster = str(row.get("cluster", ""))
        if cluster and cluster not in cluster_keys:
            cluster_keys[cluster] = str(row.get("speaker_id") or cluster)

    best_by_participant: dict[str, DiarizationTurn] = {}
    for turn in diarization:
        duration = float(turn.end) - float(turn.start)
        participant_key = cluster_keys.get(turn.label)
        if participant_key is None or duration < minimum_seconds:
            continue
        current = best_by_participant.get(participant_key)
        if current is None or duration > float(current.end) - float(current.start):
            best_by_participant[participant_key] = turn

    output: list[dict[str, Any]] = []
    for participant in participants:
        enriched = dict(participant)
        turn = best_by_participant.get(str(participant.get("id", "")))
        if turn is not None:
            duration = float(turn.end) - float(turn.start)
            snippet_duration = min(duration, maximum_seconds)
            start = float(turn.start) + (duration - snippet_duration) / 2
            enriched["voice_snippet"] = {
                "start_seconds": round(start, 3),
                "end_seconds": round(start + snippet_duration, 3),
            }
        output.append(enriched)
    return output


class MeetingPipeline:
    def __init__(self, store: LocalStore):
        self.store = store
        self.asr_backend = os.environ.get("MEETER_ASR_BACKEND", "whisper").strip().lower()
        self.whisper = WhisperAdapter()
        self.qwen_asr = QwenASRAdapter()
        self.diarizer = DiarizationAdapter()
        self.embedder = EmbeddingAdapter()
        self.llm = LlamaCppAdapter()
        self.batch_llm = LlamaCppAdapter("MEETER_LLM_BATCH_MODEL", max_tokens=900)

    def readiness(self) -> dict[str, Any]:
        keys = {
            "transcription": "MEETER_QWEN_ASR_MODEL" if self.asr_backend == "qwen3" else "MEETER_WHISPER_MODEL",
            "diarization": "MEETER_DIARIZATION_MODEL",
            "recognition": "MEETER_EMBEDDING_MODEL",
            "summarization": "MEETER_LLM_MODEL",
        }
        configured = {name: bool(os.environ.get(env, "").strip()) for name, env in keys.items()}
        return {"ready": configured["transcription"] and configured["diarization"], "components": configured, "network": "loopback-only"}

    def process(self, audio_path: Path, source_name: str, progress: Progress) -> dict[str, Any]:
        if self.asr_backend == "qwen3":
            progress("Separating speakers", 12)
            diarization = self.diarizer.diarize(audio_path)
            progress("Transcribing local speaker batches", 31)
            rows, language, duration = self.qwen_asr.transcribe_turns(audio_path, diarization)
        else:
            progress("Transcribing and separating speakers", 12)
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="meeter-audio") as executor:
                transcription_future = executor.submit(self.whisper.transcribe, audio_path)
                diarization_future = executor.submit(self.diarizer.diarize, audio_path)
                segments, language, duration = transcription_future.result()
                diarization = diarization_future.result()
            rows = assign_diarization(segments, diarization)
        progress("Recognizing known voices", 61)
        profiles = self.store.load_speakers()
        try:
            embeddings = self.embedder.embed_clusters(audio_path, diarization)
        except ModelNotReady:
            embeddings = {}
        rows, participants = recognize_clusters(rows, embeddings, profiles)
        participants = select_voice_snippets(diarization, rows, participants)
        progress("Writing minutes and actions", 76)
        summary, model_note = summarize(
            rows,
            self.llm if self.llm.configured else None,
            self.batch_llm if self.batch_llm.configured else None,
        )
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
        return meeting.to_dict()

    def enroll(self, audio_path: Path, name: str) -> dict[str, Any]:
        embedding = self.embedder.embed_file(audio_path)
        profiles = self.store.load_speakers()
        profile_id = "speaker_" + "".join(ch.lower() for ch in name if ch.isalnum())[:24]
        profile = {"id": profile_id, "name": name.strip(), "embedding": embedding}
        profiles = [item for item in profiles if item.get("id") != profile_id]
        profiles.append(profile)
        self.store.save_speakers(profiles)
        return {"id": profile_id, "name": name.strip()}

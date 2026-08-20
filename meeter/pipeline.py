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


def aggregate_languages(weighted_languages: list[tuple[str, int]]) -> str:
    """Choose a meeting language from evidence volume, not one noisy chunk."""
    weights: dict[str, int] = {}
    for language, weight in weighted_languages:
        normalized = str(language).strip().lower()
        if normalized and normalized != "und":
            weights[normalized] = weights.get(normalized, 0) + max(1, int(weight))
    total = sum(weights.values())
    english = sum(value for key, value in weights.items() if key in {"en", "english"})
    hindi = sum(value for key, value in weights.items() if key in {"hi", "hindi"})
    mixed = sum(value for key, value in weights.items() if key == "hi-en")
    english += mixed // 2
    hindi += mixed - mixed // 2
    if total and english / total >= 0.2 and hindi / total >= 0.2:
        return "hi-en"
    if english:
        return "en"
    if hindi:
        return "hi"
    return max(weights, key=weights.get) if weights else "und"


def cluster_voice_embeddings(
    embeddings: dict[str, list[float]], threshold: float
) -> tuple[dict[str, str], dict[str, list[float]]]:
    """Order-independent meeting-wide agglomerative clustering over voice vectors."""
    groups = [{"members": [key], "vectors": [vector]} for key, vector in embeddings.items() if vector]
    while len(groups) > 1:
        best: tuple[float, int, int] | None = None
        for left in range(len(groups)):
            for right in range(left + 1, len(groups)):
                scores = [
                    cosine_similarity(a, b)
                    for a in groups[left]["vectors"]
                    for b in groups[right]["vectors"]
                ]
                average = sum(scores) / len(scores)
                candidate = (average, left, right)
                if best is None or candidate > best:
                    best = candidate
        if best is None or best[0] < threshold:
            break
        _, left, right = best
        groups[left]["members"].extend(groups[right]["members"])
        groups[left]["vectors"].extend(groups[right]["vectors"])
        groups.pop(right)

    mapping: dict[str, str] = {}
    prototypes: dict[str, list[float]] = {}
    for index, group in enumerate(sorted(groups, key=lambda item: min(item["members"])), 1):
        key = f"voice_{index}"
        for member in group["members"]:
            mapping[member] = key
        vectors = group["vectors"]
        prototypes[key] = [sum(values) / len(vectors) for values in zip(*vectors)]
    return mapping, prototypes


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
    unknown_threshold = float(os.environ.get("MEETER_UNKNOWN_CLUSTER_THRESHOLD", "0.76"))
    cluster_names: dict[str, tuple[str, str | None, float | None, str]] = {}
    unknown_index = 0
    unknown_embeddings = {
        cluster: cluster_embeddings[cluster]
        for cluster in dict.fromkeys(row["cluster"] for row in rows)
        if cluster_embeddings.get(cluster)
    }
    unknown_mapping, _ = cluster_voice_embeddings(unknown_embeddings, unknown_threshold)
    unknown_numbers: dict[str, int] = {}
    for cluster in dict.fromkeys(row["cluster"] for row in rows):
        embedding = cluster_embeddings.get(cluster)
        scored = [(profile, cosine_similarity(embedding or [], profile.get("embedding", []))) for profile in profiles]
        best_profile, best_score = max(scored, key=lambda pair: pair[1], default=(None, -1.0))
        if best_profile and best_score >= threshold:
            profile_id = str(best_profile.get("id"))
            cluster_names[cluster] = (str(best_profile["name"]), profile_id, round(best_score, 3), profile_id)
        else:
            participant_key = unknown_mapping.get(cluster, "")
            if not participant_key:
                unknown_index += 1
                participant_key = f"unknown_{unknown_index}"
            if participant_key not in unknown_numbers:
                unknown_numbers[participant_key] = len(unknown_numbers) + 1
            number = unknown_numbers[participant_key]
            participant_key = f"unknown_{number}"
            cluster_names[cluster] = (f"Unknown speaker {number}", None, None, participant_key)
    participant_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        name, speaker_id, confidence, participant_key = cluster_names[row["cluster"]]
        row.update({"speaker": name, "speaker_id": speaker_id, "confidence": confidence, "participant_key": participant_key})
        key = participant_key
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
            cluster_keys[cluster] = str(row.get("participant_key") or row.get("speaker_id") or cluster)

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


def _deduplicate_chunk_rows(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign each overlap to one chunk using the midpoint between chunk boundaries."""
    ordered = sorted(chunks, key=lambda item: (float(item["start"]), int(item["index"])))
    rows: list[dict[str, Any]] = []
    for index, chunk in enumerate(ordered):
        lower = float("-inf") if index == 0 else (float(ordered[index - 1]["end"]) + float(chunk["start"])) / 2
        upper = float("inf") if index == len(ordered) - 1 else (float(chunk["end"]) + float(ordered[index + 1]["start"])) / 2
        for row in chunk.get("rows", []):
            midpoint = (float(row["start"]) + float(row["end"])) / 2
            if lower <= midpoint < upper:
                rows.append(dict(row))
    return sorted(rows, key=lambda item: (float(item["start"]), float(item["end"])))


def reconcile_chunk_clusters(
    chunks: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    threshold: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[float]]]:
    """Map per-chunk speaker clusters onto stable meeting-wide voice clusters."""
    threshold = threshold if threshold is not None else float(os.environ.get("MEETER_STREAM_CLUSTER_THRESHOLD", "0.76"))
    speaker_threshold = float(os.environ.get("MEETER_SPEAKER_THRESHOLD", "0.72"))
    all_embeddings = {
        str(label): vector for chunk in chunks for label, vector in chunk.get("embeddings", {}).items() if vector
    }
    mapping: dict[str, str] = {}
    prototypes: dict[str, list[float]] = {}
    unmatched: dict[str, list[float]] = {}
    for label, embedding in all_embeddings.items():
        scored = [(profile, cosine_similarity(embedding, profile.get("embedding", []))) for profile in profiles]
        profile, score = max(scored, key=lambda pair: pair[1], default=(None, -1.0))
        if profile is not None and score >= speaker_threshold:
            target = f"known:{profile['id']}"
            mapping[label] = target
            prototypes.setdefault(target, embedding)
        else:
            unmatched[label] = embedding
    unknown_mapping, unknown_prototypes = cluster_voice_embeddings(unmatched, threshold)
    mapping.update(unknown_mapping)
    prototypes.update(unknown_prototypes)
    unknown_index = len(prototypes)

    for chunk in chunks:
        for row in chunk.get("rows", []):
            local_key = str(row.get("cluster", ""))
            if local_key not in mapping:
                unknown_index += 1
                mapping[local_key] = f"stream_{unknown_index}"
            row["cluster"] = mapping[local_key]
    return _deduplicate_chunk_rows(chunks), prototypes


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
            rows, language, duration = self.qwen_asr.transcribe_turns(
                audio_path,
                diarization,
                lambda completed, total: progress(
                    f"Transcribing local speaker batches ({completed}/{total})",
                    31 + round(25 * completed / total),
                ),
            )
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
        return self._build_meeting(rows, participants, diarization, duration, language, source_name, progress)

    def process_stream_chunk(
        self,
        audio_path: Path,
        index: int,
        start_seconds: float,
        end_seconds: float,
    ) -> dict[str, Any]:
        """Perform the expensive audio work while the rest of the meeting is still recording."""
        if self.asr_backend == "qwen3":
            diarization = self.diarizer.diarize(audio_path)
            rows, language, decoded_duration = self.qwen_asr.transcribe_turns(audio_path, diarization)
        else:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="meeter-stream") as executor:
                transcription_future = executor.submit(self.whisper.transcribe, audio_path)
                diarization_future = executor.submit(self.diarizer.diarize, audio_path)
                segments, language, decoded_duration = transcription_future.result()
                diarization = diarization_future.result()
            rows = assign_diarization(segments, diarization)
        try:
            embeddings = self.embedder.embed_clusters(audio_path, diarization)
        except ModelNotReady:
            embeddings = {}

        prefix = f"chunk_{index}:"
        for row in rows:
            row["start"] = float(row["start"]) + start_seconds
            row["end"] = float(row["end"]) + start_seconds
            row["cluster"] = prefix + str(row.get("cluster", "UNKNOWN"))
        return {
            "index": index,
            "start": start_seconds,
            "end": max(end_seconds, start_seconds + decoded_duration),
            "rows": rows,
            "embeddings": {prefix + str(label): vector for label, vector in embeddings.items()},
            "language": language,
        }

    def finalize_stream_chunks(
        self,
        chunks: list[dict[str, Any]],
        source_name: str,
        duration: float,
        progress: Progress,
    ) -> dict[str, Any]:
        progress("Reconciling overlapping batches", 68)
        profiles = self.store.load_speakers()
        rows, embeddings = reconcile_chunk_clusters(chunks, profiles)
        rows, participants = recognize_clusters(rows, embeddings, profiles)
        diarization = [
            DiarizationTurn(float(row["start"]), float(row["end"]), str(row["cluster"])) for row in rows
        ]
        participants = select_voice_snippets(diarization, rows, participants)
        language = aggregate_languages([
            (
                str(chunk.get("language", "und")),
                sum(len(str(row.get("text", "")).split()) for row in chunk.get("rows", [])),
            )
            for chunk in chunks
        ])
        return self._build_meeting(rows, participants, diarization, duration, language, source_name, progress)

    def _build_meeting(
        self,
        rows: list[dict[str, Any]],
        participants: list[dict[str, Any]],
        diarization: list[DiarizationTurn],
        duration: float,
        language: str,
        source_name: str,
        progress: Progress,
    ) -> dict[str, Any]:
        progress("Writing minutes and reconciling actions", 76)
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

from __future__ import annotations

import math
import os
import platform
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ModelNotReady(RuntimeError):
    pass


def _approved_path(env_name: str) -> Path:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        raise ModelNotReady(f"{env_name} is not configured")
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise ModelNotReady(f"Configured local path does not exist: {path}")
    return path


@dataclass(slots=True)
class DiarizationTurn:
    start: float
    end: float
    label: str


class WhisperAdapter:
    def __init__(self) -> None:
        self._model: Any = None

    def transcribe(self, audio_path: Path) -> tuple[list[dict[str, Any]], str, float]:
        model_path = _approved_path("MEETER_WHISPER_MODEL")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ModelNotReady("Install the approved faster-whisper package") from exc
        if self._model is None:
            device = os.environ.get("MEETER_WHISPER_DEVICE", "cpu")
            compute = os.environ.get("MEETER_WHISPER_COMPUTE", "int8" if device == "cpu" else "float16")
            self._model = WhisperModel(str(model_path), device=device, compute_type=compute, local_files_only=True)
        segments, info = self._model.transcribe(
            str(audio_path),
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            word_timestamps=False,
            initial_prompt=os.environ.get(
                "MEETER_WHISPER_PROMPT",
                "This is a business meeting with natural Hindi and English code-switching. "
                "Preserve the script actually spoken, including Devanagari or Romanized Hindi, "
                "English product names, people names, numbers, dates, and technical terms.",
            ),
        )
        rows = [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()} for s in segments if s.text.strip()]
        duration = max((row["end"] for row in rows), default=0.0)
        detected_language = str(info.language or "und")
        if os.environ.get("MEETER_EXPECT_CODE_SWITCHING", "0") == "1" and detected_language in {"hi", "en"}:
            detected_language = "hi-en"
        return rows, detected_language, duration


class DiarizationAdapter:
    def __init__(self) -> None:
        self._pipeline: Any = None

    def diarize(self, audio_path: Path) -> list[DiarizationTurn]:
        model_path = _approved_path("MEETER_DIARIZATION_MODEL")
        if model_path.suffix == ".onnx":
            return self._diarize_sherpa(audio_path, model_path)
        try:
            from pyannote.audio import Pipeline
        except ImportError as exc:
            raise ModelNotReady("Install the approved pyannote.audio package") from exc
        if self._pipeline is None:
            self._pipeline = Pipeline.from_pretrained(str(model_path))
        result = self._pipeline(str(audio_path))
        return [DiarizationTurn(float(turn.start), float(turn.end), str(label)) for turn, _, label in result.itertracks(yield_label=True)]

    def _diarize_sherpa(self, audio_path: Path, model_path: Path) -> list[DiarizationTurn]:
        try:
            import sherpa_onnx
            from faster_whisper.audio import decode_audio
        except ImportError as exc:
            raise ModelNotReady("Install the approved sherpa-onnx and faster-whisper packages") from exc
        embedding_path = _approved_path("MEETER_EMBEDDING_MODEL")
        if self._pipeline is None:
            config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(model_path)),
                ),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(embedding_path),
                    num_threads=int(os.environ.get("MEETER_SHERPA_THREADS", "4")),
                    provider=os.environ.get("MEETER_SHERPA_PROVIDER", "cpu"),
                ),
                clustering=sherpa_onnx.FastClusteringConfig(
                    num_clusters=-1,
                    threshold=float(os.environ.get("MEETER_DIARIZATION_THRESHOLD", "0.5")),
                ),
                min_duration_on=0.3,
                min_duration_off=0.5,
            )
            if not config.validate():
                raise ModelNotReady("The configured sherpa-onnx diarization models are invalid")
            self._pipeline = sherpa_onnx.OfflineSpeakerDiarization(config)
        samples = decode_audio(str(audio_path), sampling_rate=self._pipeline.sample_rate)
        result = self._pipeline.process(samples).sort_by_start_time()
        return [DiarizationTurn(float(turn.start), float(turn.end), f"SPEAKER_{int(turn.speaker):02d}") for turn in result]


class EmbeddingAdapter:
    def __init__(self) -> None:
        self._inference: Any = None

    def _is_sherpa(self) -> bool:
        return _approved_path("MEETER_EMBEDDING_MODEL").suffix == ".onnx"

    def _load(self) -> None:
        if self._inference is not None:
            return
        model_path = _approved_path("MEETER_EMBEDDING_MODEL")
        if model_path.suffix == ".onnx":
            try:
                import sherpa_onnx
            except ImportError as exc:
                raise ModelNotReady("Install the approved sherpa-onnx package") from exc
            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(model_path),
                num_threads=int(os.environ.get("MEETER_SHERPA_THREADS", "4")),
                provider=os.environ.get("MEETER_SHERPA_PROVIDER", "cpu"),
            )
            if not config.validate():
                raise ModelNotReady("The configured speaker embedding model is invalid")
            self._inference = sherpa_onnx.SpeakerEmbeddingExtractor(config)
            return
        try:
            from pyannote.audio import Inference, Model
        except ImportError as exc:
            raise ModelNotReady("Install the approved pyannote.audio package") from exc
        self._inference = Inference(Model.from_pretrained(str(model_path)), window="whole")

    def embed_file(self, audio_path: Path) -> list[float]:
        self._load()
        if self._is_sherpa():
            from faster_whisper.audio import decode_audio

            samples = decode_audio(str(audio_path), sampling_rate=16000)
            return self._sherpa_embedding(samples, 16000)
        vector = self._inference(str(audio_path))
        return [float(value) for value in vector.reshape(-1)]

    def embed_clusters(self, audio_path: Path, turns: list[DiarizationTurn]) -> dict[str, list[float]]:
        self._load()
        if self._is_sherpa():
            return self._sherpa_cluster_embeddings(audio_path, turns)
        try:
            import numpy as np
            from pyannote.core import Segment
        except ImportError as exc:
            raise ModelNotReady("Install approved numpy and pyannote packages") from exc
        grouped: dict[str, list[Any]] = defaultdict(list)
        for turn in turns:
            if turn.end - turn.start < 1.0:
                continue
            vector = self._inference.crop(str(audio_path), Segment(turn.start, turn.end))
            grouped[turn.label].append(vector.reshape(-1))
        return {label: [float(value) for value in np.mean(vectors, axis=0)] for label, vectors in grouped.items() if vectors}

    def _sherpa_embedding(self, samples: Any, sample_rate: int) -> list[float]:
        stream = self._inference.create_stream()
        stream.accept_waveform(sample_rate=sample_rate, waveform=samples)
        stream.input_finished()
        if not self._inference.is_ready(stream):
            raise ModelNotReady("The voice sample is too short for speaker recognition")
        return [float(value) for value in self._inference.compute(stream)]

    def _sherpa_cluster_embeddings(self, audio_path: Path, turns: list[DiarizationTurn]) -> dict[str, list[float]]:
        try:
            import numpy as np
            from faster_whisper.audio import decode_audio
        except ImportError as exc:
            raise ModelNotReady("Install the approved numpy and faster-whisper packages") from exc
        sample_rate = 16000
        samples = decode_audio(str(audio_path), sampling_rate=sample_rate)
        grouped: dict[str, list[Any]] = defaultdict(list)
        for turn in turns:
            start = max(0, int(turn.start * sample_rate))
            end = min(len(samples), int(turn.end * sample_rate))
            if end - start < sample_rate:
                continue
            try:
                grouped[turn.label].append(np.asarray(self._sherpa_embedding(samples[start:end], sample_rate)))
            except ModelNotReady:
                continue
        return {label: [float(value) for value in np.mean(vectors, axis=0)] for label, vectors in grouped.items() if vectors}


class LlamaCppAdapter:
    def __init__(self) -> None:
        self._model: Any = None

    @property
    def configured(self) -> bool:
        return bool(os.environ.get("MEETER_LLM_MODEL", "").strip())

    def __call__(self, prompt: str) -> str:
        model_path = _approved_path("MEETER_LLM_MODEL")
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise ModelNotReady("Install the approved llama-cpp-python package") from exc
        if self._model is None:
            self._model = Llama(
                model_path=str(model_path),
                n_ctx=int(os.environ.get("MEETER_LLM_CONTEXT", "8192")),
                n_threads=max(2, (os.cpu_count() or 4) - 1),
                n_gpu_layers=int(os.environ.get("MEETER_LLM_GPU_LAYERS", "-1" if platform.system() == "Darwin" else "0")),
                n_batch=int(os.environ.get("MEETER_LLM_BATCH", "512")),
                verbose=False,
            )
        string_or_null = {"anyOf": [{"type": "string"}, {"type": "null"}]}
        response_schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "decisions": {"type": "array", "items": {"type": "string"}},
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "owner": string_or_null,
                            "due": string_or_null,
                            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                            "context": {"type": "string"},
                        },
                        "required": ["text", "owner", "due", "priority", "context"],
                    },
                },
                "discussion": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}, "detail": {"type": "string"}},
                        "required": ["title", "detail"],
                    },
                },
                "risks": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "summary", "decisions", "actions", "discussion", "risks"],
        }
        result = self._model.create_chat_completion(
            messages=[{"role": "user", "content": prompt + "\n/no_think"}],
            temperature=0.3,
            top_p=0.8,
            top_k=20,
            presence_penalty=1.0,
            response_format={"type": "json_object", "schema": response_schema},
            max_tokens=1800,
        )
        return str(result["choices"][0]["message"]["content"])


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else -1.0

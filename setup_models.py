#!/usr/bin/env python3
"""Download the explicitly selected local model artifacts during setup.

The application never calls this module at runtime. Network access is confined to
this one-time setup step, and all resulting paths are written to local.env.
"""
from __future__ import annotations

import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

from huggingface_hub import snapshot_download


ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
WHISPER_DIR = MODELS / "faster-whisper-large-v3"
DIARIZATION_DIR = MODELS / "sherpa-onnx-pyannote-segmentation-3-0"
DIARIZATION_MODEL = DIARIZATION_DIR / "model.onnx"
EMBEDDING_MODEL = MODELS / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
LLM_MODEL = MODELS / "Qwen3-4B-Q4_K_M.gguf"

SEGMENTATION_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMBEDDING_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
LLM_URL = "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf?download=true"


def download(url: str, target: Path) -> None:
    if target.is_file() and target.stat().st_size > 1024:
        print(f"✓ {target.name} already present")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    print(f"↓ {target.name}")
    request = urllib.request.Request(url, headers={"User-Agent": "Meeter local setup/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as source, partial.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def install_segmentation() -> None:
    if DIARIZATION_MODEL.is_file():
        print("✓ Diarization model already present")
        return
    archive = MODELS / "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
    download(SEGMENTATION_URL, archive)
    print("↳ Extracting diarization model")
    with tarfile.open(archive, "r:bz2") as bundle:
        bundle.extractall(MODELS, filter="data")
    archive.unlink(missing_ok=True)
    if not DIARIZATION_MODEL.is_file():
        raise RuntimeError("Diarization archive did not contain the expected model.onnx")


def install_huggingface_models() -> None:
    if not (WHISPER_DIR / "model.bin").is_file():
        print("↓ Whisper large-v3 (about 3.1 GB)")
        snapshot_download(
            repo_id="Systran/faster-whisper-large-v3",
            local_dir=WHISPER_DIR,
            allow_patterns=["config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"],
        )
    else:
        print("✓ Whisper model already present")

    if not LLM_MODEL.is_file():
        print("↓ Qwen3 4B Q4_K_M (about 2.5 GB, Apache 2.0)")
        download(LLM_URL, LLM_MODEL)
    else:
        print("✓ Local summary model already present")


def write_environment() -> None:
    values = {
        "MEETER_WHISPER_MODEL": WHISPER_DIR,
        "MEETER_WHISPER_DEVICE": "cpu",
        "MEETER_WHISPER_COMPUTE": "int8",
        "MEETER_EXPECT_CODE_SWITCHING": "1",
        "MEETER_DIARIZATION_MODEL": DIARIZATION_MODEL,
        "MEETER_EMBEDDING_MODEL": EMBEDDING_MODEL,
        "MEETER_LLM_MODEL": LLM_MODEL,
        "MEETER_LLM_CONTEXT": "8192",
        "MEETER_LLM_GPU_LAYERS": "-1",
        "MEETER_SHERPA_THREADS": "4",
        "MEETER_KEEP_AUDIO": "0",
    }
    lines = [f'export {key}="{str(value).replace(chr(34), chr(92) + chr(34))}"' for key, value in values.items()]
    (ROOT / "local.env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("✓ Wrote local.env")


def main() -> None:
    os.environ.setdefault("HF_HOME", str(MODELS / ".hf-cache"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    MODELS.mkdir(parents=True, exist_ok=True)
    install_segmentation()
    download(EMBEDDING_URL, EMBEDDING_MODEL)
    install_huggingface_models()
    write_environment()
    print("\nLocal model setup complete.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSetup interrupted; rerun the command to resume.", file=sys.stderr)
        raise SystemExit(130)

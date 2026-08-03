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
QWEN_ASR_DIR = MODELS / "Qwen3-ASR-0.6B"
DIARIZATION_DIR = MODELS / "sherpa-onnx-pyannote-segmentation-3-0"
DIARIZATION_MODEL = DIARIZATION_DIR / "model.onnx"
EMBEDDING_MODEL = MODELS / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
LLM_MODEL = MODELS / "Qwen3-1.7B-Q4_K_M.gguf"

SEGMENTATION_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMBEDDING_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
LLM_URL = "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf?download=true"


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
    if not (QWEN_ASR_DIR / "model.safetensors").is_file():
        print("↓ Qwen3-ASR 0.6B (about 1.9 GB)")
        snapshot_download(
            repo_id="Qwen/Qwen3-ASR-0.6B",
            local_dir=QWEN_ASR_DIR,
            allow_patterns=["*.json", "*.txt", "*.safetensors", "README.md", "LICENSE*"],
        )
    else:
        print("✓ Qwen3-ASR model already present")

    if not LLM_MODEL.is_file():
        print("↓ Qwen3 1.7B Q4_K_M (about 1.3 GB, Apache 2.0)")
        download(LLM_URL, LLM_MODEL)
    else:
        print("✓ Local reconciliation model already present")



def write_environment() -> None:
    values = {
        "MEETER_ASR_BACKEND": "qwen3",
        "MEETER_QWEN_ASR_MODEL": QWEN_ASR_DIR,
        "MEETER_ASR_BATCH_SIZE": "8",
        "MEETER_DIARIZATION_MODEL": DIARIZATION_MODEL,
        "MEETER_EMBEDDING_MODEL": EMBEDDING_MODEL,
        "MEETER_LLM_MODEL": LLM_MODEL,
        "MEETER_LLM_CONTEXT": "8192",
        "MEETER_LLM_GPU_LAYERS": "-1",
        "MEETER_SUMMARY_BATCH_CHARS": "4000",
        "MEETER_SUMMARY_BATCH_SECONDS": "300",
        "MEETER_EXTRACTIVE_RECONCILIATION": "1",
        "MEETER_SUMMARY_RECONCILE_MIN_CHARS": "18000",
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

# Meeter

Meeter is a privacy-first meeting notes app that runs on `127.0.0.1` and keeps audio, transcripts, speaker profiles, and summaries on the laptop.

It ships with a polished zero-dependency demo mode. For real audio processing, point it at local model files and install the optional Python packages described below. Meeter never downloads a model at runtime.

## What it does

- Records from the browser or imports common audio/video files.
- Transcribes with a local Whisper model through `faster-whisper`.
- Handles Hindi–English code-switching, including Devanagari and Romanized Hindi, while preserving English names and technical terms.
- Diarizes with local sherpa-onnx models, recognizes enrolled voices, and labels every unmatched voice as a stable unknown speaker.
- Produces decisions, risks, discussion notes, and actionable tasks using a local GGUF model through `llama-cpp-python` (with a deterministic offline fallback).
- Copies individual actions, downloads calendar events, and prepares an Asana-safe clipboard payload without silently sending meeting data off-device.
- Stores data locally and binds only to the loopback interface.

## Installed quick start

```bash
./run-meeter.command
```

Open <http://127.0.0.1:4317>. Choose **Explore demo** to see the complete experience without installing models.

On a new laptop, run `./setup-local.command` once. It creates an isolated Python 3.12 runtime and downloads only the pinned local models; runtime launches are forced into offline mode.

Run the checks with:

```bash
python3 -m unittest discover -s tests -v
```

## Manual or managed model setup

Meeter intentionally refuses to fetch models. Download and approve model files through your company's normal software-distribution process, then configure absolute local paths:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-local-models.txt

export MEETER_WHISPER_MODEL="/approved-models/faster-whisper-large-v3"
export MEETER_DIARIZATION_MODEL="/approved-models/sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
export MEETER_EMBEDDING_MODEL="/approved-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
export MEETER_LLM_MODEL="/approved-models/Qwen3-4B-Q4_K_M.gguf"
python3 server.py
```

Suggested hardware profiles:

| Laptop | Speech model | Local LLM |
| --- | --- | --- |
| 16 GB RAM, CPU only | distil-large-v3 or small.en | 3B Q4 GGUF |
| Apple Silicon, 16–32 GB | large-v3 | Qwen3 4B Q4 GGUF |
| 32 GB+ / approved GPU | large-v3 | 8B–14B Q4 GGUF |

Python 3.12 is installed inside the project. PyAV handles common audio/video formats without a separate ffmpeg installation.

## Privacy boundary

The server rejects non-loopback clients, sets a restrictive content security policy, and does not contain analytics, telemetry, remote fonts, CDNs, or network model identifiers. Calendar actions are generated as local `.ics` files. The Asana action copies a structured task payload; opening/pasting it into Asana is an explicit user action because sending data directly to Asana would contradict the local-only guarantee.

Data defaults to the operating system's application-data directory. Override it for managed deployments:

```bash
export MEETER_DATA_DIR="/path/controlled/by/IT"
```

Imported audio is deleted after processing by default; only the transcript and generated notes remain. Set `MEETER_KEEP_AUDIO=1` only if your retention policy explicitly permits keeping source recordings.

For stronger protection, place that directory on an encrypted, access-controlled volume. OS full-disk encryption and managed browser microphone policies remain the responsibility of device management.

## Project layout

- `server.py` — loopback-only HTTP server and asynchronous job endpoints.
- `meeter/pipeline.py` — local transcription, diarization, recognition, and summary orchestration.
- `meeter/local_models.py` — lazy adapters for optional local model runtimes.
- `meeter/summarizer.py` — prompt/schema validation and offline fallback.
- `web/` — dependency-free responsive UI.
- `tests/` — storage, summary, export, and API-contract checks.

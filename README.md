# Meeter

Meeter is a privacy-first meeting notes app that runs on `127.0.0.1` and keeps audio, transcripts, speaker profiles, and summaries on the laptop.

It ships with a polished zero-dependency demo mode. For real audio processing, point it at local model files and install the optional Python packages described below. Meeter never downloads a model at runtime.

## What it does

- Records microphone and shared device/tab audio from the browser, with independent live source toggles, or imports common audio/video files.
- Processes live recordings in overlapping 75-second batches while capture continues; stopping normally requires only reconciliation, with automatic full-recording fallback if a batch fails.
- Transcribes diarized speaker batches with local Qwen3-ASR-1.7B on Apple Silicon or CUDA. A `faster-whisper` backend remains available for managed legacy installs.
- Handles Hindi–English code-switching, including Devanagari and Romanized Hindi, while preserving English names and technical terms.
- Diarizes with local sherpa-onnx models, recognizes enrolled voices, and labels every unmatched voice as a stable unknown speaker.
- Produces minutes and actions with Qwen3-1.7B Q8_0 through `llama-cpp-python`; long transcripts are first reduced into bounded deterministic evidence batches and later reconciled (with a fully deterministic offline fallback).
- Copies individual actions or complete action lists and downloads calendar events without sending meeting data off-device.
- Stores data locally and binds only to the loopback interface.

Original meeting audio is retained locally by default so transcript timestamps can play it back. Turn this off under **Settings → Keep meeting audio** for future meetings. Administrators can manage the setting with `MEETER_KEEP_AUDIO=1` or `MEETER_KEEP_AUDIO=0`; when present, the environment value disables the in-app toggle.

Device audio availability depends on the browser and operating system. When it is enabled, choose a browser tab or screen in the share picker and turn on its **Share audio** option. Tab audio is the most consistently supported choice.

Live batching uses independent overlapping `MediaRecorder` instances because browser `timeslice` fragments are not guaranteed to be independently decodable. A continuous recorder is kept as the retained playback source and safety fallback. Model inference is intentionally serialized—recording and inference run in parallel, but multiple local model calls do not compete for the same GPU memory. Job status is persisted under the local data directory, so a disconnected browser poll does not drop work and an app restart reports an interrupted job explicitly.

On Apple Silicon, Qwen ASR automatically uses PyTorch MPS and the summary model uses llama.cpp Metal offload. MPS inference is intentionally serialized: concurrent generations contend for the same unified memory and normally run slower. CPU diarization, browser capture, uploads, and storage continue independently. The default MPS ASR batch is two duration-sorted turns to avoid padding every turn in a batch to its longest clip; tune `MEETER_ASR_BATCH_SIZE` only after measuring your machine.

To compare MPS batch sizes without changing models or decoding settings, source `local.env` and run `./.venv/bin/python benchmark_asr_batches.py /path/to/representative-audio.webm`. It reports elapsed time, real-time factor, turn count, and word count for sizes 2, 3, and 4.

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

## Read-only MCP server

Meeter includes a local MCP server for sharing approved meeting context with MCP-compatible
AI clients. By default it starts with Meeter at `http://127.0.0.1:4318/mcp` using Streamable HTTP, reads the same local meeting files, and has no tools that
create, update, or delete data.

Install the stable MCP SDK:

```bash
uv pip install --python .venv/bin/python -r requirements-mcp.txt
```

If you do not use Meeter's project environment, install it with
`python3 -m pip install -r requirements-mcp.txt` instead. The launcher selects an installed
environment automatically.

Open **Settings → AI agent access (MCP)** for copyable setup instructions for Codex, Claude Code,
Cursor, and Gemini CLI. Meeter must remain open, and cloud-hosted agents cannot connect to a
laptop's localhost address. The service can be disabled there at any time.

For clients that require stdio, configure the fallback launcher:

```bash
/absolute/path/to/meeter/run-meeter-mcp.command
```

The default `insights` privacy level exposes only meeting metadata, summaries, decisions,
risks, discussions, and actions. It never exposes transcripts, audio, source filenames, voice
profiles, speaker embeddings, or model metadata. Email addresses and phone numbers are
redacted by default.

Privacy is fixed when the process starts, so an AI cannot elevate its own access:

```bash
# Allow query-matched transcript turns, but never a full transcript
./run-meeter-mcp.command --privacy excerpts

# Explicitly allow full text transcripts
./run-meeter-mcp.command --privacy full

# Limit the server to selected meetings
./run-meeter-mcp.command \
  --allowed-meetings meeting_abc123,meeting_def456
```

Equivalent environment variables are `MEETER_MCP_PRIVACY`, `MEETER_MCP_REDACT_PII`, and
`MEETER_MCP_ALLOWED_MEETINGS`. `MEETER_DATA_DIR` continues to select Meeter's data directory.
Do not disable PII redaction or enable transcript access unless the connected client is trusted
to receive that information.

Available tools in the default mode:

- `list_meetings` — browse permitted meetings by title, participant, or date.
- `get_meeting_insights` — retrieve structured insights without transcript text.
- `search_meetings` — search permitted insight fields.
- `get_action_items` — collect action items, optionally filtered by owner.

The `excerpts` mode adds `get_transcript_excerpts`, which requires a search phrase and returns
only matching turns. The `full` mode additionally adds `get_meeting_transcript`.

## Manual or managed model setup

Meeter intentionally refuses to fetch models. Download and approve model files through your company's normal software-distribution process, then configure absolute local paths:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-local-models.txt

export MEETER_ASR_BACKEND="qwen3"
export MEETER_QWEN_ASR_MODEL="/approved-models/Qwen3-ASR-1.7B"
export MEETER_DIARIZATION_MODEL="/approved-models/sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
export MEETER_DIARIZATION_THRESHOLD="1.15"
export MEETER_EMBEDDING_MODEL="/approved-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
export MEETER_LLM_MODEL="/approved-models/Qwen3-1.7B-Q8_0.gguf"
python3 server.py
```

Suggested hardware profiles:

| Laptop | Speech model | Local summary models |
| --- | --- | --- |
| 16 GB RAM, CPU only | faster-whisper distil-large-v3 | Qwen3 1.7B reconciliation |
| Apple Silicon, 16–32 GB | Qwen3-ASR-1.7B | Qwen3 1.7B reconciliation |
| 32 GB+ / approved GPU | Qwen3-ASR-1.7B | 1.7B batches + 4B reconciliation |

Python 3.12 is installed inside the project. PyAV handles common audio/video formats without a separate ffmpeg installation.

## Privacy boundary

The server rejects non-loopback clients, sets a restrictive content security policy, and does not contain analytics, telemetry, remote fonts, CDNs, or network model identifiers. Calendar actions are generated as local `.ics` files. Clipboard actions remain explicit and local so meeting data is never silently sent to another service.

Data defaults to the operating system's application-data directory. Override it for managed deployments:

```bash
export MEETER_DATA_DIR="/path/controlled/by/IT"
```

Original meeting audio is retained after successful processing by default to support local playback. Disable **Settings → Keep meeting audio** before recording or importing when the source should be deleted after processing. Managed deployments can enforce either behavior with `MEETER_KEEP_AUDIO=1` or `MEETER_KEEP_AUDIO=0`.

For stronger protection, place that directory on an encrypted, access-controlled volume. OS full-disk encryption and managed browser microphone policies remain the responsibility of device management.

## Project layout

- `server.py` — loopback-only HTTP server and asynchronous job endpoints.
- `meeter/pipeline.py` — local transcription, diarization, recognition, and summary orchestration.
- `meeter/local_models.py` — lazy Qwen3-ASR, Whisper, speaker, and GGUF adapters.
- `meeter/summarizer.py` — prompt/schema validation and offline fallback.
- `web/` — dependency-free responsive UI.
- `tests/` — storage, summary, export, and API-contract checks.

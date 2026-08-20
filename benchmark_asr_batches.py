#!/usr/bin/env python3
"""Measure quality-neutral Qwen ASR batch-size choices on one local audio clip."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from meeter.local_models import DiarizationAdapter, QwenASRAdapter, decode_audio_samples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--sizes", default="2,3,4")
    parser.add_argument("--seconds", type=int, default=180, help="Benchmark only this leading audio window")
    args = parser.parse_args()
    sizes = [int(value) for value in args.sizes.split(",")]
    samples = decode_audio_samples(args.audio)[: args.seconds * 16000]
    diarization = DiarizationAdapter().diarize(args.audio, samples)
    results = []
    # Load once at the largest capacity so checkpoint and filesystem caching do
    # not make later candidates appear artificially faster.
    os.environ["MEETER_ASR_BATCH_SIZE"] = str(max(sizes))
    adapter = QwenASRAdapter()
    adapter._load()
    for size in sizes:
        os.environ["MEETER_ASR_BATCH_SIZE"] = str(size)
        started = time.perf_counter()
        rows, language, duration = adapter.transcribe_turns(args.audio, diarization, samples=samples)
        elapsed = time.perf_counter() - started
        results.append({
            "batch_size": size,
            "seconds": round(elapsed, 3),
            "audio_seconds": round(duration, 3),
            "realtime_factor": round(elapsed / duration, 3),
            "turns": len(rows),
            "words": sum(len(row["text"].split()) for row in rows),
            "language": language,
        })
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

# Local model notices

Meeter's setup script downloads model weights only during the explicit setup step. Review these upstream terms before distributing the resulting `models/` directory through company device management.

| Component | Upstream | Stated license |
| --- | --- | --- |
| Speech transcription | [Systran/faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3) | MIT |
| MoM generation | [Qwen/Qwen3-4B-GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF) | Apache 2.0 |
| Diarization runtime | [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Apache 2.0 |
| Segmentation and embedding weights | [sherpa-onnx speaker models](https://github.com/k2-fsa/sherpa-onnx/releases) | Per-model terms included or linked by upstream |

These notices are informational and are not legal advice. The setup keeps upstream license files when they are included in model archives.

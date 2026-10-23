# Supertonic inference helper

`supertonic_helper.py` is the upstream Python ONNX helper downloaded on 2026-10-22:
https://raw.githubusercontent.com/supertone-oss-archive/supertonic/main/py/helper.py

Code license: MIT, preserved in `SUPERTONIC_LICENSE`.
Weights use OpenRAIL-M; their license is downloaded to `models/supertonic-3/LICENSE`.
The adapter supplies CPU-only ONNX sessions with four worker threads. This helper
does not download anything or access a network. The upstream repository is archived.

Local change: crop each vocoder waveform to its own duration before joining chunks,
then report the actual joined length. This prevents padded earlier chunks from
displacing and truncating the final syllables of a long reply. A regression lives
in `tests/test_supertonic_tts.py`.

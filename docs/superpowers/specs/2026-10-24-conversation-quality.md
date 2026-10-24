# Conversation quality upgrade

Implement the six improvements approved in chat, in order. Per-server profiles are excluded.

1. Local diagnostics: recognized speech, deterministic routing reason, answer, timings and the exact memory evidence passed to the model. No hidden reasoning. Bounded recent records, redact secrets.
2. Hardware tuning: compare installed local models/settings using the same synthetic questions, measure the full text-to-first-PCM path with resident STT, recommend but never silently apply. Keep test results and support cancellation. No universal intelligence score.
3. Memory revisions: preserve author, date and previous values for changed personal facts; current recall uses current values. Provide an attributed recall check. Deletion removes revisions too.
4. Feedback: correct a selected recognized question or answer; store a local regression case and explicit corrected example. Run saved cases locally before accepting a trained candidate. No invented learned weights.
5. Turn-taking: keep named addressing, let only the current speaker continue a related topic; wait for the speaker to finish and cancel speech on explicit hush. Do not enable answering all speech.
6. Voice errands: visible stages and truthful outcome, category-aware disambiguation and deterministic commands. Delivery means completed playback, not just generation. Cancellation never reports success.

Use current Python/PySide/Ollama architecture; no new cloud access. Preserve models, protected token, persona and working databases. Changes remain compatible with existing SQLite data. Private diagnostics/feedback never enter public source/release. Validate each subsystem then build/release one update.

Approach: extend existing bounded session state, file IPC and SQLite stores rather than introducing a second service. This keeps installation unchanged; measurements remain hardware/load dependent.

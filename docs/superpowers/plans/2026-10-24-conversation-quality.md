# Conversation quality implementation plan

> **For agentic workers:** Use superpowers:executing-plans sequentially. Steps use checkbox syntax.

**Goal:** Deliver six approved conversation and desktop improvements without per-server profiles.

**Architecture:** Extend current session diagnostics and desktop IPC, add local tuning/feedback helpers, migrate memory revisions. Keep deterministic voice command execution.

**Tech Stack:** Python 3.11, unittest, PySide6, SQLite, Ollama, Whisper, Supertonic.

**Spec:** docs/superpowers/specs/2026-10-24-conversation-quality.md

## Global constraints
- Local inference, no new cloud access; preserve personal data.
- Keep named addressing and immediate hush; never expose hidden model reasoning.
- Implement tasks 1–6 in order; task 7 excluded.

## Review focus
- Stale diagnostics must not create a correction for a different speaker/turn (task 4).
- Autotuning cancellation must release local models/resources and retain original config (task 2).
- Forget/delete must remove revisions and prevent cached resurrection (task 3).
- Another speaker or explicit third-party address must not inherit the followup window (task 5).
- Partial/canceled playback and failed return must never report a completed errand (task 6).

### Task 1: Recent turn diagnostics
Files: bot/diagnostics.py, bot/session.py, bot/desktop_control.py, desktop/views.py, desktop/app.py, tests/test_turn_diagnostics.py, tests_desktop/test_window.py.
Interface: TurnJournal.heard(...)->id, update(id,**fields), snapshot()->list; state sessions[gid].turns.
- [x] RED: tests for bounds/redaction/evidence and actual response routing.
- [x] Implement journal and desktop selected-turn details; only evidence present in final prompt.
- [x] GREEN: backend diagnostics + Qt tests; commit.

### Task 2: Local hardware tuning
Files: desktop/tuning.py, desktop/views.py, desktop/app.py, tests/test_tuning.py.
Interface: run_tuning(root,candidates,cancel,progress)->report, choose_recommendation(rows)->row|None.
- [x] RED: recommendation gates quality before latency, canceled/failed runs excluded, config unchanged.
- [x] Implement isolated resident-STT text-to-first-PCM worker and cancellable comparison; explicit apply button.
- [x] GREEN: tests and one real local candidate measurement; commit.

### Task 3: Memory revisions and recall
Files: bot/memory.py, desktop/storage.py, bot/desktop_control.py, desktop/views.py, desktop/app.py, tests/test_memory_revisions.py.
Interface: note_history migration, history(guild,record_id), recall_check(guild,uid,question)->data.
- [x] RED: changed occupation has current/previous values with authors/dates; deletion purges history.
- [x] Implement revision migration/write and UI inspect/check action, no generated proof claims.
- [x] GREEN: legacy and new memory tests; commit.

### Task 4: Corrective feedback and regressions
Files: bot/feedback.py, bot/session.py, bot/desktop_control.py, desktop/protocol.py, desktop/app.py, desktop/views.py, tools/run_learning_cycle.py, tests/test_feedback.py.
Interface: FeedbackStore.add(guild,turn,question,answer), cases(guild); feedback_add IPC validates live turn ID.
- [x] RED: immutable source attribution, empty/stale/cross-server cases rejected, private facts excluded from weight evaluation.
- [x] Implement correction dialog/actions, local persisted cases and bounded runtime regression command; candidate acceptance checks those cases.
- [x] GREEN: feedback/candidate and UI tests; commit.

### Task 5: Conversation turn-taking
Files: bot/conversation.py, bot/session.py, bot/config.py, desktop/views.py, desktop/app.py, tests/test_turn_taking.py.
- [x] RED: only owner continues related topic, another addressee rejected; fragmented speech buffered with bounded wait; hush cancels pending output.
- [x] Implement explicit followup setting and pending speaker completion gate without changing rare participation frequency.
- [x] GREEN: routing/control/recovery tests; commit.

### Task 6: Reliable voice errand progress
Files: bot/voice_routes.py, bot/desktop_control.py, desktop/views.py, desktop/app.py, tests/test_voice_routes.py.
Interface: VoiceRoutes.status(guild)->dict bounded stage history, owner and outcome.
- [x] RED: move failure, incomplete playback, cancellation, return failure never mark success.
- [x] Implement stage transitions and desktop progress display, preserve disambiguation.
- [x] GREEN: errands + full backend/Qt suite; commit.

### Release
- [x] Review full diff, privacy allowlist/manifest, runtime and visual QA.
- [x] Build next version, publish source + EXE + SHA, install and validate when desktop control available.

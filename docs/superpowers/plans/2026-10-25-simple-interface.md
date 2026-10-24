# Simple interface and bot name

User clarification: the broken change is the bot's name, not the color theme. Keep current visual palette; fix identity and everyday usability.

Design: one visible name field on Home saves bot_name and wake_word together, then restarts an already-running bot in its current voice channel. The persona file stays intact; current identity is explicitly injected into runtime prompts. Server nickname follows configured name; global account username is not edited automatically. Unknown Vosk names use full Whisper confirmation. Existing Sobesednik/Serёga compatibility stays for default names; a custom name addresses only that chosen bot.

Simplify Home to launch controls, name and channel selection, shortcuts. Put installation and file paths in an expandable maintenance block. Split bot settings into Voice, Model and Conversation tabs; keep Save fixed. Fold advanced comparison/training/context details without removing functions.

- [ ] RED: name/wake saved together, persona unchanged, current identity in prompt, custom wake gate fallback, channel retained on restart.
- [ ] Implement identity validation, runtime prompt/wake support, visible Home form and explicit save.
- [ ] RED: Home contains channel/name controls, installed maintenance collapsed, settings tabs and Save visible.
- [ ] Implement progressive disclosure, move channel choice to Home, simplify labels and settings grouping.
- [ ] Run backend/Qt regressions, render control frames, fresh critical review.
- [ ] Build and publish next patch, install, verify private data preservation and real native UI.

Constraints: preserve stored models/memories/persona/token and chosen bot name; no automatic renaming to a test value. No color theme feature requested. No per-server profiles. Reuse existing checkout/release workflow already authorized.

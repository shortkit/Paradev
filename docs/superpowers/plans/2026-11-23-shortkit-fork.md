# ShortKit Paradev fork implementation plan

> **For agentic workers:** Execute the authorized workflow using Paradev CLI Codex terminal sessions. Keep this plan and evidence current; never claim a packaged update was tested from unit tests alone.

**Goal:** Develop and update a separately installed fork from within itself while terminal agents keep running.

**Architecture:** A production-mode fork with isolated identity and state, plus an external local build/install helper and retained compatible recovery build. Live verification occurs with CLI-launched Codex terminals in the fork.

**Tech Stack:** Bun 1.4.3, Electron 44, electron-builder 26, Python standard library for local process and bundle operations where appropriate.

**Spec:** ../specs/2026-11-23-shortkit-fork-design.md

## Global Constraints

- Repository shortkit/Paradev; upstream ParadevAI/Paradev; base desktop-v2.0.11.
- App Paradev ShortKit; bundle id dev.shortkit.paradev; URL scheme superset-shortkit; app home ~/.paradev-shortkit.
- Existing installed official app and sessions must remain intact.
- No production cloud database changes, no authentication bypass, no credentials in source or logs.
- Keep build/install processes independent of the app being replaced.

## Review Focus

- Invalid or incomplete candidate must fail before quitting or replacing the app.
- Concurrent installer calls must serialize; a second operation cannot race the bundle swap.
- Ordinary quit must preserve daemon and agent PIDs; no process-group kill.
- Fork profile, URL scheme and CLI must not address the official host by accident.
- A rollback must not run an old binary against incompatible migrated databases.

## Task 1: Isolated packaged fork

- [x] Create separate fork build configuration and reproducible command, preserving upstream defaults.
- [x] Disable official update feed only in the fork build.
- [x] Ensure home, profile, protocol, CLI routing and agent provisioning are isolated.
- [x] Add appropriate focused tests for fork-specific logic; verify failures then passes.
- [ ] Build an arm64 app and inspect packaged identity, runtime dependencies and update behavior.
- [ ] Install and launch build A; verify real hosted sign-in and separate host.

## Task 2: Local update helper

- [x] Implement prepare, apply and compatible rollback with build identity metadata.
- [x] Test invalid candidate rejection, install lock, exact app targeting and migration compatibility before use.
- [x] Stage candidate fully before graceful app exit, retain backup, and relaunch with health validation.
- [x] Document commands and blocked cases (active chats, browser-dependent work, incompatible schema).

## Task 3: Live self-development acceptance

- [ ] Register shortkit/Paradev in the fork using its CLI, and create a separate feature worktree.
- [ ] Launch a Codex agent through that CLI to make a small visible change and commit it.
- [ ] Build B from the committed change, then start a second Codex session and a heartbeat command in fork terminals.
- [ ] Record terminal IDs, PIDs, continuous output and build A screenshot.
- [ ] Apply B while test terminals run; verify same live processes, reattachment, changed UI, and agent follow-up.
- [ ] Reject an invalid candidate without affecting running app; exercise compatible app recovery and reapply B.
- [ ] Save concise verification evidence and a repeatable user workflow; commit and push fork branch only after review.

## Progress

Fork created and transferred to shortkit organization. Clone /Users/michaelseleman/paradev-fork. Origin points to shortkit/Paradev; upstream points to ParadevAI/Paradev. Bootstrap workspace created through the installed Paradev CLI, id e1f4d1c3-d08e-4ed3-ad5f-9e8bcf8932d1. Toolchain Bun 1.4.3 installed privately under ~/.local/share/paradev-fork/toolchain; global Bun unchanged.

Fork code and fixture coverage are in this worktree. The build, apply, and recovery commands are documented in `scripts/shortkit/README.md`. Packaged verification and live acceptance belong to the coordinator.

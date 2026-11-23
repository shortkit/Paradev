# Local update verification — 2026-11-23

The tested implementation is commit `d999b39b4da7e835194d9721a0da6653cf0fd9a8`, based on `desktop-v2.0.11`. The installed app uses that commit. Subsequent documentation commits do not change the installed executable.

## Verified on macOS arm64

- Built and signed the separate `Paradev ShortKit.app` locally; verified its bundle identity, packaged resources and native modules.
- Signed in to the hosted service and authenticated the fork CLI through the normal login flows.
- Registered `shortkit/Paradev` as a separate project and created a feature worktree using the installed fork CLI.
- Launched Codex through that CLI. It changed the visible build label from A to `ShortKit · B — live edit`, committed the change, and integrated the reviewed update fixes.
- Prepared the final B bundle and invoked the installer from terminals inside the installed fork.
- A second Codex session actively monitored the update. Its subprocess observed A, a temporary desktop outage, and B ready; that same session then successfully called the fork CLI. The editing Codex session also responded after the update.
- Both Codex processes, the detached terminal daemon and a one-second heartbeat retained their original PIDs throughout update, rollback and reapplication. The official Superset app, host and daemon also retained their original PIDs.

| Operation | Result | Total helper time | Largest heartbeat interval |
| --- | --- | --- | --- |
| Apply B over A | Passed | 22.419 s | 1.025 s |
| Reapply the same B | Passed; desktop and host did not restart; prior A retained | 2.228 s | 1.005 s |
| Roll back to A | Passed | 17.123 s | 1.007 s |
| Apply B again | Passed | 20.407 s | 1.005 s |

Heartbeat sequence numbers were contiguous across each operation. Total helper time includes verification and staging; it is not the UI outage duration. The first monitoring run observed about eight seconds between its last ready A sample and first ready B sample.

An invalid candidate and a held installer lock were also rejected against the live installation without terminating it. All 28 installer tests passed. Focused renderer/window tests passed (13 tests). Earlier scoped provisioning, runtime and desktop checks are recorded in the local evidence. A repository-wide compiled translation ID check was not completed because the filtered dependency installation omitted Next.js SWC; strict translation compilation and the packaged desktop build passed.

## Remaining verification and limits

Visual inspection of the running app is pending. Native desktop automation was unavailable, and production intentionally blocks remote debugging. No screenshot or visual UI acceptance is claimed, and that protection was not removed.

After the first successful B startup, one later health read reported `ready:false`. Opening the workspace through the fork CLI restored `ready:true` with the same desktop and host PIDs. Logs did not retain the selected window, route or layout state at the false sample, so its exact trigger is unknown. No speculative correction was made. The subsequent same-build, rollback and reapply checks passed. If the app is running without an acknowledged usable window, open its workspace before retrying a health check.

The desktop UI restarts during replacement. Terminal continuity was verified for these compatible builds; arbitrary future protocol or database changes cannot be assumed compatible. The helper checks those boundaries. Active built-in chats block replacement, and browser-dependent work must be idle. Remote relay access is disabled in this local fork. Builds are locally ad-hoc signed, not notarized releases.

Raw machine-local evidence is retained in `~/.local/share/paradev-fork/evidence/2026-11-23/`, including build logs, installer results, process baselines, review notes and `live-update-results.json`. Credentials and raw terminal transcripts are not published in this repository.

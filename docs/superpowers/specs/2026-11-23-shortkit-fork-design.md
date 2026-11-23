# ShortKit Paradev fork

The user approved implementing and verifying the self-development workflow discussed in this conversation, with Codex terminal agents launched through the Paradev CLI. The repository belongs to shortkit/Paradev, independent of shortkit/shortkit.

## Approach

Start from desktop-v2.0.11. Keep upstream history and a clean main. Maintain fork changes on shortkit/stable. Install a distinct production-mode macOS arm64 app, Paradev ShortKit, bundle id dev.shortkit.paradev, with its own persistent app home and Electron profile. Register its own superset-shortkit URL scheme. Use real hosted sign-in, without bypassing authentication or entitlement checks.

Use a local build and installation helper initially. Native signed update feeds require a Developer ID certificate not currently available here; a local helper is sufficient for this single-machine workflow. Disable the upstream automatic updater for this fork so it cannot replace the fork with an official build. Preserve upstream behavior for non-fork builds.

Build immutable, identified artifacts before touching the installed app. Gracefully quit only the fork, atomically swap a fully staged valid bundle, reopen, and check its health and build identity. Never terminate terminal daemons or use Quit Completely. Retain a previous compatible app for recovery. Refuse active built-in chats; browser automation must be idle. No claim of universal zero downtime. Database-incompatible downgrades are unsupported and must be refused.

Do not mutate the existing ShortKit repository or restart the installed official app. Development worktrees and packaged fork state must stay separate. Global agent plugin reconciliation must not reap existing installations when the new app starts with an empty ledger. Use scoped integration provisioning or preserve the shared desired state, and verify that the fork CLI addresses the fork host.

## Verification

Install build A, sign in, register this repository in the fork, and create a worktree through its CLI. Launch Codex through that CLI to make an actual visible source edit. Build B from the resulting commit. Launch a second Codex session and a timestamped heartbeat process before applying B. Record shell/agent PIDs, terminal IDs, profile paths and build identities before and after. Verify continued execution, reattachment, visible changed UI and a follow-up response from the same agent. Verify invalid candidate rejection and same-schema app recovery. Capture real app screenshots.

## Alternatives

Normal development hot reload was rejected for daily work because development shutdown deliberately kills its terminal daemon. A separately managed host can protect chat execution from desktop restarts, but adds version and lifecycle coordination; it is beyond the first terminal-preserving workflow unless validation requires it. A signed native update feed remains a later option.

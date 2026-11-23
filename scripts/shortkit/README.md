# Local Paradev ShortKit updates

See [verification results](VERIFICATION.md) for the tested build, live session-continuity results and visual verification.

The helper builds a production macOS arm64 directory bundle from a Git commit. It installs at `~/Applications/Paradev ShortKit.app`. The fork uses `dev.shortkit.paradev`, the `superset-shortkit` URL scheme, `~/.paradev-shortkit` for host data, and `~/Library/Application Support/Paradev ShortKit` for Electron. Hosted sign-in runs against the production service. The notification server uses loopback port 51742.

## Prepare a build

Prepare requires Python 3.9 or newer and two private macOS arm64 tools. Place Bun 1.4.3 at `~/.local/share/paradev-fork/toolchain/bun-darwin-aarch64/bun` and Node.js v22.23.5 at `~/.local/share/paradev-fork/toolchain/node/bin/node`. Use the official Darwin arm64 Node archive and verify its checksum before extracting it. The desktop build imports TypeScript directly in Node. Prepare checks both versions and puts their directories first on the build PATH for fresh and reused stages.

Commit the change in the fork worktree, then prepare its bundle.

```sh
python3 scripts/shortkit/update.py prepare HEAD
```

For an existing space-free build checkout with filtered dependencies, Electron, and Electron-native modules already installed, use the stage option. Advance the dedicated checkout to the same commit first. Refresh its dependencies and native modules when the lockfile or Electron version changes.

```sh
revision=$(git rev-parse HEAD)
stage="$HOME/.local/share/paradev-fork/builds/bootstrap"
git -C "$stage" checkout --detach "$revision"
python3 scripts/shortkit/update.py prepare "$revision" --stage "$stage"
```

The stage must have clean Git status and use production build settings. The fresh-stage path creates a detached checkout and prepares its own dependencies. Both paths compile the app and CLI, validate native modules, package with local ad-hoc signing, and verify the resulting bundle. The resulting path is `~/.local/share/paradev-fork/builds/<full-commit>/Paradev ShortKit.app`. Repeating a successful prepare verifies and reuses that artifact.

## Install and update

Run the helper from a regular shell or a daemon-backed terminal in the fork. Keep a fork workspace or Home open: the current renderer health acknowledgement does not cover Settings. Finish browser automation and other work that requires continuous access to a browser pane first. Browser panes and the desktop browser bridge disconnect during restart. The helper relies on the operator to confirm browser automation is idle. Wait for built-in chats to finish, including approvals, queued prompts, and background work.

```sh
candidate="$HOME/.local/share/paradev-fork/builds/$(git rev-parse HEAD)/Paradev ShortKit.app"
python3 scripts/shortkit/update.py verify "$candidate"
python3 scripts/shortkit/update.py apply "$candidate"
```

Apply checks the staged copy, executable architecture, code signature, ASAR identity, main entry points, packaged desktop preload and renderer HTML, and native bindings under the candidate's Electron ABI. It compares terminal protocol versions, migration histories, and applied SQLite migrations. The running app answers on a private Unix socket once its windows and services have initialized. Before ordinary quit it queries every active or expected organization host for live chat activity. An unavailable host, unknown response, or duplicate running bundle identity blocks the update.

The helper waits for the desktop and its host services to exit, exchanges the bundles on the installation volume, then checks the running PID, executable, commit, data paths, renderer health and organization hosts. The desktop's ordinary production quit leaves terminal daemons running. Terminal reattachment and the particular agent's continued execution are part of the live acceptance check.

Use the installed fork shim for local operations, including from a stock terminal.

```sh
"$HOME/.paradev-shortkit/bin/superset" ws list --local
```

The fork uses a stable, separately derived host ID and keeps its local host off the relay. Codex and Claude wrappers, shell startup files and notification scripts live under the fork home. Existing global notify hooks select the local scripts via `SUPERSET_HOME_DIR`. Fork plugin reconciliation targets `~/.paradev-shortkit/agent-config`. Agents using their normal configuration continue to see the plugins installed there.

## Recover a compatible build

```sh
python3 scripts/shortkit/update.py rollback
```

The installer retains verified bundles by commit under `~/Applications/.paradev-shortkit-update/`. Before requesting quit it flushes the recovery bundles and records the source, destination, terminal protocols and expected organizations. After the new app passes its health check it saves the installed build and previous distinct build in `installed.json`. Reapplying the same verified commit preserves the recovery selection and running terminals.

If the helper was interrupted, resume using the retained candidate:

```sh
python3 scripts/shortkit/update.py apply
```

A pending update can also be canceled with `rollback` when it has a verified predecessor. An interrupted first install can be resumed with `apply`. An interrupted rollback can be finished with either command. Once the helper records a decision to restore the predecessor, repeating the original `apply "$candidate"` also finishes that restoration. Keep the state directory while recovery is pending. Its lock serializes concurrent helpers, and the next invocation checks the installed identity to determine which side of the exchange is on disk.

Rollback checks both terminal protocols and the migration hashes applied to each existing desktop, host and chat database. A migration present only in the newer bundle can be left behind if it was never applied. An incompatible applied migration needs a forward fix. If a candidate fails its health check, the helper attempts to reopen its verified, compatible predecessor. A running app or host that cannot report chat safety needs an ordinary quit before retrying recovery.

If the failed candidate is installed and its applied migrations block the predecessor, prepare a fixed build and supply its bundle path explicitly.

```sh
python3 scripts/shortkit/update.py apply "$fixed_bundle"
```

The helper verifies that the installed build matches the retained failed candidate and that the fix preserves its protocols, migration history and applied database hashes. Before requesting quit it checks chat and host safety and carries the expected organizations into the repair. The failed build's archive retains the superseded transaction as `transaction-<digest>.json`. If repair is interrupted after quit was requested, bare `apply` resumes from the retained fix. A successful repair keeps the last verified predecessor as the rollback selection, subject to the applied-migration check.

Rollback can replace a damaged, stopped installation if its plist still proves the exact fork identity, commit and protocol pair, the verified receipt (or original prepared artifact for older installations) agrees, and its host services have exited. The helper excludes the damaged build from subsequent recovery. When the installed bundle is missing, the helper checks for surviving bundle processes, host services, terminal daemon PIDs and sockets before installing. If a daemon is live, preserve its work and recover the matching installed bundle first. The helper leaves databases and terminal processes in place.

The baseline build label is `ShortKit · A` in `apps/desktop/src/shared/shortkit-build.ts`. The fork development session can change this label for build B.

Run the installer tests with `python3 -B -m unittest discover -s scripts/shortkit -v`.

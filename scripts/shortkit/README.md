# Local Paradev ShortKit updates

The helper builds a production macOS arm64 directory bundle from a Git commit. It installs at `~/Applications/Paradev ShortKit.app`. The fork uses `dev.shortkit.paradev`, the `superset-shortkit` URL scheme, `~/.paradev-shortkit` for host data, and `~/Library/Application Support/Paradev ShortKit` for Electron. Hosted sign-in runs against the production service. The notification server uses loopback port 51742.

## Prepare a build

From the fork worktree, commit the change and run the helper with Python 3.9 or newer. It uses Bun 1.4.3 from `~/.local/share/paradev-fork/toolchain/bun-darwin-aarch64/bun`.

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

Run the helper from a regular shell or a daemon-backed terminal in the fork. Finish browser automation and other work that requires continuous access to a browser pane first. Browser panes and the desktop browser bridge disconnect during restart. Wait for built-in chats to finish, including approvals, queued prompts, and background work.

```sh
candidate="$HOME/.local/share/paradev-fork/builds/$(git rev-parse HEAD)/Paradev ShortKit.app"
python3 scripts/shortkit/update.py verify "$candidate"
python3 scripts/shortkit/update.py apply "$candidate"
```

Apply checks the staged copy, executable architecture, code signature, ASAR identity and entry points, and native bindings under the candidate's Electron ABI. It compares terminal protocol versions, migration histories, and applied SQLite migrations. The running app answers on a private Unix socket once its windows and services have initialized. Before ordinary quit it queries every active or expected organization host for live chat activity. An unavailable host, unknown response, or duplicate running bundle identity blocks the update.

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

The installer keeps recovery copies by commit under `~/Applications/.paradev-shortkit-update/`. Rollback uses the same lock, staging, validation, chat check and health check as apply. On a failed launch, apply attempts to reopen the previous compatible copy. If a host or a running candidate cannot report chat safety, finish or close its work and quit the fork normally before retrying recovery. A migration missing from the recovery build blocks a downgrade. A forward fix is needed if the new binary already applied an incompatible migration. The helper never restores databases or terminates a terminal daemon.

The baseline build label is `ShortKit · A` in `apps/desktop/src/shared/shortkit-build.ts`. The fork development session can change this label for build B.

Run the installer tests with `python3 -B -m unittest discover -s scripts/shortkit -v`.

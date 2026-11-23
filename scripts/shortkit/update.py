#!/usr/bin/env python3
"""Prepare and exchange the local macOS Paradev ShortKit application."""

from __future__ import annotations

import argparse
import contextlib
import ctypes
from dataclasses import dataclass
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time

NAME = "Paradev ShortKit"
APP = NAME + ".app"
ID = "dev.shortkit.paradev"
DATA = Path.home() / ".paradev-shortkit"
ROOT = Path(__file__).resolve().parents[2]
STORE = Path.home() / ".local/share/paradev-fork"
INSTALL = Path.home() / "Applications" / APP
MIGRATIONS = {
    "local.db": "migrations",
    "host.db": "host-migrations",
    "chat.db": "chat-migrations",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


@contextlib.contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as descriptor:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"Operation locked at {path}") from exc
        yield


@dataclass
class Build:
    commit: str
    protocols: str
    migrations: dict


def read_build(app):
    require(not app.is_symlink(), f"Refusing linked bundle {app}")
    contents = app / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    schemes = [
        scheme
        for item in info.get("CFBundleURLTypes", [])
        for scheme in item.get("CFBundleURLSchemes", [])
    ]
    require(
        info.get("CFBundleIdentifier") == ID
        and schemes == ["superset-shortkit"]
        and all(
            info.get(key) == NAME
            for key in ("CFBundleExecutable", "CFBundleName", "CFBundleDisplayName")
        ),
        f"Wrong application identity at {app}",
    )
    commit = info.get("ShortKitCommit", "")
    require(re.fullmatch("[0-9a-f]{40}", commit), "Missing committed build identity")
    protocols = info.get("ShortKitDaemonProtocols", "")
    require(
        re.fullmatch(r"\d+/\d+", protocols), "Missing terminal daemon protocol versions"
    )
    migrations = {}
    for db, name in MIGRATIONS.items():
        folder = contents / "Resources/resources" / name
        entries = json.loads((folder / "meta/_journal.json").read_text())["entries"]
        history = {}
        for entry in entries:
            tag, stamp = entry["tag"], entry["when"]
            require(
                re.fullmatch(r"[\w-]+", tag)
                and type(stamp) is int
                and stamp not in history,
                f"Invalid migration journal in {name}",
            )
            sql = (folder / (tag + ".sql")).read_bytes()
            require(sql, f"Empty migration {tag}")
            history[stamp] = hashlib.sha256(sql).hexdigest()
        require(history, f"Missing migrations for {db}")
        migrations[db] = history
    return Build(commit, protocols, migrations)


def check_compatible(home, candidate, current=None):
    if current:
        require(
            current.protocols == candidate.protocols,
            "Incompatible terminal daemon protocols",
        )
        for db, history in current.migrations.items():
            other = list(candidate.migrations[db].items())
            require(
                other[: len(history)] == list(history.items()),
                f"Incompatible migration history for {db}",
            )
    paths = [home / "local.db"]
    host = home / "host"
    if host.exists():
        for org in host.iterdir():
            if org.is_dir():
                paths.extend(org / db for db in ("host.db", "chat.db"))
    for path in paths:
        if not path.exists():
            continue
        with contextlib.closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        ) as db:
            rows = db.execute("SELECT created_at, hash FROM __drizzle_migrations")
            known = candidate.migrations[path.name]
            for stamp, digest in rows:
                require(
                    known.get(stamp) == digest,
                    f"Incompatible applied migration {stamp} in {path}",
                )


def process_exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def select_pid(app, processes):
    matches = []
    for proc in processes:
        same_path = Path(proc["path"]) == app
        same_id = proc["id"] == ID
        if same_path or same_id:
            require(
                same_path and same_id and type(proc["pid"]) is int and proc["pid"] > 1,
                "Running application identity or install path differs",
            )
            matches.append(proc["pid"])
    require(len(matches) <= 1, "Ambiguous running application identity")
    return matches[0] if matches else None


def output(*command, **kwargs):
    return subprocess.check_output(command, text=True, **kwargs).strip()


def run(*command, **kwargs):
    subprocess.run(command, check=True, **kwargs)


def wait_until(check, reason, timeout=45):
    deadline = time.monotonic() + timeout
    while True:
        if check():
            return
        if time.monotonic() >= deadline:
            raise ValueError(reason)
        time.sleep(0.25)


def verify_asar(archive, commit):
    with archive.open("rb") as file:
        header = file.read(16)
        require(len(header) == 16, "Missing ASAR header")
        prefix, header_size, payload, json_size = struct.unpack("<4I", header)
        require(
            prefix == 4 and 0 < json_size <= payload <= header_size <= 64 << 20,
            "Invalid ASAR header",
        )
        tree = json.loads(file.read(json_size))["files"]
        data = 8 + header_size
        limit = os.fstat(file.fileno()).st_size

        def packed(entry):
            start, size = data + int(entry["offset"]), entry["size"]
            require(
                size > 0 and data <= start < start + size <= limit,
                "Incomplete packed resource",
            )
            file.seek(start)
            return file.read(size)

        pkg = json.loads(packed(tree["package.json"]))
        require(
            pkg.get("productName") == NAME
            and pkg.get("name") == "paradev-shortkit"
            and pkg.get("main", "").removeprefix("./") == "dist/main/index.js",
            "Wrong Electron package identity",
        )
        dist = tree["dist"]["files"]
        main = dist["main"]["files"]
        for entry in (
            "index.js",
            "host-service.js",
            "pty-daemon.js",
            "terminal-host.js",
        ):
            packed(main[entry])
        packed(dist["renderer"]["files"]["index.html"])

        def compiled(files):
            for name, entry in files.items():
                if "files" in entry:
                    yield from compiled(entry["files"])
                elif name.endswith(".js") and "offset" in entry:
                    yield packed(entry)

        require(
            any(commit.encode() in chunk for chunk in compiled(main)),
            "Compiled fork commit mismatch",
        )


class Mac:
    def __init__(self, home):
        self.home = home

    def verify(self, app):
        contents = app / "Contents"
        resources = contents / "Resources"
        executable = contents / "MacOS" / NAME
        verify_asar(resources / "app.asar", read_build(app).commit)
        require(
            not (resources / "app-update.yml").exists(), "Unexpected native update feed"
        )
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
        cli = resources / "resources/bin/superset"
        require(os.access(cli, os.X_OK), "Missing bundled CLI")
        for binary in (executable, cli):
            require(
                "arm64" in output("/usr/bin/lipo", "-archs", binary).split(),
                f"Invalid arm64 binary {binary}",
            )

        modules = resources / "app.asar.unpacked/node_modules"
        pty = next(
            (
                path
                for path in (
                    modules / "node-pty/build/Release/pty.node",
                    modules / "node-pty/prebuilds/darwin-arm64/pty.node",
                )
                if path.is_file()
            ),
            None,
        )
        require(
            pty and os.access(pty.with_name("spawn-helper"), os.X_OK),
            "Missing PTY binding or spawn-helper",
        )
        bindings = [
            pty,
            modules / "better-sqlite3/build/Release/better_sqlite3.node",
            modules / "@parcel/watcher-darwin-arm64/watcher.node",
            modules / "native-keymap/build/Release/keymapping.node",
            modules
            / "@superset/macos-process-metrics/build/Release/macos_process_metrics.node",
        ]
        # Use Electron's Node ABI without executing the application's main entry.
        with tempfile.TemporaryDirectory() as probe:
            run(
                executable,
                "-e",
                "for (const binding of process.argv.slice(1)) require(binding)",
                *bindings,
                cwd=probe,
                env={
                    "HOME": str(Path.home()),
                    "PATH": "/usr/bin:/bin",
                    "ELECTRON_RUN_AS_NODE": "1",
                },
                timeout=30,
            )

    def copy(self, src, dst):
        run("/bin/cp", "-cR", src, dst)

    def exchange(self, left, right):
        rename = ctypes.CDLL(None, use_errno=True).renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        if rename(os.fsencode(left), os.fsencode(right), 2) != 0:  # Darwin RENAME_SWAP
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))

    def pid(self, app):
        script = """
ObjC.import('AppKit')
const apps = $.NSWorkspace.sharedWorkspace.runningApplications;
const result = [];
for (let i = 0; i < apps.count; i++) {
    const p = apps.objectAtIndex(i);
    if (!p.bundleURL || !p.bundleIdentifier) continue;
    const path = ObjC.unwrap(p.bundleURL.path), id = ObjC.unwrap(p.bundleIdentifier);
    if (path && id) {
        result.push({path, id, pid: p.processIdentifier});
    }
}
JSON.stringify(result)
"""
        return select_pid(
            app,
            json.loads(
                output(
                    "/usr/bin/osascript", "-l", "JavaScript", "-e", script, timeout=5
                )
            ),
        )

    def status(self, app, build, pid, quit_app=False):
        endpoint = str(self.home / "desktop-update.sock")
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(5)
            sock.connect(endpoint)
            method = f"POST /quit/{pid}/{build.commit}" if quit_app else "GET /status"
            sock.sendall(f"{method} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
            with http.client.HTTPResponse(sock) as response:
                response.begin()
                if response.status != 200:
                    raise ValueError(
                        f"Fork control returned {response.status}: {response.read(256).decode()}"
                    )
                status = json.load(response)
        require(
            status.get("pid") == pid
            and status.get("commit") == build.commit
            and status.get("executable") == str(app / "Contents/MacOS" / NAME)
            and status.get("userData")
            == str(Path.home() / "Library/Application Support" / NAME)
            and status.get("home") == str(self.home),
            "Fork runtime identity mismatch",
        )
        require(
            isinstance(status.get("organizations"), list)
            and type(status.get("activeChats")) is bool
            and isinstance(status.get("hostPids"), list)
            and all(type(p) is int and p > 1 for p in status["hostPids"]),
            "Unknown fork host state",
        )
        return status

    def live_host_pids(self):
        hosts = self.home / "host"
        live = set()
        if hosts.exists():
            for org in hosts.iterdir():
                manifest = org / "manifest.json"
                if not manifest.exists():
                    continue
                pid = json.loads(manifest.read_text())["pid"]
                require(type(pid) is int and pid > 1, "Unknown host process")
                if process_exists(pid):
                    live.add(pid)
        return live

    def stop(self, app, build):
        pid = self.pid(app)
        if pid is None:
            require(
                not self.live_host_pids(),
                "Fork host is still running without its desktop",
            )
            return []
        require(build, "Unidentified fork is already running")
        status = self.status(app, build, pid)
        require(
            self.live_host_pids() <= set(status["hostPids"]),
            "Cannot establish chat safety for every fork host",
        )
        require(not status["activeChats"], "Active built-in chat work")
        stopped = self.status(app, build, pid, quit_app=True)
        wait_until(
            lambda: self.pid(app) is None, "Fork did not finish its ordinary quit"
        )

        wait_until(
            lambda: not any(process_exists(p) for p in stopped["hostPids"]),
            "Fork host service is still exiting",
        )
        return status["organizations"]

    def launch(self, app, build, organizations):
        run("/usr/bin/open", "-a", app)

        def ready():
            pid = self.pid(app)
            if pid is None:
                return False
            try:
                status = self.status(app, build, pid)
            except (OSError, ValueError, http.client.HTTPException):
                return False
            return status.get("ready") is True and set(organizations) <= set(
                status["organizations"]
            )

        wait_until(
            ready,
            f"Fork {build.commit} failed runtime identity or health checks",
            timeout=75,
        )


class Installer:
    def __init__(self, installed, home, mac):
        self.app, self.home, self.mac = installed, home, mac
        self.state = installed.parent / ".paradev-shortkit-update"

    def apply(self, candidate):
        with lock(self.state / "install.lock"):
            self.replace(candidate)

    def rollback(self):
        with lock(self.state / "install.lock"):
            commit = (self.state / "previous").read_text().strip()
            require(
                re.fullmatch("[a-f0-9]{40}", commit), "Invalid previous build record"
            )
            self.replace(self.state / commit / APP)

    def replace(self, candidate):
        require(
            self.app.name == APP and not self.app.is_symlink(),
            "Invalid installation target",
        )
        current = read_build(self.app) if self.app.exists() else None
        wanted = read_build(candidate)
        check_compatible(self.home, wanted, current)
        with tempfile.TemporaryDirectory(dir=self.state) as scratch:
            staged = Path(scratch) / APP
            self.mac.copy(candidate, staged)
            require(read_build(staged) == wanted, "Candidate changed during staging")
            self.mac.verify(staged)
            if current:
                self.mac.verify(self.app)
                backup = self.state / current.commit / APP
                if not backup.exists():
                    backup.parent.mkdir(exist_ok=True)
                    saved = Path(scratch) / "recovery" / APP
                    saved.parent.mkdir()
                    self.mac.copy(self.app, saved)
                    saved.rename(backup)
                require(read_build(backup) == current, "Recovery identity mismatch")
                self.mac.verify(backup)
                pointer = self.state / "previous.tmp"
                pointer.write_text(current.commit)
            organizations = self.mac.stop(self.app, current)
            if current:
                try:
                    check_compatible(self.home, wanted, current)
                    self.mac.exchange(self.app, staged)
                except Exception:
                    self.mac.launch(self.app, current, organizations)
                    raise
            else:
                staged.rename(self.app)
            try:
                if current:
                    pointer.replace(self.state / "previous")
                self.mac.launch(self.app, wanted, organizations)
            except Exception as failure:
                if not current:
                    raise
                # A failed launch may already have migrated a database.
                check_compatible(self.home, current)
                self.mac.stop(self.app, wanted)
                check_compatible(self.home, current)
                self.mac.exchange(self.app, staged)
                self.mac.launch(self.app, current, organizations)
                raise ValueError(
                    f"Update failed; restored {current.commit}: {failure}"
                ) from failure


def build_toolchain(toolchain, inherited_path):
    bun = toolchain / "bun-darwin-aarch64/bun"
    node = toolchain / "node/bin/node"
    for executable, expected in ((bun, "1.4.3"), (node, "v22.23.5")):
        actual = output(executable, "--version")
        require(actual == expected, f"Expected {executable} {expected}, found {actual}")
    return bun, os.pathsep.join((str(bun.parent), str(node.parent), inherited_path))


def prepare(revision, stage):
    commit = output(
        "git",
        "rev-parse",
        "--verify",
        "--end-of-options",
        revision + "^{commit}",
        cwd=ROOT,
    )
    builds = STORE / "builds"
    require(
        not any(c.isspace() for c in str(builds)),
        "Build storage must be on a path without spaces",
    )
    artifact = builds / commit / APP
    mac = Mac(DATA)
    bun, build_path = build_toolchain(
        STORE / "toolchain", os.environ.get("PATH", os.defpath)
    )
    # Never compile the login shell's stock workspace, service addresses, or credentials.
    env = {
        k: os.environ[k]
        for k in (
            "HOME",
            "TMPDIR",
            "LANG",
            "DEVELOPER_DIR",
            "PDEV_BUNDLE_DIR",
            "BUN_INSTALL_CACHE_DIR",
        )
        if k in os.environ
    }
    env.update(
        PATH=build_path,
        NODE_ENV="production",
        SHORTKIT_BUILD_COMMIT=commit,
        DESKTOP_DISPLAY_NAME=NAME,
        SUPERSET_WORKSPACE_NAME="superset",
        DESKTOP_NOTIFICATIONS_PORT="51742",
        TARGET_ARCH="arm64",
        TARGET_PLATFORM="darwin",
        CSC_IDENTITY_AUTO_DISCOVERY="false",
    )
    with lock(builds / "prepare.lock"):
        if artifact.exists():
            require(
                read_build(artifact).commit == commit,
                "Existing artifact identity mismatch",
            )
            mac.verify(artifact)
            return artifact
        with tempfile.TemporaryDirectory(dir=builds) as tmp:
            if stage:
                source = stage.resolve()
                require(
                    source.is_relative_to(builds.resolve()) and " " not in str(source),
                    "Use a dedicated space-free checkout inside the builds directory",
                )
            else:
                source = Path(tmp) / "source"
                run("git", "clone", "--local", "--no-checkout", "--quiet", ROOT, source)
                run("git", "checkout", "--detach", "--quiet", commit, cwd=source)
            require(
                output("git", "rev-parse", "HEAD", cwd=source) == commit,
                f"Stage must be at {commit}",
            )
            require(
                not output("git", "status", "--porcelain", cwd=source),
                "Stage has uncommitted source",
            )
            desktop = source / "apps/desktop"
            for directory in (source, desktop):
                for name in (
                    ".env",
                    ".env.local",
                    ".env.production",
                    ".env.production.local",
                ):
                    require(
                        not (directory / name).exists(),
                        f"Remove local build overrides from stage: {name}",
                    )
            if not stage:
                install_env = {**env, "NODE_ENV": "development"}
                run(
                    bun,
                    "install",
                    "--frozen-lockfile",
                    "--ignore-scripts",
                    "--filter",
                    "@superset/desktop",
                    "--filter",
                    "@superset/cli",
                    "--filter",
                    "@superset/i18n",
                    "--filter",
                    "@superset/repo",
                    cwd=source,
                    env=install_env,
                )
                run(bun, "run", "install:electron", cwd=desktop, env=env)
                run(bun, "run", "install:deps", cwd=desktop, env=env)
            for step in (
                "generate:icons",
                "compile:app",
                "copy:native-modules",
                "validate:native-runtime",
            ):
                run(bun, "run", step, cwd=desktop, env=env)
            out = Path(tmp) / "package"
            run(
                bun,
                "run",
                "electron-builder",
                "--config",
                "electron-builder.shortkit.ts",
                "--dir",
                "--mac",
                "--arm64",
                "--publish",
                "never",
                f"-c.directories.output={out}",
                cwd=desktop,
                env=env,
            )
            app = out / "mac-arm64" / APP
            require(read_build(app).commit == commit, "Packaged commit mismatch")
            mac.verify(app)
            artifact.parent.mkdir(exist_ok=True)
            app.rename(artifact)
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser(
        "prepare", help="build and verify a committed arm64 candidate"
    )
    build.add_argument("revision", nargs="?", default="HEAD")
    build.add_argument(
        "--stage",
        type=Path,
        help="clean build checkout with prepared native dependencies",
    )
    sub.add_parser("verify", help="inspect a built candidate").add_argument(
        "candidate", type=Path
    )
    apply = sub.add_parser("apply", help="install or update the fork")
    apply.add_argument("candidate", type=Path)
    sub.add_parser("rollback", help="restore the previous compatible bundle")
    args = parser.parse_args()
    require(sys.platform == "darwin", "This local installer requires macOS")
    mac = Mac(DATA)
    installer = Installer(INSTALL, DATA, mac)
    if args.command == "prepare":
        print(prepare(args.revision, args.stage))
    elif args.command == "verify":
        candidate = args.candidate.absolute()
        build = read_build(candidate)
        mac.verify(candidate)
        print(f"{ID} {build.commit} {candidate}")
    else:
        print(
            "Browser panes and browser automation disconnect during restart. Finish browser-dependent work before applying.",
            flush=True,
        )
        if args.command == "apply":
            installer.apply(args.candidate.absolute())
        else:
            installer.rollback()
        print(f"Running {read_build(INSTALL).commit} at {INSTALL}")


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
        plistlib.InvalidFileException,
        subprocess.SubprocessError,
        http.client.HTTPException,
    ) as exc:
        sys.exit(f"shortkit-update: {exc}")

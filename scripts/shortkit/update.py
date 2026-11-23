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
import shutil
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


INVALID_BUNDLE = (
    OSError,
    ValueError,
    TypeError,
    KeyError,
    plistlib.InvalidFileException,
    subprocess.SubprocessError,
)


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


@dataclass(frozen=True)
class Identity:
    commit: str
    protocols: str


@dataclass(frozen=True)
class Build(Identity):
    migrations: dict


def read_identity(app):
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
    return Identity(commit, protocols)


def read_build(app):
    identity = read_identity(app)
    migrations = {}
    for db, name in MIGRATIONS.items():
        folder = app / "Contents/Resources/resources" / name
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
    return Build(identity.commit, identity.protocols, migrations)


def check_migration_history(current, candidate):
    for db, history in current.migrations.items():
        other = list(candidate.migrations[db].items())
        require(
            other[: len(history)] == list(history.items()),
            f"Incompatible migration history for {db}",
        )


def check_compatible(home, candidate):
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
        preload = dist.get("preload", {}).get("files", {}).get("index.js")
        require(preload and "offset" in preload, "Missing packed desktop preload")
        packed(preload)

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

    def check_no_terminal_daemons(self, app):
        def check_pid(pid):
            require(type(pid) is int and pid > 1, "Unknown terminal daemon PID")
            require(
                not process_exists(pid),
                "Live terminal daemon without an installed bundle",
            )

        def check_socket(path):
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(1)
                try:
                    sock.connect(str(path))
                except (FileNotFoundError, ConnectionRefusedError):
                    return
                raise ValueError(
                    f"Live terminal daemon socket without an installed bundle: {path}"
                )

        contents = str(app / "Contents") + "/"
        commands = output("/bin/ps", "-axww", "-o", "comm=", timeout=5)
        require(
            not any(
                command.lstrip().startswith(contents)
                for command in commands.splitlines()
            ),
            "Live bundle process without an installed bundle",
        )
        legacy_pid = self.home / "terminal-host.pid"
        if legacy_pid.exists():
            check_pid(int(legacy_pid.read_text().strip()))
        temporary_root = Path(
            os.environ.get("TMPDIR")
            or os.environ.get("TMP")
            or os.environ.get("TEMP")
            or "/tmp"
        )
        legacy_socket = self.home / "terminal-host.sock"
        if len(os.fsencode(legacy_socket)) >= 104:
            key = hashlib.sha256(os.fsencode(self.home)).hexdigest()[:16]
            suffix = Path(f"paradev-terminal-{os.getuid()}") / f"{key}.sock"
            legacy_socket = temporary_root / suffix
            if len(os.fsencode(legacy_socket)) >= 104:
                legacy_socket = Path("/tmp") / suffix
        check_socket(legacy_socket)
        hosts = self.home / "host"
        for org in hosts.iterdir() if hosts.exists() else ():
            if not org.is_dir():
                continue
            manifest = org / "pty-daemon-manifest.json"
            if manifest.exists():
                daemon = json.loads(manifest.read_text())
                check_pid(daemon["pid"])
                if "handoffSuccessorPid" in daemon:
                    check_pid(daemon["handoffSuccessorPid"])
                check_socket(daemon["socketPath"])
            key = hashlib.sha256(f"{org.name}:{self.home}".encode()).hexdigest()[:12]
            check_socket(temporary_root / f"superset-ptyd-{key}.sock")

    def stop(self, app, build, before_quit, *, stopped_only=False):
        pid = self.pid(app)
        if pid is None:
            require(
                not self.live_host_pids(),
                "Fork host is still running without its desktop",
            )
            if build is None:
                self.check_no_terminal_daemons(app)
            before_quit([])
            return
        require(not stopped_only, "Damaged installation must already be stopped")
        require(build, "Unidentified fork is already running")
        status = self.status(app, build, pid)
        require(
            self.live_host_pids() <= set(status["hostPids"]),
            "Cannot establish chat safety for every fork host",
        )
        require(not status["activeChats"], "Active built-in chat work")
        before_quit(status["organizations"])
        stopped = self.status(app, build, pid, quit_app=True)
        wait_until(
            lambda: self.pid(app) is None, "Fork did not finish its ordinary quit"
        )

        wait_until(
            lambda: not any(process_exists(p) for p in stopped["hostPids"]),
            "Fork host service is still exiting",
        )

    def launch(self, app, build, organizations):
        if self.pid(app) is None:
            require(
                not self.live_host_pids(),
                "Fork host is still running without its desktop",
            )
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


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync_bundle(app):
    for root, _, files in os.walk(app, topdown=False):
        for name in files:
            file = Path(root) / name
            if not file.is_symlink():
                with file.open("rb") as descriptor:
                    os.fsync(descriptor.fileno())
        sync_dir(root)


def save_record(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as descriptor:
        json.dump(value, descriptor)
        descriptor.flush()
        os.fsync(descriptor.fileno())
    temporary.replace(path)
    sync_dir(path.parent)


class Installer:
    def __init__(self, installed, home, mac):
        self.app, self.home, self.mac = installed, home, mac
        self.state = installed.parent / ".paradev-shortkit-update"
        self.pending = self.state / "transaction.json"
        self.receipt = self.state / "installed.json"
        self.stage = self.state / "staging" / APP

    def archive(self, commit):
        require(
            isinstance(commit, str) and re.fullmatch("[a-f0-9]{40}", commit),
            "Invalid recovery build record",
        )
        return self.state / commit / APP

    def verified(self, app):
        build = read_build(app)
        self.mac.verify(app)
        return build

    def stage_copy(self, source, expected):
        if self.stage.parent.exists():
            shutil.rmtree(self.stage.parent)
        self.stage.parent.mkdir()
        self.mac.copy(source, self.stage)
        require(self.verified(self.stage) == expected, "Build changed during staging")
        sync_bundle(self.stage)

    def retain(self, app, build):
        saved = self.archive(build.commit)
        if saved.exists():
            require(self.verified(saved) == build, "Recovery identity mismatch")
            return
        self.stage_copy(app, build)
        saved.parent.mkdir(exist_ok=True)
        self.stage.rename(saved)
        sync_dir(saved.parent)
        sync_dir(self.state)

    def previous(self):
        if self.receipt.exists():
            commit = json.loads(self.receipt.read_text())["previous"]
        else:
            legacy = self.state / "previous"
            commit = legacy.read_text().strip() if legacy.exists() else None
        return commit

    def apply(self, candidate=None):
        with lock(self.state / "install.lock"):
            self.change(candidate, rollback=False)

    def rollback(self):
        with lock(self.state / "install.lock"):
            self.change(None, rollback=True)

    def installed_identity(self):
        require(
            self.app.name == APP and not self.app.is_symlink(),
            "Invalid installation target",
        )
        return read_identity(self.app) if self.app.exists() else None

    def start_transaction(self, candidate, rollback):
        current = self.installed_identity()
        previous = self.previous()
        if rollback:
            require(previous, "No previous build recorded")
            candidate = self.archive(previous)
        require(
            candidate is not None,
            "Supply a candidate; there is no unfinished transaction",
        )
        wanted = self.verified(candidate)
        if rollback:
            require(wanted.commit == previous, "Previous build identity mismatch")
        check_compatible(self.home, wanted)
        recoverable = False
        if current is not None:
            require(
                current.protocols == wanted.protocols,
                "Incompatible terminal daemon protocols",
            )
            try:
                installed = self.verified(self.app)
            except INVALID_BUNDLE:
                if not rollback:
                    raise
                self.check_damaged_source(current)
            else:
                if installed.commit == wanted.commit:
                    require(
                        installed == wanted, "Conflicting builds for the same commit"
                    )
                    self.mac.launch(self.app, installed, [])
                    return None
                if not rollback:
                    check_migration_history(installed, wanted)
                self.retain(self.app, installed)
                recoverable = True
        self.retain(candidate, wanted)
        return dict(
            source=current.commit if current else None,
            target=wanted.commit,
            protocols=wanted.protocols,
            previous=previous,
            rollback=rollback,
            recoverable=recoverable,
            organizations=[],
        )

    def change(self, candidate, rollback):
        if self.pending.exists():
            tx = json.loads(self.pending.read_text())
            if candidate is not None:
                require(
                    self.verified(candidate).commit == tx["target"],
                    "Unfinished transaction: retry its target or run rollback",
                )
            reverse = candidate is None and (
                tx.get("restoring", False) or (rollback and not tx["rollback"])
            )
            require(
                not reverse or tx["recoverable"],
                "No compatible predecessor recorded; retry apply",
            )
        else:
            tx = self.start_transaction(candidate, rollback)
            if tx is None:
                return
            reverse = False

        try:
            self.transition(tx, reverse)
        except Exception as failure:
            if reverse or not tx["recoverable"] or not self.pending.exists():
                raise
            # Stop may have failed before the desktop acknowledged quit. Never
            # turn that failure into a request to stop a different live build.
            if self.installed_identity() == Identity(tx["source"], tx["protocols"]):
                if self.mac.pid(self.app) is None:
                    self.transition(tx, True)
                raise
            self.transition(tx, True)
            raise ValueError(
                f"Update failed; restored {tx['source']}: {failure}"
            ) from failure

    def check_damaged_source(self, identity):
        require(
            self.mac.pid(self.app) is None and not self.mac.live_host_pids(),
            "Damaged installation must already be stopped",
        )
        if self.receipt.exists():
            receipt = json.loads(self.receipt.read_text())
            require(
                (receipt["commit"], receipt["protocols"])
                == (identity.commit, identity.protocols),
                "Damaged installation differs from its verified receipt",
            )
        else:
            # Installations made before receipts can use their verified prepare artifact.
            original = self.verified(STORE / "builds" / identity.commit / APP)
            require(
                (original.commit, original.protocols)
                == (identity.commit, identity.protocols),
                "Damaged installation differs from its verified artifact",
            )

    def transition(self, tx, reverse):
        destination = tx["source"] if reverse else tx["target"]
        other = tx["target"] if reverse else tx["source"]
        archive = self.archive(destination)
        wanted = self.verified(archive)
        require(
            wanted.commit == destination and wanted.protocols == tx["protocols"],
            "Staged recovery identity mismatch",
        )
        current = self.installed_identity()
        commit = current.commit if current else None
        require(
            commit in (other, destination)
            and (not current or current.protocols == wanted.protocols),
            "Installed bundle differs from the transaction",
        )
        check_compatible(self.home, wanted)

        def record_intent(organizations):
            tx["restoring"] = reverse
            sync_dir(self.app.parent)
            tx["organizations"] = sorted(set(tx["organizations"]) | set(organizations))
            save_record(self.pending, tx)

        if commit != destination:
            self.stage_copy(archive, wanted)
            stopped_only = (
                current is not None
                and current.commit == tx["source"]
                and not tx["recoverable"]
            )
            if current and not stopped_only:
                try:
                    self.verified(self.app)
                except INVALID_BUNDLE:
                    if not reverse:
                        raise
                    stopped_only = True

            self.mac.stop(self.app, current, record_intent, stopped_only=stopped_only)
            require(
                self.installed_identity() == current, "Installation changed during quit"
            )
            check_compatible(self.home, wanted)
            if current:
                self.mac.exchange(self.app, self.stage)
            else:
                self.stage.rename(self.app)
            sync_dir(self.app.parent)
        else:
            require(
                self.verified(self.app) == wanted,
                "Installed build differs from recovery copy",
            )
            if tx.get("restoring", False) != reverse:
                record_intent([])
        self.mac.launch(self.app, wanted, tx["organizations"])
        previous = (
            tx["previous"] if reverse else (tx["source"] if tx["recoverable"] else None)
        )
        save_record(
            self.receipt,
            dict(commit=destination, protocols=wanted.protocols, previous=previous),
        )
        self.pending.unlink()
        sync_dir(self.state)
        if self.stage.parent.exists():
            shutil.rmtree(self.stage.parent)


def build_toolchain(toolchain, inherited_path):
    bun = toolchain / "bun-darwin-aarch64/bun"
    node = toolchain / "node/bin/node"
    for executable, expected in ((bun, "1.4.3"), (node, "v22.23.5")):
        actual = output(executable, "--version")
        require(actual == expected, f"Expected {executable} {expected}, found {actual}")
    return bun, os.pathsep.join((str(bun.parent), str(node.parent), inherited_path))


def package_desktop(desktop, out, env):
    run(
        desktop / "node_modules/.bin/electron-builder",
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
            package_desktop(desktop, out, env)
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
    apply.add_argument(
        "candidate",
        nargs="?",
        type=Path,
        help="verified bundle, or omit to resume the pending transaction",
    )
    sub.add_parser("rollback", help="restore the previous compatible bundle")
    args = parser.parse_args()
    require(sys.platform == "darwin", "This local installer requires macOS")
    if args.command == "prepare":
        print(prepare(args.revision, args.stage))
    elif args.command == "verify":
        candidate = args.candidate.absolute()
        build = read_build(candidate)
        Mac(DATA).verify(candidate)
        print(f"{ID} {build.commit} {candidate}")
    else:
        installer = Installer(INSTALL, DATA, Mac(DATA))
        print(
            "Browser panes and browser automation disconnect during restart. Finish browser-dependent work before applying.",
            flush=True,
        )
        if args.command == "apply":
            installer.apply(args.candidate.absolute() if args.candidate else None)
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

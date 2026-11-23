import argparse
import contextlib
import json
import socket
import threading
from pathlib import Path
import plistlib
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import update


class BuildToolchainTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bun = self.root / "bun-darwin-aarch64/bun"
        self.node = self.root / "node/bin/node"
        self.system = self.root / "system"
        self.executable(self.bun, "1.4.3")
        self.executable(self.system / "node", "v22.16.0")

    def executable(self, path, version):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\necho {version}\n")
        path.chmod(0o755)

    def test_build_uses_private_node_ahead_of_inherited_path(self):
        self.executable(self.node, "v22.23.5")
        bun, path = update.build_toolchain(self.root, str(self.system))
        self.assertEqual(bun, self.bun)
        self.assertEqual(shutil.which("node", path=path), str(self.node))
        self.assertEqual(shutil.which("bun", path=path), str(self.bun))

    def test_incompatible_private_node_blocks_prepare(self):
        self.executable(self.node, "v22.16.0")
        with self.assertRaisesRegex(ValueError, "22.23.5"):
            update.build_toolchain(self.root, str(self.system))

    def test_packaging_invokes_desktop_builder_with_private_node(self):
        self.executable(self.node, "v22.23.5")
        desktop = self.root / "stage/apps/desktop"
        builder = desktop / "node_modules/.bin/electron-builder"
        builder.parent.mkdir(parents=True)
        (desktop / "electron-builder.ts").touch()
        builder.write_text("#!/bin/sh\nnode --version > builder-runtime\n")
        builder.chmod(0o755)
        _, path = update.build_toolchain(self.root, str(self.system))
        update.package_desktop(desktop, self.root / "package", {"PATH": path})
        runtime = desktop / "builder-runtime"
        self.assertTrue(runtime.is_file(), "Desktop-local builder was never invoked")
        self.assertEqual(runtime.read_text().strip(), "v22.23.5")


class FakeMac:
    def __init__(self):
        self.stops = []
        self.opened = []
        self.unhealthy = None
        self.unsafe = False

    def verify(self, app):
        if not (app / "Contents/Resources/app.asar").is_file():
            raise ValueError("missing ASAR")

    def stop(self, app, build):
        if self.unsafe:
            raise ValueError("chat state unavailable")
        self.stops.append(build.commit)
        return []

    def launch(self, app, build, organizations):
        self.opened.append(build.commit)
        if build.commit == self.unhealthy:
            raise ValueError("health check failed")

    def copy(self, src, dst):
        shutil.copytree(src, dst)

    def exchange(self, left, right):
        tmp = left.with_name("exchange")
        left.rename(tmp)
        right.rename(left)
        tmp.rename(right)


class InstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.app = self.bundle(self.root / "Applications", "a")
        self.candidate = self.bundle(self.root / "artifacts", "b")
        self.home = self.root / ".paradev-shortkit"
        self.mac = FakeMac()
        self.installer = update.Installer(self.app, self.home, self.mac)

    def bundle(self, directory, revision):
        app = directory / "Paradev ShortKit.app"
        contents = app / "Contents"
        resources = contents / "Resources"
        resources.mkdir(parents=True)
        (resources / "app.asar").write_bytes(b"fixture")
        info = dict(
            CFBundleName="Paradev ShortKit",
            CFBundleDisplayName="Paradev ShortKit",
            CFBundleIdentifier="dev.shortkit.paradev",
            CFBundleExecutable="Paradev ShortKit",
            CFBundleURLTypes=[dict(CFBundleURLSchemes=["superset-shortkit"])],
            ShortKitCommit=revision * 40,
            ShortKitDaemonProtocols="2/2",
        )
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        for name in ["migrations", "host-migrations", "chat-migrations"]:
            folder = resources / "resources" / name
            (folder / "meta").mkdir(parents=True)
            (folder / "meta/_journal.json").write_text(
                json.dumps({"entries": [{"tag": "0000_start", "when": 100}]})
            )
            (folder / "0000_start.sql").write_text("select 1")
        return app

    def test_invalid_candidate_preserves_running_install(self):
        (self.candidate / "Contents/Resources/app.asar").unlink()
        with self.assertRaisesRegex(ValueError, "ASAR"):
            self.installer.apply(self.candidate)
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)
        self.assertEqual(self.mac.stops, [])

    def test_launch_only_requests_loopback_debugging_with_an_explicit_port(self):
        build = update.read_build(self.app)
        with patch.object(update, "run") as launch, patch.object(update, "wait_until"):
            update.Mac(self.home).launch(self.app, build, [])
            launch.assert_called_once_with("/usr/bin/open", "-a", self.app)
            launch.reset_mock()
            update.Mac(self.home, debug_port=9417).launch(self.app, build, [])
            launch.assert_called_once_with(
                "/usr/bin/open",
                "-a",
                self.app,
                "--args",
                "--remote-debugging-port=9417",
                "--remote-debugging-address=127.0.0.1",
            )

    def test_debug_port_requires_an_integer_in_the_allowed_range(self):
        for value in ("1024", "65535"):
            self.assertEqual(update.parse_debug_port(value), int(value))
        for value in ("1023", "65536", "9417.5", "port"):
            with self.assertRaises(argparse.ArgumentTypeError, msg=value):
                update.parse_debug_port(value)

    def test_display_name_alone_never_identifies_the_target(self):
        plist = self.app / "Contents/Info.plist"
        info = plistlib.loads(plist.read_bytes())
        info["CFBundleIdentifier"] = "com.superset.desktop"
        plist.write_bytes(plistlib.dumps(info))
        with self.assertRaisesRegex(ValueError, "identity"):
            self.installer.apply(self.candidate)
        self.assertFalse(self.mac.stops)
        self.assertEqual(plistlib.loads(plist.read_bytes()), info)

    def test_busy_install_lock_precedes_quit(self):
        with update.lock(self.installer.state / "install.lock"):
            with self.assertRaisesRegex(ValueError, "locked"):
                self.installer.apply(self.candidate)
        self.assertFalse(self.mac.stops)

    def test_changed_migration_and_newer_live_database_block_downgrade(self):
        self.home.mkdir()
        with contextlib.closing(sqlite3.connect(self.home / "local.db")) as db:
            db.execute(
                "CREATE TABLE __drizzle_migrations (created_at INTEGER, hash TEXT)"
            )
            db.execute("INSERT INTO __drizzle_migrations VALUES (101, 'newer')")
            db.commit()
        with self.assertRaisesRegex(ValueError, "migration"):
            self.installer.apply(self.candidate)
        (self.home / "local.db").unlink()
        (
            self.candidate
            / "Contents/Resources/resources/host-migrations/0000_start.sql"
        ).write_text("select 2")
        with self.assertRaisesRegex(ValueError, "migration"):
            self.installer.apply(self.candidate)
        self.assertFalse(self.mac.stops)

    def test_unknown_chat_state_does_not_replace_app(self):
        self.mac.unsafe = True
        with self.assertRaisesRegex(ValueError, "chat"):
            self.installer.apply(self.candidate)
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)

    def test_swap_failure_reopens_the_unchanged_app(self):
        def fail(left, right):
            raise OSError("swap failed")

        self.mac.exchange = fail
        with self.assertRaisesRegex(OSError, "swap failed"):
            self.installer.apply(self.candidate)
        self.assertEqual(self.mac.opened, ["a" * 40])
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)

    def test_failed_health_restores_previous_compatible_build(self):
        self.mac.unhealthy = "b" * 40
        with self.assertRaisesRegex(ValueError, "restored"):
            self.installer.apply(self.candidate)
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)
        self.assertEqual(self.mac.opened, ["b" * 40, "a" * 40])

    def test_rollback_rechecks_current_data(self):
        self.installer.apply(self.candidate)
        self.installer.rollback()
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)
        self.assertEqual(self.mac.opened, ["b" * 40, "a" * 40])

    def test_read_live_health_preserves_the_full_success_response(self):
        # Keep the Unix path below the Darwin limit, including a long TMPDIR.
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            home = Path(folder)
            endpoint = str(home / "desktop-update.sock")
            build = update.read_build(self.app)
            status = {
                "pid": 123,
                "commit": build.commit,
                "home": folder,
                "executable": str(self.app / "Contents/MacOS/Paradev ShortKit"),
                "userData": str(
                    Path.home() / "Library/Application Support/Paradev ShortKit"
                ),
                "activeChats": False,
                "ready": True,
                "organizations": ["one"],
                "hostPids": [321],
            }
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(endpoint)
                listener.listen(1)

                def serve():
                    conn, _ = listener.accept()
                    with conn:
                        conn.recv(1024)
                        conn.sendall(
                            b"HTTP/1.0 200 OK\r\n\r\n" + json.dumps(status).encode()
                        )

                thread = threading.Thread(target=serve)
                thread.start()
                try:
                    self.assertEqual(
                        update.Mac(home).status(self.app, build, 123), status
                    )
                finally:
                    thread.join(1)

    def test_process_selection_requires_the_exact_bundle_path_and_identifier(self):
        def proc(path, identifier):
            return {"path": str(path), "id": identifier, "pid": 321}

        owned = proc(self.app, "dev.shortkit.paradev")
        stock = proc(self.app.with_name("Superset.app"), "com.superset.desktop")
        self.assertEqual(update.select_pid(self.app, [stock, owned]), 321)
        for conflict in [
            proc(self.candidate, owned["id"]),
            proc(self.app, stock["id"]),
        ]:
            with self.assertRaisesRegex(ValueError, "identity"):
                update.select_pid(self.app, [conflict])

import contextlib
import json
import socket
import threading
from pathlib import Path
import plistlib
import shutil
import sqlite3
import struct
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


class PackagedPreloadTests(unittest.TestCase):
    def test_preload_must_be_a_nonempty_packed_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "app.asar"
            entries = {
                "package.json": json.dumps(
                    dict(
                        productName=update.NAME,
                        name="paradev-shortkit",
                        main="dist/main/index.js",
                    )
                ).encode(),
                "dist/main/index.js": b"a" * 40,
                "dist/main/host-service.js": b"host",
                "dist/main/pty-daemon.js": b"pty",
                "dist/main/terminal-host.js": b"terminal",
                "dist/renderer/index.html": b"renderer",
            }

            def pack():
                tree = {}
                payload = bytearray()
                for path, data in entries.items():
                    parts = path.split("/")
                    directory = tree
                    for part in parts[:-1]:
                        directory = directory.setdefault(part, {"files": {}})["files"]
                    directory[parts[-1]] = dict(
                        offset=str(len(payload)), size=len(data)
                    )
                    payload.extend(data)
                header = json.dumps({"files": tree}).encode()
                padded = header + bytes((-len(header)) % 4)
                archive.write_bytes(
                    struct.pack("<4I", 4, 8 + len(padded), 4 + len(padded), len(header))
                    + padded
                    + payload
                )

            pack()
            with self.assertRaisesRegex(ValueError, "preload"):
                update.verify_asar(archive, "a" * 40)
            entries["dist/preload/index.js"] = b""
            pack()
            with self.assertRaisesRegex(ValueError, "packed resource"):
                update.verify_asar(archive, "a" * 40)
            entries["dist/preload/index.js"] = b"preload"
            pack()
            update.verify_asar(archive, "a" * 40)


class FakeMac:
    def __init__(self):
        self.stops = []
        self.opened = []
        self.unhealthy = None
        self.unsafe = False
        self.running = None
        self.organizations = []

    def verify(self, app):
        if not (app / "Contents/Resources/app.asar").is_file():
            raise ValueError("missing ASAR")

    def pid(self, app):
        return self.running

    def live_host_pids(self):
        return set()

    def stop(self, app, build, before_quit, *, stopped_only=False):
        if self.unsafe:
            raise ValueError("chat state unavailable")
        if stopped_only and self.running:
            raise ValueError("Damaged installation must already be stopped")
        before_quit(self.organizations)
        self.running = None
        self.stops.append(build.commit if build else None)

    def launch(self, app, build, organizations):
        self.opened.append(build.commit)
        self.running = 123
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

    def test_retry_of_same_commit_preserves_previous_without_quit(self):
        self.installer.apply(self.candidate)
        stops = list(self.mac.stops)
        self.installer.apply(self.candidate)
        self.assertEqual(self.mac.stops, stops)
        self.installer.rollback()
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)

    def test_interruption_after_exchange_leaves_previous_recoverable(self):
        swap = self.mac.exchange

        def interrupt(left, right):
            swap(left, right)
            raise KeyboardInterrupt

        self.mac.organizations = ["first"]
        with (
            patch.object(self.mac, "exchange", interrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.installer.apply(self.candidate)
        self.assertEqual(update.read_build(self.app).commit, "b" * 40)
        self.assertTrue(self.installer.pending.exists())
        resumed = update.Installer(self.app, self.home, self.mac)
        with patch.object(self.mac, "launch", wraps=self.mac.launch) as launch:
            resumed.rollback()
            self.assertEqual(launch.call_args.args[2], ["first"])
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)
        self.assertFalse(resumed.pending.exists())

    def test_interrupted_automatic_restore_resumes_the_recovery_build(self):
        self.mac.unhealthy = "b" * 40
        launch = self.mac.launch

        def interrupt_recovery(app, build, organizations):
            if build.commit == "a" * 40:
                raise KeyboardInterrupt
            launch(app, build, organizations)

        with (
            patch.object(self.mac, "launch", interrupt_recovery),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.installer.apply(self.candidate)
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)
        self.installer.apply()
        self.assertEqual(self.mac.opened, ["b" * 40, "a" * 40])
        self.assertIsNone(self.installer.previous())

    def test_explicit_retry_finishes_an_interrupted_restoration(self):
        self.mac.unhealthy = "b" * 40
        exchange = self.mac.exchange

        def interrupt_after_restoring(left, right):
            exchange(left, right)
            if update.read_identity(left).commit == "a" * 40:
                raise KeyboardInterrupt

        with (
            patch.object(self.mac, "exchange", interrupt_after_restoring),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.installer.apply(self.candidate)
        self.assertTrue(json.loads(self.installer.pending.read_text())["restoring"])
        self.mac.unhealthy = None
        self.installer.apply(self.candidate)
        self.assertEqual(update.read_identity(self.app).commit, "a" * 40)
        self.assertEqual(self.mac.opened, ["b" * 40, "a" * 40])
        self.assertFalse(self.installer.pending.exists())

    def test_retry_after_recording_success_does_not_roll_back_twice(self):
        self.installer.apply(self.candidate)
        unlink = Path.unlink

        def interrupt(path, *args, **kwargs):
            if path == self.installer.pending:
                raise KeyboardInterrupt
            return unlink(path, *args, **kwargs)

        with (
            patch.object(Path, "unlink", interrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.installer.rollback()
        stops = list(self.mac.stops)
        self.installer.rollback()
        self.assertEqual(self.mac.stops, stops)
        self.assertEqual(self.installer.previous(), "b" * 40)
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)

    def test_retry_after_quit_can_finish_from_the_durable_stage(self):
        with (
            patch.object(self.mac, "exchange", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.installer.apply(self.candidate)
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)
        resumed = update.Installer(self.app, self.home, self.mac)
        shutil.rmtree(self.candidate)
        resumed.apply()
        self.assertEqual(update.read_build(self.app).commit, "b" * 40)
        resumed.rollback()
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)

    def add_chat_migration(self, app=None):
        resources = (app or self.candidate) / "Contents/Resources/resources"
        folder = resources / "chat-migrations"
        journal = folder / "meta/_journal.json"
        data = json.loads(journal.read_text())
        data["entries"].append({"tag": "0001_chat", "when": 101})
        journal.write_text(json.dumps(data))
        (folder / "0001_chat.sql").write_text("select 2")

    def test_rollback_allows_unapplied_migration(self):
        self.add_chat_migration()
        self.installer.apply(self.candidate)
        self.installer.rollback()
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)

    def test_rollback_refuses_applied_migration_before_quit(self):
        self.add_chat_migration()
        self.installer.apply(self.candidate)
        self.apply_chat_migration()
        with self.assertRaisesRegex(ValueError, "applied migration"):
            self.installer.rollback()
        self.assertEqual(self.mac.stops, ["a" * 40])

    def apply_chat_migration(self):
        org = self.home / "host/org"
        org.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(org / "chat.db")) as db:
            db.execute(
                "CREATE TABLE __drizzle_migrations (created_at INTEGER, hash TEXT)"
            )
            db.execute(
                "INSERT INTO __drizzle_migrations VALUES (101, ?)",
                (update.read_build(self.candidate).migrations["chat.db"][101],),
            )
            db.commit()

    def test_compatible_forward_repair_after_failed_build_migrates_database(self):
        self.add_chat_migration()
        self.mac.unhealthy = "b" * 40
        self.mac.organizations = ["original-org"]
        launch = self.mac.launch

        def launch_with_migration(app, build, organizations):
            self.apply_chat_migration()
            launch(app, build, organizations)

        with (
            patch.object(self.mac, "launch", launch_with_migration),
            self.assertRaisesRegex(ValueError, "applied migration"),
        ):
            self.installer.apply(self.candidate)
        pending = json.loads(self.installer.pending.read_text())
        fix = self.bundle(self.root / "repair", "c")
        with self.assertRaisesRegex(ValueError, "applied migration"):
            self.installer.apply(fix)
        self.add_chat_migration(fix)
        self.mac.unsafe = True
        with self.assertRaisesRegex(ValueError, "chat"):
            self.installer.apply(fix)
        self.assertEqual(self.mac.stops, ["a" * 40])
        self.assertEqual(json.loads(self.installer.pending.read_text()), pending)

        self.mac.unsafe = False
        self.mac.organizations = ["repair-org"]
        with (
            patch.object(self.mac, "exchange", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.installer.apply(fix)
        shutil.rmtree(fix)
        resumed = update.Installer(self.app, self.home, self.mac)
        with patch.object(self.mac, "launch", wraps=launch) as opened:
            resumed.apply()
            self.assertEqual(opened.call_args.args[2], ["original-org", "repair-org"])
        self.assertEqual(update.read_identity(self.app).commit, "c" * 40)
        self.assertEqual(resumed.previous(), "a" * 40)
        self.assertFalse(resumed.pending.exists())
        failed_archive = resumed.archive("b" * 40)
        self.assertEqual(update.read_identity(failed_archive).commit, "b" * 40)
        records = failed_archive.parent.glob("transaction-*.json")
        self.assertIn(pending, [json.loads(record.read_text()) for record in records])
        with self.assertRaisesRegex(ValueError, "applied migration"):
            resumed.rollback()

    def test_only_a_stopped_identified_damaged_bundle_can_be_recovered(self):
        self.installer.apply(self.candidate)
        (self.app / "Contents/Resources/app.asar").unlink()
        with self.assertRaisesRegex(ValueError, "already be stopped"):
            self.installer.rollback()
        self.mac.running = None
        plist = self.app / "Contents/Info.plist"
        info = plistlib.loads(plist.read_bytes())
        for key, bad, message in [
            ("CFBundleIdentifier", "com.superset.desktop", "identity"),
            ("ShortKitCommit", "c" * 40, "verified receipt"),
            ("ShortKitDaemonProtocols", "2/3", "protocols"),
        ]:
            plist.write_bytes(plistlib.dumps({**info, key: bad}))
            with self.assertRaisesRegex(ValueError, message):
                self.installer.rollback()
        plist.write_bytes(plistlib.dumps(info))
        self.installer.rollback()
        self.assertEqual(update.read_build(self.app).commit, "a" * 40)
        self.assertIsNone(self.installer.previous())

    def test_missing_install_refuses_surviving_terminal_daemons(self):
        shutil.rmtree(self.app)
        org = self.home / "host/org"
        org.mkdir(parents=True)
        mac = update.Mac(self.home)
        with (
            patch.object(mac, "pid", return_value=None),
            patch.object(update, "output", return_value=""),
            patch.object(update, "process_exists", return_value=True),
        ):
            for record, contents in [
                (self.home / "terminal-host.pid", "321"),
                (org / "pty-daemon-manifest.json", json.dumps({"pid": 321})),
            ]:
                record.write_text(contents)
                with self.assertRaisesRegex(ValueError, "Live terminal daemon"):
                    mac.stop(self.app, None, lambda _: self.fail("unsafe install"))
                record.unlink()

    def test_reinstall_checks_a_manifestless_daemon_socket(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            org = self.home / "host/org"
            org.mkdir(parents=True)
            key = update.hashlib.sha256(f"org:{self.home}".encode()).hexdigest()[:12]
            endpoint = Path(folder) / f"superset-ptyd-{key}.sock"
            mac = update.Mac(self.home)
            with (
                socket.socket(socket.AF_UNIX) as listener,
                patch.dict(update.os.environ, {"TMPDIR": folder}),
                patch.object(update, "output", return_value=""),
            ):
                listener.bind(str(endpoint))
                listener.listen(1)
                with self.assertRaisesRegex(ValueError, "Live terminal daemon socket"):
                    mac.check_no_terminal_daemons(self.app)

    def test_first_install_can_resume_after_exchange_without_original_artifact(self):
        shutil.rmtree(self.app)
        with (
            patch.object(self.mac, "launch", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.installer.apply(self.candidate)
        with self.assertRaisesRegex(ValueError, "No compatible predecessor"):
            self.installer.rollback()
        shutil.rmtree(self.candidate)
        self.installer.apply()
        self.assertEqual(update.read_build(self.app).commit, "b" * 40)
        self.assertIsNone(self.installer.previous())
        self.assertFalse(self.installer.pending.exists())

    def test_old_previous_pointer_still_selects_the_verified_backup(self):
        previous = self.installer.archive("b" * 40)
        previous.parent.mkdir(parents=True)
        self.mac.copy(self.candidate, previous)
        (self.installer.state / "previous").write_text("b" * 40)
        self.installer.rollback()
        self.assertEqual(update.read_build(self.app).commit, "b" * 40)
        self.assertEqual(self.installer.previous(), "a" * 40)

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

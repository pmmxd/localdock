from __future__ import annotations

import base64
import ctypes
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from localdock import __version__
from localdock.db import Database
from localdock.manager import ManagerApp, load_or_create_token
from localdock.service_runner import ServiceRunner
from localdock.validation import (
    COMMON_RESERVED_PORTS,
    ValidationError,
    suggested_port,
    validate_service_payload,
)
from localdock.worker import (
    BridgeError,
    StaticSiteServer,
    CommandJobManager,
    FileWatcher,
    SHUTDOWN_PATH,
    _make_directory,
    _move_path,
    _export_path,
    _export_to_folder,
    _copy_path,
    _list_path,
    _read_path,
    _trash_path,
    _write_file,
    perform_bridge_action,
    perform_open_with,
    resolve_bridge_path,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ENTRY = PROJECT_ROOT / "app.py"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, process: subprocess.Popen[bytes], timeout: float = 4) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read().decode("utf-8", errors="replace") if process.stdout else ""
            raise AssertionError(f"Worker exited before listening:\n{output}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("Worker did not start listening in time.")


def process_is_alive(pid: int) -> bool:
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class DatabaseAndValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / f".test-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.site = self.root / "site"
        self.site.mkdir()
        (self.site / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
        (self.site / "nested").mkdir()
        (self.site / "nested" / "home.html").write_text(
            "<h1>nested</h1>", encoding="utf-8"
        )
        self.database = Database(self.root / "data" / "test.db")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": "Demo",
            "root_path": str(self.site),
            "entry_file": "index.html",
            "port": 8123,
            "auto_open": True,
        }
        payload.update(overrides)
        return payload

    def test_database_crud(self) -> None:
        values = validate_service_payload(self.payload(), self.database)
        created = self.database.create_service(values)
        self.assertEqual(created["name"], "Demo")
        self.assertTrue(created["auto_open"])
        self.assertEqual(len(self.database.list_services()), 1)

        updated_values = validate_service_payload(
            self.payload(name="Updated", port=8124),
            self.database,
            created["id"],
        )
        updated = self.database.update_service(created["id"], updated_values)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["name"], "Updated")
        self.assertEqual(updated["port"], 8124)
        self.assertTrue(self.database.delete_service(created["id"]))
        self.assertEqual(self.database.list_services(), [])

    def test_nested_entry_and_duplicate_port(self) -> None:
        viewer = self.root / "viewer.exe"
        viewer.write_bytes(b"MZ")
        values = validate_service_payload(
            self.payload(entry_file="nested/home.html"), self.database
        )
        self.assertEqual(values["entry_file"], "nested/home.html")
        created = self.database.create_service(values)
        with self.assertRaises(ValidationError):
            validate_service_payload(self.payload(name="Second"), self.database)
        self.assertIsNotNone(self.database.get_service(created["id"]))

    def test_rejects_path_traversal_and_missing_entry(self) -> None:
        with self.assertRaises(ValidationError):
            validate_service_payload(
                self.payload(entry_file="../outside.html"), self.database
            )
        with self.assertRaises(ValidationError):
            validate_service_payload(
                self.payload(entry_file="missing.html"), self.database
            )
        with self.assertRaises(ValidationError):
            validate_service_payload(
                self.payload(root_path=str(self.root / "missing")), self.database
            )

    def test_manager_state_and_csv_export(self) -> None:
        values = validate_service_payload(self.payload(), self.database)
        self.database.create_service(values)
        runner = ServiceRunner(
            self.database,
            self.root,
            self.root / "logs",
            self.root / "runtime.json",
            APP_ENTRY,
        )
        app = ManagerApp(
            self.database,
            runner,
            PROJECT_ROOT / "web",
            "test-token",
            self.root,
        )
        state = app.state()
        self.assertEqual(state["version"], __version__)
        self.assertEqual(state["summary"]["total"], 1)
        self.assertEqual(state["summary"]["running"], 0)
        exported = app.export_csv().decode("utf-8-sig")
        self.assertIn("Demo", exported)
        self.assertIn("root_path", exported)
        backup = app.create_backup()
        try:
            self.assertTrue(backup.read_bytes().startswith(b"SQLite format 3"))
        finally:
            backup.unlink(missing_ok=True)

    def test_new_services_receive_unique_automatic_ports(self) -> None:
        runner = ServiceRunner(
            self.database,
            self.root,
            self.root / "logs",
            self.root / "runtime.json",
            APP_ENTRY,
        )
        app = ManagerApp(
            self.database,
            runner,
            PROJECT_ROOT / "web",
            "test-token",
            self.root,
        )
        first = app.create_service(self.payload(name="First", port=6000))
        second = app.create_service(self.payload(name="Second", port=6000))
        self.assertGreaterEqual(first["port"], 7000)
        self.assertGreaterEqual(second["port"], 7000)
        self.assertNotEqual(first["port"], second["port"])
        manual = app.create_service(
            self.payload(name="Manual", port=6100, port_mode="manual")
        )
        self.assertEqual(manual["port"], 6100)

    def test_manual_service_group_assignment(self) -> None:
        runner = ServiceRunner(
            self.database,
            self.root,
            self.root / "logs",
            self.root / "runtime.json",
            APP_ENTRY,
        )
        app = ManagerApp(
            self.database,
            runner,
            PROJECT_ROOT / "web",
            "test-token",
            self.root,
        )
        first = app.create_service(self.payload(name="Frontend", port=6200))
        other_site = self.root / "other-site"
        other_site.mkdir()
        (other_site / "index.html").write_text("other", encoding="utf-8")
        second = app.create_service(
            self.payload(
                name="Backend",
                port=6201,
                root_path=str(other_site),
            )
        )
        third_site = self.root / "third-site"
        third_site.mkdir()
        (third_site / "index.html").write_text("third", encoding="utf-8")
        third = app.create_service(
            self.payload(
                name="Worker",
                port=6202,
                root_path=str(third_site),
            )
        )
        self.assertEqual(first["group_name"], "")
        self.assertEqual(second["group_name"], "")

        result = app.assign_service_group(
            {
                "ids": [first["id"], second["id"]],
                "group_name": "My Project",
            }
        )
        self.assertEqual(result["updated"], 2)
        self.assertEqual(
            self.database.get_service(first["id"])["group_name"],
            "My Project",
        )
        self.assertEqual(
            self.database.get_service(second["id"])["group_name"],
            "My Project",
        )
        renamed = app.rename_service_group(
            {"old_name": "My Project", "new_name": "Renamed Project"}
        )
        self.assertEqual(renamed["updated"], 2)
        self.assertEqual(
            self.database.get_service(second["id"])["group_name"],
            "Renamed Project",
        )
        app.assign_service_group(
            {"ids": [third["id"]], "group_name": "Renamed Project"}
        )
        self.assertEqual(
            self.database.get_service(third["id"])["group_name"],
            "Renamed Project",
        )

        app.assign_service_group(
            {"ids": [first["id"]], "group_name": ""}
        )
        self.assertEqual(self.database.get_service(first["id"])["group_name"], "")
        self.assertEqual(
            self.database.get_service(second["id"])["group_name"],
            "Renamed Project",
        )

    def test_new_frontend_and_backend_auto_group(self) -> None:
        runner = ServiceRunner(
            self.database,
            self.root,
            self.root / "logs",
            self.root / "runtime.json",
            APP_ENTRY,
        )
        app = ManagerApp(
            self.database,
            runner,
            PROJECT_ROOT / "web",
            "test-token",
            self.root,
        )
        project = self.root / "sample-project"
        frontend = project / "frontend"
        backend = project / "backend"
        frontend.mkdir(parents=True)
        backend.mkdir(parents=True)
        (frontend / "index.html").write_text("frontend", encoding="utf-8")
        (backend / "index.html").write_text("backend", encoding="utf-8")
        front = app.create_service(
            self.payload(name="Sample Front", root_path=str(frontend))
        )
        back = app.create_service(
            self.payload(name="Sample Back", root_path=str(backend))
        )
        self.assertEqual(front["group_name"], "")
        self.assertEqual(back["group_name"], "sample-project")
        self.assertEqual(
            self.database.get_service(front["id"])["group_name"],
            "sample-project",
        )

    def test_automatic_port_avoids_reserved_services(self) -> None:
        chosen = suggested_port(self.database, 51820)
        self.assertNotIn(chosen, COMMON_RESERVED_PORTS)

    def test_bridge_configuration_is_validated(self) -> None:
        defaults = validate_service_payload(self.payload(), self.database)
        self.assertTrue(defaults["bridge_enabled"])
        self.assertTrue(defaults["write_enabled"])
        self.assertTrue(defaults["watch_enabled"])
        self.assertTrue(defaults["auto_reload"])
        self.assertEqual(Path(defaults["bridge_root"]), self.site.resolve())

        commands_json = json.dumps(
            [
                {
                    "id": "print-value",
                    "label": "Print value",
                    "executable": sys.executable,
                    "args": ["-c", "print('ok')"],
                    "timeout": 30,
                    "detached": True,
                }
            ]
        )
        viewer = self.root / "viewer.exe"
        viewer.write_bytes(b"MZ")
        values = validate_service_payload(
            self.payload(
                bridge_enabled=True,
                bridge_root=str(self.root),
                external_app_path=str(viewer),
                write_enabled=True,
                watch_enabled=True,
                watch_root=str(self.root),
                auto_reload=True,
                command_enabled=True,
                commands_json=commands_json,
                auto_command_id="print-value",
            ),
            self.database,
        )
        self.assertTrue(values["bridge_enabled"])
        self.assertTrue(values["write_enabled"])
        self.assertTrue(values["command_enabled"])
        self.assertTrue(values["watch_enabled"])
        self.assertEqual(values["watch_interval"], 10)
        self.assertEqual(Path(values["external_app_path"]), viewer.resolve())
        self.assertEqual(values["auto_command_id"], "print-value")
        self.assertEqual(json.loads(values["commands_json"])[0]["id"], "print-value")
        self.assertTrue(json.loads(values["commands_json"])[0]["detached"])
        self.assertEqual(Path(values["bridge_root"]), self.root.resolve())

        automatic_watch = validate_service_payload(
            self.payload(
                bridge_enabled=True,
                bridge_root=str(self.root),
                watch_enabled=True,
                watch_root="",
            ),
            self.database,
        )
        self.assertEqual(Path(automatic_watch["watch_root"]), self.root.resolve())

        site_watch = validate_service_payload(
            self.payload(watch_enabled=True, watch_root=""),
            self.database,
        )
        self.assertEqual(Path(site_watch["watch_root"]), self.site.resolve())

        created = self.database.create_service(values)
        self.assertTrue(created["bridge_enabled"])

        with self.assertRaises(ValidationError):
            validate_service_payload(
                self.payload(
                    bridge_enabled=True,
                    bridge_root=str(self.root / "missing"),
                ),
                self.database,
            )
        with self.assertRaises(ValidationError):
            validate_service_payload(
                self.payload(
                    bridge_enabled=True,
                    bridge_root=str(self.root),
                    command_enabled=True,
                    commands_json=commands_json,
                    watch_enabled=True,
                    watch_root=str(self.root),
                    auto_command_id="missing-command",
                ),
                self.database,
            )
        with self.assertRaises(ValidationError):
            validate_service_payload(
                self.payload(
                    command_enabled=True,
                    commands_json='[{"id":"bad id","executable":"cmd"}]',
                ),
                self.database,
            )
        with self.assertRaises(ValidationError):
            validate_service_payload(
                self.payload(
                    bridge_enabled=False,
                    write_enabled=True,
                ),
                self.database,
            )

    def test_stale_backend_without_health_does_not_kill_reused_pid(self) -> None:
        runtime_path = self.root / "runtime.json"
        runtime_path.write_text(
            json.dumps(
                {
                    "service-id": {
                        "service_id": "service-id",
                        "service_type": "node",
                        "pid": os.getpid(),
                        "port": 1,
                        "health_path": "/api/health",
                    }
                }
            ),
            encoding="utf-8",
        )
        runner = ServiceRunner(
            self.database,
            self.root,
            self.root / "logs",
            runtime_path,
            APP_ENTRY,
        )
        with (
            patch.object(runner, "_backend_health", return_value=False),
            patch.object(runner, "_terminate_pid_tree") as terminate,
        ):
            runner.cleanup_stale_workers()
        terminate.assert_not_called()
        self.assertEqual(runner._runtime, {})

    def test_manager_token_is_stable(self) -> None:
        token_path = self.root / "manager.token"
        first = load_or_create_token(token_path)
        second = load_or_create_token(token_path)
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 32)


class StaticWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / f".test-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.site = self.root / "site"
        self.site.mkdir()
        (self.site / "index.html").write_text("<h1>Hello</h1>", encoding="utf-8")
        (self.site / "style.css").write_text("body { color: red; }", encoding="utf-8")
        (self.root / "secret.txt").write_text("secret", encoding="utf-8")
        self.port = free_port()
        self.token = "worker-test-token"
        config = {
            "service_id": "a" * 32,
            "root_path": str(self.site),
            "entry_file": "index.html",
            "port": self.port,
            "token": self.token,
            "bridge_enabled": True,
            "bridge_root": str(self.root),
            "write_enabled": True,
            "backup_dir": str(self.root / "backups"),
            "watch_enabled": True,
            "watch_root": str(self.root),
            "watch_interval": 3,
            "auto_reload": False,
            "command_enabled": True,
            "commands": [
                {
                    "id": "print-value",
                    "label": "Print value",
                    "executable": sys.executable,
                    "args": [
                        "-c",
                        "import sys; print(sys.argv[1])",
                        "{value}",
                    ],
                    "cwd": "",
                    "timeout": 30,
                    "allow_args": False,
                },
                {
                    "id": "sleep",
                    "label": "Sleep",
                    "executable": sys.executable,
                    "args": ["-c", "import time; time.sleep(30)"],
                    "cwd": "",
                    "timeout": 60,
                    "allow_args": False,
                },
            ],
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(config, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        creation_flags = (
            subprocess.CREATE_NO_WINDOW
            if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        self.process = subprocess.Popen(
            [sys.executable, str(APP_ENTRY), "--worker", encoded],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        wait_for_port(self.port, self.process)

    def tearDown(self) -> None:
        if self.process.poll() is None:
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
            try:
                connection.request(
                    "POST",
                    SHUTDOWN_PATH,
                    headers={"X-LocalDock-Worker-Token": self.token},
                )
                connection.getresponse().read()
            except OSError:
                pass
            finally:
                connection.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=2)
        if self.process.stdout is not None:
            self.process.stdout.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def request(self, path: str) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post_json(
        self, path: str, payload: dict[str, object]
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            body = json.dumps(payload).encode("utf-8")
            connection.request(
                "POST",
                path,
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_serves_index_css_and_redirect(self) -> None:
        status, headers, body = self.request("/")
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/index.html")

        status, headers, _ = self.request("/?subject=english&grade=7")
        self.assertEqual(status, 302)
        self.assertEqual(
            headers["Location"],
            "/index.html?subject=english&grade=7",
        )

        status, headers, body = self.request("/index.html")
        self.assertEqual(status, 200)
        self.assertIn(b"Hello", body)
        self.assertIn("text/html", headers["Content-Type"])

        status, headers, body = self.request("/style.css")
        self.assertEqual(status, 200)
        self.assertIn(b"color: red", body)

        nested = self.site / "nested"
        nested.mkdir()
        (nested / "index.html").write_text("nested", encoding="utf-8")
        status, headers, _ = self.request("/nested/?grade=2")
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/nested/index.html?grade=2")

    def test_rejects_encoded_parent_traversal(self) -> None:
        status, _, body = self.request("/%2e%2e/secret.txt")
        self.assertEqual(status, 403)
        self.assertNotIn(b"secret", body)

    def test_missing_file_is_404(self) -> None:
        status, _, _ = self.request("/missing.js")
        self.assertEqual(status, 404)

    def test_dynamic_status_and_bridge_script(self) -> None:
        status, _, body = self.request("/api/status")
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(payload["server_mode"])
        self.assertTrue(payload["bridge"])
        self.assertTrue(payload["watch"]["enabled"])
        self.assertIn("open", payload["features"])
        self.assertIn("openWith", payload["features"])
        self.assertIn("export", payload["features"])
        self.assertIn("exportToFolder", payload["features"])
        self.assertIn("list", payload["features"])
        self.assertIn("read", payload["features"])
        self.assertIn("copy", payload["features"])
        self.assertEqual(payload["book_count"], 0)

        status, headers, body = self.request("/__localdock/bridge.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn(b"window.LocalDock", body)
        self.assertIn(b"/__localdock/api/trash", body)
        self.assertIn(b"/__localdock/api/run", body)
        self.assertIn(b"/__localdock/api/open-with", body)
        self.assertIn(b"/__localdock/api/export", body)
        self.assertIn(b"/__localdock/api/export-to-folder", body)
        self.assertIn(b"/__localdock/api/list", body)
        self.assertIn(b"/__localdock/api/read", body)
        self.assertIn(b"/__localdock/api/copy", body)

    def test_injected_bridge_script_and_manual_refresh(self) -> None:
        (self.site / "index.html").write_text(
            "<html><head><title>Bridge</title></head><body>"
            "<script>window.pageScriptRan = true;</script></body></html>",
            encoding="utf-8",
        )
        status, _, body = self.request("/index.html")
        self.assertEqual(status, 200)
        self.assertIn(b"/__localdock/bridge.js", body)
        self.assertLess(
            body.index(b"/__localdock/bridge.js"),
            body.index(b"window.pageScriptRan"),
        )

        status, _, body = self.post_json("/api/rescan", {})
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["watch"]["enabled"])

    def test_bridge_rejects_relative_paths_outside_default_root(self) -> None:
        status, _, body = self.post_json("/api/open", {"path": "../secret.txt"})
        self.assertEqual(status, 400)
        self.assertIn("相对路径超出默认操作目录", body.decode("utf-8"))

        for endpoint in (
            "/api/open-with",
            "/api/export",
            "/api/export-to-folder",
            "/api/list",
            "/api/read",
            "/api/copy",
        ):
            status, _, body = self.post_json(
                endpoint,
                {
                    "path": "../secret.txt",
                    "source": "../secret.txt",
                    "destination": "../other.txt",
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("相对路径超出默认操作目录", body.decode("utf-8"))

    def test_bridge_opens_allowed_file_through_helper(self) -> None:
        config = {
            "service_id": "a" * 32,
            "root_path": str(self.site),
            "entry_file": "index.html",
            "port": self.port,
            "token": self.token,
            "bridge_enabled": True,
            "bridge_root": str(self.root),
        }
        self.assertEqual(
            resolve_bridge_path(config, "site/index.html"),
            (self.site / "index.html").resolve(),
        )
        with patch("localdock.worker.os.startfile") as startfile:
            resolved = perform_bridge_action(config, "open", "site/index.html")
        startfile.assert_called_once_with(str(resolved))

    def test_bridge_can_choose_external_software_and_export_file(self) -> None:
        config = {
            "root_path": str(self.site),
            "bridge_enabled": True,
            "bridge_root": str(self.site),
            "write_enabled": True,
        }
        video = self.root / "sample.mkv"
        video.write_bytes(b"video")
        with patch("localdock.worker._launch_with_dialog") as chooser:
            resolved = perform_open_with(config, str(video))
        chooser.assert_called_once_with(video.resolve())
        self.assertEqual(resolved, video.resolve())

        viewer = self.root / "viewer.exe"
        viewer.write_bytes(b"MZ")
        direct_config = dict(config, external_app_path=str(viewer))
        with (
            patch("localdock.worker.subprocess.Popen") as launch,
            patch("localdock.worker._launch_with_dialog") as chooser,
        ):
            perform_open_with(direct_config, str(video))
        launch.assert_called_once()
        chooser.assert_not_called()

        with (
            patch("localdock.worker.os.startfile", side_effect=OSError("no app")),
            patch("localdock.worker._launch_with_dialog") as fallback,
        ):
            perform_bridge_action(config, "open", str(video))
        fallback.assert_called_once_with(video.resolve())

        executable = self.root / "unsafe.exe"
        executable.write_bytes(b"MZ")
        with self.assertRaises(BridgeError):
            perform_open_with(config, str(executable))

        exported = self.root / "exported.mkv"
        with patch("localdock.worker._choose_export_target", return_value=exported):
            result = _export_path(config, str(video))
        self.assertEqual(result, exported.resolve())
        self.assertEqual(exported.read_bytes(), b"video")

        other = self.root / "other"
        other.mkdir()
        duplicate = other / "sample.mkv"
        duplicate.write_bytes(b"other-video")
        exports = self.root / "exports"
        exports.mkdir()
        (exports / "sample.mkv").write_bytes(b"existing")
        with patch("localdock.worker._choose_export_folder", return_value=exports):
            batch = _export_to_folder(
                config,
                {"paths": [str(video), str(duplicate)]},
            )
        self.assertFalse(batch["canceled"])
        self.assertEqual(batch["failures"], [])
        self.assertEqual([Path(value).name for value in batch["paths"]], ["sample (1).mkv", "sample (2).mkv"])
        self.assertEqual((exports / "sample.mkv").read_bytes(), b"existing")
        self.assertEqual((exports / "sample (1).mkv").read_bytes(), b"video")
        self.assertEqual((exports / "sample (2).mkv").read_bytes(), b"other-video")

        listed = _list_path(config, {"path": ".", "include_hidden": True})
        self.assertTrue(any(item["name"] == "index.html" for item in listed["entries"]))
        read = _read_path(config, {"path": str(self.site / "index.html")})
        self.assertIn("Hello", read["content"])

        copied = _copy_path(
            config,
            {
                "source": str(self.root / "secret.txt"),
                "destination": str(self.site / "imported.txt"),
            },
        )
        self.assertEqual(copied.read_text(encoding="utf-8"), "secret")
        with self.assertRaises(BridgeError):
            _copy_path(
                dict(config, write_enabled=False),
                {
                    "source": str(self.root / "secret.txt"),
                    "destination": str(self.site / "read-only-copy.txt"),
                },
            )
        self.assertFalse((self.site / "read-only-copy.txt").exists())

    def test_bridge_disabled_rejects_actions(self) -> None:
        config = {
            "root_path": str(self.site),
            "bridge_enabled": False,
            "bridge_root": None,
        }
        with self.assertRaises(BridgeError):
            resolve_bridge_path(config, "index.html")

    def test_write_operation_rejects_path_outside_root(self) -> None:
        status, _, body = self.post_json(
            "/api/write",
            {"path": "../outside.txt", "content": "hello", "create": True},
        )
        self.assertEqual(status, 400)
        self.assertIn("相对路径超出默认操作目录", body.decode("utf-8"))

    def test_command_job_runs_and_can_be_cancelled(self) -> None:
        status, _, body = self.post_json(
            "/__localdock/api/run",
            {
                "command": "print-value",
                "params": {"value": "hello-job"},
            },
        )
        self.assertEqual(status, 202)
        job_id = json.loads(body.decode("utf-8"))["job"]["id"]
        deadline = time.monotonic() + 8
        job = None
        while time.monotonic() < deadline:
            status, _, body = self.request(f"/__localdock/api/jobs/{job_id}")
            self.assertEqual(status, 200)
            job = json.loads(body.decode("utf-8"))["job"]
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.1)
        self.assertEqual(job["status"], "succeeded")
        self.assertIn("hello-job", job["output"])

        status, _, body = self.post_json(
            "/__localdock/api/run",
            {"command": "sleep"},
        )
        self.assertEqual(status, 202)
        sleep_id = json.loads(body.decode("utf-8"))["job"]["id"]
        status, _, _ = self.post_json(
            f"/__localdock/api/jobs/{sleep_id}/cancel",
            {},
        )
        self.assertEqual(status, 200)

    def test_write_move_mkdir_and_trash_endpoints(self) -> None:
        status, _, _ = self.post_json(
            "/__localdock/api/mkdir",
            {"path": "site/newdir"},
        )
        self.assertEqual(status, 200)

        status, _, _ = self.post_json(
            "/api/write",
            {
                "path": "site/newdir/new.txt",
                "content": "hello",
                "create": True,
            },
        )
        self.assertEqual(status, 200)
        new_file = self.site / "newdir" / "new.txt"
        self.assertEqual(new_file.read_text(encoding="utf-8"), "hello")

        status, _, _ = self.post_json(
            "/__localdock/api/move",
            {
                "source": "site/newdir/new.txt",
                "destination": "site/moved.txt",
            },
        )
        self.assertEqual(status, 200)
        moved_file = self.site / "moved.txt"
        self.assertTrue(moved_file.is_file())

        status, _, _ = self.post_json(
            "/api/delete",
            {"path": "site/moved.txt"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(moved_file.exists())

        status, _, body = self.post_json(
            "/api/write",
            {
                "path": "site/index.html",
                "content": "<h1>Updated</h1>",
                "overwrite": True,
            },
        )
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(Path(payload["backup_path"]).is_file())
        self.assertIn("Updated", (self.site / "index.html").read_text(encoding="utf-8"))


class FileOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / f".test-{uuid.uuid4().hex}"
        self.site = self.root / "site"
        self.site.mkdir(parents=True)
        self.file = self.site / "document.txt"
        self.file.write_text("original", encoding="utf-8")
        self.config = {
            "service_id": "b" * 32,
            "root_path": str(self.site),
            "entry_file": "index.html",
            "bridge_enabled": True,
            "bridge_root": str(self.root),
            "write_enabled": True,
            "backup_dir": str(self.root / "backups"),
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_write_file_atomically_with_backup(self) -> None:
        result = _write_file(
            self.config,
            {
                "path": "site/document.txt",
                "content": "updated",
                "overwrite": True,
            },
        )
        self.assertEqual(self.file.read_text(encoding="utf-8"), "updated")
        backup = Path(result["backup_path"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_text(encoding="utf-8"), "original")

    def test_write_can_create_new_file(self) -> None:
        target = self.site / "new.txt"
        _write_file(
            self.config,
            {
                "path": "site/new.txt",
                "content": "hello",
                "create": True,
            },
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "hello")

    def test_absolute_external_file_write_uses_quarantine_backup(self) -> None:
        external = self.root / "external.txt"
        external.write_text("outside-default-root", encoding="utf-8")
        config = dict(self.config, bridge_root=str(self.site))
        result = _write_file(
            config,
            {
                "path": str(external),
                "content": "updated-system-file",
                "overwrite": True,
            },
        )
        self.assertEqual(external.read_text(encoding="utf-8"), "updated-system-file")
        backup = Path(result["backup_path"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_text(encoding="utf-8"), "outside-default-root")
        self.assertIn("external", backup.parts)

    def test_move_and_mkdir_stay_inside_allowed_root(self) -> None:
        folder = _make_directory(self.config, {"path": "site/subdir"})
        self.assertTrue(folder.is_dir())
        moved = _move_path(
            self.config,
            {
                "source": "site/document.txt",
                "destination": "site/subdir/document.txt",
            },
        )
        self.assertEqual(moved, (folder / "document.txt").resolve())
        self.assertFalse(self.file.exists())
        with self.assertRaises(BridgeError):
            _move_path(
                self.config,
                {
                    "source": "site/subdir/document.txt",
                    "destination": "../outside.txt",
                },
            )

    def test_trash_uses_windows_recycle_helper(self) -> None:
        with patch("localdock.worker._send_to_recycle_bin") as recycle:
            resolved = _trash_path(self.config, "site/document.txt")
        recycle.assert_called_once_with(self.file.resolve())
        self.assertEqual(resolved, self.file.resolve())

    def test_write_rejects_disabled_permission(self) -> None:
        config = dict(self.config)
        config["write_enabled"] = False
        with self.assertRaises(BridgeError):
            _write_file(
                config,
                {
                    "path": "site/new.txt",
                    "content": "hello",
                    "create": True,
                },
            )

    def test_file_watcher_reports_change(self) -> None:
        watcher = FileWatcher(self.root, interval=3, debounce=2)
        subscriber = watcher.subscribe()
        try:
            watcher.start()
            time.sleep(0.3)
            self.file.write_text("changed by watcher", encoding="utf-8")
            event = subscriber.get(timeout=8)
        finally:
            watcher.close()
        self.assertEqual(event["kind"], "change")
        self.assertTrue(
            any(
                item["action"] == "modified"
                and item["path"].replace("\\", "/") == "site/document.txt"
                for item in event["events"]
            )
        )


class AutoCommandCoordinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / f".test-{uuid.uuid4().hex}"
        self.site = self.root / "site"
        self.site.mkdir(parents=True)
        (self.site / "index.html").write_text("ok", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_watch_batch_starts_configured_rebuild_command(self) -> None:
        class FakeWatcher:
            def __init__(self) -> None:
                self.events: list[tuple[str, list[dict[str, object]]]] = []

            def publish_control(
                self, kind: str, events: list[dict[str, object]]
            ) -> None:
                self.events.append((kind, events))

        class FakeManager:
            def __init__(self) -> None:
                self.calls = []
                self.callback = None

            def run(self, payload, on_complete=None):
                self.calls.append(payload)
                self.callback = on_complete
                return {
                    "id": "c" * 32,
                    "command": payload["command"],
                    "status": "queued",
                }

        config = {
            "service_id": "d" * 32,
            "root_path": str(self.site),
            "entry_file": "index.html",
            "port": 0,
            "token": "test",
            "watch_enabled": True,
            "watch_root": str(self.root),
            "command_enabled": True,
            "commands": [],
            "auto_command_id": "rebuild-index",
        }
        server = StaticSiteServer(("127.0.0.1", 0), config)
        watcher = FakeWatcher()
        manager = FakeManager()
        server.watcher = watcher
        server.command_manager = manager
        try:
            server.handle_watch_batch(
                [{"action": "modified", "path": "data/book.pdf"}]
            )
            self.assertEqual(manager.calls[0]["command"], "rebuild-index")
            self.assertEqual(watcher.events[0][1][0]["action"], "started")

            manager.callback(
                {
                    "id": "c" * 32,
                    "command": "rebuild-index",
                    "status": "succeeded",
                    "exit_code": 0,
                    "error": None,
                }
            )
            self.assertEqual(watcher.events[-1][1][0]["action"], "succeeded")

            server.handle_watch_batch(
                [{"action": "modified", "path": "site/index.html"}]
            )
            self.assertEqual(len(manager.calls), 1)
        finally:
            server.server_close()


class AutoCommandWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / f".test-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_file_change_runs_registered_command(self) -> None:
        root = self.root
        site = root / "site"
        site.mkdir(parents=True)
        (site / "index.html").write_text("ok", encoding="utf-8")
        port = free_port()
        generated = site / "generated.txt"
        script = (
            "from pathlib import Path; "
            f"Path({str(generated)!r}).write_text('rebuilt', encoding='utf-8')"
        )
        config = {
            "service_id": "e" * 32,
            "root_path": str(site),
            "entry_file": "index.html",
            "port": port,
            "token": "auto-command-token",
            "watch_enabled": True,
            "watch_root": str(root),
            "watch_interval": 3,
            "auto_reload": True,
            "command_enabled": True,
            "commands": [
                {
                    "id": "rebuild",
                    "label": "Rebuild",
                    "executable": sys.executable,
                    "args": ["-c", script],
                    "cwd": "",
                    "timeout": 30,
                    "allow_args": False,
                }
            ],
            "auto_command_id": "rebuild",
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(config, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        creation_flags = (
            subprocess.CREATE_NO_WINDOW
            if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        process = subprocess.Popen(
            [sys.executable, str(APP_ENTRY), "--worker", encoded],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        try:
            wait_for_port(port, process)
            (root / "trigger.txt").write_text("changed", encoding="utf-8")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not generated.exists():
                time.sleep(0.1)
            self.assertTrue(generated.is_file())
            self.assertEqual(generated.read_text(encoding="utf-8"), "rebuilt")
        finally:
            if process.poll() is None:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", port, timeout=1
                )
                try:
                    connection.request(
                        "POST",
                        SHUTDOWN_PATH,
                        headers={"X-LocalDock-Worker-Token": config["token"]},
                    )
                    connection.getresponse().read()
                except OSError:
                    pass
                finally:
                    connection.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=2)
            if process.stdout is not None:
                process.stdout.close()

    def test_command_manager_cancel_reaches_cancelled_state(self) -> None:
        manager = CommandJobManager(
            [
                {
                    "id": "sleep",
                    "label": "Sleep",
                    "executable": sys.executable,
                    "args": ["-c", "import time; time.sleep(30)"],
                    "cwd": "",
                    "timeout": 60,
                    "allow_args": False,
                }
            ],
            self.root,
        )
        try:
            job = manager.run({"command": "sleep"})
            manager.cancel(job["id"])
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                current = manager.get(job["id"])
                if current["status"] == "cancelled":
                    break
                time.sleep(0.1)
            self.assertEqual(current["status"], "cancelled")
        finally:
            manager.close_all()

    def test_detached_command_is_not_killed_by_timeout(self) -> None:
        marker = self.root / "detached-finished.txt"
        script = (
            "import time; from pathlib import Path; time.sleep(1.2); "
            f"Path({str(marker)!r}).write_text('ok', encoding='utf-8')"
        )
        manager = CommandJobManager(
            [
                {
                    "id": "launch-gui",
                    "label": "Launch GUI",
                    "executable": sys.executable,
                    "args": ["-c", script],
                    "cwd": "",
                    "timeout": 1,
                    "allow_args": False,
                    "detached": True,
                }
            ],
            self.root,
        )
        try:
            job = manager.run({"command": "launch-gui"})
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(0.1)
            self.assertTrue(marker.is_file())
            self.assertEqual(manager.get(job["id"])["status"], "succeeded")
        finally:
            manager.close_all()

    @unittest.skipUnless(os.name == "nt", "cmd wrapper test requires Windows")
    def test_command_manager_runs_cmd_wrapper(self) -> None:
        tools = self.root / "tools"
        tools.mkdir()
        wrapper = tools / "run.cmd"
        wrapper.write_text("@echo off\r\necho wrapper:%1\r\n", encoding="utf-8")
        manager = CommandJobManager(
            [
                {
                    "id": "wrapper",
                    "label": "Wrapper",
                    "executable": str(wrapper),
                    "args": ["{value}"],
                    "cwd": "",
                    "timeout": 10,
                    "allow_args": False,
                }
            ],
            self.root,
        )
        try:
            job = manager.run(
                {
                    "command": "wrapper",
                    "params": {"value": "hello-wrapper"},
                }
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                current = manager.get(job["id"])
                if current["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.1)
            self.assertEqual(current["status"], "succeeded")
            self.assertIn("wrapper:hello-wrapper", current["output"])
        finally:
            manager.close_all()


class NodeBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / f".test-node-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.db = Database(self.root / "data" / "test.db")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_node_backend_starts_checks_health_and_stops(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not available")
        (self.root / "server.mjs").write_text(
            "import http from 'node:http';\n"
            "const port = Number(process.env.PORT);\n"
            "http.createServer((req, res) => {\n"
            "  res.writeHead(200, {'content-type': 'application/json'});\n"
            "  res.end(JSON.stringify({ok: true, env: process.env.TEST_FLAG || '', pid: process.pid}));\n"
            "}).listen(port, '127.0.0.1');\n",
            encoding="utf-8",
        )
        (self.root / ".env").write_text("TEST_FLAG=loaded\n", encoding="utf-8")
        port = free_port()
        values = validate_service_payload(
            {
                "name": "Node Fixture",
                "service_type": "node",
                "root_path": str(self.root),
                "port": port,
                "auto_open": False,
                "backend_json": {
                    "runtime_path": node,
                    "args": ["server.mjs"],
                    "cwd": "",
                    "env_file": ".env",
                    "health_path": "/health",
                    "startup_timeout": 10,
                    "shutdown_timeout": 3,
                    "auto_restart": True,
                    "restart_delay": 1,
                },
            },
            self.db,
        )
        self.assertEqual(values["service_type"], "node")
        self.assertFalse(values["bridge_enabled"])
        self.assertEqual(json.loads(values["backend_json"])["args"], ["server.mjs"])
        service = self.db.create_service(values)
        runner = ServiceRunner(
            self.db,
            self.root / "data",
            self.root / "logs",
            self.root / "runtime.json",
            APP_ENTRY,
        )
        try:
            runner.start(service)
            status, pid, _ = runner.status(service)
            self.assertEqual(status, "running")
            self.assertGreater(pid or 0, 0)
            self.assertEqual(runner.url_for(service), f"http://127.0.0.1:{port}/")
            self.assertIn("starting backend", runner.read_log(service["id"]))
            first_pid = pid
            runner._processes[service["id"]].kill()
            deadline = time.monotonic() + 7
            restarted_pid = None
            while time.monotonic() < deadline:
                status, current_pid, _ = runner.status(service)
                if status == "running" and current_pid and current_pid != first_pid:
                    restarted_pid = current_pid
                    break
                time.sleep(0.2)
            self.assertIsNotNone(restarted_pid)
            self.assertTrue(runner.stop(service["id"]))
            self.assertEqual(runner.status(service)[0], "stopped")
        finally:
            runner.stop_all()

    def test_python_backend_starts_checks_health_and_stops(self) -> None:
        (self.root / "server.py").write_text(
            "import json\n"
            "import os\n"
            "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
            "\n"
            "class Handler(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        if self.path != '/api/health':\n"
            "            self.send_error(404)\n"
            "            return\n"
            "        body = json.dumps({\n"
            "            'runtime': os.environ.get('BACKEND_KIND'),\n"
            "            'port': os.environ.get('PORT'),\n"
            "        }).encode('utf-8')\n"
            "        self.send_response(200)\n"
            "        self.send_header('Content-Type', 'application/json')\n"
            "        self.send_header('Content-Length', str(len(body)))\n"
            "        self.end_headers()\n"
            "        self.wfile.write(body)\n"
            "\n"
            "    def log_message(self, format, *args):\n"
            "        pass\n"
            "\n"
            "port = int(os.environ['PORT'])\n"
            "ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()\n",
            encoding="utf-8",
        )
        (self.root / ".env").write_text("BACKEND_KIND=python\n", encoding="utf-8")
        port = free_port()
        values = validate_service_payload(
            {
                "name": "Python Fixture",
                "service_type": "node",
                "root_path": str(self.root),
                "port": port,
                "auto_open": False,
                "backend_json": {
                    "runtime_path": sys.executable,
                    "args": ["server.py"],
                    "cwd": "",
                    "env_file": ".env",
                    "health_path": "/api/health",
                    "startup_timeout": 10,
                    "shutdown_timeout": 3,
                    "auto_restart": False,
                    "restart_delay": 1,
                },
            },
            self.db,
        )
        service = self.db.create_service(values)
        runner = ServiceRunner(
            self.db,
            self.root / "data",
            self.root / "logs",
            self.root / "runtime.json",
            APP_ENTRY,
        )
        try:
            runner.start(service)
            self.assertEqual(runner.status(service)[0], "running")
            self.assertIn("starting backend", runner.read_log(service["id"]))
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request("GET", "/api/health")
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
            finally:
                connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(payload, {"runtime": "python", "port": str(port)})
            self.assertTrue(runner.stop(service["id"]))
            self.assertEqual(runner.status(service)[0], "stopped")
        finally:
            runner.stop_all()

    def test_backend_stop_terminates_spawned_child_process(self) -> None:
        (self.root / "child.py").write_text(
            "import os\n"
            "import sys\n"
            "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
            "\n"
            "with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
            "    handle.write(str(os.getpid()))\n"
            "\n"
            "class Handler(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        self.send_response(200)\n"
            "        self.end_headers()\n"
            "\n"
            "    def log_message(self, format, *args):\n"
            "        pass\n"
            "\n"
            "ThreadingHTTPServer(\n"
            "    ('127.0.0.1', int(os.environ['PORT'])), Handler\n"
            ").serve_forever()\n",
            encoding="utf-8",
        )
        (self.root / "parent.py").write_text(
            "import subprocess\n"
            "import sys\n"
            "import time\n"
            "\n"
            "subprocess.Popen([sys.executable, 'child.py', 'child.pid'])\n"
            "while True:\n"
            "    time.sleep(1)\n",
            encoding="utf-8",
        )
        port = free_port()
        values = validate_service_payload(
            {
                "name": "Child Process Fixture",
                "service_type": "node",
                "root_path": str(self.root),
                "port": port,
                "auto_open": False,
                "backend_json": {
                    "runtime_path": sys.executable,
                    "args": ["parent.py"],
                    "health_path": "/api/health",
                    "startup_timeout": 10,
                    "shutdown_timeout": 3,
                },
            },
            self.db,
        )
        service = self.db.create_service(values)
        runner = ServiceRunner(
            self.db,
            self.root / "data",
            self.root / "logs",
            self.root / "runtime.json",
            APP_ENTRY,
        )
        child_pid = 0
        try:
            runner.start(service)
            pid_path = self.root / "child.pid"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not pid_path.exists():
                time.sleep(0.05)
            self.assertTrue(pid_path.is_file())
            child_pid = int(pid_path.read_text(encoding="utf-8"))
            self.assertTrue(process_is_alive(child_pid))
            self.assertTrue(runner.stop(service["id"]))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and process_is_alive(child_pid):
                time.sleep(0.1)
            self.assertFalse(process_is_alive(child_pid))
        finally:
            runner.stop_all()
            if child_pid and process_is_alive(child_pid) and os.name == "nt":
                subprocess.run(
                    ["taskkill.exe", "/PID", str(child_pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )


if __name__ == "__main__":
    unittest.main()

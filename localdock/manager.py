from __future__ import annotations

import csv
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .config import (
    APP_NAME,
    AUTO_SERVICE_PORT_START,
    DATA_DIR,
    MANAGER_HOST,
    MANAGER_PORT,
    RUNTIME_PATH,
    TOKEN_PATH,
    WEB_DIR,
)
from .db import Database
from .service_runner import ServiceError, ServiceRunner
from .validation import (
    ValidationError,
    suggested_port,
    validate_service_payload,
)


class ApiError(RuntimeError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


PROJECT_SERVICE_DIRS = {
    "frontend",
    "backend",
    "server",
    "client",
    "localdock-site",
    "web",
    "api",
    "ui",
    "dist",
}


def project_root_for_path(value: str) -> Path:
    path = Path(value)
    parts = list(path.parts)
    while len(parts) > 1 and parts[-1].casefold() in PROJECT_SERVICE_DIRS:
        parts.pop()
    return Path(*parts)


def console_write(message: str) -> None:
    stream = getattr(sys, "stdout", None)
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except (OSError, ValueError):
        pass


def load_or_create_token(path: Path = TOKEN_PATH) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        token = path.read_text(encoding="ascii").strip()
        if 32 <= len(token) <= 128 and re.fullmatch(r"[A-Za-z0-9_-]+", token):
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="ascii")
    return token


class ManagerServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        manager: "ManagerApp",
    ) -> None:
        self.manager = manager
        super().__init__(server_address, ManagerRequestHandler)


class ManagerApp:
    def __init__(
        self,
        database: Database,
        runner: ServiceRunner,
        web_dir: Path,
        token: str,
        data_dir: Path,
        manager_url: str = "",
    ) -> None:
        self.database = database
        self.runner = runner
        self.web_dir = Path(web_dir)
        self.token = token
        self.data_dir = Path(data_dir)
        self.manager_url = manager_url
        self.server: ManagerServer | None = None
        self._shutting_down = False
        self._mutation_lock = threading.RLock()

    def state(self) -> dict[str, Any]:
        services = []
        for stored in self.database.list_services():
            status, pid, started_at = self.runner.status(stored)
            service = dict(stored)
            service.update(
                {
                    "status": status,
                    "pid": pid,
                    "started_at": started_at,
                    "url": self.runner.url_for(stored),
                    "log_path": str(self.runner.log_path(stored["id"])),
                }
            )
            services.append(service)
        running = sum(1 for service in services if service["status"] == "running")
        return {
            "ok": True,
            "version": __version__,
            "manager": {
                "name": APP_NAME,
                "url": self.manager_url,
                "data_dir": str(self.data_dir),
            },
            "summary": {
                "total": len(services),
                "running": running,
                "stopped": len(services) - running,
            },
            "services": services,
            "suggested_port": suggested_port(
                self.database, AUTO_SERVICE_PORT_START
            ),
        }

    def create_service(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._mutation_lock:
            service_payload = dict(payload)
            port_mode = str(service_payload.get("port_mode", "auto")).lower()
            if port_mode != "manual":
                service_payload["port"] = suggested_port(
                    self.database, AUTO_SERVICE_PORT_START
                )
            values = validate_service_payload(service_payload, self.database)
            service = self.database.create_service(values)
            return self._auto_group_new_service(service)

    def _auto_group_new_service(self, service: dict[str, Any]) -> dict[str, Any]:
        project_root = project_root_for_path(service["root_path"])
        related = [
            item
            for item in self.database.list_services()
            if item["id"] != service["id"]
            and os.path.normcase(str(project_root_for_path(item["root_path"])))
            == os.path.normcase(str(project_root))
        ]
        if not related:
            return service
        manual_groups = {
            str(item.get("group_name") or "").strip()
            for item in related
            if str(item.get("group_name") or "").strip()
        }
        if len(manual_groups) > 1:
            return service
        group_name = next(iter(manual_groups), project_root.name or "自动分组")
        service_ids = [service["id"]]
        if not manual_groups:
            service_ids.extend(
                item["id"] for item in related if not str(item.get("group_name") or "").strip()
            )
        self.database.set_service_group(service_ids, group_name)
        return self.database.get_service(service["id"]) or service

    def update_service(
        self, service_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        existing = self.database.get_service(service_id)
        if existing is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "站点不存在。")
        status, _, _ = self.runner.status(existing)
        if status == "running":
            raise ApiError(HTTPStatus.CONFLICT, "请先停止站点再修改配置。")
        merged = dict(existing)
        merged.update(payload)
        values = validate_service_payload(merged, self.database, service_id)
        updated = self.database.update_service(service_id, values)
        if updated is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "站点不存在。")
        return updated

    def assign_service_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_ids = payload.get("ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ApiError(HTTPStatus.BAD_REQUEST, "请选择至少一个站点。")
        service_ids = list(dict.fromkeys(str(item) for item in raw_ids))
        if len(service_ids) > 500 or any(
            re.fullmatch(r"[0-9a-f]{32}", service_id) is None
            for service_id in service_ids
        ):
            raise ApiError(HTTPStatus.BAD_REQUEST, "站点选择无效。")
        group_name = str(payload.get("group_name") or "").strip()
        if len(group_name) > 80:
            raise ApiError(HTTPStatus.BAD_REQUEST, "分组名称不能超过 80 个字符。")
        services = self.database.list_services()
        if any(
            not any(item["id"] == service_id for item in services)
            for service_id in service_ids
        ):
            raise ApiError(HTTPStatus.NOT_FOUND, "一个或多个站点不存在。")
        existing_groups = {
            str(item.get("group_name") or "").strip().casefold()
            for item in services
            if str(item.get("group_name") or "").strip()
        }
        if (
            group_name
            and len(service_ids) < 2
            and group_name.casefold() not in existing_groups
        ):
            raise ApiError(HTTPStatus.BAD_REQUEST, "创建分组至少需要选择两个站点。")
        with self._mutation_lock:
            updated = self.database.set_service_group(service_ids, group_name)
        return {"updated": updated, "group_name": group_name}

    def rename_service_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        old_name = str(payload.get("old_name") or "").strip()
        new_name = str(payload.get("new_name") or "").strip()
        if not old_name or not new_name:
            raise ApiError(HTTPStatus.BAD_REQUEST, "分组名称不能为空。")
        if len(new_name) > 80:
            raise ApiError(HTTPStatus.BAD_REQUEST, "分组名称不能超过 80 个字符。")
        services = self.database.list_services()
        if not any(
            str(item.get("group_name") or "").casefold() == old_name.casefold()
            for item in services
        ):
            raise ApiError(HTTPStatus.NOT_FOUND, "分组不存在。")
        if new_name.casefold() != old_name.casefold() and any(
            str(item.get("group_name") or "").casefold() == new_name.casefold()
            for item in services
        ):
            raise ApiError(HTTPStatus.CONFLICT, "该分组名称已存在。")
        updated = self.database.rename_service_group(old_name, new_name)
        return {"updated": updated, "group_name": new_name}

    def delete_service(self, service_id: str) -> None:
        existing = self.database.get_service(service_id)
        if existing is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "站点不存在。")
        self.runner.stop(service_id)
        self.database.delete_service(service_id)
        self.runner.remove_log(service_id)

    def start_service(self, service_id: str, open_browser: bool = True) -> dict[str, Any]:
        service = self.database.get_service(service_id)
        if service is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "站点不存在。")
        runtime = self.runner.start(service)
        url = self.runner.url_for(service)
        if open_browser and service["auto_open"]:
            webbrowser.open(url)
        return {"runtime": runtime, "url": url}

    def stop_service(self, service_id: str) -> None:
        if self.database.get_service(service_id) is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "站点不存在。")
        self.runner.stop(service_id)

    def restart_service(self, service_id: str) -> dict[str, Any]:
        self.stop_service(service_id)
        return self.start_service(service_id, open_browser=False)

    def start_all(self) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for service in self.database.list_services():
            try:
                self.start_service(service["id"], open_browser=False)
                results.append({"id": service["id"], "name": service["name"]})
            except ServiceError as exc:
                errors.append(
                    {
                        "id": service["id"],
                        "name": service["name"],
                        "message": str(exc),
                    }
                )
        return {"started": results, "errors": errors}

    def stop_all(self) -> int:
        return self.runner.stop_all()

    def open_service(self, service_id: str) -> str:
        service = self.database.get_service(service_id)
        if service is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "站点不存在。")
        status, _, _ = self.runner.status(service)
        if status != "running":
            raise ApiError(HTTPStatus.CONFLICT, "站点尚未启动。")
        url = self.runner.url_for(service)
        webbrowser.open(url)
        return url

    def request_shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True

        def finish() -> None:
            time.sleep(0.25)
            self.runner.stop_all()
            if self.server is not None:
                self.server.shutdown()

        threading.Thread(target=finish, name="localdock-shutdown", daemon=True).start()

    def export_csv(self) -> bytes:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "name",
                "root_path",
                "entry_file",
                "service_type",
                "backend_json",
                "port",
                "auto_open",
                "bridge_enabled",
                "bridge_root",
                "write_enabled",
                "watch_enabled",
                "watch_root",
                "watch_interval",
                "auto_reload",
                "command_enabled",
                "commands_json",
                "auto_command_id",
                "created_at",
                "last_started_at",
            ]
        )
        for service in self.database.list_services():
            writer.writerow(
                [
                    service["name"],
                    service["root_path"],
                    service["entry_file"],
                    service["service_type"],
                    service["backend_json"],
                    service["port"],
                    "true" if service["auto_open"] else "false",
                    "true" if service["bridge_enabled"] else "false",
                    service["bridge_root"] or "",
                    "true" if service["write_enabled"] else "false",
                    "true" if service["watch_enabled"] else "false",
                    service["watch_root"] or "",
                    service["watch_interval"],
                    "true" if service["auto_reload"] else "false",
                    "true" if service["command_enabled"] else "false",
                    service["commands_json"],
                    service["auto_command_id"] or "",
                    service["created_at"],
                    service["last_started_at"] or "",
                ]
            )
        return output.getvalue().encode("utf-8-sig")

    def create_backup(self) -> Path:
        backup_dir = self.data_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix="localdock-",
            suffix=".db",
            dir=backup_dir,
            delete=False,
        )
        handle.close()
        destination = Path(handle.name)
        self.database.backup_to(destination)
        return destination


class ManagerRequestHandler(BaseHTTPRequestHandler):
    server_version = "LocalDock/0.1"
    sys_version = ""

    @property
    def app(self) -> ManagerApp:
        return self.server.manager  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            host = self.headers.get("Host", "").rsplit(":", 1)[0].strip("[]").lower()
            if host not in {"127.0.0.1", "localhost"}:
                raise ApiError(HTTPStatus.FORBIDDEN, "不允许的本地管理地址。")
            if path == "/api/health" and method in {"GET", "HEAD"}:
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "app": APP_NAME, "version": __version__},
                    head_only=method == "HEAD",
                )
                return
            if path.startswith("/api/"):
                self._require_token()
                self._handle_api(method, path, query)
                return
            if method in {"GET", "HEAD"}:
                self._serve_web(path, head_only=method == "HEAD")
                return
            self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, "不支持该请求。")
        except ApiError as exc:
            self._send_error(exc.status, exc.message)
        except ValidationError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except ServiceError as exc:
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
        except sqlite3.IntegrityError as exc:
            self._send_error(HTTPStatus.CONFLICT, f"数据冲突：{exc}")
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"内部错误：{exc}")

    def _require_token(self) -> None:
        provided = self.headers.get("X-LocalDock-Token", "")
        if not hmac.compare_digest(provided, self.app.token):
            raise ApiError(HTTPStatus.FORBIDDEN, "无效的管理器令牌。")

    def _handle_api(
        self, method: str, path: str, query: dict[str, list[str]]
    ) -> None:
        if path == "/api/state" and method == "GET":
            self._send_json(HTTPStatus.OK, self.app.state())
            return
        if path == "/api/suggested-port" and method == "GET":
            port = suggested_port(self.app.database, AUTO_SERVICE_PORT_START)
            self._send_json(HTTPStatus.OK, {"ok": True, "port": port})
            return

        if path == "/api/services" and method == "POST":
            payload = self._read_json()
            service = self.app.create_service(payload)
            self._send_json(HTTPStatus.CREATED, {"ok": True, "service": service})
            return
        if path == "/api/services/group" and method == "POST":
            result = self.app.assign_service_group(self._read_json())
            self._send_json(HTTPStatus.OK, {"ok": True, **result})
            return
        if path == "/api/groups/rename" and method == "POST":
            result = self.app.rename_service_group(self._read_json())
            self._send_json(HTTPStatus.OK, {"ok": True, **result})
            return

        match = re.fullmatch(r"/api/services/([0-9a-f]+)", path)
        if match:
            service_id = match.group(1)
            if method == "PUT":
                service = self.app.update_service(service_id, self._read_json())
                self._send_json(HTTPStatus.OK, {"ok": True, "service": service})
                return
            if method == "DELETE":
                self.app.delete_service(service_id)
                self._send_json(HTTPStatus.OK, {"ok": True})
                return

        match = re.fullmatch(
            r"/api/services/([0-9a-f]+)/(start|stop|restart|open|logs|refresh-content)",
            path,
        )
        if match:
            service_id, action = match.groups()
            if action == "start" and method == "POST":
                result = self.app.start_service(service_id)
                self._send_json(HTTPStatus.OK, {"ok": True, **result})
                return
            if action == "stop" and method == "POST":
                self.app.stop_service(service_id)
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            if action == "restart" and method == "POST":
                result = self.app.restart_service(service_id)
                self._send_json(HTTPStatus.OK, {"ok": True, **result})
                return
            if action == "open" and method == "POST":
                url = self.app.open_service(service_id)
                self._send_json(HTTPStatus.OK, {"ok": True, "url": url})
                return
            if action == "refresh-content" and method == "POST":
                results = self.app.runner.refresh(service_id)
                self._send_json(HTTPStatus.OK, {"ok": True, **results})
                return
            if action == "logs" and method == "GET":
                tail = int(query.get("tail", ["240"])[0])
                service = self.app.database.get_service(service_id)
                if service is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "站点不存在。")
                content = self.app.runner.read_log(service_id, tail=tail)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "content": content,
                        "path": str(self.app.runner.log_path(service_id)),
                    },
                )
                return

        if path == "/api/start-all" and method == "POST":
            self._send_json(HTTPStatus.OK, {"ok": True, **self.app.start_all()})
            return
        if path == "/api/stop-all" and method == "POST":
            count = self.app.stop_all()
            self._send_json(HTTPStatus.OK, {"ok": True, "stopped": count})
            return
        if path == "/api/export.csv" and method == "GET":
            body = self.app.export_csv()
            self._send_bytes(
                HTTPStatus.OK,
                body,
                "text/csv; charset=utf-8",
                filename="localdock-services.csv",
            )
            return
        if path == "/api/backup" and method == "GET":
            backup = self.app.create_backup()
            try:
                body = backup.read_bytes()
            finally:
                backup.unlink(missing_ok=True)
            self._send_bytes(
                HTTPStatus.OK,
                body,
                "application/vnd.sqlite3",
                filename="localdock-backup.db",
            )
            return
        if path == "/api/dialog/folder" and method == "POST":
            self._send_json(
                HTTPStatus.OK, {"ok": True, "path": self._choose_folder()}
            )
            return
        if path == "/api/dialog/file" and method == "POST":
            self._send_json(
                HTTPStatus.OK, {"ok": True, "path": self._choose_file()}
            )
            return
        if path == "/api/shutdown" and method == "POST":
            self.app.request_shutdown()
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        raise ApiError(HTTPStatus.NOT_FOUND, "接口不存在。")

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求长度无效。") from exc
        if length <= 0:
            return {}
        if length > 128 * 1024:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "请求内容过大。")
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON 格式无效。") from exc
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求内容必须是 JSON 对象。")
        return payload

    def _choose_folder(self) -> str:
        root = None
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory(
                parent=root,
                title="选择 HTML 项目目录",
                mustexist=True,
            )
            return str(Path(path).resolve()) if path else ""
        except Exception as exc:
            raise ApiError(
                HTTPStatus.NOT_IMPLEMENTED, f"无法打开目录选择器：{exc}"
            ) from exc
        finally:
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass

    def _choose_file(self) -> str:
        root = None
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                parent=root,
                title="选择外部软件",
                filetypes=[("Windows 程序", "*.exe *.com"), ("所有文件", "*.*")],
            )
            return str(Path(path).resolve()) if path else ""
        except Exception as exc:
            raise ApiError(
                HTTPStatus.NOT_IMPLEMENTED, f"无法打开文件选择器：{exc}"
            ) from exc
        finally:
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass

    def _serve_web(self, path: str, head_only: bool) -> None:
        if path in {"/", "/index.html"}:
            index = (self.app.web_dir / "index.html").read_text(encoding="utf-8")
            body = index.replace("__LOCALDOCK_TOKEN__", self.app.token).encode("utf-8")
            self._send_bytes(
                HTTPStatus.OK,
                body,
                "text/html; charset=utf-8",
                head_only=head_only,
            )
            return
        if path.startswith("/assets/"):
            name = path.removeprefix("/assets/")
            if not name or Path(name).name != name or "\\" in name:
                raise ApiError(HTTPStatus.NOT_FOUND, "资源不存在。")
            asset = self.app.web_dir / "assets" / name
            if not asset.is_file():
                raise ApiError(HTTPStatus.NOT_FOUND, "资源不存在。")
            content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {
                "application/javascript",
                "image/svg+xml",
            }:
                content_type += "; charset=utf-8"
            self._send_bytes(
                HTTPStatus.OK,
                asset.read_bytes(),
                content_type,
                head_only=head_only,
            )
            return
        raise ApiError(HTTPStatus.NOT_FOUND, "页面不存在。")

    def _send_json(
        self, status: HTTPStatus, payload: dict[str, Any], head_only: bool = False
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(
            status,
            body,
            "application/json; charset=utf-8",
            head_only=head_only,
        )

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"ok": False, "error": message})

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        filename: str | None = None,
        head_only: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def log_message(self, format_string: str, *args: Any) -> None:
        console_write(
            f"[{self.log_date_time_string()}] "
            f"{self.client_address[0]} {format_string % args}"
        )


def create_manager_runtime(
    data_dir: Path = DATA_DIR,
    web_dir: Path = WEB_DIR,
    token_path: Path | None = None,
) -> tuple[Database, ServiceRunner, ManagerApp]:
    data_dir = Path(data_dir)
    database = Database(data_dir / "localdock.db")
    runner = ServiceRunner(
        database=database,
        data_dir=data_dir,
        log_dir=data_dir / "logs",
        runtime_path=data_dir / "runtime.json",
    )
    token = load_or_create_token(token_path or data_dir / "manager.token")
    app = ManagerApp(
        database=database,
        runner=runner,
        web_dir=web_dir,
        token=token,
        data_dir=data_dir,
    )
    return database, runner, app


def _existing_manager_url(data_dir: Path) -> str | None:
    state_path = data_dir / "manager.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        url = str(state["url"]).rstrip("/")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=0.8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if response.status == 200 and payload.get("app") == APP_NAME:
            return f"{url}/"
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return None
    return None


def _write_manager_state(data_dir: Path, url: str, pid: int) -> None:
    state_path = data_dir / "manager.json"
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"url": url, "pid": pid}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(state_path)


def _clear_manager_state(data_dir: Path, pid: int) -> None:
    state_path = data_dir / "manager.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if int(state.get("pid", 0)) == pid:
            state_path.unlink(missing_ok=True)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass


def run_manager(
    port: int = MANAGER_PORT,
    host: str = MANAGER_HOST,
    open_browser: bool = True,
    data_dir: Path = DATA_DIR,
    web_dir: Path = WEB_DIR,
) -> int:
    existing_url = _existing_manager_url(data_dir)
    if existing_url:
        if open_browser:
            webbrowser.open(existing_url)
        console_write(f"{APP_NAME} is already running: {existing_url}")
        return 0

    database, runner, app = create_manager_runtime(data_dir, web_dir)
    runner.cleanup_stale_workers()

    server: ManagerServer | None = None
    selected_port = port
    for candidate in range(port, port + 20):
        try:
            server = ManagerServer((host, candidate), app)
            selected_port = candidate
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError(f"无法绑定管理端口 {port}-{port + 19}。")

    app.server = server
    app.manager_url = f"http://{host}:{selected_port}/"
    _write_manager_state(data_dir, app.manager_url, os.getpid())
    if open_browser:
        threading.Timer(0.45, webbrowser.open, args=(app.manager_url,)).start()
    console_write(f"{APP_NAME} manager: {app.manager_url}")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop_all()
        server.server_close()
        _clear_manager_state(data_dir, os.getpid())
    return 0

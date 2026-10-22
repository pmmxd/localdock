from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

from .db import Database
from .worker import HEALTH_PATH, SHUTDOWN_PATH


class ServiceError(RuntimeError):
    pass


class ServiceRunner:
    def __init__(
        self,
        database: Database,
        data_dir: Path,
        log_dir: Path,
        runtime_path: Path,
        app_entry: Path | None = None,
    ) -> None:
        self.database = database
        self.data_dir = Path(data_dir)
        self.log_dir = Path(log_dir)
        self.runtime_path = Path(runtime_path)
        self.app_entry = app_entry or Path(__file__).resolve().parent.parent / "app.py"
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._runtime: dict[str, dict[str, Any]] = self._load_runtime()
        self._lock = threading.RLock()

    def _load_runtime(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.runtime_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return {
                    str(key): value
                    for key, value in payload.items()
                    if isinstance(value, dict)
                }
        except (OSError, ValueError, TypeError):
            pass
        return {}

    def _save_runtime(self) -> None:
        self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.runtime_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._runtime, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.runtime_path)

    @staticmethod
    def url_for(service: dict[str, Any]) -> str:
        from urllib.parse import quote

        entry = str(service["entry_file"]).replace("\\", "/").lstrip("/")
        return (
            f"http://127.0.0.1:{int(service['port'])}/"
            f"{quote(entry, safe='/')}"
        )

    def log_path(self, service_id: str) -> Path:
        return self.log_dir / f"{service_id}.log"

    def read_log(self, service_id: str, tail: int = 240) -> str:
        path = self.log_path(service_id)
        if not path.exists():
            return ""
        tail = max(1, min(int(tail), 2000))
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            read_size = min(size, 192 * 1024)
            handle.seek(size - read_size)
            content = handle.read().decode("utf-8", errors="replace")
        return "\n".join(content.splitlines()[-tail:])

    def remove_log(self, service_id: str) -> None:
        try:
            self.log_path(service_id).unlink(missing_ok=True)
        except OSError:
            pass

    def _worker_command(self, config: dict[str, Any]) -> list[str]:
        encoded = base64.urlsafe_b64encode(
            json.dumps(config, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        if getattr(sys, "frozen", False):
            return [sys.executable, "--worker", encoded]
        return [sys.executable, str(self.app_entry), "--worker", encoded]

    @staticmethod
    def _port_available(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                return False
        return True

    @staticmethod
    def _probe_port(port: int, timeout: float = 0.3) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True
        except OSError:
            return False

    def _wait_until_ready(
        self, process: subprocess.Popen[bytes], port: int, timeout: float = 4.0
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False
            if self._probe_port(port, timeout=0.15):
                return True
            time.sleep(0.08)
        return False

    def _request_shutdown(self, runtime: dict[str, Any], timeout: float = 1.2) -> bool:
        port = int(runtime["port"])
        token = str(runtime["token"])
        connection = HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            connection.request(
                "POST",
                SHUTDOWN_PATH,
                headers={"X-LocalDock-Worker-Token": token},
            )
            response = connection.getresponse()
            response.read()
            return 200 <= response.status < 300
        except (OSError, ValueError):
            return False
        finally:
            connection.close()

    def _worker_health(self, runtime: dict[str, Any]) -> bool:
        connection = HTTPConnection(
            "127.0.0.1", int(runtime["port"]), timeout=0.35
        )
        try:
            connection.request("GET", HEALTH_PATH)
            response = connection.getresponse()
            body = response.read()
            if response.status != 200:
                return False
            payload = json.loads(body.decode("utf-8"))
            return payload.get("service_id") == runtime.get("service_id")
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        finally:
            connection.close()

    def cleanup_stale_workers(self) -> None:
        with self._lock:
            for service_id, runtime in list(self._runtime.items()):
                if self._worker_health(runtime):
                    self._request_shutdown(runtime)
                    time.sleep(0.12)
                self._runtime.pop(service_id, None)
            self._save_runtime()

    def status(self, service: dict[str, Any]) -> tuple[str, int | None, str | None]:
        service_id = str(service["id"])
        process = self._processes.get(service_id)
        runtime = self._runtime.get(service_id)
        if process is not None and process.poll() is None:
            return (
                "running",
                process.pid,
                str(runtime.get("started_at")) if runtime else None,
            )
        if runtime and self._worker_health(runtime):
            return "running", int(runtime.get("pid", 0)) or None, str(
                runtime.get("started_at")
            )
        if process is not None and process.poll() is not None:
            self._processes.pop(service_id, None)
        if runtime and not self._worker_health(runtime):
            self._runtime.pop(service_id, None)
            self._save_runtime()
        return "stopped", None, None

    def start(self, service: dict[str, Any]) -> dict[str, Any]:
        service_id = str(service["id"])
        with self._lock:
            current, _, _ = self.status(service)
            if current == "running":
                return self._runtime.get(service_id, {})
            port = int(service["port"])
            if not self._port_available(port):
                raise ServiceError(f"端口 {port} 已被其他程序占用。")

            token = uuid.uuid4().hex
            started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            config = {
                "service_id": service_id,
                "root_path": service["root_path"],
                "entry_file": service["entry_file"],
                "port": port,
                "token": token,
                "bridge_enabled": bool(service.get("bridge_enabled", False)),
                "bridge_root": service.get("bridge_root"),
                "write_enabled": bool(service.get("write_enabled", False)),
                "backup_dir": str(self.data_dir / "file_backups" / service_id),
                "watch_enabled": bool(service.get("watch_enabled", False)),
                "watch_root": service.get("watch_root"),
                "watch_interval": int(service.get("watch_interval", 5)),
                "auto_reload": bool(service.get("auto_reload", False)),
                "command_enabled": bool(service.get("command_enabled", False)),
                "commands": json.loads(service.get("commands_json") or "[]"),
                "auto_command_id": service.get("auto_command_id") or "",
            }
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.log_path(service_id)
            creation_flags = (
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )
            with log_path.open("ab") as log_file:
                log_file.write(
                    (
                        f"\n--- {datetime.now().astimezone().isoformat(timespec='seconds')} "
                        f"starting port {port} ---\n"
                    ).encode("utf-8")
                )
                log_file.flush()
                process = subprocess.Popen(
                    self._worker_command(config),
                    cwd=service["root_path"],
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=creation_flags,
                )

            runtime = {
                "service_id": service_id,
                "pid": process.pid,
                "port": port,
                "token": token,
                "started_at": started_at,
            }
            self._processes[service_id] = process
            self._runtime[service_id] = runtime
            self._save_runtime()

            if not self._wait_until_ready(process, port):
                self._terminate_process(process)
                self._processes.pop(service_id, None)
                self._runtime.pop(service_id, None)
                self._save_runtime()
                log_tail = self.read_log(service_id, tail=30)
                detail = f"\n{log_tail}" if log_tail else ""
                raise ServiceError(f"服务启动失败。{detail}")

            self.database.mark_started(service_id)
            return runtime

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    def stop(self, service_id: str) -> bool:
        with self._lock:
            runtime = self._runtime.get(service_id)
            stopped = False
            if runtime is not None:
                stopped = self._request_shutdown(runtime)
            process = self._processes.get(service_id)
            if process is not None:
                if process.poll() is None:
                    try:
                        process.wait(timeout=2.5 if stopped else 0.2)
                    except subprocess.TimeoutExpired:
                        pass
                if process.poll() is None:
                    self._terminate_process(process)
                self._processes.pop(service_id, None)
            self._runtime.pop(service_id, None)
            self._save_runtime()
            return stopped or process is not None

    def refresh(self, service_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._runtime.get(service_id)
            if runtime is None or not self._worker_health(runtime):
                raise ServiceError("站点尚未运行，无法刷新。")
            connection = HTTPConnection(
                "127.0.0.1",
                int(runtime["port"]),
                timeout=3,
            )
            try:
                connection.request(
                    "POST",
                    "/__localdock/api/refresh",
                    body="{}",
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                body = response.read()
                if response.status != 200:
                    raise ServiceError("站点刷新失败。")
                return json.loads(body.decode("utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise ServiceError(f"站点刷新失败：{exc}") from exc
            finally:
                connection.close()

    def stop_all(self) -> int:
        with self._lock:
            service_ids = {
                str(service["id"]) for service in self.database.list_services()
            }
            service_ids.update(self._runtime)
            service_ids.update(self._processes)
            count = 0
            for service_id in service_ids:
                if self.stop(service_id):
                    count += 1
            return count

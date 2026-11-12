from __future__ import annotations

import base64
import json
import os
import shutil
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
        self._monitors: dict[str, threading.Thread] = {}
        self._stopping: set[str] = set()
        self._restart_attempts: dict[str, int] = {}

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

        if service.get("service_type") == "node":
            return f"http://127.0.0.1:{int(service['port'])}/"

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

    @staticmethod
    def _backend_health(runtime: dict[str, Any], timeout: float = 0.5) -> bool:
        connection = HTTPConnection(
            "127.0.0.1",
            int(runtime["port"]),
            timeout=timeout,
        )
        try:
            connection.request("GET", str(runtime.get("health_path") or "/"))
            response = connection.getresponse()
            response.read()
            return 200 <= response.status < 400
        except (OSError, ValueError):
            return False
        finally:
            connection.close()

    def cleanup_stale_workers(self) -> None:
        with self._lock:
            for service_id, runtime in list(self._runtime.items()):
                if runtime.get("service_type") == "node":
                    if self._backend_health(runtime):
                        self._terminate_pid_tree(int(runtime.get("pid", 0)))
                elif self._worker_health(runtime):
                    self._request_shutdown(runtime)
                    time.sleep(0.12)
                self._runtime.pop(service_id, None)
            self._save_runtime()

    def status(self, service: dict[str, Any]) -> tuple[str, int | None, str | None]:
        service_id = str(service["id"])
        process = self._processes.get(service_id)
        runtime = self._runtime.get(service_id)
        if process is not None and process.poll() is None:
            if (
                runtime
                and runtime.get("service_type") == "node"
                and not self._backend_health(runtime)
            ):
                return "stopped", None, None
            return (
                "running",
                process.pid,
                str(runtime.get("started_at")) if runtime else None,
            )
        healthy = False
        if runtime:
            healthy = (
                self._backend_health(runtime)
                if runtime.get("service_type") == "node"
                else self._worker_health(runtime)
            )
        if runtime and healthy:
            return "running", int(runtime.get("pid", 0)) or None, str(
                runtime.get("started_at")
            )
        if process is not None and process.poll() is not None:
            self._processes.pop(service_id, None)
        if runtime and not healthy:
            self._runtime.pop(service_id, None)
            self._save_runtime()
        return "stopped", None, None

    def start(self, service: dict[str, Any]) -> dict[str, Any]:
        if service.get("service_type") == "node":
            return self._start_backend(service)
        return self._start_static(service)

    def _start_static(self, service: dict[str, Any]) -> dict[str, Any]:
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
                "external_app_path": service.get("external_app_path"),
                "write_enabled": bool(service.get("write_enabled", False)),
                "backup_dir": str(self.data_dir / "file_backups" / service_id),
                "watch_enabled": bool(service.get("watch_enabled", False)),
                "watch_root": service.get("watch_root"),
                "watch_interval": int(service.get("watch_interval", 10)),
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
    def _load_env_file(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or not key.replace("_", "").isalnum():
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key] = value
        return values

    def _start_backend(
        self,
        service: dict[str, Any],
        *,
        automatic: bool = False,
    ) -> dict[str, Any]:
        service_id = str(service["id"])
        with self._lock:
            if not automatic:
                self._stopping.discard(service_id)
                self._restart_attempts[service_id] = 0
            process = self._processes.get(service_id)
            if process is not None and process.poll() is None:
                runtime = self._runtime.get(service_id)
                if runtime and self._backend_health(runtime):
                    return runtime
                self._terminate_process_tree(process)
                self._processes.pop(service_id, None)
                self._runtime.pop(service_id, None)
                self._save_runtime()

            port = int(service["port"])
            if not self._port_available(port):
                raise ServiceError(f"端口 {port} 已被其他程序占用。")

            backend = dict(service.get("backend") or {})
            root = Path(str(service["root_path"])).resolve()
            runtime_value = str(backend.get("runtime_path") or "node").strip()
            runtime_path = Path(runtime_value)
            if runtime_path.is_absolute():
                executable = runtime_path
            elif len(runtime_path.parts) > 1:
                executable = (root / runtime_path).resolve()
            else:
                resolved = shutil.which(runtime_value)
                if not resolved:
                    raise ServiceError(f"找不到后端运行程序：{runtime_value}")
                executable = Path(resolved)
            if not executable.is_file():
                raise ServiceError(f"后端运行程序不存在：{executable}")

            cwd_value = str(backend.get("cwd") or "")
            cwd = (root / cwd_value).resolve() if cwd_value else root
            if not cwd.is_dir() or not cwd.is_relative_to(root):
                raise ServiceError("后端工作目录无效。")

            env = os.environ.copy()
            env_file = backend.get("env_file")
            if env_file:
                env_path = (root / str(env_file)).resolve()
                if not env_path.is_relative_to(root) or not env_path.is_file():
                    raise ServiceError("后端环境变量文件无效。")
                env.update(self._load_env_file(env_path))
            env["PORT"] = str(port)

            args = [
                str(value).replace("{port}", str(port))
                for value in backend.get("args", [])
            ]
            health_path = str(backend.get("health_path") or "/api/health")
            startup_timeout = max(1, min(int(backend.get("startup_timeout", 30)), 300))
            shutdown_timeout = max(1, min(int(backend.get("shutdown_timeout", 5)), 60))
            auto_restart = bool(backend.get("auto_restart", False))
            restart_delay = max(1, min(int(backend.get("restart_delay", 2)), 60))
            started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            creation_flags = (
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )

            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.log_path(service_id)
            with log_path.open("ab") as log_file:
                log_file.write(
                    (
                        f"\n--- {datetime.now().astimezone().isoformat(timespec='seconds')} "
                        f"starting backend on port {port} ---\n"
                    ).encode("utf-8")
                )
                log_file.flush()
                process = subprocess.Popen(
                    [str(executable), *args],
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=creation_flags,
                )

            runtime = {
                "service_id": service_id,
                "service_type": "node",
                "pid": process.pid,
                "port": port,
                "started_at": started_at,
                "health_path": health_path,
                "shutdown_timeout": shutdown_timeout,
                "auto_restart": auto_restart,
                "restart_delay": restart_delay,
            }
            self._processes[service_id] = process
            self._runtime[service_id] = runtime
            self._save_runtime()

            deadline = time.monotonic() + startup_timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                if self._backend_health(runtime):
                    self.database.mark_started(service_id)
                    self._ensure_backend_monitor(service_id, process, auto_restart)
                    return runtime
                time.sleep(0.2)

            self._terminate_process_tree(process, timeout=2)
            self._processes.pop(service_id, None)
            self._runtime.pop(service_id, None)
            self._save_runtime()
            log_tail = self.read_log(service_id, tail=30)
            detail = f"\n{log_tail}" if log_tail else ""
            raise ServiceError(f"后端服务启动失败。{detail}")

    def _ensure_backend_monitor(
        self,
        service_id: str,
        process: subprocess.Popen[bytes],
        enabled: bool,
    ) -> None:
        if not enabled:
            return
        existing = self._monitors.get(service_id)
        if existing is not None and existing.is_alive():
            return
        monitor = threading.Thread(
            target=self._backend_monitor,
            args=(service_id, process),
            name=f"localdock-backend-monitor-{service_id[:8]}",
            daemon=True,
        )
        self._monitors[service_id] = monitor
        monitor.start()

    def _backend_monitor(
        self,
        service_id: str,
        process: subprocess.Popen[bytes],
    ) -> None:
        process.wait()
        service = None
        delay = 2
        with self._lock:
            if self._processes.get(service_id) is process:
                self._processes.pop(service_id, None)
            runtime = self._runtime.get(service_id)
            if runtime and runtime.get("pid") == process.pid:
                delay = int(runtime.get("restart_delay", 2))
                self._runtime.pop(service_id, None)
                self._save_runtime()
            if (
                service_id in self._stopping
                or not runtime
                or not runtime.get("auto_restart")
            ):
                return
            attempts = self._restart_attempts.get(service_id, 0) + 1
            self._restart_attempts[service_id] = attempts
            if attempts <= 5:
                service = self.database.get_service(service_id)
        if service is None:
            return
        time.sleep(delay)
        with self._lock:
            if self._monitors.get(service_id) is threading.current_thread():
                self._monitors.pop(service_id, None)
            if service_id in self._stopping or self.status(service)[0] == "running":
                return
            try:
                self._start_backend(service, automatic=True)
            except ServiceError as exc:
                with self.log_path(service_id).open("a", encoding="utf-8") as log:
                    log.write(f"Automatic restart failed: {exc}\n")

    def _join_backend_monitor(self, service_id: str, timeout: float = 0.2) -> None:
        monitor = self._monitors.get(service_id)
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=timeout)

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

    @classmethod
    def _terminate_process_tree(
        cls,
        process: subprocess.Popen[bytes],
        timeout: float = 2.0,
    ) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            creation_flags = (
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                check=False,
            )
        else:
            process.terminate()
            try:
                process.wait(timeout=timeout)
                return
            except subprocess.TimeoutExpired:
                pass
            process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    @staticmethod
    def _terminate_pid_tree(pid: int) -> None:
        if pid <= 0:
            return
        if os.name == "nt":
            creation_flags = (
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )
            subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                check=False,
            )
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass

    def stop(self, service_id: str) -> bool:
        with self._lock:
            runtime = self._runtime.get(service_id)
            stopped = False
            is_backend = bool(runtime and runtime.get("service_type") == "node")
            if is_backend:
                self._stopping.add(service_id)
                self._restart_attempts.pop(service_id, None)
            if runtime is not None and not is_backend:
                stopped = self._request_shutdown(runtime)
            process = self._processes.get(service_id)
            if process is not None:
                if is_backend:
                    timeout = float(runtime.get("shutdown_timeout", 5)) if runtime else 5.0
                    stopped = process.poll() is None
                    self._terminate_process_tree(process, timeout=timeout)
                elif process.poll() is None:
                    try:
                        process.wait(timeout=2.5 if stopped else 0.2)
                    except subprocess.TimeoutExpired:
                        pass
                if process.poll() is None:
                    self._terminate_process(process)
                self._processes.pop(service_id, None)
            elif is_backend and runtime is not None:
                stopped = self._backend_health(runtime)
                self._terminate_pid_tree(int(runtime.get("pid", 0)))
            self._runtime.pop(service_id, None)
            self._save_runtime()
            return stopped or process is not None

    def refresh(self, service_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._runtime.get(service_id)
            if runtime is not None and runtime.get("service_type") == "node":
                raise ServiceError("后端服务不支持静态内容刷新。")
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

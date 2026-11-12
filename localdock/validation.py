from __future__ import annotations

import os
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .db import Database


class ValidationError(ValueError):
    pass


COMMON_RESERVED_PORTS = {
    # VPN and tunneling services
    500,
    1701,
    1723,
    4500,
    51820,
    51821,
    51822,
    # Common development servers
    3000,
    3001,
    4000,
    4200,
    5000,
    5001,
    5173,
    5174,
    7001,
    7070,
    *range(7080, 7100),
    7199,
    7443,
    8000,
    8080,
    8081,
    8888,
    9000,
    9001,
    # Databases, Kubernetes and collaboration services
    1433,
    1521,
    3306,
    5432,
    6379,
    6443,
    6667,
    6697,
    27017,
    # Remote management services
    3389,
    5900,
    5985,
    5986,
}


def find_free_port(
    start: int,
    excluded: set[int] | None = None,
    end: int = 65535,
    reserved: set[int] | None = None,
) -> int:
    import socket

    excluded = excluded or set()
    reserved = reserved or set()
    for port in range(start, min(end, 65535) + 1):
        if port in excluded or port in reserved:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise ValidationError("没有可用的本地端口。")


def suggested_port(database: Database, start: int = 7000) -> int:
    used = {int(service["port"]) for service in database.list_services()}
    return find_free_port(start, used, reserved=COMMON_RESERVED_PORTS)


def validate_command_config(
    value: object,
) -> tuple[str, list[dict[str, Any]]]:
    if value in (None, ""):
        raw_commands: object = []
    elif isinstance(value, str):
        try:
            raw_commands = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"命令配置 JSON 无效：{exc}") from exc
    else:
        raw_commands = value
    if not isinstance(raw_commands, list):
        raise ValidationError("命令配置必须是 JSON 数组。")
    if len(raw_commands) > 20:
        raise ValidationError("每个站点最多配置 20 个命令。")

    commands: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_commands, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"第 {index} 个命令配置必须是 JSON 对象。")
        command_id = str(item.get("id", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", command_id):
            raise ValidationError(
                f"第 {index} 个命令 ID 只能包含字母、数字、点、下划线和短横线。"
            )
        if command_id in seen_ids:
            raise ValidationError(f"命令 ID 重复：{command_id}")
        seen_ids.add(command_id)
        executable = str(item.get("executable", "")).strip().strip('"')
        if not executable or "\x00" in executable:
            raise ValidationError(f"命令 {command_id} 缺少可执行文件。")
        raw_args = item.get("args", [])
        if not isinstance(raw_args, list) or not all(
            isinstance(argument, str) for argument in raw_args
        ):
            raise ValidationError(f"命令 {command_id} 的 args 必须是字符串数组。")
        try:
            timeout = int(item.get("timeout", 300))
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"命令 {command_id} 的超时时间无效。") from exc
        if timeout < 1 or timeout > 3600:
            raise ValidationError(f"命令 {command_id} 的超时时间应为 1 到 3600 秒。")
        detached = item.get("detached", False)
        if not isinstance(detached, bool):
            detached = str(detached).lower() in {"1", "true", "yes", "on"}
        commands.append(
            {
                "id": command_id,
                "label": str(item.get("label") or command_id).strip()[:80],
                "executable": executable,
                "args": raw_args,
                "cwd": str(item.get("cwd") or "").strip(),
                "timeout": timeout,
                "allow_args": item.get("allow_args") is True,
                "detached": detached,
            }
        )
    return json.dumps(commands, ensure_ascii=False), commands


def validate_backend_config(
    value: object,
    root: Path,
) -> tuple[str, dict[str, Any]]:
    if value in (None, ""):
        raw: object = {}
    elif isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"后端服务配置 JSON 无效：{exc}") from exc
    else:
        raw = value
    if not isinstance(raw, dict):
        raise ValidationError("后端服务配置必须是 JSON 对象。")

    runtime_value = str(raw.get("runtime_path") or "").strip().strip('"')
    if not runtime_value:
        bundled = root / "runtime" / "node.exe"
        runtime_value = str(bundled) if bundled.is_file() else "node"
    runtime = Path(os.path.expandvars(os.path.expanduser(runtime_value)))
    if runtime.is_absolute():
        runtime = runtime.resolve()
        if not runtime.is_file():
            raise ValidationError("后端运行程序不存在。")
        runtime_value = str(runtime)
    elif len(runtime.parts) > 1:
        runtime = (root / runtime).resolve()
        if not runtime.is_file():
            raise ValidationError("项目内的后端运行程序不存在。")
        runtime_value = str(runtime)

    raw_args = raw.get("args", ["dist/server/index.js"])
    if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
        raise ValidationError("后端启动参数必须是字符串数组。")
    if not raw_args:
        raise ValidationError("后端启动参数不能为空。")
    if len(raw_args) > 64 or sum(len(item) for item in raw_args) > 8192:
        raise ValidationError("后端启动参数数量或长度超过限制。")

    raw_cwd = str(raw.get("cwd") or "").strip().strip('"')
    cwd = (root / raw_cwd).resolve() if raw_cwd else root
    if not cwd.is_relative_to(root) or not cwd.is_dir():
        raise ValidationError("后端工作目录必须位于项目内且存在。")
    try:
        cwd_value = cwd.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValidationError("后端工作目录无效。") from exc
    if cwd_value == ".":
        cwd_value = ""

    raw_env = str(raw.get("env_file") or "").strip().strip('"')
    env_file: str | None = None
    if raw_env:
        env_path = (root / raw_env).resolve()
        if not env_path.is_relative_to(root) or not env_path.is_file():
            raise ValidationError("后端环境变量文件必须位于项目内且存在。")
        env_file = str(env_path.relative_to(root).as_posix())

    health_path = str(raw.get("health_path") or "/api/health").strip()
    if not health_path.startswith("/") or health_path.startswith("//"):
        raise ValidationError("后端健康检查必须是本地路径，例如 /api/health。")
    try:
        startup_timeout = int(raw.get("startup_timeout", 30))
        shutdown_timeout = int(raw.get("shutdown_timeout", 5))
        restart_delay = int(raw.get("restart_delay", 2))
    except (TypeError, ValueError) as exc:
        raise ValidationError("后端超时或重启间隔无效。") from exc
    if not 1 <= startup_timeout <= 300:
        raise ValidationError("后端启动超时应为 1 到 300 秒。")
    if not 1 <= shutdown_timeout <= 60:
        raise ValidationError("后端停止超时应为 1 到 60 秒。")
    if not 1 <= restart_delay <= 60:
        raise ValidationError("后端重启间隔应为 1 到 60 秒。")
    auto_restart = raw.get("auto_restart", False)
    if not isinstance(auto_restart, bool):
        auto_restart = str(auto_restart).lower() in {"1", "true", "yes", "on"}

    config = {
        "runtime_path": runtime_value,
        "args": raw_args,
        "cwd": cwd_value,
        "env_file": env_file,
        "health_path": health_path,
        "startup_timeout": startup_timeout,
        "shutdown_timeout": shutdown_timeout,
        "auto_restart": auto_restart,
        "restart_delay": restart_delay,
    }
    return json.dumps(config, ensure_ascii=False), config


def validate_service_payload(
    payload: dict[str, Any],
    database: Database,
    service_id: str | None = None,
) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValidationError("请输入站点名称。")
    if len(name) > 80:
        raise ValidationError("站点名称不能超过 80 个字符。")

    service_type = str(payload.get("service_type", "static")).strip().lower()
    if service_type not in {"static", "node"}:
        raise ValidationError("服务类型必须是 static 或 node。")

    raw_root = str(payload.get("root_path", "")).strip().strip('"')
    if not raw_root:
        raise ValidationError("请选择 HTML 项目目录。")
    expanded = os.path.expandvars(os.path.expanduser(raw_root))
    root = Path(expanded).resolve()
    if not root.exists():
        raise ValidationError("项目目录不存在。")
    if not root.is_dir():
        raise ValidationError("项目路径必须是文件夹。")

    if service_type == "static":
        raw_entry = str(payload.get("entry_file", "index.html")).strip() or "index.html"
        entry_path = PurePosixPath(raw_entry.replace("\\", "/"))
        if entry_path.is_absolute() or ".." in entry_path.parts:
            raise ValidationError("入口文件必须位于项目目录内。")
        if not entry_path.parts:
            raise ValidationError("请输入入口文件。")

        entry_file = entry_path.as_posix()
        if entry_file.startswith("./"):
            entry_file = entry_file[2:]
        file_path = (root / Path(entry_file)).resolve()
        try:
            file_path.relative_to(root)
        except ValueError as exc:
            raise ValidationError("入口文件必须位于项目目录内。") from exc
        if not file_path.is_file():
            raise ValidationError(f"入口文件不存在：{entry_file}")
    else:
        entry_file = "index.html"

    backend_json = "{}"
    backend_config: dict[str, Any] = {}
    if service_type == "node":
        backend_json, backend_config = validate_backend_config(
            payload.get("backend_json", {}),
            root,
        )

    try:
        port = int(payload.get("port", 0))
    except (TypeError, ValueError) as exc:
        raise ValidationError("端口必须是数字。") from exc
    if port < 1024 or port > 65535:
        raise ValidationError("端口范围应为 1024 到 65535。")

    existing = database.find_by_port(port)
    if existing is not None and existing["id"] != service_id:
        raise ValidationError(
            f"端口 {port} 已分配给“{existing['name']}”，"
            "请点击端口右侧的“自动”按钮分配其他端口。"
        )

    auto_open = payload.get("auto_open", True)
    if not isinstance(auto_open, bool):
        auto_open = str(auto_open).lower() not in {"0", "false", "no", "off"}

    bridge_enabled = payload.get("bridge_enabled", service_type == "static")
    if not isinstance(bridge_enabled, bool):
        bridge_enabled = str(bridge_enabled).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    bridge_root: str | None = None
    if bridge_enabled:
        raw_bridge_root = str(payload.get("bridge_root") or "").strip().strip('"')
        bridge_path = (
            Path(os.path.expandvars(os.path.expanduser(raw_bridge_root))).resolve()
            if raw_bridge_root
            else root
        )
        if not bridge_path.exists() or not bridge_path.is_dir():
            raise ValidationError("文件操作目录不存在。")
        bridge_root = str(bridge_path)

    raw_external_app = str(payload.get("external_app_path") or "").strip().strip('"')
    external_app_path: str | None = None
    if raw_external_app:
        external_app = Path(
            os.path.expandvars(os.path.expanduser(raw_external_app))
        ).resolve()
        if not external_app.is_file():
            raise ValidationError("外部软件不存在。")
        if os.name == "nt" and external_app.suffix.casefold() not in {".exe", ".com"}:
            raise ValidationError("Windows 外部软件必须是 .exe 或 .com 文件。")
        if not bridge_enabled:
            raise ValidationError("指定外部软件前，请先启用本地文件操作。")
        external_app_path = str(external_app)

    write_enabled = payload.get("write_enabled", service_type == "static")
    if not isinstance(write_enabled, bool):
        write_enabled = str(write_enabled).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if write_enabled and not bridge_enabled:
        raise ValidationError("启用写操作前，请先启用本地文件操作。")

    watch_enabled = payload.get("watch_enabled", service_type == "static")
    if not isinstance(watch_enabled, bool):
        watch_enabled = str(watch_enabled).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    watch_root: str | None = None
    try:
        watch_interval = int(payload.get("watch_interval", 10))
    except (TypeError, ValueError) as exc:
        raise ValidationError("监控间隔必须是数字。") from exc
    if watch_interval < 3 or watch_interval > 60:
        raise ValidationError("监控间隔应为 3 到 60 秒。")
    if watch_enabled:
        raw_watch_root = str(payload.get("watch_root") or "").strip().strip('"')
        watch_path = (
            Path(os.path.expandvars(os.path.expanduser(raw_watch_root))).resolve()
            if raw_watch_root
            else Path(bridge_root or root).resolve()
        )
        if not watch_path.exists() or not watch_path.is_dir():
            raise ValidationError("监控目录不存在。")
        watch_root = str(watch_path)

    auto_reload = payload.get("auto_reload", service_type == "static")
    if not isinstance(auto_reload, bool):
        auto_reload = str(auto_reload).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if auto_reload and not watch_enabled:
        raise ValidationError("启用页面自动刷新前，请先启用文件监控。")

    command_enabled = payload.get("command_enabled", False)
    if not isinstance(command_enabled, bool):
        command_enabled = str(command_enabled).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    commands_json, commands = validate_command_config(
        payload.get("commands_json", [])
    )
    if command_enabled and not bridge_enabled:
        raise ValidationError("启用命令运行前，请先启用本地文件操作。")

    auto_command_id = str(payload.get("auto_command_id") or "").strip()
    if auto_command_id:
        if not watch_enabled:
            raise ValidationError("配置自动命令前，请先启用文件监控。")
        if not command_enabled:
            raise ValidationError("配置自动命令前，请先启用命令运行。")
        if auto_command_id not in {command["id"] for command in commands}:
            raise ValidationError("自动命令 ID 不在已登记的命令列表中。")

    if service_type == "node":
        bridge_enabled = False
        bridge_root = None
        external_app_path = None
        write_enabled = False
        watch_enabled = False
        watch_root = None
        watch_interval = 10
        auto_reload = False
        command_enabled = False
        commands_json = "[]"
        auto_command_id = None

    return {
        "name": name,
        "root_path": str(root),
        "entry_file": entry_file,
        "service_type": service_type,
        "backend_json": backend_json,
        "port": port,
        "auto_open": auto_open,
        "bridge_enabled": bridge_enabled,
        "bridge_root": bridge_root,
        "external_app_path": external_app_path,
        "write_enabled": write_enabled,
        "watch_enabled": watch_enabled,
        "watch_root": watch_root,
        "watch_interval": watch_interval,
        "auto_reload": auto_reload,
        "command_enabled": command_enabled,
        "commands_json": commands_json,
        "auto_command_id": auto_command_id or None,
    }

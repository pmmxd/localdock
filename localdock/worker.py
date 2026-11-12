from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit
from ctypes import wintypes


SHUTDOWN_PATH = "/__localdock/shutdown"
HEALTH_PATH = "/__localdock/health"
STATUS_PATHS = {"/api/status", "/__localdock/api/status"}
OPEN_PATHS = {"/api/open", "/__localdock/api/open"}
OPEN_WITH_PATHS = {"/api/open-with", "/__localdock/api/open-with"}
EXPORT_PATHS = {"/api/export", "/__localdock/api/export"}
EXPORT_FOLDER_PATHS = {
    "/api/export-to-folder",
    "/__localdock/api/export-to-folder",
}
LIST_PATHS = {"/api/list", "/__localdock/api/list"}
READ_PATHS = {"/api/read", "/__localdock/api/read"}
COPY_PATHS = {"/api/copy", "/__localdock/api/copy"}
PICK_FOLDER_PATHS = {"/api/pick-folder", "/__localdock/api/pick-folder"}
PICK_FILES_PATHS = {"/api/pick-files", "/__localdock/api/pick-files"}
REVEAL_PATHS = {"/__localdock/api/reveal"}
TRASH_PATHS = {"/api/delete", "/__localdock/api/trash"}
MOVE_PATHS = {"/api/move", "/__localdock/api/move"}
WRITE_PATHS = {"/api/write", "/__localdock/api/write"}
MKDIR_PATHS = {"/api/mkdir", "/__localdock/api/mkdir"}
BRIDGE_SCRIPT_PATH = "/__localdock/bridge.js"
EVENTS_PATH = "/__localdock/api/events"
REFRESH_PATHS = {"/api/rescan", "/__localdock/api/refresh"}
RUN_PATHS = {"/api/run", "/__localdock/api/run"}
JOB_PATH_PATTERN = re.compile(r"^/(?:__localdock/)?api/jobs/([0-9a-f]{32})$")
JOB_CANCEL_PATTERN = re.compile(
    r"^/(?:__localdock/)?api/jobs/([0-9a-f]{32})/cancel$"
)
MAX_BRIDGE_BODY = 16 * 1024
MAX_WRITE_BODY = 16 * 1024 * 1024
MAX_WRITE_REQUEST_BODY = 24 * 1024 * 1024
MAX_COMMAND_OUTPUT = 256 * 1024
MAX_EXPORT_FILES = 200
AUTO_COMMAND_COOLDOWN_SECONDS = 30.0

OPENABLE_EXTENSIONS = {
    ".7z",
    ".aac",
    ".ass",
    ".avi",
    ".azw",
    ".azw3",
    ".cbr",
    ".cbz",
    ".csv",
    ".djvu",
    ".doc",
    ".docx",
    ".epub",
    ".flac",
    ".gif",
    ".htm",
    ".html",
    ".jpeg",
    ".jpg",
    ".json",
    ".log",
    ".m2ts",
    ".m4a",
    ".m4v",
    ".md",
    ".mkv",
    ".mobi",
    ".mov",
    ".mp3",
    ".mp4",
    ".nfo",
    ".ogg",
    ".opus",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".rtf",
    ".srt",
    ".tar",
    ".ts",
    ".txt",
    ".vtt",
    ".wav",
    ".webp",
    ".webm",
    ".wmv",
    ".xls",
    ".xlsx",
    ".zip",
}

BLOCKED_OPEN_WITH_EXTENSIONS = {
    ".appref-ms",
    ".application",
    ".bat",
    ".chm",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".hta",
    ".jar",
    ".job",
    ".js",
    ".jse",
    ".lnk",
    ".msi",
    ".pif",
    ".ps1",
    ".py",
    ".pyw",
    ".reg",
    ".scr",
    ".scf",
    ".sh",
    ".url",
    ".vb",
    ".vbe",
    ".vbs",
    ".ws",
    ".wsc",
    ".wsh",
    ".wsf",
}

BRIDGE_SCRIPT = r"""
(() => {
  const watchers = new Set();
  let eventSource = null;
  let streamStarted = false;

  const notify = (event) => {
    for (const callback of watchers) {
      try { callback(event); } catch (error) { console.error(error); }
    }
  };

  const startStream = () => {
    if (streamStarted) return;
    streamStarted = true;
    eventSource = new EventSource("/__localdock/api/events");
    eventSource.onmessage = (message) => {
      try { notify(JSON.parse(message.data)); } catch {}
    };
  };

  const call = async (path, options = {}) => {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    });
    let payload = {};
    try { payload = await response.json(); } catch {}
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `LocalDock request failed (${response.status})`);
    }
    return payload;
  };
  window.LocalDock = Object.freeze({
    open: (path) => call("/__localdock/api/open", { path }),
    openWith: (path) => call("/__localdock/api/open-with", { path }),
    export: (path, options = {}) =>
      call("/__localdock/api/export", { path, ...options }),
    exportToFolder: (paths, options = {}) => {
      const values = Array.isArray(paths) ? paths : [paths];
      return call("/__localdock/api/export-to-folder", { paths: values, ...options });
    },
    pickFolder: () => call("/__localdock/api/pick-folder", {}),
    pickFiles: (options = {}) => call("/__localdock/api/pick-files", options),
    list: (path, options = {}) =>
      call("/__localdock/api/list", { path, ...options }),
    read: (path, options = {}) =>
      call("/__localdock/api/read", { path, ...options }),
    copy: (source, destination, options = {}) =>
      call("/__localdock/api/copy", { source, destination, ...options }),
    reveal: (path) => call("/__localdock/api/reveal", { path }),
    trash: (path) => call("/__localdock/api/trash", { path }),
    move: (source, destination) =>
      call("/__localdock/api/move", { source, destination }),
    write: (path, content, options = {}) =>
      call("/__localdock/api/write", { path, content, ...options }),
    mkdir: (path, options = {}) =>
      call("/__localdock/api/mkdir", { path, ...options }),
    run: (command, params = {}, options = {}) =>
      call("/__localdock/api/run", { command, params, ...options }),
    job: async (id) => {
      const response = await fetch(`/__localdock/api/jobs/${encodeURIComponent(id)}`);
      if (!response.ok) throw new Error(`LocalDock job failed (${response.status})`);
      return response.json();
    },
    cancelJob: (id) =>
      call(`/__localdock/api/jobs/${encodeURIComponent(id)}/cancel`, {}),
    refresh: () => call("/__localdock/api/refresh", {}),
    watch: (callback) => {
      if (typeof callback !== "function") {
        throw new TypeError("LocalDock.watch requires a callback");
      }
      watchers.add(callback);
      startStream();
      return () => watchers.delete(callback);
    },
    status: async () => {
      const response = await fetch("/api/status", { cache: "no-store" });
      if (!response.ok) throw new Error(`LocalDock status failed (${response.status})`);
      return response.json();
    },
  });

  LocalDock.status().then((status) => {
    if (!status.watch?.enabled || !status.watch?.auto_reload) return;
    const prefix = String(status.watch.site_prefix || "").replaceAll("\\", "/");
    const reloadPage = () => {
      try {
        const now = Date.now();
        const last = Number(sessionStorage.getItem("localdock-last-reload") || 0);
        if (now - last < 800) return;
        sessionStorage.setItem("localdock-last-reload", String(now));
      } catch {}
      location.reload();
    };
    const isSiteEvent = (event) => {
      const changed = String(event.path || "").replaceAll("\\", "/");
      if (!prefix || prefix === ".") return true;
      return changed === prefix || changed.startsWith(`${prefix}/`);
    };
    LocalDock.watch((event) => {
      if (event.kind === "change" && (event.events || []).some(isSiteEvent)) {
        window.setTimeout(reloadPage, 250);
      } else if (
        event.kind === "rebuild" &&
        (event.events || []).some((item) => item.action === "succeeded")
      ) {
        window.setTimeout(reloadPage, 250);
      }
    });
  }).catch(() => {});
})();
""".strip().encode("utf-8")


class BridgeError(ValueError):
    pass


class FileWatcher:
    def __init__(
        self,
        root: Path,
        interval: int,
        debounce: float,
        on_batch: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.interval = max(3, min(int(interval), 60))
        self.debounce = max(0.25, min(float(debounce), 5.0))
        self._stop = threading.Event()
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._subscriber_lock = threading.Lock()
        self._publish_lock = threading.Lock()
        self._sequence = 0
        self._threads: list[threading.Thread] = []
        self._handle: Any = None
        self.on_batch = on_batch

    def start(self) -> None:
        reader = threading.Thread(
            target=self._reader_loop,
            name="localdock-file-watcher",
            daemon=True,
        )
        publisher = threading.Thread(
            target=self._publisher_loop,
            name="localdock-file-publisher",
            daemon=True,
        )
        self._threads = [reader, publisher]
        for thread in self._threads:
            thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._handle:
            try:
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CancelIoEx(self._handle, None)
                kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=64)
        with self._subscriber_lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._subscriber_lock:
            self._subscribers.discard(subscriber)

    def publish_manual(self) -> None:
        self._publish("manual", [])

    def _ingest(self, events: list[dict[str, str]]) -> None:
        if not events:
            return
        try:
            self._events.put_nowait({"events": events})
        except queue.Full:
            self._events.put_nowait(
                {"events": [{"action": "overflow", "path": ""}]}
            )

    def _publish(self, kind: str, events: list[dict[str, Any]]) -> None:
        with self._publish_lock:
            self._sequence += 1
            payload = {
                "seq": self._sequence,
                "kind": kind,
                "root": str(self.root),
                "occurred_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "events": events,
            }
        with self._subscriber_lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(payload)
            except queue.Full:
                pass

    def _publisher_loop(self) -> None:
        pending: list[dict[str, str]] = []
        due_at: float | None = None
        while not self._stop.is_set():
            now = time.monotonic()
            timeout = 60.0 if due_at is None else max(0.0, due_at - now)
            try:
                batch = self._events.get(timeout=timeout)
            except queue.Empty:
                batch = None
            if batch is not None:
                pending.extend(batch["events"])
                due_at = time.monotonic() + self.debounce
            if pending and due_at is not None and time.monotonic() >= due_at:
                batch = pending
                self._publish("change", batch)
                if self.on_batch is not None:
                    try:
                        self.on_batch(batch)
                    except Exception as exc:
                        console_write(f"LocalDock watch callback failed: {exc}")
                pending = []
                due_at = None

    def publish_control(self, kind: str, events: list[dict[str, Any]]) -> None:
        self._publish(kind, events)

    def _reader_loop(self) -> None:
        if os.name == "nt" and self._native_loop():
            return
        if not self._stop.is_set():
            self._poll_loop()

    def _native_loop(self) -> bool:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.restype = wintypes.HANDLE
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        read_changes = kernel32.ReadDirectoryChangesW
        read_changes.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        handle = create_file(
            str(self.root),
            0x0001,
            0x0007,
            None,
            3,
            0x02000000,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            return False
        self._handle = handle
        buffer = ctypes.create_string_buffer(64 * 1024)
        returned = wintypes.DWORD()
        actions = {
            1: "created",
            2: "deleted",
            3: "modified",
            4: "renamed_from",
            5: "renamed_to",
        }
        try:
            while not self._stop.is_set():
                ok = read_changes(
                    handle,
                    buffer,
                    len(buffer),
                    True,
                    0x0001 | 0x0002 | 0x0008 | 0x0010,
                    ctypes.byref(returned),
                    None,
                    None,
                )
                if not ok:
                    if self._stop.is_set():
                        break
                    self._ingest([{"action": "overflow", "path": ""}])
                    time.sleep(max(1.0, self.interval / 2))
                    continue
                raw = buffer.raw[: returned.value]
                offset = 0
                events = []
                while offset + 12 <= len(raw):
                    next_offset, action, name_length = struct.unpack_from(
                        "<III", raw, offset
                    )
                    name = raw[
                        offset + 12 : offset + 12 + name_length
                    ].decode("utf-16-le", errors="replace")
                    events.append(
                        {
                            "action": actions.get(action, "modified"),
                            "path": name.replace("\\", "/"),
                        }
                    )
                    if next_offset == 0:
                        break
                    offset += next_offset
                self._ingest(events)
        finally:
            try:
                kernel32.CancelIoEx(handle, None)
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            self._handle = None
        return True

    def _snapshot(self) -> dict[str, tuple[int, int, bool]]:
        snapshot: dict[str, tuple[int, int, bool]] = {}
        for base, dirs, files in os.walk(self.root):
            base_path = Path(base)
            for name in dirs + files:
                path = base_path / name
                try:
                    info = path.stat()
                except OSError:
                    continue
                relative = path.relative_to(self.root).as_posix()
                snapshot[relative] = (info.st_mtime_ns, info.st_size, path.is_dir())
        return snapshot

    def _poll_loop(self) -> None:
        previous = self._snapshot()
        while not self._stop.wait(self.interval):
            current = self._snapshot()
            events: list[dict[str, str]] = []
            for path in current.keys() - previous.keys():
                events.append({"action": "created", "path": path})
            for path in previous.keys() - current.keys():
                events.append({"action": "deleted", "path": path})
            for path in current.keys() & previous.keys():
                if current[path] != previous[path]:
                    events.append({"action": "modified", "path": path})
            previous = current
            self._ingest(events)


class CommandJobManager:
    def __init__(
        self,
        commands: list[dict[str, Any]],
        allowed_root: Path,
    ) -> None:
        self.commands = {
            str(command["id"]): command
            for command in commands
            if isinstance(command, dict) and command.get("id")
        }
        self.allowed_root = Path(allowed_root).resolve()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._detached_processes: list[subprocess.Popen[bytes]] = []
        self._lock = threading.RLock()
        self._semaphore = threading.Semaphore(2)

    def run(
        self,
        payload: dict[str, Any],
        on_complete: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        command_id = str(payload.get("command", "")).strip()
        command = self.commands.get(command_id)
        if command is None:
            raise BridgeError("命令未在站点白名单中配置。")

        raw_params = payload.get("params", {})
        if not isinstance(raw_params, dict) or len(raw_params) > 64:
            raise BridgeError("命令参数无效。")
        params = {str(key): str(value) for key, value in raw_params.items()}
        if any("\x00" in value for value in params.values()):
            raise BridgeError("命令参数无效。")

        extra_args = payload.get("args", [])
        if extra_args is None:
            extra_args = []
        if not isinstance(extra_args, list) or not all(
            isinstance(argument, str) for argument in extra_args
        ):
            raise BridgeError("附加参数必须是字符串数组。")
        if extra_args and command.get("allow_args") is not True:
            raise BridgeError("该命令不允许网页追加参数。")
        if len(extra_args) > 64 or sum(len(argument) for argument in extra_args) > 8192:
            raise BridgeError("附加参数数量或长度超过限制。")

        command_args = []
        for template in command.get("args", []):
            command_args.append(self._substitute(str(template), params))
        command_args.extend(extra_args)

        cwd_value = str(payload.get("cwd") or command.get("cwd") or "").strip()
        cwd_path = Path(cwd_value) if cwd_value else self.allowed_root
        if not cwd_path.is_absolute():
            cwd_path = self.allowed_root / cwd_path
        cwd = cwd_path.resolve()
        if not cwd.is_relative_to(self.allowed_root) or not cwd.is_dir():
            raise BridgeError("命令工作目录无效。")

        executable = str(command["executable"]).strip().strip('"')
        executable_path = Path(executable)
        if executable_path.is_absolute() or any(
            separator in executable for separator in ("\\", "/")
        ):
            if not executable_path.is_absolute():
                executable_path = cwd / executable_path
            executable = str(executable_path.resolve())
        command_vector = self._build_command(executable, command_args)

        with self._lock:
            self._detached_processes = [
                process
                for process in self._detached_processes
                if process.poll() is None
            ]
            active = sum(
                1
                for job in self._jobs.values()
                if job["status"] in {"queued", "running"}
            )
            if active >= 8:
                raise BridgeError("命令队列已满，请稍后再试。")
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "command": command_id,
                "label": command.get("label") or command_id,
                "status": "queued",
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "started_at": None,
                "finished_at": None,
                "exit_code": None,
                "output": "",
                "output_truncated": False,
                "error": None,
                "detached": bool(command.get("detached")),
                "_cancel": threading.Event(),
                "_process": None,
                "_output": bytearray(),
            }
            self._jobs[job_id] = job
            thread = threading.Thread(
                target=self._execute,
                args=(
                    job,
                    command_vector,
                    cwd,
                    int(command["timeout"]),
                    bool(command.get("detached")),
                    on_complete,
                ),
                name=f"localdock-job-{job_id[:8]}",
                daemon=True,
            )
            thread.start()
        return self.get(job_id)

    @staticmethod
    def _build_command(executable: str, command_args: list[str]) -> list[str]:
        suffix = Path(executable).suffix.casefold()
        if suffix not in {".cmd", ".bat", ".ps1"}:
            return [executable, *command_args]
        dangerous = set("&|<>^%!\r\n")
        if any(character in argument for argument in command_args for character in dangerous):
            raise BridgeError("脚本参数包含不允许的命令拼接字符。")
        if suffix == ".ps1":
            return [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                executable,
                *command_args,
            ]
        command_line = subprocess.list2cmdline([executable, *command_args])
        return ["cmd.exe", "/d", "/s", "/c", command_line]

    @staticmethod
    def _substitute(template: str, params: dict[str, str]) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in params:
                raise BridgeError(f"缺少命令参数：{key}")
            value = params[key]
            if "\x00" in value:
                raise BridgeError(f"命令参数无效：{key}")
            return value

        return re.sub(r"\{([A-Za-z0-9_.-]+)\}", replace, template)

    def _read_output(self, job: dict[str, Any]) -> None:
        process = job.get("_process")
        if process is None or process.stdout is None:
            return
        while True:
            chunk = process.stdout.read(64 * 1024)
            if not chunk:
                return
            current = job["_output"]
            remaining = MAX_COMMAND_OUTPUT - len(current)
            if remaining > 0:
                current.extend(chunk[:remaining])
            if len(chunk) > remaining:
                job["output_truncated"] = True

    def _execute(
        self,
        job: dict[str, Any],
        command: list[str],
        cwd: Path,
        timeout: int,
        detached: bool,
        on_complete: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        with self._semaphore:
            if job["_cancel"].is_set():
                job["status"] = "cancelled"
                job["finished_at"] = datetime.now().astimezone().isoformat(
                    timespec="seconds"
                )
                return
            job["status"] = "running"
            job["started_at"] = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            started = time.monotonic()
            creation_flags = (
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )
            try:
                if detached:
                    process = subprocess.Popen(
                        command,
                        cwd=cwd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        shell=False,
                        creationflags=creation_flags,
                    )
                    with self._lock:
                        self._detached_processes.append(process)
                    job["_process"] = process
                    # Give launchers a short window to report immediate startup errors.
                    time.sleep(0.25)
                    exit_code = process.poll()
                    job["exit_code"] = exit_code if exit_code is not None else 0
                    job["status"] = (
                        "succeeded" if exit_code in {None, 0} else "failed"
                    )
                    if exit_code not in {None, 0}:
                        job["error"] = f"命令退出码：{exit_code}"
                    return
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    creationflags=creation_flags,
                )
                job["_process"] = process
                reader = threading.Thread(
                    target=self._read_output,
                    args=(job,),
                    name=f"localdock-job-output-{job['id'][:8]}",
                    daemon=True,
                )
                reader.start()
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if job["_cancel"].is_set():
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        job["status"] = "cancelled"
                        break
                    if time.monotonic() >= deadline:
                        process.kill()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            pass
                        job["status"] = "timeout"
                        job["error"] = f"命令超过 {timeout} 秒限制。"
                        break
                    time.sleep(0.2)
                reader.join(timeout=2)
                job["exit_code"] = process.returncode
                if job["status"] == "running":
                    job["status"] = "succeeded" if process.returncode == 0 else "failed"
                    if process.returncode != 0:
                        job["error"] = f"命令退出码：{process.returncode}"
            except OSError as exc:
                job["status"] = "failed"
                job["error"] = str(exc)
            finally:
                job["finished_at"] = datetime.now().astimezone().isoformat(
                    timespec="seconds"
                )
                job["duration_seconds"] = round(time.monotonic() - started, 2)
                job["output"] = bytes(job["_output"]).decode(
                    "utf-8", errors="replace"
                )
                process = job.get("_process")
                if process is not None and process.stdout is not None:
                    try:
                        process.stdout.close()
                    except OSError:
                        pass
                job["_process"] = None
                self._prune()
                if on_complete is not None:
                    try:
                        on_complete(
                            {
                                key: value
                                for key, value in job.items()
                                if not key.startswith("_")
                            }
                        )
                    except Exception as exc:
                        console_write(f"LocalDock command callback failed: {exc}")

    def _prune(self) -> None:
        with self._lock:
            if len(self._jobs) <= 50:
                return
            finished = [
                job
                for job in self._jobs.values()
                if job["status"] not in {"queued", "running"}
            ]
            for job in finished[: max(0, len(self._jobs) - 50)]:
                self._jobs.pop(job["id"], None)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise BridgeError("命令任务不存在。")
            return {
                key: value
                for key, value in job.items()
                if not key.startswith("_")
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise BridgeError("命令任务不存在。")
            if job["status"] in {"succeeded", "failed", "cancelled", "timeout"}:
                return self.get(job_id)
            job["_cancel"].set()
            process = job.get("_process")
            if process is not None and process.poll() is None:
                process.terminate()
            return self.get(job_id)

    def close_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
            for process in self._detached_processes:
                process.poll()
            self._detached_processes.clear()
        for job in jobs:
            if job["status"] in {"queued", "running"}:
                self.cancel(job["id"])


def resolve_bridge_path(
    config: dict[str, Any],
    requested: str,
    *,
    must_exist: bool = True,
    allow_root: bool = False,
) -> Path:
    if not config.get("bridge_enabled"):
        raise BridgeError("当前站点未启用本地文件操作。")
    bridge_root_value = config.get("bridge_root") or config.get("root_path")
    bridge_root = Path(str(bridge_root_value)).resolve()
    raw_path = str(requested or "").strip()
    if not raw_path:
        raise BridgeError("缺少文件路径。")
    if "\x00" in raw_path:
        raise BridgeError("文件路径无效。")
    candidate = Path(raw_path)
    relative_request = not candidate.is_absolute()
    if relative_request:
        candidate = bridge_root / candidate
    resolved = candidate.resolve()
    if relative_request and not resolved.is_relative_to(bridge_root):
        raise BridgeError("相对路径超出默认操作目录。请使用绝对路径访问其他位置。")
    if not allow_root and (
        resolved == bridge_root or resolved.parent == resolved
    ):
        raise BridgeError("不能对授权根目录或磁盘根目录本身执行此操作。")
    if must_exist and not resolved.exists():
        raise BridgeError("文件或目录不存在。")
    return resolved


def ensure_write_enabled(config: dict[str, Any]) -> None:
    if not config.get("write_enabled"):
        raise BridgeError("当前站点未启用删除、移动或写入权限。")


def _send_to_recycle_bin(path: Path) -> None:
    if os.name != "nt":
        raise BridgeError("系统回收站删除仅支持 Windows。")

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.USHORT),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    operation = SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = 3  # FO_DELETE
    operation.pFrom = str(path) + "\0"
    operation.pTo = None
    operation.fFlags = 0x40 | 0x10 | 0x04 | 0x400
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise BridgeError(f"移入回收站失败，错误代码：{result}")


def _backup_existing_file(config: dict[str, Any], source: Path) -> Path | None:
    if not source.is_file():
        return None
    bridge_root = Path(str(config["bridge_root"])).resolve()
    backup_value = config.get("backup_dir")
    backup_root = (
        Path(str(backup_value)).resolve()
        if backup_value
        else bridge_root / ".localdock-backups"
    )
    try:
        relative = source.relative_to(bridge_root)
    except ValueError:
        key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]
        relative = Path("external") / f"{key}-{source.name}"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = backup_root / relative.parent / f"{source.stem}.{stamp}{source.suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _trash_path(config: dict[str, Any], requested: str) -> Path:
    ensure_write_enabled(config)
    resolved = resolve_bridge_path(config, requested)
    _send_to_recycle_bin(resolved)
    return resolved


def _move_path(config: dict[str, Any], payload: dict[str, Any]) -> Path:
    ensure_write_enabled(config)
    source = resolve_bridge_path(config, str(payload.get("source", "")))
    destination_value = str(payload.get("destination", "")).strip()
    destination = resolve_bridge_path(
        config,
        destination_value,
        must_exist=False,
    )
    if destination.exists() and destination.is_dir():
        destination = destination / source.name
    if destination.exists():
        raise BridgeError("目标位置已存在同名文件或目录。")
    if not destination.parent.exists():
        raise BridgeError("目标目录不存在。")
    if source.is_dir() and destination.is_relative_to(source):
        raise BridgeError("不能把目录移动到自身内部。")
    shutil.move(str(source), str(destination))
    return destination


def _write_file(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    ensure_write_enabled(config)
    target = resolve_bridge_path(
        config,
        str(payload.get("path", "")),
        must_exist=False,
    )
    if target.exists() and target.is_dir():
        raise BridgeError("目标路径是目录。")
    exists = target.exists()
    if exists and payload.get("overwrite") is not True:
        raise BridgeError("文件已存在，修改时必须明确允许覆盖。")
    if not exists and payload.get("create") is not True:
        raise BridgeError("创建新文件时必须明确允许创建。")
    if not target.parent.exists() or not target.parent.is_dir():
        raise BridgeError("目标文件的父目录不存在。")

    if exists and payload.get("expected_mtime_ns") is not None:
        try:
            expected = int(payload["expected_mtime_ns"])
        except (TypeError, ValueError) as exc:
            raise BridgeError("文件版本参数无效。") from exc
        if target.stat().st_mtime_ns != expected:
            raise BridgeError("文件已被修改，请重新读取后再写入。")

    content = payload.get("content", "")
    encoding = str(payload.get("encoding", "utf-8")).casefold()
    if not isinstance(content, str):
        raise BridgeError("文件内容必须是字符串。")
    try:
        if encoding == "base64":
            data = base64.b64decode(content, validate=True)
        elif encoding == "utf-8":
            data = content.encode("utf-8")
        else:
            raise BridgeError("仅支持 utf-8 或 base64 编码。")
    except (ValueError, UnicodeError) as exc:
        raise BridgeError("文件内容编码无效。") from exc
    if len(data) > MAX_WRITE_BODY:
        raise BridgeError("写入内容超过 16 MB 限制。")

    backup = _backup_existing_file(config, target) if exists else None
    previous_mode = stat.S_IMODE(target.stat().st_mode) if exists else None
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".localdock.tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as writer:
            writer.write(data)
            writer.flush()
            os.fsync(writer.fileno())
        if previous_mode is not None:
            os.chmod(temporary, previous_mode)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "path": str(target),
        "encoding": encoding,
        "bytes": len(data),
        "backup_path": str(backup) if backup else None,
    }


def _make_directory(config: dict[str, Any], payload: dict[str, Any]) -> Path:
    ensure_write_enabled(config)
    target = resolve_bridge_path(
        config,
        str(payload.get("path", "")),
        must_exist=False,
    )
    parents = payload.get("parents") is True
    if target.exists():
        if target.is_dir():
            return target
        raise BridgeError("同名文件已存在。")
    target.mkdir(parents=parents, exist_ok=False)
    return target


def _launch_with_dialog(path: Path) -> None:
    creation_flags = (
        subprocess.CREATE_NO_WINDOW
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
        else 0
    )
    if os.name == "nt":
        subprocess.Popen(
            ["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(path)],
            creationflags=creation_flags,
        )
        return

    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        executable = filedialog.askopenfilename(parent=root, title="选择打开软件")
    finally:
        if root is not None:
            root.destroy()
    if not executable:
        raise BridgeError("未选择打开软件。")
    subprocess.Popen([executable, str(path)])


def perform_open_with(config: dict[str, Any], requested: str) -> Path:
    resolved = resolve_bridge_path(config, requested)
    if not resolved.is_file():
        raise BridgeError("只能使用外部软件打开文件。")
    if resolved.suffix.casefold() in BLOCKED_OPEN_WITH_EXTENSIONS:
        raise BridgeError("不允许通过页面启动可执行文件或脚本。")
    configured = str(config.get("external_app_path") or "").strip()
    if configured:
        executable = Path(configured).resolve()
        if not executable.is_file():
            raise BridgeError("配置的外部软件不存在。")
        creation_flags = (
            subprocess.CREATE_NO_WINDOW
            if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        subprocess.Popen(
            [str(executable), str(resolved)],
            cwd=str(executable.parent),
            creationflags=creation_flags,
        )
    else:
        _launch_with_dialog(resolved)
    return resolved


def _choose_export_target(source: Path) -> Path | None:
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.asksaveasfilename(
            parent=root,
            title="导出到本地",
            initialdir=str(source.parent),
            initialfile=source.name,
            defaultextension=source.suffix,
            confirmoverwrite=True,
        )
        return Path(selected).resolve() if selected else None
    except Exception as exc:
        raise BridgeError(f"无法打开导出对话框：{exc}") from exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def _export_path(config: dict[str, Any], requested: str) -> Path | None:
    source = resolve_bridge_path(config, requested)
    if not source.is_file():
        raise BridgeError("只能导出文件，不能导出整个目录。")
    selected = _choose_export_target(source)
    if selected is None:
        return None
    if selected == source:
        raise BridgeError("不能覆盖源文件，请选择另一个导出位置。")
    if not selected.parent.is_dir():
        raise BridgeError("导出目录不存在。")
    shutil.copy2(source, selected)
    return selected


def _choose_export_folder(initial_dir: Path) -> Path | None:
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            parent=root,
            title="选择导出文件夹",
            initialdir=str(initial_dir),
            mustexist=True,
        )
        return Path(selected).resolve() if selected else None
    except Exception as exc:
        raise BridgeError(f"无法打开文件夹选择器：{exc}") from exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def _copy_export_without_overwrite(source: Path, folder: Path) -> Path:
    index = 0
    while True:
        name = source.name if index == 0 else f"{source.stem} ({index}){source.suffix}"
        target = folder / name
        try:
            with source.open("rb") as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
            try:
                shutil.copystat(source, target)
            except OSError:
                pass
            return target
        except FileExistsError:
            index += 1


def _export_to_folder(
    config: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    raw_paths = payload.get("paths", payload.get("path"))
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    if (
        not isinstance(raw_paths, list)
        or not raw_paths
        or len(raw_paths) > MAX_EXPORT_FILES
        or not all(isinstance(value, str) for value in raw_paths)
    ):
        raise BridgeError(f"一次可导出 1 到 {MAX_EXPORT_FILES} 个文件。")

    sources = []
    for value in raw_paths:
        source = resolve_bridge_path(config, value)
        if not source.is_file():
            raise BridgeError(f"只能导出文件：{source}")
        if source not in sources:
            sources.append(source)

    selected = _choose_export_folder(sources[0].parent)
    if selected is None:
        return {"paths": [], "failures": [], "canceled": True}
    exported: list[str] = []
    failures: list[str] = []
    for source in sources:
        try:
            target = _copy_export_without_overwrite(source, selected)
            exported.append(str(target))
        except OSError as exc:
            failures.append(f"{source.name}: {exc}")
    return {"paths": exported, "failures": failures, "canceled": False}


def _pick_system_path(kind: str) -> Path | list[Path] | None:
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if kind == "folder":
            selected = filedialog.askdirectory(
                parent=root,
                title="选择文件夹",
                mustexist=True,
            )
            return Path(selected).resolve() if selected else None
        selected = filedialog.askopenfilenames(
            parent=root,
            title="选择文件",
        )
        return [Path(path).resolve() for path in selected] if selected else None
    except Exception as exc:
        raise BridgeError(f"无法打开系统选择器：{exc}") from exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def _list_path(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    target = resolve_bridge_path(
        config,
        str(payload.get("path", "")),
        allow_root=True,
    )
    include_hidden = payload.get("include_hidden") is True
    try:
        limit = max(1, min(int(payload.get("limit", 1000)), 5000))
    except (TypeError, ValueError) as exc:
        raise BridgeError("目录条目限制无效。") from exc

    if target.is_file():
        candidates = [target]
    elif target.is_dir():
        try:
            with os.scandir(target) as iterator:
                candidates = [
                    Path(entry.path)
                    for entry in iterator
                    if include_hidden or not entry.name.startswith(".")
                ]
        except OSError as exc:
            raise BridgeError(f"无法读取目录：{exc}") from exc
    else:
        raise BridgeError("路径不存在。")

    candidates.sort(key=lambda path: (not path.is_dir(), path.name.casefold()))
    entries = []
    for path in candidates[:limit]:
        try:
            info = path.stat(follow_symlinks=False)
            entries.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "type": "directory" if path.is_dir() else "file",
                    "size": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "symlink": path.is_symlink(),
                }
            )
        except OSError:
            continue
    return {"path": str(target), "entries": entries, "truncated": len(candidates) > limit}


def _read_path(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    target = resolve_bridge_path(config, str(payload.get("path", "")))
    if not target.is_file():
        raise BridgeError("只能读取文件。")
    size = target.stat().st_size
    if size > MAX_WRITE_BODY:
        raise BridgeError("读取文件不能超过 16 MB。")
    data = target.read_bytes()
    encoding = str(payload.get("encoding", "utf-8")).casefold()
    if encoding == "base64":
        content = base64.b64encode(data).decode("ascii")
    elif encoding == "utf-8":
        content = data.decode("utf-8", errors="replace")
    else:
        raise BridgeError("读取编码仅支持 utf-8 或 base64。")
    return {
        "path": str(target),
        "content": content,
        "encoding": encoding,
        "bytes": size,
        "mtime_ns": target.stat().st_mtime_ns,
    }


def _copy_path(config: dict[str, Any], payload: dict[str, Any]) -> Path:
    ensure_write_enabled(config)
    source = resolve_bridge_path(config, str(payload.get("source", "")))
    destination = resolve_bridge_path(
        config,
        str(payload.get("destination", "")),
        must_exist=False,
    )
    if not source.is_file():
        raise BridgeError("当前复制接口只支持文件。")
    if destination.exists() and destination.is_dir():
        destination = destination / source.name
    if destination.exists():
        raise BridgeError("目标文件已存在，复制不会覆盖。")
    if not destination.parent.is_dir():
        raise BridgeError("目标目录不存在。")
    shutil.copy2(source, destination)
    return destination


def perform_bridge_action(
    config: dict[str, Any], action: str, requested: str
) -> Path:
    resolved = resolve_bridge_path(config, requested)
    if action not in {"open", "reveal"}:
        raise BridgeError("不支持的本地操作。")
    if action == "open":
        if resolved.is_file() and resolved.suffix.casefold() not in OPENABLE_EXTENSIONS:
            raise BridgeError("该文件类型不允许通过页面打开。")
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise BridgeError("当前系统不支持调用默认软件。")
        try:
            startfile(str(resolved))
        except OSError:
            _launch_with_dialog(resolved)
        return resolved

    if resolved.is_file():
        creation_flags = (
            subprocess.CREATE_NO_WINDOW
            if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        subprocess.Popen(
            ["explorer.exe", "/select,", str(resolved)],
            creationflags=creation_flags,
        )
    else:
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise BridgeError("当前系统不支持打开资源管理器。")
        startfile(str(resolved))
    return resolved


def console_write(message: str) -> None:
    stream = getattr(sys, "stdout", None)
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except (OSError, ValueError):
        pass


class StaticSiteServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        config: dict[str, Any],
    ) -> None:
        self.site_config = config
        self._status_lock = threading.Lock()
        self._status_version: int | None = None
        self._status_cache: dict[str, Any] | None = None
        self.watcher: FileWatcher | None = None
        allowed_root = Path(
            str(
                config.get("bridge_root")
                or config.get("root_path")
            )
        ).resolve()
        self.command_manager: CommandJobManager | None = (
            CommandJobManager(config.get("commands", []), allowed_root)
            if config.get("command_enabled")
            else None
        )
        self._auto_command_lock = threading.Lock()
        self._auto_command_running = False
        self._auto_command_pending = False
        self._auto_command_ignore_until = 0.0
        super().__init__(server_address, StaticRequestHandler)

    def invalidate_status(self) -> None:
        with self._status_lock:
            self._status_cache = None
            self._status_version = None

    def handle_watch_batch(self, events: list[dict[str, Any]]) -> None:
        auto_command_id = str(
            self.site_config.get("auto_command_id") or ""
        ).strip()
        manager = self.command_manager
        watcher = self.watcher
        if not auto_command_id or manager is None or watcher is None:
            return
        if time.monotonic() < self._auto_command_ignore_until:
            return

        watch_root = Path(
            str(
                self.site_config.get("watch_root")
                or self.site_config.get("bridge_root")
                or self.site_config.get("root_path")
            )
        ).resolve()
        site_root = Path(self.site_config["root_path"]).resolve()
        try:
            site_prefix = site_root.relative_to(watch_root).as_posix()
        except ValueError:
            site_prefix = ""

        def is_site_event(event: dict[str, Any]) -> bool:
            changed = str(event.get("path", "")).replace("\\", "/")
            if not site_prefix or site_prefix == ".":
                return False
            return changed == site_prefix or changed.startswith(f"{site_prefix}/")

        interesting = []
        for event in events:
            if is_site_event(event):
                continue
            if event.get("action") == "overflow":
                interesting.append(event)
                continue
            path = str(event.get("path", ""))
            if PurePosixPath(path).suffix.casefold() in OPENABLE_EXTENSIONS:
                interesting.append(event)
        if not interesting:
            return

        with self._auto_command_lock:
            if self._auto_command_running:
                self._auto_command_pending = True
                return
            self._auto_command_running = True
        try:
            job = manager.run(
                {"command": auto_command_id},
                on_complete=self._auto_command_finished,
            )
        except (BridgeError, OSError, ValueError) as exc:
            with self._auto_command_lock:
                self._auto_command_running = False
            watcher.publish_control(
                "rebuild",
                [
                    {
                        "action": "failed",
                        "path": "",
                        "command": auto_command_id,
                        "error": str(exc),
                    }
                ],
            )
            return
        watcher.publish_control(
            "rebuild",
            [
                {
                    "action": "started",
                    "path": "",
                    "command": auto_command_id,
                    "job": job["id"],
                }
            ],
        )

    def _auto_command_finished(self, job: dict[str, Any]) -> None:
        watcher = self.watcher
        with self._auto_command_lock:
            self._auto_command_running = False
            pending = self._auto_command_pending
            self._auto_command_pending = False
            self._auto_command_ignore_until = (
                time.monotonic() + AUTO_COMMAND_COOLDOWN_SECONDS
            )
        if watcher is not None:
            watcher.publish_control(
                "rebuild",
                [
                    {
                        "action": job.get("status", "failed"),
                        "path": "",
                        "command": job.get("command", ""),
                        "job": job.get("id", ""),
                        "exit_code": job.get("exit_code"),
                        "error": job.get("error") or "",
                    }
                ],
            )
        if pending:
            timer = threading.Timer(
                0.5,
                self.handle_watch_batch,
                args=([{"action": "pending", "path": ""}],),
            )
            timer.daemon = True
            timer.start()

    def status_payload(self) -> dict[str, Any]:
        entry = (
            Path(self.site_config["root_path"]).resolve()
            / Path(self.site_config["entry_file"])
        )
        try:
            stat = entry.stat()
            version = int(stat.st_mtime_ns)
        except OSError:
            stat = None
            version = 0

        with self._status_lock:
            if self._status_cache is None or self._status_version != version:
                book_count = 0
                if stat is not None:
                    try:
                        book_count = entry.read_bytes().count(b'"ap":"')
                    except OSError:
                        book_count = 0
                last_scan = (
                    datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    if stat is not None
                    else ""
                )
                bridge_enabled = bool(self.site_config.get("bridge_enabled"))
                write_enabled = bool(self.site_config.get("write_enabled"))
                features = (
                    [
                        "open",
                        "openWith",
                        "export",
                        "exportToFolder",
                        "list",
                        "read",
                        "copy",
                        "pickFolder",
                        "pickFiles",
                        "reveal",
                    ]
                    if bridge_enabled
                    else []
                )
                if write_enabled:
                    features.extend(["copy", "trash", "move", "write", "mkdir"])
                watch_enabled = bool(self.site_config.get("watch_enabled"))
                if watch_enabled:
                    features.append("watch")
                command_enabled = bool(self.site_config.get("command_enabled"))
                if command_enabled:
                    features.extend(["run", "jobs"])
                watch_root = Path(
                    str(
                        self.site_config.get("watch_root")
                        or self.site_config.get("bridge_root")
                        or self.site_config.get("root_path")
                    )
                ).resolve()
                site_root = Path(self.site_config["root_path"]).resolve()
                try:
                    site_prefix = site_root.relative_to(watch_root).as_posix()
                except ValueError:
                    site_prefix = ""
                self._status_cache = {
                    "ok": True,
                    "server_mode": True,
                    "service": (
                        "book-library"
                        if Path(self.site_config["entry_file"]).name.casefold()
                        == "书库总览.html".casefold()
                        else "localdock-static"
                    ),
                    "root": str(
                        self.site_config.get("bridge_root")
                        or self.site_config.get("root_path")
                    ),
                    "bridge": bridge_enabled,
                    "write": write_enabled,
                    "features": features,
                    "watch": {
                        "enabled": watch_enabled,
                        "auto_reload": bool(
                            self.site_config.get("auto_reload")
                        ),
                        "interval": int(
                            self.site_config.get("watch_interval", 10)
                        ),
                        "root": str(watch_root) if watch_enabled else None,
                        "site_prefix": site_prefix,
                    },
                    "commands": (
                        [
                            {
                                "id": command.get("id"),
                                "label": command.get("label"),
                            }
                            for command in self.site_config.get("commands", [])
                        ]
                        if command_enabled
                        else []
                    ),
                    "auto_command_id": (
                        self.site_config.get("auto_command_id") or None
                    ),
                    "version": version,
                    "book_count": book_count,
                    "last_scan": last_scan,
                    "scanning": False,
                    "source": "localdock",
                }
                self._status_version = version
            return dict(self._status_cache)


class StaticRequestHandler(BaseHTTPRequestHandler):
    server_version = "LocalDockStatic/0.1"
    sys_version = ""

    @property
    def config(self) -> dict[str, Any]:
        return self.server.site_config  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        if self._handle_internal("GET"):
            return
        self._serve_file(head_only=False)

    def do_HEAD(self) -> None:
        if self._handle_internal("HEAD"):
            return
        self._serve_file(head_only=True)

    def do_POST(self) -> None:
        if self._handle_internal("POST"):
            return
        self._send_error_page(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed")

    def _handle_internal(self, method: str) -> bool:
        path = urlsplit(self.path).path
        if path == HEALTH_PATH and method in {"GET", "HEAD"}:
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service_id": self.config["service_id"],
                    "port": self.config["port"],
                },
                head_only=method == "HEAD",
            )
            return True

        if path in STATUS_PATHS and method in {"GET", "HEAD"}:
            payload = self.server.status_payload()  # type: ignore[attr-defined]
            self._send_json(HTTPStatus.OK, payload, head_only=method == "HEAD")
            return True

        if path == BRIDGE_SCRIPT_PATH and method in {"GET", "HEAD"}:
            self._send_bytes(
                HTTPStatus.OK,
                BRIDGE_SCRIPT,
                "application/javascript; charset=utf-8",
                head_only=method == "HEAD",
            )
            return True

        if path == EVENTS_PATH and method == "GET":
            self._serve_events()
            return True

        if path in REFRESH_PATHS and method == "POST":
            self._handle_refresh()
            return True

        if path in RUN_PATHS and method == "POST":
            self._handle_run()
            return True

        job_match = JOB_PATH_PATTERN.fullmatch(path)
        if job_match and method == "GET":
            self._handle_job(job_match.group(1))
            return True
        cancel_match = JOB_CANCEL_PATTERN.fullmatch(path)
        if cancel_match and method == "POST":
            self._handle_cancel_job(cancel_match.group(1))
            return True

        if path in OPEN_PATHS and method == "POST":
            self._handle_bridge("open")
            return True

        if path in OPEN_WITH_PATHS and method == "POST":
            self._handle_bridge("openWith")
            return True

        if path in EXPORT_PATHS and method == "POST":
            self._handle_bridge("export")
            return True

        if path in EXPORT_FOLDER_PATHS and method == "POST":
            self._handle_bridge("exportToFolder")
            return True

        if path in LIST_PATHS and method == "POST":
            self._handle_bridge("list")
            return True

        if path in READ_PATHS and method == "POST":
            self._handle_bridge("read")
            return True

        if path in COPY_PATHS and method == "POST":
            self._handle_bridge("copy")
            return True

        if path in PICK_FOLDER_PATHS and method == "POST":
            self._handle_bridge("pickFolder")
            return True

        if path in PICK_FILES_PATHS and method == "POST":
            self._handle_bridge("pickFiles")
            return True

        if path in REVEAL_PATHS and method == "POST":
            self._handle_bridge("reveal")
            return True

        if path in TRASH_PATHS and method == "POST":
            self._handle_bridge("trash")
            return True

        if path in MOVE_PATHS and method == "POST":
            self._handle_bridge("move")
            return True

        if path in WRITE_PATHS and method == "POST":
            self._handle_bridge("write")
            return True

        if path in MKDIR_PATHS and method == "POST":
            self._handle_bridge("mkdir")
            return True

        if path == SHUTDOWN_PATH and method == "POST":
            provided = self.headers.get("X-LocalDock-Worker-Token", "")
            if not hmac.compare_digest(provided, self.config["token"]):
                self._send_json(HTTPStatus.FORBIDDEN, {"ok": False}, head_only=False)
                return True
            self._send_json(HTTPStatus.OK, {"ok": True}, head_only=False)
            threading.Timer(0.15, self.server.shutdown).start()
            return True

        if path.startswith("/__localdock/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False}, head_only=False)
            return True
        return False

    def _read_local_json(self) -> dict[str, Any] | None:
        origin = self.headers.get("Origin", "").rstrip("/")
        host = self.headers.get("Host", "")
        if origin and origin != f"http://{host}":
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "不允许跨站调用本地命令。"},
                head_only=False,
            )
            return None
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type.casefold() != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"ok": False, "error": "命令请求必须使用 JSON。"},
                head_only=False,
            )
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BRIDGE_BODY:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "命令请求内容无效。"},
                head_only=False,
            )
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "命令请求 JSON 无效。"},
                head_only=False,
            )
            return None
        if not isinstance(payload, dict):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "命令请求必须是 JSON 对象。"},
                head_only=False,
            )
            return None
        return payload

    def _handle_run(self) -> None:
        manager = self.server.command_manager  # type: ignore[attr-defined]
        if manager is None:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "当前站点未启用命令运行。"},
                head_only=False,
            )
            return
        payload = self._read_local_json()
        if payload is None:
            return
        try:
            job = manager.run(payload)
        except BridgeError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": str(exc)},
                head_only=False,
            )
            return
        self._send_json(
            HTTPStatus.ACCEPTED,
            {"ok": True, "job": job},
            head_only=False,
        )

    def _handle_job(self, job_id: str) -> None:
        manager = self.server.command_manager  # type: ignore[attr-defined]
        if manager is None:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "当前站点未启用命令运行。"},
                head_only=False,
            )
            return
        try:
            job = manager.get(job_id)
        except BridgeError as exc:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error": str(exc)},
                head_only=False,
            )
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "job": job}, head_only=False)

    def _handle_cancel_job(self, job_id: str) -> None:
        manager = self.server.command_manager  # type: ignore[attr-defined]
        if manager is None:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "当前站点未启用命令运行。"},
                head_only=False,
            )
            return
        payload = self._read_local_json()
        if payload is None:
            return
        try:
            job = manager.cancel(job_id)
        except BridgeError as exc:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error": str(exc)},
                head_only=False,
            )
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "job": job}, head_only=False)

    def _serve_events(self) -> None:
        watcher = self.server.watcher  # type: ignore[attr-defined]
        if watcher is None:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "当前站点未启用文件监控。"},
                head_only=False,
            )
            return
        subscriber = watcher.subscribe()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            ready = {
                "seq": 0,
                "kind": "ready",
                "root": str(watcher.root),
                "events": [],
            }
            self.wfile.write(
                f"data: {json.dumps(ready, ensure_ascii=False)}\n\n".encode("utf-8")
            )
            self.wfile.flush()
            while True:
                try:
                    event = subscriber.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                else:
                    self.wfile.write(
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode(
                            "utf-8"
                        )
                    )
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            watcher.unsubscribe(subscriber)
            self.close_connection = True

    def _handle_refresh(self) -> None:
        origin = self.headers.get("Origin", "").rstrip("/")
        host = self.headers.get("Host", "")
        if origin and origin != f"http://{host}":
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "不允许跨站调用刷新接口。"},
                head_only=False,
            )
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type.casefold() != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"ok": False, "error": "刷新请求必须使用 JSON。"},
                head_only=False,
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 0 or length > MAX_BRIDGE_BODY:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "请求内容无效。"},
                head_only=False,
            )
            return
        if length:
            self.rfile.read(length)
        self.server.invalidate_status()  # type: ignore[attr-defined]
        watcher = self.server.watcher  # type: ignore[attr-defined]
        if watcher is not None:
            watcher.publish_manual()
        status = self.server.status_payload()  # type: ignore[attr-defined]
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "version": status["version"],
                "watch": status["watch"],
            },
            head_only=False,
        )

    def _handle_bridge(self, action: str) -> None:
        if not self.config.get("bridge_enabled"):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "当前站点未启用本地文件操作。"},
                head_only=False,
            )
            return

        if action in {"copy", "trash", "move", "write", "mkdir"} and not self.config.get(
            "write_enabled"
        ):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "当前站点未启用删除、移动或写入权限。"},
                head_only=False,
            )
            return

        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type.casefold() != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"ok": False, "error": "本地操作请求必须使用 JSON。"},
                head_only=False,
            )
            return

        origin = self.headers.get("Origin", "").rstrip("/")
        host = self.headers.get("Host", "")
        if origin and origin != f"http://{host}":
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "不允许跨站调用本地文件操作。"},
                head_only=False,
            )
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        limit = MAX_WRITE_REQUEST_BODY if action == "write" else MAX_BRIDGE_BODY
        if length <= 0 or length > limit:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "请求内容无效。"},
                head_only=False,
            )
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            result: dict[str, Any]
            if action in {"open", "reveal"}:
                resolved = perform_bridge_action(
                    self.config,
                    action,
                    str(payload.get("path", "")),
                )
                result = {"path": str(resolved)}
            elif action == "openWith":
                resolved = perform_open_with(
                    self.config,
                    str(payload.get("path", "")),
                )
                result = {"path": str(resolved)}
            elif action == "export":
                exported = _export_path(
                    self.config,
                    str(payload.get("path", "")),
                )
                if exported is None:
                    result = {"path": "", "canceled": True}
                else:
                    result = {"path": str(exported), "canceled": False}
                    if payload.get("open_with") is True and exported.is_file():
                        _launch_with_dialog(exported)
            elif action == "exportToFolder":
                result = _export_to_folder(self.config, payload)
            elif action == "list":
                result = _list_path(self.config, payload)
            elif action == "read":
                result = _read_path(self.config, payload)
            elif action == "copy":
                resolved = _copy_path(self.config, payload)
                result = {"path": str(resolved)}
            elif action == "pickFolder":
                selected = _pick_system_path("folder")
                result = {"path": str(selected) if selected else "", "canceled": not selected}
            elif action == "pickFiles":
                selected = _pick_system_path("files")
                result = {"paths": [str(path) for path in selected], "canceled": not selected}
            elif action == "trash":
                resolved = _trash_path(self.config, str(payload.get("path", "")))
                result = {
                    "path": str(resolved),
                    "trash_path": "",
                    "version": self.server.status_payload()["version"],
                }
            elif action == "move":
                resolved = _move_path(self.config, payload)
                result = {"path": str(resolved)}
            elif action == "write":
                result = _write_file(self.config, payload)
            elif action == "mkdir":
                resolved = _make_directory(self.config, payload)
                result = {"path": str(resolved)}
            else:
                raise BridgeError("不支持的本地操作。")
        except (BridgeError, OSError, ValueError, UnicodeDecodeError) as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": str(exc) or "本地操作失败。"},
                head_only=False,
            )
            return

        console_write(f"LocalDock file action '{action}': {result.get('path', '')}")
        self._send_json(
            HTTPStatus.OK,
            {"ok": True, "action": action, **result},
            head_only=False,
        )

    def _entry_url(self, base: str = "") -> str:
        entry = str(self.config["entry_file"]).replace("\\", "/").strip("/")
        if base:
            target = f"{base.rstrip('/')}/{entry}"
        else:
            target = entry
        return "/" + quote(target, safe="/")

    def _serve_file(self, head_only: bool) -> None:
        root = Path(self.config["root_path"]).resolve()
        parsed_request = urlsplit(self.path)
        request_path = unquote(parsed_request.path)
        request_query = parsed_request.query

        def entry_redirect(base: str = "") -> None:
            location = self._entry_url(base)
            if request_query:
                location += f"?{request_query}"
            self._redirect(location)

        if "\x00" in request_path:
            self._send_error_page(HTTPStatus.BAD_REQUEST, "Invalid path")
            return

        relative_text = request_path.lstrip("/").replace("\\", "/")
        if not relative_text:
            entry_redirect()
            return

        relative = PurePosixPath(relative_text)
        if relative_text.endswith("/"):
            entry_redirect(relative_text)
            return

        candidate = (root / Path(*relative.parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            self._send_error_page(HTTPStatus.FORBIDDEN, "Path is outside the site root")
            return

        if candidate.is_dir():
            entry_redirect(relative_text)
            return
        if not candidate.is_file():
            self._send_error_page(HTTPStatus.NOT_FOUND, "File not found")
            return

        if (
            candidate.suffix.casefold() in {".html", ".htm"}
            and (
                self.config.get("bridge_enabled")
                or self.config.get("watch_enabled")
            )
        ):
            body = candidate.read_bytes()
            if b"/__localdock/bridge.js" not in body:
                tag = b'<script src="/__localdock/bridge.js"></script>'
                body_lower = body.lower()
                closing_head = body_lower.find(b"</head>")
                if closing_head >= 0:
                    body = body[:closing_head] + tag + body[closing_head:]
                else:
                    closing_body = body_lower.rfind(b"</body>")
                    if closing_body >= 0:
                        body = body[:closing_body] + tag + body[closing_body:]
                    else:
                        body += tag
            self._send_bytes(
                HTTPStatus.OK,
                body,
                "text/html; charset=utf-8",
                head_only=head_only,
            )
            return

        content_type, _ = mimetypes.guess_type(candidate.name)
        content_type = content_type or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
            "application/manifest+json",
            "image/svg+xml",
        }:
            content_type += "; charset=utf-8"

        try:
            size = candidate.stat().st_size
            with candidate.open("rb") as source:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if not head_only:
                    shutil.copyfileobj(source, self.wfile, length=64 * 1024)
        except (BrokenPipeError, ConnectionResetError):
            return
        except OSError:
            self._send_error_page(
                HTTPStatus.INTERNAL_SERVER_ERROR, "Unable to read file"
            )

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(
        self, status: HTTPStatus, payload: dict[str, Any], head_only: bool
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        head_only: bool,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_error_page(self, status: HTTPStatus, message: str) -> None:
        body = (
            "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
            f"<title>{status.value}</title>"
            "<style>body{font:15px system-ui;margin:48px;color:#24312e}"
            "h1{font-size:24px}p{color:#62706c}</style>"
            f"<h1>{status.value} {status.phrase}</h1><p>{message}</p></html>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format_string: str, *args: Any) -> None:
        console_write(
            f"[{self.log_date_time_string()}] "
            f"{self.client_address[0]} {format_string % args}"
        )


def decode_config(encoded: str) -> dict[str, Any]:
    raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
    config = json.loads(raw.decode("utf-8"))
    required = {"service_id", "root_path", "entry_file", "port", "token"}
    missing = required.difference(config)
    if missing:
        raise ValueError(f"Worker config is missing: {', '.join(sorted(missing))}")
    return config


def run_worker(encoded_config: str) -> None:
    config = decode_config(encoded_config)
    server = StaticSiteServer(("127.0.0.1", int(config["port"])), config)
    if config.get("watch_enabled"):
        watch_root = Path(
            str(
                config.get("watch_root")
                or config.get("bridge_root")
                or config.get("root_path")
            )
        ).resolve()
        interval = int(config.get("watch_interval", 10))
        server.watcher = FileWatcher(
            watch_root,
            interval=interval,
            debounce=max(0.5, min(2.0, interval * 0.2)),
            on_batch=server.handle_watch_batch,
        )
        server.watcher.start()
    console_write(
        f"Static service '{config['service_id']}' listening on "
        f"http://127.0.0.1:{config['port']}/"
    )
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if server.command_manager is not None:
            server.command_manager.close_all()
        if server.watcher is not None:
            server.watcher.close()
        server.server_close()


def worker_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", required=True)
    args = parser.parse_args(argv)
    run_worker(args.worker)

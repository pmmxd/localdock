from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS services (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    entry_file TEXT NOT NULL DEFAULT 'index.html',
                    service_type TEXT NOT NULL DEFAULT 'static'
                        CHECK (service_type IN ('static', 'node')),
                    backend_json TEXT NOT NULL DEFAULT '{}',
                    port INTEGER NOT NULL UNIQUE,
                    auto_open INTEGER NOT NULL DEFAULT 1
                        CHECK (auto_open IN (0, 1)),
                    bridge_enabled INTEGER NOT NULL DEFAULT 1
                        CHECK (bridge_enabled IN (0, 1)),
                    bridge_root TEXT,
                    external_app_path TEXT,
                    write_enabled INTEGER NOT NULL DEFAULT 1
                        CHECK (write_enabled IN (0, 1)),
                    watch_enabled INTEGER NOT NULL DEFAULT 1
                        CHECK (watch_enabled IN (0, 1)),
                    watch_root TEXT,
                    watch_interval INTEGER NOT NULL DEFAULT 10,
                    auto_reload INTEGER NOT NULL DEFAULT 1
                        CHECK (auto_reload IN (0, 1)),
                    command_enabled INTEGER NOT NULL DEFAULT 0
                        CHECK (command_enabled IN (0, 1)),
                    commands_json TEXT NOT NULL DEFAULT '[]',
                    auto_command_id TEXT,
                    group_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_started_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_services_name
                    ON services(name);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(services)")
            }
            if "bridge_enabled" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN "
                    "bridge_enabled INTEGER NOT NULL DEFAULT 0"
                )
            if "bridge_root" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN bridge_root TEXT"
                )
            if "external_app_path" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN external_app_path TEXT"
                )
            if "write_enabled" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN "
                    "write_enabled INTEGER NOT NULL DEFAULT 0"
                )
            if "watch_enabled" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN "
                    "watch_enabled INTEGER NOT NULL DEFAULT 0"
                )
            if "watch_root" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN watch_root TEXT"
                )
            if "watch_interval" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN "
                    "watch_interval INTEGER NOT NULL DEFAULT 5"
                )
            if "auto_reload" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN "
                    "auto_reload INTEGER NOT NULL DEFAULT 0"
                )
            if "command_enabled" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN "
                    "command_enabled INTEGER NOT NULL DEFAULT 0"
                )
            if "commands_json" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN "
                    "commands_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "auto_command_id" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN auto_command_id TEXT"
                )
            if "service_type" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN "
                    "service_type TEXT NOT NULL DEFAULT 'static'"
                )
            if "backend_json" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN "
                    "backend_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "group_name" not in columns:
                connection.execute(
                    "ALTER TABLE services ADD COLUMN "
                    "group_name TEXT NOT NULL DEFAULT ''"
                )
            capability_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if capability_version < 1:
                connection.execute(
                    "UPDATE services SET bridge_enabled = 1, write_enabled = 1, "
                    "watch_enabled = 1, auto_reload = 1, "
                    "watch_interval = CASE WHEN watch_interval < 10 THEN 10 "
                    "ELSE watch_interval END"
                )
                connection.execute("PRAGMA user_version = 1")

    @staticmethod
    def _service_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        service = dict(row)
        service["auto_open"] = bool(service["auto_open"])
        service["bridge_enabled"] = bool(service.get("bridge_enabled", False))
        service["write_enabled"] = bool(service.get("write_enabled", False))
        service["watch_enabled"] = bool(service.get("watch_enabled", False))
        service["auto_reload"] = bool(service.get("auto_reload", False))
        service["command_enabled"] = bool(service.get("command_enabled", False))
        try:
            backend = json.loads(service.get("backend_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            backend = {}
        service["backend"] = backend if isinstance(backend, dict) else {}
        return service

    def list_services(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM services ORDER BY name COLLATE NOCASE, created_at"
            ).fetchall()
        return [self._service_from_row(row) for row in rows if row is not None]

    def get_service(self, service_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM services WHERE id = ?", (service_id,)
            ).fetchone()
        return self._service_from_row(row)

    def find_by_port(self, port: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM services WHERE port = ?", (port,)
            ).fetchone()
        return self._service_from_row(row)

    def create_service(self, values: dict[str, Any]) -> dict[str, Any]:
        service_id = values.get("id") or uuid.uuid4().hex
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO services (
                    id, name, root_path, entry_file, service_type, backend_json,
                    port, auto_open,
                    bridge_enabled, bridge_root, external_app_path, write_enabled,
                    watch_enabled, watch_root, watch_interval, auto_reload,
                    command_enabled, commands_json, auto_command_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service_id,
                    values["name"],
                    values["root_path"],
                    values["entry_file"],
                    values.get("service_type", "static"),
                    values.get("backend_json", "{}"),
                    values["port"],
                    int(values["auto_open"]),
                    int(values["bridge_enabled"]),
                    values["bridge_root"],
                    values.get("external_app_path"),
                    int(values["write_enabled"]),
                    int(values["watch_enabled"]),
                    values["watch_root"],
                    values["watch_interval"],
                    int(values["auto_reload"]),
                    int(values["command_enabled"]),
                    values["commands_json"],
                    values["auto_command_id"],
                    now,
                    now,
                ),
            )
        service = self.get_service(service_id)
        if service is None:
            raise RuntimeError("Failed to load the newly created service.")
        return service

    def update_service(
        self, service_id: str, values: dict[str, Any]
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE services
                SET name = ?, root_path = ?, entry_file = ?, service_type = ?,
                    backend_json = ?, port = ?,
                    auto_open = ?, bridge_enabled = ?, bridge_root = ?,
                    external_app_path = ?,
                    write_enabled = ?, watch_enabled = ?, watch_root = ?,
                    watch_interval = ?, auto_reload = ?, command_enabled = ?,
                    commands_json = ?, auto_command_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    values["name"],
                    values["root_path"],
                    values["entry_file"],
                    values.get("service_type", "static"),
                    values.get("backend_json", "{}"),
                    values["port"],
                    int(values["auto_open"]),
                    int(values["bridge_enabled"]),
                    values["bridge_root"],
                    values.get("external_app_path"),
                    int(values["write_enabled"]),
                    int(values["watch_enabled"]),
                    values["watch_root"],
                    values["watch_interval"],
                    int(values["auto_reload"]),
                    int(values["command_enabled"]),
                    values["commands_json"],
                    values["auto_command_id"],
                    utc_now(),
                    service_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_service(service_id)

    def delete_service(self, service_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM services WHERE id = ?", (service_id,)
            )
        return cursor.rowcount > 0

    def mark_started(self, service_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE services SET last_started_at = ? WHERE id = ?",
                (utc_now(), service_id),
            )

    def set_service_group(self, service_ids: list[str], group_name: str) -> int:
        if not service_ids:
            return 0
        placeholders = ",".join("?" for _ in service_ids)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE services SET group_name = ?, updated_at = ? "
                f"WHERE id IN ({placeholders})",
                (group_name, utc_now(), *service_ids),
            )
        return cursor.rowcount

    def rename_service_group(self, old_name: str, new_name: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE services SET group_name = ?, updated_at = ? "
                "WHERE group_name = ? COLLATE NOCASE",
                (new_name, utc_now(), old_name),
            )
        return cursor.rowcount

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

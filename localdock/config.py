from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "LocalDock"
MANAGER_HOST = "127.0.0.1"
MANAGER_PORT = 7080
AUTO_SERVICE_PORT_START = 7000
DEFAULT_SERVICE_PORT = AUTO_SERVICE_PORT_START


def _resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def _data_root(resource_root: Path) -> Path:
    override = os.environ.get("LOCALDOCK_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return resource_root / "data"


RESOURCE_DIR = _resource_root()
WEB_DIR = RESOURCE_DIR / "web"
DATA_DIR = _data_root(RESOURCE_DIR)
LOG_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "localdock.db"
RUNTIME_PATH = DATA_DIR / "runtime.json"
TOKEN_PATH = DATA_DIR / "manager.token"
MANAGER_LOG_PATH = LOG_DIR / "manager.log"


def ensure_app_dirs() -> None:
    for directory in (DATA_DIR, LOG_DIR, BACKUP_DIR):
        directory.mkdir(parents=True, exist_ok=True)

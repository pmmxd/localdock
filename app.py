from __future__ import annotations

import argparse
import logging
import sys
import traceback

from localdock.config import MANAGER_LOG_PATH, MANAGER_PORT, ensure_app_dirs
from localdock.manager import run_manager
from localdock.worker import run_worker


def configure_logging() -> None:
    ensure_app_dirs()
    logging.basicConfig(
        filename=MANAGER_LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LocalDock local static site manager")
    parser.add_argument("--worker", help="Internal static worker configuration")
    parser.add_argument("--port", type=int, default=MANAGER_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    if args.worker:
        run_worker(args.worker)
        return 0

    configure_logging()
    return run_manager(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        configure_logging()
        logging.exception("LocalDock failed")
        traceback.print_exc()
        raise SystemExit(1)

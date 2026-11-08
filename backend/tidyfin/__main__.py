"""Entry point: ``python -m tidyfin`` starts the server."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .logbus import setup_logging


def main():
    import uvicorn

    from .api import create_app

    setup_logging(getattr(logging, os.getenv("TIDYFIN_LOG_LEVEL", "INFO").upper(), logging.INFO))
    uvicorn.run(create_app(Path(os.getenv("TIDYFIN_DATA_DIR", "/data"))), host=os.getenv("TIDYFIN_HOST", "0.0.0.0"),
                port=int(os.getenv("TIDYFIN_PORT", "8484")), log_level="info")


if __name__ == "__main__":
    main()

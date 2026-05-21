from __future__ import annotations

import logging
import os
import sys


def configure_root_logger() -> logging.Logger:
    """Configure the spillover root logger from SPILLOVER_LOG_LEVEL env var."""
    logger = logging.getLogger("spillover")
    if logger.handlers:
        return logger  # already configured
    level_name = os.environ.get("SPILLOVER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"spillover.{name}")

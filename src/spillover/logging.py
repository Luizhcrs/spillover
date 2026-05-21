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


_REDACT_HEADERS = {
    "authorization",
    "x-api-key",
    "anthropic-api-key",
    "openai-api-key",
    "cookie",
    "set-cookie",
}


def redact(headers: dict | None) -> dict:
    """Return a copy of the headers dict with sensitive values masked."""
    if not headers:
        return {}
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _REDACT_HEADERS:
            if isinstance(v, str) and len(v) > 12:
                out[k] = v[:6] + "..." + v[-3:]
            else:
                out[k] = "***"
        else:
            out[k] = v
    return out

from __future__ import annotations

import hashlib
import os
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

_HEX_ID = re.compile(r"^[0-9a-f]{6,64}$")
_PATH_PROJECT = re.compile(r"^/p/([0-9a-zA-Z_\-]{1,64})(/.*)?$")
_EXEMPT_PATHS = {"/metrics", "/health", "/"}


def _resolve_project_id(raw: str) -> str:
    if _HEX_ID.match(raw):
        return raw
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class ProjectIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        path_match = _PATH_PROJECT.match(request.url.path)
        raw = None
        if path_match:
            raw = path_match.group(1)
            # Rewrite scope: ASGI scope path is mutable in Starlette
            new_path = path_match.group(2) or "/"
            request.scope["path"] = new_path
            request.scope["raw_path"] = new_path.encode("utf-8")
        else:
            raw = request.headers.get("x-project") or os.environ.get(
                "SPILLOVER_PROJECT_ID"
            )

        if not raw:
            raw = os.environ.get("SPILLOVER_DEFAULT_PROJECT_ID", "default")
        request.state.project_id = _resolve_project_id(raw)
        return await call_next(request)

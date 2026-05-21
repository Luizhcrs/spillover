from __future__ import annotations

import hashlib
import os
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

_HEX_ID = re.compile(r"^[0-9a-f]{6,64}$")

# Paths that bypass project_id resolution entirely (admin/observability).
_EXEMPT_PATHS = {"/metrics", "/health", "/"}


def _resolve_project_id(raw: str) -> str:
    if _HEX_ID.match(raw):
        return raw
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class ProjectIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        raw = request.headers.get("x-project") or os.environ.get(
            "SPILLOVER_PROJECT_ID"
        )
        if not raw:
            return JSONResponse(
                {
                    "error": (
                        "missing X-Project header and SPILLOVER_PROJECT_ID "
                        "env var; one of them must be set"
                    )
                },
                status_code=400,
            )
        request.state.project_id = _resolve_project_id(raw)
        return await call_next(request)

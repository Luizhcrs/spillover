from __future__ import annotations

import hashlib
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_HEX_ID = re.compile(r"^[0-9a-f]{6,64}$")


_EXEMPT_PATHS = {"/metrics", "/health", "/"}


class ProjectIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        raw = request.headers.get("x-project")
        if not raw:
            return JSONResponse(
                {"error": "missing X-Project header"}, status_code=400
            )
        if _HEX_ID.match(raw):
            project_id = raw
        else:
            project_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        request.state.project_id = project_id
        return await call_next(request)

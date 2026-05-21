from __future__ import annotations

import json

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from spillover.config import Config
from spillover.proxy.middleware import ProjectIdMiddleware
from spillover.proxy.streaming import duplicate_stream


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="spillover", version="0.1.0")
    app.add_middleware(ProjectIdMiddleware)
    app.state.config = config
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    @app.on_event("shutdown")
    async def _close():
        await app.state.http_client.aclose()

    @app.post("/v1/messages")
    async def messages(request: Request):
        body = await request.body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            return JSONResponse(
                {"error": f"invalid JSON in request body: {e.msg}"},
                status_code=400,
            )
        upstream_url = f"{config.upstream_base_url}/v1/messages"
        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in {"host", "content-length", "x-project"}
        }
        is_stream = bool(payload.get("stream"))

        if not is_stream:
            r = await app.state.http_client.post(
                upstream_url, headers=fwd_headers, content=body
            )
            return JSONResponse(
                content=r.json(),
                status_code=r.status_code,
                headers={"content-type": "application/json"},
            )

        # Streaming: open the upstream stream, capture status before yielding
        # body so non-200 responses propagate cleanly.
        upstream = await app.state.http_client.send(
            app.state.http_client.build_request(
                "POST", upstream_url, headers=fwd_headers, content=body
            ),
            stream=True,
        )
        sink: list[bytes] = []

        async def proxy_stream():
            try:
                async for chunk in duplicate_stream(upstream.aiter_bytes(), sink):
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            proxy_stream(),
            media_type="text/event-stream",
            status_code=upstream.status_code,
        )

    return app

from __future__ import annotations

import json
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from spillover.adapters.anthropic import AnthropicAdapter
from spillover.archive.writer import Turn, archive_raw
from spillover.config import Config
from spillover.eviction.selector import ActiveTurn, select_for_eviction
from spillover.eviction.tokenizer import count_tokens
from spillover.proxy.middleware import ProjectIdMiddleware
from spillover.proxy.streaming import duplicate_stream
from spillover.storage.sqlite import open_project_db


def _extract_usage_non_streaming(body: bytes) -> tuple[int, int] | None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    usage = data.get("usage")
    if not usage:
        return None
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def _extract_usage_sse(captured: list[bytes]) -> tuple[int, int] | None:
    """Walk captured SSE chunks for the message_stop / message_delta usage."""
    joined = b"".join(captured).decode("utf-8", errors="replace")
    input_tokens = 0
    output_tokens = 0
    found = False
    for line in joined.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        usage = obj.get("usage") or (obj.get("message") or {}).get("usage") or {}
        if usage:
            input_tokens = int(usage.get("input_tokens", input_tokens))
            output_tokens = int(usage.get("output_tokens", output_tokens))
            found = True
    return (input_tokens, output_tokens) if found else None


def _extract_assistant_text_sse(captured: list[bytes]) -> str:
    joined = b"".join(captured).decode("utf-8", errors="replace")
    text = ""
    for line in joined.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            obj = json.loads(line[len("data:"):].strip())
        except json.JSONDecodeError:
            continue
        delta = obj.get("delta") or {}
        if "text" in delta:
            text += delta["text"]
    return text


def _maybe_evict(
    config: Config,
    project_id: str,
    inbound_payload: dict,
    assistant_text: str | None,
    usage: tuple[int, int],
) -> None:
    input_tokens, output_tokens = usage
    fill_ratio = (input_tokens + output_tokens) / config.window_max
    if fill_ratio < config.watermark:
        return

    adapter = AnthropicAdapter()
    conv = adapter.parse(inbound_payload)
    if not conv.turns:
        return

    new_user_tokens = conv.turns[-1].token_count
    new_assistant_tokens = count_tokens(assistant_text or "")
    tokens_to_free = new_user_tokens + new_assistant_tokens
    if tokens_to_free <= 0:
        return

    active = [
        ActiveTurn(
            index=t.source_index if t.source_index is not None else i,
            token_count=t.token_count,
            role=t.role,
            pinned=False,
            memory_type=None,
            is_system=False,
        )
        for i, t in enumerate(conv.turns)
    ]
    result = select_for_eviction(
        active, tokens_to_free=tokens_to_free, recent_buffer=4
    )
    if not result.evicted_indexes:
        return

    db = open_project_db(config.db_root, project_id)
    try:
        ts = int(time.time() * 1000)
        episode_ids: list[str] = []
        for idx in result.evicted_indexes:
            turn = next(
                t for t in conv.turns if (t.source_index or 0) == idx
            )
            eid = archive_raw(
                db,
                Turn(
                    project_id=project_id,
                    role=turn.role,
                    content=turn.content,
                    tool_calls=turn.tool_calls,
                    code_refs=[],
                    token_count=turn.token_count,
                    ts=ts,
                ),
            )
            episode_ids.append(eid)
        if episode_ids:
            placeholders = ",".join("?" for _ in episode_ids)
            db.execute(
                f"UPDATE episodes SET evicted=1 WHERE id IN ({placeholders})",
                episode_ids,
            )
    finally:
        db.close()


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
        project_id = request.state.project_id
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
            resp_bytes = r.content
            if r.status_code == 200:
                usage = _extract_usage_non_streaming(resp_bytes)
                if usage is not None:
                    try:
                        resp_json = json.loads(resp_bytes)
                    except json.JSONDecodeError:
                        resp_json = {}
                    assistant_text = "".join(
                        b.get("text", "")
                        for b in resp_json.get("content", [])
                        if isinstance(b, dict)
                    )
                    _maybe_evict(
                        config, project_id, payload, assistant_text, usage
                    )
            return JSONResponse(
                content=json.loads(resp_bytes),
                status_code=r.status_code,
                headers={"content-type": "application/json"},
            )

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
                if upstream.status_code == 200:
                    usage = _extract_usage_sse(sink)
                    if usage is not None:
                        assistant_text = _extract_assistant_text_sse(sink)
                        _maybe_evict(
                            config, project_id, payload, assistant_text, usage
                        )

        return StreamingResponse(
            proxy_stream(),
            media_type="text/event-stream",
            status_code=upstream.status_code,
        )

    return app

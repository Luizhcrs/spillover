from __future__ import annotations

import asyncio
import functools
import json
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from spillover.adapters.anthropic import AnthropicAdapter
from spillover.adapters.base import Adapter, Conversation
from spillover.adapters.openai import OpenAIAdapter
from spillover.archive.writer import Turn, archive_raw
from spillover.config import Config
from spillover.eviction.selector import ActiveTurn, select_for_eviction
from spillover.eviction.tokenizer import count_tokens
from spillover.facet.embed import embed_text
from spillover.facet.entities import extract_entities
from spillover.facet.worker import FacetEvent, FacetWorker
from spillover.logging import configure_root_logger, get_logger
from spillover.proxy.middleware import ProjectIdMiddleware
from spillover.proxy.streaming import duplicate_stream
from spillover.retriever.budget import trim_to_budget
from spillover.retriever.fusion import rrf_fuse
from spillover.retriever.graph import graph_walk
from spillover.retriever.render import render_ltm_block
from spillover.retriever.vector import vector_topk
from spillover.storage.kuzu import open_project_kuzu
from spillover.storage.sqlite import open_project_db

_log = get_logger("proxy")


async def _run_sync(loop, fn, *args, **kwargs):
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


def _extract_usage_non_streaming(
    body: bytes, provider: str = "anthropic"
) -> tuple[int, int] | None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    usage = data.get("usage")
    if not usage:
        return None
    if provider == "openai":
        return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def _extract_usage_sse(captured: list[bytes]) -> tuple[int, int] | None:
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


def _stream_rewrite_enabled(config: Config) -> bool:
    import os
    return os.environ.get("SPILLOVER_STREAM_REWRITE", "1") != "0"


def _retrieve_ltm_block(config: Config, project_id: str, conv: Conversation) -> str:
    """Run hybrid retrieval and return the <spillover-ltm> string (or empty)."""
    if not conv.turns:
        return ""
    recent = conv.turns[-3:]
    query_parts = []
    for t in recent:
        if isinstance(t.content, str):
            query_parts.append(t.content)
        elif isinstance(t.content, list):
            query_parts.append(
                " ".join(
                    b.get("text", "")
                    for b in t.content
                    if isinstance(b, dict)
                )
            )
    query_text = "\n".join(query_parts)
    if not query_text.strip():
        return ""

    db = open_project_db(config.db_root, project_id)
    try:
        n = db.execute("SELECT COUNT(*) FROM vec_episodes").fetchone()[0]
        if n == 0:
            return ""
        emb = embed_text(query_text)
        v_hits = vector_topk(db, emb, k=config.retriever_vector_k)

        seeds = [e.name for e in extract_entities(query_text)][:20]
        g_hits: list = []
        if seeds:
            try:
                kuzu_conn = open_project_kuzu(config.db_root, project_id)
                g_hits = graph_walk(
                    kuzu_conn, seeds, k_hop=2, limit=config.retriever_graph_k
                )
            except Exception:
                log = get_logger("retriever")
                log.exception("graph walk failed project=%s", project_id)

        fused = rrf_fuse(v_hits, g_hits)[: config.retriever_topk]
        budget = int(config.window_max * config.ltm_budget_pct)
        trimmed = trim_to_budget(db, fused, max_tokens=budget)
        return render_ltm_block(db, trimmed)
    finally:
        db.close()


def _inject_ltm(payload: dict, ltm_text: str) -> None:
    if not ltm_text:
        return
    existing = payload.get("system")
    if existing is None:
        payload["system"] = ltm_text
    elif isinstance(existing, str):
        payload["system"] = ltm_text + "\n\n" + existing
    elif isinstance(existing, list):
        payload["system"] = [{"type": "text", "text": ltm_text}, *existing]


def _maybe_evict(
    config: Config,
    project_id: str,
    inbound_payload: dict,
    assistant_text: str | None,
    usage: tuple[int, int],
    adapter: Adapter | None = None,
) -> tuple[list[str], int]:
    """Return (archived_ids, tokens_archived) for this call (may be empty)."""
    input_tokens, output_tokens = usage
    fill_ratio = (input_tokens + output_tokens) / config.window_max
    if fill_ratio < config.watermark:
        return [], 0

    _adapter = adapter or AnthropicAdapter()
    conv = _adapter.parse(inbound_payload)
    if not conv.turns:
        return [], 0

    new_user_tokens = next(
        (t.token_count for t in reversed(conv.turns) if t.role == "user"),
        0,
    )
    new_assistant_tokens = count_tokens(assistant_text or "")
    tokens_to_free = new_user_tokens + new_assistant_tokens
    if tokens_to_free <= 0:
        return [], 0

    turns_by_source = {
        t.source_index: t for t in conv.turns if t.source_index is not None
    }
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
        return [], 0

    log = get_logger("eviction")
    log.info(
        "eviction project=%s tokens_to_free=%d freed=%d pass=%d "
        "budget_pressure=%s evicted_count=%d",
        project_id,
        tokens_to_free,
        result.tokens_freed,
        result.pass_used,
        result.budget_pressure,
        len(result.evicted_indexes),
    )

    db = open_project_db(config.db_root, project_id)
    archived_ids: list[str] = []
    tokens_archived = 0
    try:
        ts = int(time.time() * 1000)
        episode_ids: list[str] = []
        for idx in result.evicted_indexes:
            turn = turns_by_source.get(idx)
            if turn is None:
                continue
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
            tokens_archived += turn.token_count
        if episode_ids:
            placeholders = ",".join("?" for _ in episode_ids)
            db.execute(
                f"UPDATE episodes SET evicted=1 WHERE id IN ({placeholders})",
                episode_ids,
            )
            archived_ids = episode_ids
    finally:
        db.close()
    return archived_ids, tokens_archived


def _detect_and_rescue(
    config: Config,
    project_id: str,
    messages: list,
) -> tuple[list, list[str]]:
    """Sync wrapper: detect compaction + archive rescued turns. Returns (rescued, rescue_ids)."""
    from spillover.archive.writer import Turn, archive_raw
    from spillover.counter_compact.detection import detect_compaction, record_seen_turns

    rescue_db = open_project_db(config.db_root, project_id)
    try:
        rescued = detect_compaction(rescue_db, project_id, messages)
        record_seen_turns(rescue_db, project_id, messages)
    finally:
        rescue_db.close()

    if not rescued:
        return [], []

    rescue_db2 = open_project_db(config.db_root, project_id)
    rescue_ids: list[str] = []
    try:
        rescue_ts = int(time.time() * 1000)
        for r in rescued:
            eid = archive_raw(
                rescue_db2,
                Turn(
                    project_id=project_id,
                    role=r.role,
                    content=r.content,
                    tool_calls=[],
                    code_refs=[],
                    token_count=r.token_count,
                    ts=rescue_ts,
                    compaction_rescued=True,
                ),
            )
            rescue_ids.append(eid)
        if rescue_ids:
            placeholders = ",".join("?" for _ in rescue_ids)
            rescue_db2.execute(
                f"UPDATE episodes SET evicted=1, compaction_rescued=1 "
                f"WHERE id IN ({placeholders})",
                rescue_ids,
            )
    finally:
        rescue_db2.close()
    return rescued, rescue_ids


def _enqueue_facets(
    app: FastAPI,
    project_id: str,
    episode_ids: list[str],
    config: Config,
) -> None:
    queue = getattr(app.state, "facet_queue", None)
    if queue is None:
        return
    from spillover.metrics.registry import facet_dropped_total
    for eid in episode_ids:
        try:
            queue.put_nowait(
                FacetEvent(
                    project_id=project_id,
                    episode_id=eid,
                    db_root=config.db_root,
                )
            )
        except asyncio.QueueFull:
            facet_dropped_total.labels(project=project_id).inc()
            _log.warning(
                "facet queue full, dropping event project=%s id=%s",
                project_id,
                eid,
            )


def create_app(config: Config) -> FastAPI:
    configure_root_logger()
    log = get_logger("proxy")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from spillover.decay.scheduler import DecayScheduler

        app.state.config = config
        app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        app.state.facet_queue = asyncio.Queue(maxsize=1024)
        app.state.facet_worker = FacetWorker(app.state.facet_queue)
        app.state.facet_worker.start()
        app.state.decay_scheduler = DecayScheduler(config.db_root)
        app.state.decay_scheduler.start()
        try:
            yield
        finally:
            await app.state.decay_scheduler.stop()
            await app.state.facet_worker.stop()
            await app.state.http_client.aclose()

    app = FastAPI(title="spillover", version="1.0.0", lifespan=lifespan)
    app.add_middleware(ProjectIdMiddleware)

    async def _handle_request(
        request: Request,
        adapter: Adapter,
        upstream_url: str,
        provider: str,
    ):
        body = await request.body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            return JSONResponse(
                {"error": f"invalid JSON in request body: {e.msg}"},
                status_code=400,
            )
        project_id = request.state.project_id

        from spillover.counter_compact.intercept import (
            make_intercept_response,
            should_intercept_request,
        )
        from spillover.counter_compact.usage_rewrite import rewrite_response_json

        # Intercept only applies to Anthropic wire format (compact signal)
        if provider == "anthropic" and should_intercept_request(payload):
            log.info("intercept compact project=%s", project_id)
            return JSONResponse(make_intercept_response(payload), status_code=200)

        loop = asyncio.get_running_loop()

        # Retrieval pass: inject LTM into the payload before forwarding.
        try:
            conv = adapter.parse(payload)
            ltm_text = await _run_sync(loop, _retrieve_ltm_block, config, project_id, conv)
            adapter.inject_ltm(payload, ltm_text)
        except Exception:
            log.exception(
                "retriever failed project=%s; proceeding without LTM", project_id
            )

        # Detect compaction + rescue (Anthropic only), offloaded to executor
        rescue_ids: list[str] = []
        if provider == "anthropic":
            _, rescue_ids = await _run_sync(
                loop, _detect_and_rescue, config, project_id, payload.get("messages") or []
            )
            if rescue_ids:
                _enqueue_facets(app, project_id, rescue_ids, config)

        forwarded_body = json.dumps(payload).encode("utf-8")
        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in {"host", "content-length", "x-project"}
        }
        is_stream = bool(payload.get("stream"))

        if not is_stream:
            r = await app.state.http_client.post(
                upstream_url, headers=fwd_headers, content=forwarded_body
            )
            resp_bytes = r.content
            archived_ids: list[str] = []
            tokens_archived = 0
            if r.status_code == 200:
                usage = adapter.extract_usage_non_streaming(resp_bytes)
                if usage is not None:
                    try:
                        resp_json = json.loads(resp_bytes)
                    except json.JSONDecodeError:
                        resp_json = {}
                    assistant_text = adapter.parse_response_text(resp_json)
                    archived_ids, tokens_archived = await _run_sync(
                        loop, _maybe_evict,
                        config, project_id, payload, assistant_text, usage, adapter,
                    )
                    if tokens_archived > 0:
                        try:
                            resp_json = json.loads(resp_bytes)
                        except json.JSONDecodeError:
                            resp_json = {}
                        resp_json = rewrite_response_json(resp_json, tokens_archived)
                        resp_bytes = json.dumps(resp_json).encode("utf-8")
            if r.status_code >= 400:
                log.warning(
                    "upstream non-2xx status=%d project=%s",
                    r.status_code,
                    project_id,
                )
            if archived_ids:
                _enqueue_facets(app, project_id, archived_ids, config)
            return JSONResponse(
                content=json.loads(resp_bytes),
                status_code=r.status_code,
                headers={"content-type": "application/json"},
            )

        # Streaming branch
        if not _stream_rewrite_enabled(config):
            # Plan 3 behavior: yield live, no SSE usage rewrite
            upstream = await app.state.http_client.send(
                app.state.http_client.build_request(
                    "POST", upstream_url, headers=fwd_headers, content=forwarded_body
                ),
                stream=True,
            )
            sink: list[bytes] = []

            async def proxy_stream_live():
                try:
                    async for chunk in duplicate_stream(upstream.aiter_bytes(), sink):
                        yield chunk
                finally:
                    await upstream.aclose()
                    archived_ids_s: list[str] = []
                    if upstream.status_code == 200:
                        usage = adapter.extract_usage_sse(sink)
                        if usage is not None:
                            assistant_text = adapter.extract_assistant_text_sse(sink)
                            _evict_loop = asyncio.get_event_loop()
                            archived_ids_s, _ = await _run_sync(
                                _evict_loop, _maybe_evict,
                                config, project_id, payload, assistant_text, usage, adapter
                            )
                    if upstream.status_code >= 400:
                        log.warning(
                            "upstream non-2xx (stream) status=%d project=%s",
                            upstream.status_code,
                            project_id,
                        )
                    if archived_ids_s:
                        _enqueue_facets(app, project_id, archived_ids_s, config)

            return StreamingResponse(
                proxy_stream_live(),
                media_type="text/event-stream",
                status_code=upstream.status_code,
            )

        # Buffered SSE with usage rewrite
        from spillover.counter_compact.sse_rewrite import rewrite_sse_body

        buf = b""
        upstream_status = 200
        async with app.state.http_client.stream(
            "POST", upstream_url, headers=fwd_headers, content=forwarded_body
        ) as upstream_stream:
            upstream_status = upstream_stream.status_code
            async for chunk in upstream_stream.aiter_bytes():
                buf += chunk

        archived_ids_buf: list[str] = []
        tokens_archived_buf = 0
        if upstream_status == 200:
            usage = adapter.extract_usage_sse([buf])
            if usage is not None:
                assistant_text_buf = adapter.extract_assistant_text_sse([buf])
                archived_ids_buf, tokens_archived_buf = await _run_sync(
                    loop, _maybe_evict,
                    config, project_id, payload, assistant_text_buf, usage, adapter,
                )
        if upstream_status >= 400:
            log.warning(
                "upstream non-2xx (stream-buf) status=%d project=%s",
                upstream_status,
                project_id,
            )
        if tokens_archived_buf > 0:
            buf = rewrite_sse_body(buf, tokens_archived_buf)
        if archived_ids_buf:
            _enqueue_facets(app, project_id, archived_ids_buf, config)

        _buf_final = buf

        async def proxy_stream_buffered():
            yield _buf_final

        return StreamingResponse(
            proxy_stream_buffered(),
            media_type="text/event-stream",
            status_code=upstream_status,
        )

    @app.post("/v1/messages")
    async def messages_anthropic(request: Request):
        return await _handle_request(
            request,
            adapter=AnthropicAdapter(),
            upstream_url=f"{config.upstream_base_url}/v1/messages",
            provider="anthropic",
        )

    @app.post("/v1/chat/completions")
    async def messages_openai(request: Request):
        return await _handle_request(
            request,
            adapter=OpenAIAdapter(),
            upstream_url=f"{config.openai_base_url}/v1/chat/completions",
            provider="openai",
        )

    @app.get("/metrics")
    async def metrics():
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        from spillover.metrics.registry import REGISTRY

        return Response(
            generate_latest(REGISTRY),
            media_type=CONTENT_TYPE_LATEST,
        )

    return app

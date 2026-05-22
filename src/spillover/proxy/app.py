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
from spillover.proxy.fallback import (
    attempt_fallback_stream,
    attempt_fallback_unary,
    make_sse_banner,
    should_fallback,
)
from spillover.proxy.retry import with_retry, with_retry_stream
from spillover.retriever.budget import trim_to_budget
from spillover.retriever.fusion import rrf_fuse
from spillover.retriever.graph import graph_walk
from spillover.retriever.lexical import bm25_topk
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


def _ltm_budget_for(config: Config, payload: dict) -> int:
    """LTM injection token budget for this request.

    Hard absolute cap protects users whose provider TPM is small (e.g. OAuth
    bearers on Pro/Team plans). Without the cap a default 200k ceiling x 0.15
    LTM share = 30k tokens injected per request, which dwarfs most TPM tiers
    and produces 429 cascades. Override via SPILLOVER_LTM_MAX_TOKENS.
    """
    import os
    from spillover.budget.profile import select_profile

    profile = select_profile(payload, config.profile_default)
    pct_budget = int(config.operational_ceiling_tokens * profile.ltm_pct)
    try:
        absolute_cap = int(os.environ.get("SPILLOVER_LTM_MAX_TOKENS", "5000"))
    except ValueError:
        absolute_cap = 5000
    return min(pct_budget, absolute_cap)


def _retrieve_ltm_block(
    config: Config, project_id: str, conv: Conversation, inbound_payload: dict | None = None
) -> str:
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
                _log.exception("graph walk failed project=%s", project_id)

        b_hits = bm25_topk(db, query_text, k=config.retriever_bm25_k)
        # Causal leg: use BM25/vector top hit episode ids as seeds
        seed_ids = [h.episode_id for h in (v_hits[:3] + b_hits[:3])]
        c_hits: list = []
        if seed_ids:
            try:
                kuzu_conn = open_project_kuzu(config.db_root, project_id)
                from spillover.retriever.causal import causality_chain
                c_hits = causality_chain(kuzu_conn, seed_ids, depth=2)
            except Exception:
                _log.exception("causal walk failed project=%s", project_id)

        fused = rrf_fuse(v_hits, g_hits, b_hits, c_hits)[: config.retriever_topk]
        from spillover.metrics.registry import retriever_hits_total
        retriever_hits_total.labels(project=project_id, source="vector").inc(len(v_hits))
        retriever_hits_total.labels(project=project_id, source="graph").inc(len(g_hits))
        retriever_hits_total.labels(project=project_id, source="bm25").inc(len(b_hits))
        retriever_hits_total.labels(project=project_id, source="causal").inc(len(c_hits))
        if inbound_payload is not None:
            budget = _ltm_budget_for(config, inbound_payload)
        else:
            budget = int(config.window_max * config.ltm_budget_pct)
        trimmed = trim_to_budget(db, fused, max_tokens=budget)
        return render_ltm_block(db, trimmed)
    finally:
        db.close()


def _inject_ltm(payload: dict, ltm_text: str) -> None:
    """Inject LTM text. Placement controlled by SPILLOVER_LTM_PLACEMENT:

    - 'turns' (default): materialise the LTM block as a synthetic
      user→assistant pair INSERTED BEFORE the latest user turn. Models read
      this as real prior conversation and cite it like they cite history.
    - 'user': prepend the LTM markdown block to the LAST user message.
    - 'system': prepend the LTM markdown block to the system field
      (legacy; smaller models tend to ignore it).
    """
    if not ltm_text:
        return
    import os

    placement = os.environ.get("SPILLOVER_LTM_PLACEMENT", "turns")
    if placement == "system":
        existing = payload.get("system")
        if existing is None:
            payload["system"] = ltm_text
        elif isinstance(existing, str):
            payload["system"] = ltm_text + "\n\n" + existing
        elif isinstance(existing, list):
            payload["system"] = [{"type": "text", "text": ltm_text}, *existing]
        return

    if placement == "user":
        messages = payload.get("messages") or []
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = ltm_text + "\n\n" + content
            elif isinstance(content, list):
                msg["content"] = [{"type": "text", "text": ltm_text}, *content]
            else:
                msg["content"] = ltm_text
            return
        payload["system"] = ltm_text
        return

    if placement == "between":
        messages = payload.get("messages") or []
        if not messages:
            payload["system"] = ltm_text
            return
        # Find the LAST user message and insert the synthetic pair BEFORE it
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                synthetic = [
                    {
                        "role": "user",
                        "content": (
                            "Before answering, recall the following retrieved from "
                            "long-term memory of this project."
                        ),
                    },
                    {"role": "assistant", "content": ltm_text},
                ]
                payload["messages"] = (
                    list(messages[:i]) + synthetic + list(messages[i:])
                )
                return
        payload["system"] = ltm_text
        return

    # placement == "turns" — materialise as a synthetic conversation pair
    messages = payload.get("messages") or []
    if not messages:
        payload["system"] = ltm_text
        return
    # Find the index of the FIRST user message and inject before it
    insert_at = 0
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            insert_at = i
            break
    synthetic = [
        {
            "role": "user",
            "content": (
                "Before we continue: recall the following from our prior work "
                "on this project, retrieved from long-term memory."
            ),
        },
        {"role": "assistant", "content": ltm_text},
    ]
    payload["messages"] = (
        list(messages[:insert_at]) + synthetic + list(messages[insert_at:])
    )


def _evict_inbound_to_ceiling(
    config: Config,
    project_id: str,
    payload: dict,
    adapter: Adapter,
) -> tuple[int, int]:
    """Trim payload BEFORE forwarding so size stays <= operational_ceiling_tokens.

    Operates only on Anthropic-shape messages (role/content list). Removes
    oldest non-recent middle turns until the payload's total token estimate
    is under the ceiling. Archives every removed turn into the project store
    so retrieval can recall them later. Returns (turns_removed, tokens_freed).

    System block + first 2 turns (anchor) + last `recent_buffer` turns are
    preserved. If even after maximum trimming the payload is still too big,
    we stop and let the upstream complain -- never drop the anchor or recent
    context to "make it fit".
    """
    messages = payload.get("messages") or []
    if not messages:
        return 0, 0

    system = payload.get("system")
    system_tokens = count_tokens(system) if system else 0
    turn_tokens = [count_tokens(m.get("content")) for m in messages]
    total = system_tokens + sum(turn_tokens)
    ceiling = config.operational_ceiling_tokens
    if total <= ceiling:
        return 0, 0

    recent_buffer = 4
    anchor = 2
    if len(messages) <= anchor + recent_buffer:
        return 0, 0

    def _is_safely_evictable(msg: dict) -> bool:
        """Skip turns that participate in tool_use/tool_result pairing.
        Dropping one half of a pair makes Anthropic return 400 invalid_request."""
        content = msg.get("content")
        if isinstance(content, str):
            return True
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in ("tool_use", "tool_result"):
                    return False
            return True
        return False

    # Candidate indexes = middle (after anchor, before tail buffer), skip
    # tool-coupled turns so we never break Anthropic's pairing invariant.
    candidate_idxs = [
        i for i in range(anchor, len(messages) - recent_buffer)
        if _is_safely_evictable(messages[i])
    ]
    if not candidate_idxs:
        return 0, 0

    # Walk middle oldest-first, drop until under ceiling
    db = open_project_db(config.db_root, project_id)
    archived_ids: list[str] = []
    tokens_freed = 0
    try:
        from spillover.archive.writer import Turn, archive_raw
        ts = int(time.time() * 1000)
        keep_mask = [True] * len(messages)
        running = total
        for idx in candidate_idxs:
            if running <= ceiling:
                break
            msg = messages[idx]
            tok = turn_tokens[idx]
            if tok <= 0:
                continue
            try:
                eid = archive_raw(
                    db,
                    Turn(
                        project_id=project_id,
                        role=msg.get("role", "user"),
                        content=msg.get("content"),
                        token_count=tok,
                        ts=ts,
                        source_index=idx,
                        memory_type=None,
                    ),
                )
                archived_ids.append(eid)
                tokens_freed += tok
                keep_mask[idx] = False
                running -= tok
            except Exception:
                _log.exception(
                    "pre-forward archive failed project=%s idx=%d", project_id, idx
                )
                break
        db.commit()
    finally:
        db.close()

    if not archived_ids:
        return 0, 0

    payload["messages"] = [m for m, keep in zip(messages, keep_mask) if keep]
    if archived_ids:
        from spillover.metrics.registry import (
            episodes_archived_total,
            overflow_triggered_total,
        )
        overflow_triggered_total.labels(project=project_id).inc()
        episodes_archived_total.labels(
            project=project_id, type="pre_forward"
        ).inc(len(archived_ids))
    return len(archived_ids), tokens_freed


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
            density=len(t.tool_calls),  # cheap proxy for semantic density v1
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
        from spillover.metrics.registry import (
            compaction_detected_total,
            episodes_archived_total,
            facet_queue_depth,
            overflow_triggered_total,
            request_duration,
            requests_total,
            retriever_hits_total,
        )

        # Passive mode (SPILLOVER_PASSIVE=1): proxy NEVER mutates the outbound
        # request. No LTM injection, no rescue payload modification, no
        # compaction interception. Upstream sees exactly what Claude Code
        # would have sent direct, so the provider's quota / rate-limit
        # behavior is identical to running without the proxy. The proxy
        # still observes the conversation and archives evicted turns on the
        # response path, so memory continues to accumulate.
        import os as _os
        passive = _os.environ.get("SPILLOVER_PASSIVE", "0") not in ("", "0", "false", "False")

        if (not passive) and provider == "anthropic" and should_intercept_request(payload):
            log.info("intercept compact project=%s", project_id)
            return JSONResponse(make_intercept_response(payload), status_code=200)

        loop = asyncio.get_running_loop()

        if not passive:
            # Retrieval pass: inject LTM into the payload before forwarding.
            try:
                conv = adapter.parse(payload)
                with request_duration.labels(phase="retrieve").time():
                    ltm_text = await _run_sync(
                        loop, _retrieve_ltm_block, config, project_id, conv, payload
                    )
                if ltm_text:
                    retriever_hits_total.labels(
                        project=project_id, source="hybrid"
                    ).inc()
                adapter.inject_ltm(payload, ltm_text)
            except Exception:
                log.exception(
                    "retriever failed project=%s; proceeding without LTM", project_id
                )

            # Detect compaction + rescue (Anthropic only), offloaded to executor.
            # Rescue MUTATES the outbound payload, so it's gated by passive mode.
            rescue_ids: list[str] = []
            if provider == "anthropic":
                rescued_list, rescue_ids = await _run_sync(
                    loop, _detect_and_rescue, config, project_id, payload.get("messages") or []
                )
                if rescued_list:
                    compaction_detected_total.labels(project=project_id).inc(
                        len(rescued_list)
                    )
                if rescue_ids:
                    episodes_archived_total.labels(
                        project=project_id, type="rescued"
                    ).inc(len(rescue_ids))
                    _enqueue_facets(app, project_id, rescue_ids, config)
                    facet_queue_depth.set(app.state.facet_queue.qsize())
        else:
            rescue_ids = []
            # Still record seen_turns so future non-passive sessions have
            # something to compare against. This does NOT mutate the payload.
            if provider == "anthropic":
                try:
                    from spillover.counter_compact.detection import record_seen_turns
                    from spillover.storage.sqlite import open_project_db
                    db = open_project_db(config.db_root, project_id)
                    try:
                        record_seen_turns(db, project_id, payload.get("messages") or [])
                        db.commit()
                    finally:
                        db.close()
                except Exception:
                    log.exception("passive seen_turns record failed project=%s", project_id)

        # Pre-forward eviction: keep outbound payload <= operational ceiling.
        # Spillover should be size-NEUTRAL with respect to the upstream:
        # Anthropic must never see a payload larger than what raw Claude Code
        # would have generated. Inflation here = TPM cap breach = 429 storm.
        if not passive:
            try:
                n_pre, tok_pre = await _run_sync(
                    loop, _evict_inbound_to_ceiling, config, project_id, payload, adapter
                )
                if n_pre > 0:
                    log.info(
                        "pre_forward_evict project=%s turns=%d tokens=%d",
                        project_id, n_pre, tok_pre,
                    )
            except Exception:
                log.exception("pre-forward eviction failed project=%s", project_id)

        forwarded_body = json.dumps(payload).encode("utf-8")
        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in {"host", "content-length", "x-project"}
        }
        is_stream = bool(payload.get("stream"))

        if not is_stream:
            with request_duration.labels(phase="upstream").time():
                # Pure passthrough -- NO proxy-side retry. Client (Claude Code)
                # already retries with its own backoff; doubling that here just
                # multiplies 429 amplification against the user's OAuth quota.
                r = await app.state.http_client.post(
                    upstream_url, headers=fwd_headers, content=forwarded_body
                )
            fallback_used: str | None = None
            primary_model = payload.get("model", "")
            fb_model = (
                config.fallback_model_anthropic
                if provider == "anthropic"
                else config.fallback_model_openai
            )
            # Fallback is opt-in (empty by default). When configured AND the
            # upstream returned a retryable status, swap models exactly once
            # (no retry chain on the fallback either).
            if fb_model and should_fallback(r, fb_model, primary_model):
                fb_resp, used = await attempt_fallback_unary(
                    app.state.http_client,
                    upstream_url,
                    fwd_headers,
                    forwarded_body,
                    primary_model,
                    fb_model,
                )
                if used and fb_resp is not None and fb_resp.status_code < 400:
                    r = fb_resp
                    fallback_used = fb_model
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
                overflow_triggered_total.labels(project=project_id).inc()
                episodes_archived_total.labels(
                    project=project_id, type="evicted"
                ).inc(len(archived_ids))
                _enqueue_facets(app, project_id, archived_ids, config)
                facet_queue_depth.set(app.state.facet_queue.qsize())
            requests_total.labels(
                project=project_id,
                provider=provider,
                status=str(r.status_code),
            ).inc()
            resp_headers = {"content-type": "application/json"}
            if fallback_used:
                resp_headers["x-spillover-fallback-model"] = fallback_used
            return JSONResponse(
                content=json.loads(resp_bytes),
                status_code=r.status_code,
                headers=resp_headers,
            )

        # Streaming branch (incremental SSE rewrite)
        from spillover.counter_compact.sse_rewrite import has_usage_marker, rewrite_sse_body

        rewrite_enabled = _stream_rewrite_enabled(config)

        # Pure passthrough on streaming. No proxy-side retry -- Claude Code
        # already retries with its own backoff. Doubling retries amplifies
        # 429 against the user's OAuth quota (proxy + client = N x M bursts).
        def _build_stream_request():
            return app.state.http_client.build_request(
                "POST", upstream_url, headers=fwd_headers, content=forwarded_body
            )

        upstream = await app.state.http_client.send(_build_stream_request(), stream=True)
        stream_fallback_used: str | None = None
        stream_primary_model = payload.get("model", "")
        stream_fb_model = (
            config.fallback_model_anthropic
            if provider == "anthropic"
            else config.fallback_model_openai
        )
        # Fallback is opt-in (empty by default) and ONE-SHOT only.
        if stream_fb_model and should_fallback(upstream, stream_fb_model, stream_primary_model):
            await upstream.aclose()

            def _build_fb_request(new_body: bytes):
                fb_hdrs = {**fwd_headers, "content-length": str(len(new_body))}
                return app.state.http_client.build_request(
                    "POST", upstream_url, headers=fb_hdrs, content=new_body
                )

            fb_upstream, fb_used = await attempt_fallback_stream(
                app.state.http_client,
                _build_fb_request,
                forwarded_body,
                stream_primary_model,
                stream_fb_model,
            )
            if fb_used and fb_upstream is not None and fb_upstream.status_code < 400:
                upstream = fb_upstream
                stream_fallback_used = stream_fb_model
            else:
                # fallback failed too -- single fresh open to surface 429 honestly
                upstream = await app.state.http_client.send(
                    _build_stream_request(), stream=True
                )
        sink: list[bytes] = []

        async def proxy_stream():
            archived_ids_s: list[str] = []
            tokens_archived_s = 0
            tail_buffer = b""
            if stream_fallback_used:
                yield make_sse_banner(stream_primary_model, stream_fallback_used)
            try:
                async for chunk in upstream.aiter_bytes():
                    sink.append(chunk)
                    if rewrite_enabled and has_usage_marker(chunk):
                        # Buffer this chunk so we can rewrite before yielding
                        tail_buffer += chunk
                        continue
                    yield chunk
            finally:
                await upstream.aclose()
                if upstream.status_code == 200:
                    usage = adapter.extract_usage_sse(sink)
                    if usage is not None:
                        assistant_text = adapter.extract_assistant_text_sse(sink)
                        _evict_loop = asyncio.get_event_loop()
                        archived_ids_s, tokens_archived_s = await _run_sync(
                            _evict_loop, _maybe_evict,
                            config, project_id, payload, assistant_text, usage, adapter,
                        )
                if rewrite_enabled and tail_buffer and tokens_archived_s > 0:
                    yield rewrite_sse_body(tail_buffer, tokens_archived_s)
                elif tail_buffer:
                    yield tail_buffer
                if upstream.status_code >= 400:
                    log.warning(
                        "upstream non-2xx (stream) status=%d project=%s",
                        upstream.status_code,
                        project_id,
                    )
                if archived_ids_s:
                    overflow_triggered_total.labels(project=project_id).inc()
                    episodes_archived_total.labels(
                        project=project_id, type="evicted"
                    ).inc(len(archived_ids_s))
                    _enqueue_facets(app, project_id, archived_ids_s, config)
                    facet_queue_depth.set(app.state.facet_queue.qsize())
                requests_total.labels(
                    project=project_id,
                    provider=provider,
                    status=str(upstream.status_code),
                ).inc()

        stream_resp_headers = {}
        if stream_fallback_used:
            stream_resp_headers["x-spillover-fallback-model"] = stream_fallback_used
        return StreamingResponse(
            proxy_stream(),
            media_type="text/event-stream",
            status_code=upstream.status_code,
            headers=stream_resp_headers,
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

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "1.2.0"}

    @app.get("/")
    async def root():
        return {
            "name": "spillover",
            "version": "1.2.0",
            "endpoints": ["/v1/messages", "/v1/chat/completions", "/metrics", "/health"],
        }

    return app

from __future__ import annotations

import asyncio
import functools
import json
import os
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
    """Retrieval block budget = SPILLOVER_RETRIEVAL_PCT * remaining_context.

    Default 15.38% matches the user's design diagram. Computed against the
    estimated remaining context (input - archived_target), so smaller inputs
    get proportionally smaller retrieval blocks.

    Hard absolute cap (SPILLOVER_LTM_MAX_TOKENS, default 20000) protects
    against runaway sizes when input is huge.
    """
    import os
    messages = payload.get("messages") or []
    system = payload.get("system")
    system_tokens = count_tokens(system) if system else 0
    total = system_tokens + sum(count_tokens(m.get("content")) for m in messages)
    try:
        archive_pct = float(os.environ.get("SPILLOVER_ARCHIVE_PCT", "0.2667"))
    except ValueError:
        archive_pct = 0.2667
    try:
        retrieval_pct = float(os.environ.get("SPILLOVER_RETRIEVAL_PCT", "0.1538"))
    except ValueError:
        retrieval_pct = 0.1538
    archive_pct = max(0.0, min(0.9, archive_pct))
    retrieval_pct = max(0.0, min(0.9, retrieval_pct))
    remaining_context = int(total * (1.0 - archive_pct))
    pct_budget = int(remaining_context * retrieval_pct)
    try:
        absolute_cap = int(os.environ.get("SPILLOVER_LTM_MAX_TOKENS", "20000"))
    except ValueError:
        absolute_cap = 20000
    try:
        min_budget = int(os.environ.get("SPILLOVER_LTM_MIN_TOKENS", "500"))
    except ValueError:
        min_budget = 500
    return max(min_budget, min(pct_budget, absolute_cap))


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
    retrieval_reserve: int = 0,
) -> tuple[int, int]:
    """Trim payload BEFORE forwarding so size stays <= operational_ceiling.

    `retrieval_reserve` reserves headroom for an LTM block that will be
    injected AFTER eviction. Eviction targets `ceiling - retrieval_reserve`
    so that the final outbound payload (surviving turns + retrieval block)
    fits within the ceiling. This is the "size-neutral" contract: Anthropic
    never sees more bytes than the user's CC client would have sent direct.

    Operates only on Anthropic-shape messages (role/content list). Removes
    oldest non-recent middle turns until total <= effective_ceiling.
    Archives every removed turn into the project store so retrieval can
    recall them later. Returns (turns_removed, tokens_freed).

    System block + first 2 turns (anchor) + last `recent_buffer` turns are
    preserved. Tool_use / tool_result blocks are skipped to keep Anthropic's
    pairing invariant intact.
    """
    messages = payload.get("messages") or []
    if not messages:
        return 0, 0

    system = payload.get("system")
    system_tokens = count_tokens(system) if system else 0
    turn_tokens = [count_tokens(m.get("content")) for m in messages]
    total = system_tokens + sum(turn_tokens)
    import os as _os
    # Live-read ceiling each request: lets `spillover ceiling N` take effect
    # without restarting the daemon. The CLI writes ~/.spillover/runtime.env
    # which we re-read on every eviction call.
    _runtime = config.db_root / "runtime.env"
    if _runtime.exists():
        try:
            for line in _runtime.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    _os.environ[k.strip()] = v.strip()
        except Exception:
            pass
    try:
        live_ceiling = int(_os.environ.get(
            "SPILLOVER_OPERATIONAL_CEILING_TOKENS",
            str(config.operational_ceiling_tokens),
        ))
    except ValueError:
        live_ceiling = config.operational_ceiling_tokens

    # Percentual de archive: SEMPRE corta essa fracao do input (default 26.67%)
    # mesmo quando input ja esta abaixo do ceiling. Implementa a regra fixa
    # do diagrama: input -> archive%(input) -> arquiva -> sobra (100-archive)%.
    try:
        archive_pct = float(_os.environ.get("SPILLOVER_ARCHIVE_PCT", "0.2667"))
    except ValueError:
        archive_pct = 0.2667
    archive_pct = max(0.0, min(0.9, archive_pct))

    # Tiny inputs (<5k tokens) don't carry useful old context to evict.
    # Apply only the ceiling rule there.
    try:
        min_total_for_pct = int(_os.environ.get("SPILLOVER_PCT_MIN_TOTAL_TOKENS", "5000"))
    except ValueError:
        min_total_for_pct = 5000
    pct_target_archive = int(total * archive_pct) if total >= min_total_for_pct else 0
    ceiling_target_archive = max(0, total - (live_ceiling - retrieval_reserve))
    # Target eviction tokens = max(pct rule, ceiling rule)
    target_to_evict = max(pct_target_archive, ceiling_target_archive)
    if target_to_evict <= 0:
        return 0, 0
    ceiling = max(0, total - target_to_evict)

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

        # Debug dump. Two levels:
        # - SPILLOVER_DUMP_REQUESTS=1: log inbound size summary to stderr.
        # - SPILLOVER_DUMP_DIR=/path: save inbound + outbound payload JSON
        #   files for every request. Use to inspect exactly what spillover
        #   transformed.
        import os as _os_dump
        if _os_dump.environ.get("SPILLOVER_DUMP_REQUESTS") not in (None, "", "0", "false"):
            try:
                _msgs = payload.get("messages") or []
                _sys = payload.get("system")
                _sys_tokens = count_tokens(_sys) if _sys else 0
                _msg_tokens = sum(count_tokens(m.get("content")) for m in _msgs)
                _model = payload.get("model", "?")
                _log.warning(
                    "INBOUND project=%s model=%s msgs=%d sys_tokens=%d msg_tokens=%d total=%d body_bytes=%d",
                    project_id, _model, len(_msgs), _sys_tokens,
                    _msg_tokens, _sys_tokens + _msg_tokens, len(body),
                )
            except Exception:
                _log.exception("INBOUND dump failed")
        _dump_dir = _os_dump.environ.get("SPILLOVER_DUMP_DIR")
        _dump_id = None
        if _dump_dir:
            try:
                from pathlib import Path as _Path
                import uuid as _uuid
                _dump_path = _Path(_dump_dir)
                _dump_path.mkdir(parents=True, exist_ok=True)
                _dump_id = f"{int(time.time())}-{_uuid.uuid4().hex[:8]}"
                _msgs = payload.get("messages") or []
                _sys = payload.get("system")
                _sys_tokens = count_tokens(_sys) if _sys else 0
                _msg_tokens = sum(count_tokens(m.get("content")) for m in _msgs)
                inbound_meta = {
                    "kind": "inbound",
                    "id": _dump_id,
                    "ts": int(time.time() * 1000),
                    "project_id": project_id,
                    "model": payload.get("model"),
                    "stream": bool(payload.get("stream")),
                    "msgs_count": len(_msgs),
                    "sys_tokens": _sys_tokens,
                    "msg_tokens": _msg_tokens,
                    "total_tokens": _sys_tokens + _msg_tokens,
                    "body_bytes": len(body),
                }
                (_dump_path / f"{_dump_id}.inbound.json").write_text(
                    json.dumps({"_meta": inbound_meta, "payload": payload}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                _log.exception("INBOUND payload dump failed")

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
            # Order matters: rescue can ADD turns, eviction RESERVES space for
            # retrieval, retrieval INJECTS within the reserve. Final payload
            # always <= operational_ceiling_tokens (size-neutral contract).

            # 1) Rescue: re-attach assistant turns the CLI's compaction may have
            # dropped. Mutates payload. Bounded by detect_compaction caps.
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

            # 2) Compute retrieval reserve so eviction leaves room for the LTM
            # block we're about to inject. Default 5k absolute cap.
            retrieval_reserve = _ltm_budget_for(config, payload)

            # 3) Pre-forward eviction: trim middle to fit under
            # (ceiling - retrieval_reserve). Archives evicted turns.
            pre_forward_tokens_freed = 0
            try:
                n_pre, tok_pre = await _run_sync(
                    loop, _evict_inbound_to_ceiling,
                    config, project_id, payload, adapter, retrieval_reserve,
                )
                if n_pre > 0:
                    pre_forward_tokens_freed = tok_pre
                    log.info(
                        "pre_forward_evict project=%s turns=%d tokens=%d reserve=%d",
                        project_id, n_pre, tok_pre, retrieval_reserve,
                    )
            except Exception:
                log.exception("pre-forward eviction failed project=%s", project_id)

            # 4) LTM retrieval + injection. Block size <= retrieval_reserve.
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

        forwarded_body = json.dumps(payload).encode("utf-8")
        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in {"host", "content-length", "x-project"}
        }
        is_stream = bool(payload.get("stream"))

        # Dump outbound (post-transform) payload for inspection.
        if _dump_dir and _dump_id:
            try:
                from pathlib import Path as _Path2
                _dump_path2 = _Path2(_dump_dir)
                _msgs2 = payload.get("messages") or []
                _sys2 = payload.get("system")
                _sys_tokens2 = count_tokens(_sys2) if _sys2 else 0
                _msg_tokens2 = sum(count_tokens(m.get("content")) for m in _msgs2)
                outbound_meta = {
                    "kind": "outbound",
                    "id": _dump_id,
                    "ts": int(time.time() * 1000),
                    "project_id": project_id,
                    "model": payload.get("model"),
                    "msgs_count": len(_msgs2),
                    "sys_tokens": _sys_tokens2,
                    "msg_tokens": _msg_tokens2,
                    "total_tokens": _sys_tokens2 + _msg_tokens2,
                    "body_bytes": len(forwarded_body),
                    "pre_forward_tokens_freed": pre_forward_tokens_freed if not passive else 0,
                    "rescue_count": len(rescue_ids) if not passive else 0,
                }
                (_dump_path2 / f"{_dump_id}.outbound.json").write_text(
                    json.dumps({"_meta": outbound_meta, "payload": payload}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                _log.exception("OUTBOUND payload dump failed")

        # Preserve query string from the inbound request when forwarding.
        # CC sends e.g. `/v1/messages?beta=true` -- stripping the query causes
        # the upstream to skip beta-gated behaviors silently.
        upstream_url_with_q = upstream_url
        if request.url.query:
            sep = "&" if "?" in upstream_url_with_q else "?"
            upstream_url_with_q = f"{upstream_url_with_q}{sep}{request.url.query}"

        if not is_stream:
            with request_duration.labels(phase="upstream").time():
                # Pure passthrough -- NO proxy-side retry. Client (Claude Code)
                # already retries with its own backoff; doubling that here just
                # multiplies 429 amplification against the user's OAuth quota.
                r = await app.state.http_client.post(
                    upstream_url_with_q, headers=fwd_headers, content=forwarded_body
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
                    upstream_url_with_q,
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
                    # Rewrite always subtracts BOTH pre-forward eviction tokens
                    # AND post-response archived tokens. Also fires unconditionally
                    # when SPILLOVER_REPORTED_INPUT_CAP is set so the cap can clamp
                    # input_tokens reported to the client (push back CC's wall).
                    total_subtract = tokens_archived + pre_forward_tokens_freed
                    _cap = os.environ.get("SPILLOVER_REPORTED_INPUT_CAP", "0")
                    if total_subtract > 0 or (_cap and _cap != "0"):
                        try:
                            resp_json = json.loads(resp_bytes)
                        except json.JSONDecodeError:
                            resp_json = {}
                        resp_json = rewrite_response_json(resp_json, total_subtract)
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
        # Use the with-query URL so beta query params reach upstream.
        def _build_stream_request():
            return app.state.http_client.build_request(
                "POST", upstream_url_with_q, headers=fwd_headers, content=forwarded_body
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
                    "POST", upstream_url_with_q, headers=fb_hdrs, content=new_body
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
                # Include pre-forward eviction tokens in the SSE usage rewrite
                # so CC's local context tracker reflects the full trim work.
                total_subtract_s = tokens_archived_s + pre_forward_tokens_freed
                _cap_s = os.environ.get("SPILLOVER_REPORTED_INPUT_CAP", "0")
                _cap_active_s = bool(_cap_s) and _cap_s != "0"
                if rewrite_enabled and tail_buffer and (total_subtract_s > 0 or _cap_active_s):
                    yield rewrite_sse_body(tail_buffer, total_subtract_s)
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

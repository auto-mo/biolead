"""FastAPI app and the SSE endpoint.

Thin on purpose. The pipeline is already an async generator of events, so streaming is a
serialisation concern and nothing more. The eval harness drains the same generator, which
means the demo and the thing being tested cannot diverge.

SSE rather than WebSockets: one-way server to client, no handshake complexity, traverses
proxies cleanly, and the browser's EventSource handles reconnection. It is also why the
deployment diagram specifies an ALB rather than API Gateway.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Body, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.clients.clinicaltrials import ClinicalTrialsClient
from app.clients.hpa import HpaClient
from app.clients.open_targets import OpenTargetsClient
from app.core.cache import CACHE
from app.core.gate import (
    COOKIE, SPEND_CAP, Gate, IPRateLimiter, client_ip,
)
from app.core.config import Config, load_config
from app.core.limits import REGISTRY
from app.services import presets
from app.services.adjudicate import build_adjudicator
from app.services.batch import parse_gene_list, run_batch
from app.services.pipeline import run_assessment

HEARTBEAT_SECONDS = 15

# Which preset gets warmed at startup. The demo opens on single-gene mode, so warming runs
# in the background and never blocks the server coming up.
WARM_PRESET = "opentargets_aga_top50"

_state: dict[str, object] = {}


async def _warm(cfg: Config) -> None:
    """Pre-populate the cache for the demo preset.

    Every network call the preset makes is cached on its inputs, so replaying the run after
    this finishes is served from memory. Failure is logged into `warm` state and nothing
    else: a cold cache means the run fetches live, which is slower and equally correct.
    """
    preset = presets.get(WARM_PRESET)
    if preset is None or not preset.symbols:
        _state["warm"] = {"ok": False, "note": f"preset {WARM_PRESET} not loaded"}
        return
    try:
        started = asyncio.get_running_loop().time()
        async for _ in run_batch(
            preset.symbols, preset.condition,
            config=cfg, ot=_state["ot"], ct=_state["ct"], hpa=_state["hpa"],  # type: ignore[arg-type]
            input_fields=preset.input_fields, source=preset.citation,
        ):
            pass
        _state["warm"] = {
            "ok": True, "preset": WARM_PRESET, "genes": len(preset.symbols),
            "seconds": round(asyncio.get_running_loop().time() - started, 2),
        }
    except Exception as exc:  # never fatal; the live path is the fallback
        _state["warm"] = {"ok": False, "note": f"{type(exc).__name__}: {exc}"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # load_config(strict=True) raises if any tier 1b fact is unsourced or unverified.
    # Failing at startup: an untraceable fact at the top of the evidence
    # hierarchy should stop the process, not produce a warning nobody reads.
    cfg = load_config(strict=True)
    _state["config"] = cfg
    _state["ot"] = await OpenTargetsClient().__aenter__()
    _state["ct"] = await ClinicalTrialsClient().__aenter__()
    _state["hpa"] = await HpaClient().__aenter__()
    _state["data_version"] = await _state["ot"].data_version()  # type: ignore[union-attr]
    # Real adjudicator when a key is present, deterministic stub otherwise. The stub is
    # flagged to the client so the UI can say the second read is not a model opinion.
    _state["adjudicator"] = build_adjudicator()
    _state["warm"] = {"ok": None, "note": "running"}
    warm_task = asyncio.create_task(_warm(cfg))
    yield
    warm_task.cancel()
    await _state["ot"].__aexit__(None, None, None)  # type: ignore[union-attr]
    await _state["ct"].__aexit__(None, None, None)  # type: ignore[union-attr]
    await _state["hpa"].__aexit__(None, None, None)  # type: ignore[union-attr]


app = FastAPI(title="BioLead", version="0.1.0", lifespan=lifespan)

# ALLOWED_ORIGINS lets the hosted deployment name its own origin without a code change.
# Credentials are allowed because the gate token is an HttpOnly cookie, and a wildcard
# origin is invalid alongside credentials, so the list is always explicit.
_origins = [o.strip() for o in os.environ.get(
    "BIOLEAD_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------------------
# The gate. Deployment concern; see the operator notes kept outside this repository.
# --------------------------------------------------------------------------------------

GATE = Gate.from_env()
IP_LIMITER = IPRateLimiter(per_minute=int(os.environ.get("BIOLEAD_IP_RATE", "30") or 30))
SPEND = SPEND_CAP

# Reachable without a token. /api/health is how a load balancer decides the task is alive.
OPEN_PATHS = {"/api/gate", "/api/health"}


@app.middleware("http")
async def gate_and_rate_limit(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in OPEN_PATHS:
        return await call_next(request)

    if GATE.enabled and GATE.verify(request.cookies.get(COOKIE)) is None:
        return JSONResponse({"error": "gate", "detail": "access code required"},
                            status_code=401)

    ip = client_ip(request)
    ok, retry_after = IP_LIMITER.allow(ip)
    if not ok:
        IP_LIMITER.prune()
        return JSONResponse(
            {"error": "rate_limited", "retry_after": retry_after},
            status_code=429, headers={"Retry-After": str(int(retry_after) + 1)})
    return await call_next(request)


@app.post("/api/gate")
async def gate(request: Request, body: dict = Body(...)) -> JSONResponse:
    """Exchange a shared code for a signed cookie. Not authentication."""
    if not GATE.enabled:
        return JSONResponse({"ok": True, "label": None, "gate": "disabled"})

    ip = client_ip(request)
    ok, retry_after = IP_LIMITER.allow(f"gate:{ip}")
    if not ok:
        return JSONResponse({"error": "rate_limited", "retry_after": retry_after},
                            status_code=429)

    label = GATE.check_code(str(body.get("code", "")))
    if label is None:
        print(f"[gate] refused ip={ip}", flush=True)
        return JSONResponse({"error": "bad_code"}, status_code=401)

    GATE.record(label, ip, request.headers.get("user-agent", ""))
    resp = JSONResponse({"ok": True, "label": label})
    resp.set_cookie(
        COOKIE, GATE.issue(label),
        max_age=GATE.ttl_seconds, httponly=True, samesite="lax",
        secure=os.environ.get("BIOLEAD_COOKIE_SECURE", "1") != "0",
    )
    return resp


@app.get("/api/gate")
async def gate_status(request: Request) -> dict:
    """Whether the gate is on, and whether this caller is already through it."""
    return {
        "enabled": GATE.enabled,
        "label": GATE.verify(request.cookies.get(COOKIE)) if GATE.enabled else None,
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.get("/api/health")
async def health() -> dict:
    cfg: Config = _state["config"]  # type: ignore[assignment]
    return {
        "status": "ok",
        "data_version": _state.get("data_version"),
        "proxies": len(cfg.proxies),
        "clinical_facts": len(cfg.clinical_facts),
        "rejected_facts": [r.fact_id for r in cfg.rejected_facts],
        "adjudicator_is_stub": getattr(_state.get("adjudicator"), "is_stub", True),
        "cache": CACHE.as_dict(),
        "limiters": REGISTRY.snapshot(),
        "warm": _state.get("warm"),
    }


@app.get("/api/proxies")
async def proxies() -> list[dict]:
    """The curated borrow table, served so the UI can show it and a reviewer can read it."""
    cfg: Config = _state["config"]  # type: ignore[assignment]
    return [p.model_dump() for p in cfg.proxies]


@app.get("/api/genes")
async def genes(
    q: str = Query(..., min_length=1, max_length=32),
    limit: int = Query(8, ge=1, le=25),
) -> dict:
    """Ranked gene candidates for the symbol box.

    This exists because of step-zero finding 2. `search("AR")` returns AR, FDXR, AREG,
    AKR1B1, ARX in that order, and the pipeline refuses to pick between them: an inexact
    symbol abstains. Showing the ranked list moves that decision to the person who knows
    which gene they meant.

    Failures return an empty list with `ok: false` rather than an HTTP error, because the
    box has to keep accepting typed input when the source is unreachable.
    """
    ot = _state.get("ot")
    if ot is None:
        return {"ok": False, "hits": []}
    try:
        hits = await asyncio.wait_for(ot.search_targets(q, limit=limit), timeout=4.0)
    except Exception:
        return {"ok": False, "hits": []}
    return {
        "ok": True,
        "hits": [
            {
                "symbol": h.symbol,
                "name": h.approved_name,
                "ensembl_id": h.ensembl_id,
                "exact": h.symbol.upper() == q.strip().upper(),
            }
            for h in hits
        ],
    }


@app.get("/api/presets")
async def preset_list() -> dict:
    """Published gene lists shipped with the tool, and whether the demo one is warm."""
    return {"presets": presets.listing(), "warm": _state.get("warm")}


@app.get("/api/endpoint_evidence")
async def endpoint_evidence(gene: str = Query(..., min_length=1, max_length=32)) -> dict:
    """Which endpoints have evidence for this gene, for ranking the endpoint menu.

    RANK, NEVER FILTER. Every endpoint comes back, each marked with what was found. An
    endpoint with nothing is often the interesting answer: FLG against cosmetic dry skin
    returns almost nothing from the borrow and is still the case worth showing, because the
    borrow is restricted to FLG and the reader needs to see the restriction do its work.

    One call to the source, not one per endpoint: the borrow rows' disease ids are resolved
    once each and cached, then asked as a single `associatedDiseases(Bs: [...])`.
    """
    cfg: Config = _state["config"]  # type: ignore[assignment]
    ot: OpenTargetsClient = _state["ot"]  # type: ignore[assignment]

    try:
        target = await asyncio.wait_for(ot.resolve_target(gene), timeout=6.0)
    except Exception:
        return {"ok": False, "gene": gene, "endpoints": []}
    if target is None:
        return {"ok": False, "gene": gene, "resolved": None, "endpoints": []}

    # Resolve each borrow's search term to a disease id. Cached, so this is one call per
    # distinct term on the first request of a session and zero thereafter.
    ids: dict[str, str] = {}
    for row in cfg.proxies:
        if not row.search_term:
            continue
        try:
            hit = await asyncio.wait_for(ot.resolve_disease(row.search_term), timeout=6.0)
        except Exception:
            continue
        if hit is not None:
            ids[row.endpoint] = hit.id

    try:
        found = await asyncio.wait_for(
            ot.associated_disease_ids(target.ensembl_id, sorted(set(ids.values()))),
            timeout=10.0,
        )
    except Exception:
        return {"ok": False, "gene": target.symbol, "resolved": target.ensembl_id,
                "endpoints": []}

    out = []
    for row in cfg.proxies:
        disease_id = ids.get(row.endpoint)
        scores = found.get(disease_id or "", {}) if disease_id else {}
        # `has_evidence` is about tier 1 or tier 2 specifically, because literature alone
        # is the passenger fingerprint and marking it as evidence would invert the meaning.
        directional = {
            k: v for k, v in scores.items()
            if k in ("genetic_association", "genetic_literature", "clinical", "known_drug")
            and v >= 0.05
        }
        out.append({
            "endpoint": row.endpoint,
            "state": (
                "no_disease_term" if disease_id is None
                else "tier_1_or_2" if directional
                else "weak_only" if scores
                else "checked_and_empty"
            ),
            "scores": {k: round(v, 3) for k, v in sorted(scores.items(), key=lambda kv: -kv[1])},
            "top_score": round(max(scores.values()), 3) if scores else 0.0,
        })

    return {"ok": True, "gene": target.symbol, "resolved": target.ensembl_id, "endpoints": out}


@app.get("/api/batch")
async def batch(
    condition: str = Query(..., min_length=1, max_length=128),
    preset: str | None = Query(None, description="id from /api/presets"),
    genes: str | None = Query(None, description="pasted list; parsed server side"),
) -> StreamingResponse:
    """Stream a batch triage run.

    GET with the list in the query string rather than POST, because EventSource cannot send
    a body and the trace has to stream. Long pastes go to the POST parse endpoint first and
    come back as a token; that is not built, so the practical ceiling here is URL length.
    """
    cfg: Config = _state["config"]  # type: ignore[assignment]

    symbols: list[str] = []
    fields: dict[str, dict[str, str]] = {}
    source: str | None = None
    parse_report: dict = {}

    if preset:
        p = presets.get(preset)
        if p is None:
            return _error_stream(f"unknown preset {preset!r}")
        symbols, fields, source = p.symbols, p.input_fields, p.citation
        parse_report = {"preset": p.id, "count": len(symbols), "citation": p.citation,
                        "url": p.url}
    elif genes:
        parsed = parse_gene_list(genes)
        symbols, fields = parsed.symbols, parsed.input_fields
        source = "pasted list"
        parse_report = {
            "detected": parsed.detected, "count": len(parsed.symbols),
            "rejected": parsed.rejected[:50], "rejected_count": len(parsed.rejected),
            "duplicates_dropped": len(parsed.duplicates),
            "truncated_at": parsed.truncated_at,
        }
    if not symbols:
        return _error_stream("no usable gene symbols in the input")

    async def stream() -> AsyncIterator[str]:
        yield _sse("parse", parse_report)
        try:
            async for ev in run_batch(
                symbols, condition, config=cfg,
                ot=_state["ot"], ct=_state["ct"], hpa=_state["hpa"],  # type: ignore[arg-type]
                input_fields=fields, source=source,
            ):
                yield _sse(ev.event, ev.data)
        except Exception as exc:
            yield _sse("error", {"error": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@app.post("/api/parse_genes")
async def parse_genes(body: dict = Body(...)) -> dict:
    """Parse a pasted or uploaded list without running it.

    Separate from the run so the interface can show what it understood before anything is
    fetched: how many symbols, what it rejected, what it deduplicated. A parser that
    silently drops lines would make the denominator of the headline number wrong.
    """
    parsed = parse_gene_list(str(body.get("text", "")))
    return {
        "symbols": parsed.symbols,
        "count": len(parsed.symbols),
        "detected": parsed.detected,
        "rejected": parsed.rejected[:100],
        "rejected_count": len(parsed.rejected),
        "duplicates_dropped": len(parsed.duplicates),
        "truncated_at": parsed.truncated_at,
    }


def _error_stream(message: str) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        yield _sse("error", {"error": message})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/assess")
async def assess(
    gene: str = Query(..., min_length=1, max_length=32),
    condition: str = Query(..., min_length=1, max_length=128),
    simulate_outage: str | None = Query(
        None,
        description=(
            "Force a source to fail, so graceful degradation is demonstrable rather than "
            "described. Values: open_targets | clinicaltrials"
        ),
    ),
) -> StreamingResponse:
    cfg: Config = _state["config"]  # type: ignore[assignment]
    ot = _state["ot"]
    ct = _state["ct"]
    hpa = _state["hpa"]

    # The demo runs from a pre-warmed cache with no network, so `could_not_check` would
    # otherwise never fire and abstention trigger 6 would be undemonstrable. This flag makes
    # the most interesting reasoning behaviour visible on stage.
    if simulate_outage == "clinicaltrials":
        ct = None
    ot_for_run = _OutageProxy(ot) if simulate_outage == "open_targets" else ot

    async def stream() -> AsyncIterator[str]:
        queue: asyncio.Queue = asyncio.Queue()

        async def produce() -> None:
            try:
                async for ev in run_assessment(
                    gene, condition, config=cfg, ot=ot_for_run, ct=ct, hpa=hpa,  # type: ignore[arg-type]
                    adjudicator=_state.get("adjudicator"),
                ):
                    await queue.put((ev.event, ev.data))
            except Exception as exc:  # surfaced, never swallowed
                await queue.put(("error", {"error": f"{type(exc).__name__}: {exc}"}))
            finally:
                await queue.put((None, None))

        task = asyncio.create_task(produce())
        try:
            while True:
                try:
                    name, data = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # Comment frame. Stops intermediary proxies closing a quiet connection.
                    yield ": heartbeat\n\n"
                    continue
                if name is None:
                    break
                yield _sse(name, data)
        finally:
            task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx must not buffer an event stream
        },
    )


class _OutageProxy:
    """Wraps the Open Targets client so evidence lookups fail while resolution still works.

    partial: the gene and condition still resolve, so the trace shows the
    pipeline getting most of the way and then losing the tier the verdict depended on.
    That is what makes the difference between `checked_and_empty` and `could_not_check`
    legible on stage.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def lookup_evidence(self, *_args, **_kwargs):
        from app.clients.open_targets import EvidenceLookup

        return EvidenceLookup(
            "COULD_NOT_CHECK",
            note="SIMULATED OUTAGE: the evidence source was unreachable for this run.",
        )

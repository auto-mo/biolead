"""Measure the real per-assessment API cost.

One assessment = exactly one Anthropic call (the adjudicator). Everything else in the pipeline
is Open Targets and ClinicalTrials.gov, which are free. So the cost of a search is the cost of
that single call, and the way to get it is to make it and read `usage`.

Output tokens include thinking, which is billed at the output rate and is on by default on
Sonnet 5. That is the part an estimate would miss.
"""
import asyncio, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from anthropic import AsyncAnthropic
from app.clients.clinicaltrials import ClinicalTrialsClient
from app.clients.open_targets import OpenTargetsClient
from app.core.config import load_config
from app.services.adjudicate import (
    MODEL, ABLATION_MODEL, SYSTEM, _NO_EFFORT, _load_env,
)

_load_env()
from app.services.pipeline import run_assessment

# $ per million tokens. Sonnet 5 is in its introductory window through 2026-08-31.
PRICING = {
    "claude-sonnet-5":  {"in": 2.00, "out": 10.00, "note": "intro thru 2026-08-31 (std 3/15)"},
    "claude-haiku-4-5": {"in": 1.00, "out":  5.00, "note": "standard"},
}

SCHEMA = {
    "type": "object",
    "properties": {
        "position": {"type": "string", "enum": ["UPSTREAM_DRIVER", "DOWNSTREAM", "INSUFFICIENT"]},
        "targetability": {"type": "string", "enum": ["ACTIONABLE", "NOT_ACTIONABLE", "UNKNOWN"]},
        "confidence": {"anyOf": [{"type": "string", "enum": ["HIGH", "MODERATE", "LOW"]},
                                 {"type": "null"}]},
        "reasoning": {"type": "string"},
        "cited_tiers": {"type": "array", "items": {"type": "string"}},
        "text_technical": {"type": "string"},
        "text_plain": {"type": "string"},
    },
    "required": ["position", "targetability", "reasoning", "cited_tiers",
                 "text_technical", "text_plain"],
    "additionalProperties": False,
}

# smallest, typical, largest packet in the benchmark set
CASES = [("AR", "rosacea"), ("FLG", "cosmetic dry skin"), ("AR", "hair thinning")]


async def packet_for(gene, cond, cfg, ot, ct):
    a = None
    async for ev in run_assessment(gene, cond, config=cfg, ot=ot, ct=ct, adjudicator=None):
        if ev.event == "assessment":
            a = ev.data
    return {
        "gene": a["gene"], "condition": a["condition_as_typed"], "mode": a["mode"],
        "proxy": a["proxy"], "tiers": a["tier_profile"]["tiers"],
        "checked_and_empty": a["tier_profile"]["checked_and_empty"],
        "could_not_check": a["tier_profile"]["could_not_check"],
    }


async def measure(client, model, packet):
    body = dict(
        model=model, max_tokens=8000, system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(packet, default=str)}],
        output_config={
            **({"effort": "high"} if not model.startswith(_NO_EFFORT) else {}),
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
    )
    r = await client.messages.create(**body)
    u = r.usage
    return u.input_tokens, u.output_tokens


async def main():
    cfg = load_config(strict=True)
    client = AsyncAnthropic()
    rows = []
    async with OpenTargetsClient() as ot, ClinicalTrialsClient() as ct:
        for gene, cond in CASES:
            pk = await packet_for(gene, cond, cfg, ot, ct)
            for model in (MODEL, ABLATION_MODEL):
                i, o = await measure(client, model, pk)
                p = PRICING[model]
                cost = i / 1e6 * p["in"] + o / 1e6 * p["out"]
                rows.append((f"{gene} x {cond}", model, i, o, cost))

    print(f"\n{'case':26} {'model':18} {'in':>7} {'out':>7} {'$ / call':>12}")
    print("-" * 76)
    for case, model, i, o, cost in rows:
        print(f"{case:26} {model:18} {i:>7} {o:>7} {cost:>12.5f}")

    for model in (MODEL, ABLATION_MODEL):
        sub = [r for r in rows if r[1] == model]
        avg = sum(r[4] for r in sub) / len(sub)
        ai = sum(r[2] for r in sub) / len(sub)
        ao = sum(r[3] for r in sub) / len(sub)
        p = PRICING[model]
        print(f"\n{model}  ({p['note']}, ${p['in']}/${p['out']} per MTok)")
        print(f"  mean {ai:.0f} in / {ao:.0f} out  ->  ${avg:.5f} per assessment")
        print(f"  1 search ${avg:.5f} | 5 screenshots ${avg*5:.4f} | "
              f"100 searches ${avg*100:.2f} | 1000 ${avg*1000:.2f}")


asyncio.run(main())

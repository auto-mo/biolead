"""Invoke the outcome subgraph directly, outside the pipeline.

The checkpoint the plan sets for this stage is *reproduces the three known outcomes, citing
the right NCT ids*. Three known cases, and the two cold cases §5.2 wrote down as predictions
before the build, so the harness records whether the prediction held rather than being fitted
to whatever happened.

Nothing here is wired into `pipeline.py`, and this file exists so a failure
now is a graph failure rather than an integration one.

Usage:
    python3 tools/run_outcome_graph.py                    # all cases, Sonnet
    python3 tools/run_outcome_graph.py --model claude-haiku-4-5
    python3 tools/run_outcome_graph.py --case SRD5A2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.clients.open_targets import OpenTargetsClient  # noqa: E402
from app.services.adjudicate import _load_env  # noqa: E402
from app.services.outcome_graph import DEFAULT_MODEL, run_outcome  # noqa: E402

OUT = ROOT / "docs" / "outcome-graph-run.json"

# gene, endpoint asked, disease actually queried, and what this case is for.
# The queried disease is the borrow where the endpoint is cosmetic, matching what the proxy
# table would hand the provider.
CASES = [
    # The three the curated file already knows. These test REDISCOVERY and nothing more:
    # passing proves the agent finds what a human already found.
    {"gene": "SRD5A2", "condition": "androgenetic alopecia",
     "disease": "androgenetic alopecia", "kind": "known",
     "expect_nct": "NCT01231607", "expect_read": "BENEFIT"},
    {"gene": "IL17A", "condition": "oily skin", "disease": "acne", "kind": "known",
     "expect_nct": "NCT02998671", "expect_read": "NO_BENEFIT"},
    {"gene": "PTGDR2", "condition": "hair thinning",
     "disease": "androgenetic alopecia", "kind": "known",
     "expect_nct": "NCT02781311", "expect_read": "NO_BENEFIT"},
    # The two cold cases, written down as predictions in §5.2 before any graph existed.
    {"gene": "RARG", "condition": "oily skin", "disease": "acne", "kind": "cold",
     "expect_nct": None, "expect_read": "BENEFIT"},
    {"gene": "IL4R", "condition": "atopic dermatitis",
     "disease": "atopic dermatitis", "kind": "cold",
     "expect_nct": None, "expect_read": "BENEFIT"},
    # The case the attribution cap exists for. Every readable trial reaches JAK2 only
    # through a drug naming four kinases, so the correct answer is that nothing here can
    # answer for JAK2.
    {"gene": "JAK2", "condition": "atopic dermatitis",
     "disease": "atopic dermatitis", "kind": "cap",
     "expect_nct": None, "expect_read": None},
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--case", default=None, help="run one gene only")
    args = ap.parse_args()
    _load_env()

    cases = [c for c in CASES if not args.case or c["gene"] == args.case]
    results = []

    async with OpenTargetsClient() as ot:
        http = httpx.AsyncClient(timeout=90)
        for c in cases:
            d = await ot.resolve_disease(c["disease"])
            if d is None:
                print(f"{c['gene']}: {c['disease']!r} did not resolve, skipped")
                continue
            t0 = time.monotonic()
            try:
                res = await run_outcome(
                    gene=c["gene"], condition=c["condition"],
                    disease_id=d.id, disease_name=d.name,
                    ot=ot, http=http, model_name=args.model)
            except Exception as exc:
                print(f"{c['gene']:8} FAILED {type(exc).__name__}: {exc}")
                results.append({**c, "error": f"{type(exc).__name__}: {exc}"})
                continue
            secs = time.monotonic() - t0

            cited_ok = (c["expect_nct"] in res.get("evidence_cited", [])
                        if c["expect_nct"] else None)
            read_ok = (res.get("read") == c["expect_read"]) if c["expect_read"] else None

            print(f"\n{'=' * 78}")
            print(f"{c['gene']} x {c['condition']}   [{c['kind']}]   {secs:.1f}s   "
                  f"{res.get('tool_calls_made')} tool calls   "
                  f"{res.get('input_tokens')} in / {res.get('output_tokens')} out")
            print(f"  state      {res.get('state')}   consensus {res.get('consensus')}   "
                  f"read {res.get('read')}")
            if res.get("borrowed"):
                print(f"  BORROWED   measured on {res.get('measured_disease')!r}, "
                      f"asked about {res.get('endpoint_asked')!r}")
            print(f"  split      {res.get('split')}")
            print(f"  reason     {(res.get('reason') or '')[:200]}")
            if res.get("minority"):
                print(f"  minority   " + "; ".join(
                    f"{m['nct_id']} {m['read']} (n={m.get('enrollment')})"
                    for m in res["minority"]))
            if res.get("excluded_by_attribution"):
                print(f"  EXCLUDED BY THE ATTRIBUTION CAP: "
                      f"{len(res['excluded_by_attribution'])} trial(s)")
                print(f"    {res['excluded_by_attribution'][0]['why'][:150]}")
            if res.get("rule_model_disagreements"):
                for dd in res["rule_model_disagreements"]:
                    print(f"  RULE vs MODEL {dd['nct_id']}: rule {dd['rule']}, "
                          f"model {dd['model']}")
            if res.get("rejections"):
                print(f"  validator rejected {len(res['rejections'])} submission(s)")
            if res.get("could_not_check"):
                print(f"  could_not_check {len(res['could_not_check'])}")
            if c["expect_nct"]:
                print(f"  CITED {c['expect_nct']}: {'yes' if cited_ok else 'NO'}")
            if c["expect_read"]:
                print(f"  read == {c['expect_read']}: {'yes' if read_ok else 'NO'}")

            results.append({**c, "seconds": round(secs, 1), "cited_ok": cited_ok,
                            "read_ok": read_ok, "result": res})

        await http.aclose()

    OUT.write_text(json.dumps({"model": args.model, "cases": results}, indent=1))
    print(f"\n{'=' * 78}")
    known = [r for r in results if r.get("kind") == "known" and "error" not in r]
    print(f"rediscovery: {sum(1 for r in known if r['cited_ok'] and r['read_ok'])}"
          f"/{len(known)} known cases cited the right trial and reached the right read")
    for r in results:
        if r.get("kind") == "cold" and "error" not in r:
            print(f"cold prediction {r['gene']}: predicted {r['expect_read']}, "
                  f"got {r['result'].get('read')}")
        if r.get("kind") == "cap" and "error" not in r:
            n = len(r["result"].get("excluded_by_attribution", []))
            print(f"attribution cap {r['gene']}: {n} trial(s) excluded, "
                  f"state {r['result'].get('state')}")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

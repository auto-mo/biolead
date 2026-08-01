"""Sweep every endpoint for genes that reach a placed verdict, and map the matrix.

For each endpoint in config/proxies.yaml: resolve its borrow, pull the targets Open Targets
already associates with that disease, run the full rule pipeline over them, and record
which matrix cell each one lands in.

Reachability is skipped (`hpa=None`). It is a gate after the verdict and takes no part in
matrix placement, and skipping it turns a 20 minute sweep into a 2 minute one.

Free: Open Targets and ClinicalTrials.gov cost nothing, and batch never adjudicates.

Usage:
    python3 tools/sweep_matrix.py [top_n_per_endpoint]
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.clients.clinicaltrials import ClinicalTrialsClient  # noqa: E402
from app.clients.open_targets import OpenTargetsClient  # noqa: E402
from app.core.config import load_config  # noqa: E402
from app.services.batch import run_batch  # noqa: E402

POSITIONS = ["UPSTREAM_DRIVER", "DOWNSTREAM", "INSUFFICIENT"]
OUTCOMES = ["ACTIONABLE", "UNKNOWN", "NOT_ACTIONABLE"]
OUT = ROOT / "docs" / "matrix-sweep.json"


async def targets_for(ot: OpenTargetsClient, disease_id: str, top_n: int) -> list[str]:
    """Symbols Open Targets associates with this disease, best first."""
    q = ("query A($d:String!,$s:Int!,$i:Int!){disease(efoId:$d){associatedTargets"
         "(page:{index:$i,size:$s}){count rows{target{approvedSymbol} score}}}}")
    out: list[str] = []
    page = 0
    while len(out) < top_n:
        r = await ot._gql(q, {"d": disease_id, "s": 500, "i": page})
        at = r["disease"]["associatedTargets"]
        rows = at["rows"]
        if not rows:
            break
        out.extend(x["target"]["approvedSymbol"] for x in rows)
        page += 1
        if page * 500 >= at["count"]:
            break
    return out[:top_n]


async def main() -> int:
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    cfg = load_config(strict=True)
    results: dict[str, dict] = {}

    async with OpenTargetsClient() as ot, ClinicalTrialsClient() as ct:
        for proxy in cfg.proxies:
            ep = proxy.endpoint
            term = proxy.search_term
            if not term:
                results[ep] = {"skipped": "no search term; the borrow was rejected",
                               "rating": proxy.rating, "rows": []}
                print(f"\n=== {ep}: no search term, skipped")
                continue

            disease = await ot.resolve_disease(term)
            if disease is None:
                results[ep] = {"skipped": f"{term!r} did not resolve", "rows": []}
                print(f"\n=== {ep}: {term!r} did not resolve")
                continue

            symbols = await targets_for(ot, disease.id, top_n)
            print(f"\n=== {ep}  borrow={proxy.borrowed_from or 'none'} "
                  f"rating={proxy.rating} refuse={proxy.refuse}")
            print(f"    {disease.name} ({disease.id}): {len(symbols)} targets to assess")

            res = None
            async for ev in run_batch(symbols, ep, config=cfg, ot=ot, ct=ct, hpa=None):
                if ev.event == "batch_result":
                    res = ev.data
            assert res is not None

            rows = []
            for r in res["rows"]:
                v = r["verdict"]
                rows.append({
                    "gene": r["gene"],
                    "position": v["position"] if v else "INSUFFICIENT",
                    "outcome": v["targetability"] if v else "UNKNOWN",
                    "confidence": v["confidence"] if v else None,
                    "rule_fired": v["rule_fired"] if v else None,
                    "evidence_class": r["evidence_class"],
                    "tiers": r["tier_counts"],
                    "scores": {k: round(x, 3) for k, x in r["datatype_scores"].items()},
                })
            results[ep] = {
                "borrowed_from": proxy.borrowed_from,
                "rating": proxy.rating,
                "refuse": proxy.refuse,
                "disease": disease.name,
                "disease_id": disease.id,
                "assessed": len(rows),
                "rows": rows,
            }
            placed = [r for r in rows if r["position"] != "INSUFFICIENT"]
            print(f"    placed {len(placed)} of {len(rows)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=1))

    # ---- the matrix -----------------------------------------------------------------
    print("\n" + "=" * 78)
    print("MATRIX, ALL ENDPOINTS POOLED")
    print("=" * 78)
    cells: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for ep, d in results.items():
        for r in d.get("rows", []):
            if r["position"] == "INSUFFICIENT":
                continue
            cells[(r["position"], r["outcome"])].append((ep, r["gene"]))

    print(f"{'':20}" + "".join(f"{o:>18}" for o in OUTCOMES))
    for p in POSITIONS[:2]:
        line = f"{p:20}"
        for o in OUTCOMES:
            line += f"{len(cells.get((p, o), [])):>18}"
        print(line)

    print("\nEMPTY CELLS")
    empty = [(p, o) for p in POSITIONS[:2] for o in OUTCOMES if not cells.get((p, o))]
    for p, o in empty:
        print(f"  {p} x {o}")
    if not empty:
        print("  none")

    print("\nPER CELL")
    for p in POSITIONS[:2]:
        for o in OUTCOMES:
            got = cells.get((p, o), [])
            if not got:
                continue
            by_ep = Counter(ep for ep, _ in got)
            print(f"\n  {p} x {o}  ({len(got)})")
            for ep, n in by_ep.most_common():
                names = [g for e, g in got if e == ep]
                print(f"    {ep:32} {n:4}  {', '.join(names[:12])}"
                      f"{' …' if len(names) > 12 else ''}")

    print("\nPER ENDPOINT")
    for ep, d in results.items():
        if "skipped" in d:
            print(f"  {ep:32} skipped: {d['skipped']}")
            continue
        rows = d["rows"]
        placed = [r for r in rows if r["position"] != "INSUFFICIENT"]
        pc = Counter((r["position"], r["outcome"]) for r in placed)
        print(f"  {ep:32} {len(placed):4}/{len(rows):4} placed  {dict(pc) or ''}")

    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

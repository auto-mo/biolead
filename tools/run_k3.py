"""k=3 across the cases where a varying trial set could flip the answer.

AIMED, NOT BROAD. A stable verdict on a lopsided case proves little: IL4R sits at 17 against
2 and would survive losing several trials. The cases at risk are the ones sitting EXACTLY ON
THE TWO-THIRDS FLOOR at 2 against 3, where one trial appearing or disappearing moves the
majority across the boundary and changes DECIDED into BALANCED.

Reports three things per case, because they are different results:
  - does the verdict vary
  - does the SPLIT vary, meaning the trial set moved even when the verdict did not
  - does the case cross the two-thirds floor between runs

A stable verdict on a varying trial set is not the same as a stable verdict on a stable set,
and only the second is reproducible.

Usage:
    python3 tools/run_k3.py [--k 3] [--model claude-haiku-4-5]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.clients.open_targets import OpenTargetsClient  # noqa: E402
from app.services.adjudicate import _load_env  # noqa: E402
from app.services.outcome_consensus import MAJORITY_FRACTION  # noqa: E402
from app.services.outcome_graph import DEFAULT_MODEL, run_outcome  # noqa: E402

OUT = ROOT / "docs" / "k3-variance.json"

CASES = [
    # ON THE FLOOR. 2 of 3 is exactly two thirds; one trial either way moves it.
    ("AHR", "atopic dermatitis", "atopic dermatitis", "on the floor, 2 of 3"),
    ("TNFRSF4", "atopic dermatitis", "atopic dermatitis", "on the floor, 2 of 3"),
    # Lopsided, for contrast.
    ("IL4R", "atopic dermatitis", "atopic dermatitis", "lopsided"),
    ("IL13", "atopic dermatitis", "atopic dermatitis", "lopsided"),
    # The demo cases, whose stability is what the deck rests on.
    ("SRD5A2", "androgenetic alopecia", "androgenetic alopecia", "demo opener"),
    ("IL17A", "oily skin", "acne", "demo passenger"),
    ("PTGDR2", "hair thinning", "androgenetic alopecia", "demo passenger"),
]


def share(tally: dict) -> float | None:
    if not tally:
        return None
    return max(tally.values()) / sum(tally.values())


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()
    _load_env()

    out = {}
    async with OpenTargetsClient() as ot:
        http = httpx.AsyncClient(timeout=120)
        for gene, condition, disease, kind in CASES:
            d = await ot.resolve_disease(disease)
            runs = []
            for i in range(args.k):
                r = await run_outcome(gene=gene, condition=condition, disease_id=d.id,
                                      disease_name=d.name, ot=ot, http=http,
                                      model_name=args.model)
                readable = sorted(t["nct_id"] for t in (r.get("trials") or []) if t.get("read"))
                model_read = [t["nct_id"] for t in (r.get("trials") or [])
                              if t.get("path") == "MODEL"]
                runs.append({
                    "read": r.get("read"), "consensus": r.get("consensus"),
                    "split": r.get("split"), "state": r.get("state"),
                    "tally": r.get("count_tally") or {},
                    "share": share(r.get("count_tally") or {}),
                    "readable_ids": readable,
                    "readable": len(readable),
                    "model_read": len(model_read),
                    "reachable": r.get("reachable_trials"),
                    "unresolved_sent": r.get("unresolved_sent_to_model"),
                })

            verdicts = {r["read"] for r in runs}
            consensuses = {r["consensus"] for r in runs}
            sets = {tuple(r["readable_ids"]) for r in runs}
            shares = [r["share"] for r in runs if r["share"] is not None]
            crosses = (any(s >= MAJORITY_FRACTION for s in shares)
                       and any(s < MAJORITY_FRACTION for s in shares))

            print(f"\n{'='*80}")
            print(f"{gene} x {condition}   [{kind}]   reachable {runs[0]['reachable']}")
            for i, r in enumerate(runs, 1):
                print(f"  run {i}: {str(r['consensus']):10} {str(r['read']):11} "
                      f"| readable {r['readable']:3} (model-read {r['model_read']:3}) "
                      f"| share {r['share'] if r['share'] is None else round(r['share'],3)} "
                      f"| {r['split']}")
            print(f"  VERDICT varies: {'YES' if len(verdicts) > 1 else 'no'}"
                  f"   ({', '.join(str(v) for v in sorted(verdicts, key=str))})")
            print(f"  SPLIT varies:   {'YES' if len(sets) > 1 else 'no'}"
                  f"   ({len(sets)} distinct readable sets)")
            print(f"  CROSSES the two-thirds floor: {'YES' if crosses else 'no'}")

            out[f"{gene} x {condition}"] = {
                "kind": kind, "k": args.k, "runs": runs,
                "verdict_varies": len(verdicts) > 1,
                "consensus_varies": len(consensuses) > 1,
                "split_varies": len(sets) > 1,
                "distinct_readable_sets": len(sets),
                "crosses_floor": crosses,
            }
        await http.aclose()

    OUT.write_text(json.dumps({"model": args.model, "k": args.k, "cases": out}, indent=1))
    print(f"\n{'='*80}")
    print(f"{sum(1 for v in out.values() if v['verdict_varies'])}/{len(out)} cases: verdict varies")
    print(f"{sum(1 for v in out.values() if v['split_varies'])}/{len(out)} cases: split varies")
    print(f"{sum(1 for v in out.values() if v['crosses_floor'])}/{len(out)} cases: cross the floor")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

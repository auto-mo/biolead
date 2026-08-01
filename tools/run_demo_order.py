"""Run the demo running order N times and diff the runs against each other.

WHAT THIS IS FOR. The outcome axis is retrieved rather than looked up, and a
retrieved answer can move between runs where a lookup cannot. The eval says whether the
answer is right; this says whether it is the SAME, which is the question a live demo asks.

Reports per case: whether the verdict moved, whether the confidence moved, whether the trial
set behind it moved, and what the spread was. A case whose verdict holds on a moving trial
set is a different result from one whose verdict holds on a fixed set, and the second is the
only one that is reproducible.

Usage:
    python3 tools/run_demo_order.py                 # 3 runs, the default provider
    python3 tools/run_demo_order.py --runs 3 --provider file
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

from app.clients.clinicaltrials import ClinicalTrialsClient  # noqa: E402
from app.clients.open_targets import OpenTargetsClient  # noqa: E402
from app.core.config import load_config  # noqa: E402
from app.services.adjudicate import _load_env  # noqa: E402
from app.services.outcome import build_outcome_provider  # noqa: E402
from app.services.pipeline import run_assessment  # noqa: E402

OUT = ROOT / "docs" / "demo-order-runs.json"

# The chips the interface offers, in the order the demo walks them. Matches DEMO_CASES in
# tools/snapshot_trials.py; the pair leads because the pair is the argument.
ORDER = [
    ("SRD5A2", "androgenetic alopecia"),   # the opener, unmarked, HIGH
    ("SRD5A2", "hair thinning"),           # the same evidence through a borrow, MODERATE
    ("AR", "androgenetic alopecia"),       # tested and silent
    ("AR", "hair thinning"),
    ("SRD5A1", "hair thinning"),
    ("IL17A", "oily skin"),                # passenger
    ("IL1RL2", "oily skin"),
    ("PTGDR2", "hair thinning"),           # passenger
    ("FLG", "atopic dermatitis"),          # the borrow pair, control half
    ("FLG", "cosmetic dry skin"),
    ("FASN", "oily skin"),                 # abstention
    ("AR", "rosacea"),                     # abstention
]

FIELDS = ("position", "targetability", "confidence", "outcome_state")


async def one(gene, cond, cfg, ot, ct, prov) -> dict:
    a = None
    async for ev in run_assessment(gene, cond, config=cfg, ot=ot, ct=ct,
                                   adjudicator=None, outcome_provider=prov):
        if ev.event == "assessment":
            a = ev.data
    v = a["final_verdict"]
    trials = [t if isinstance(t, dict) else t.model_dump() for t in (v.get("outcome_trials") or [])]
    readable = sorted(t["nct_id"] for t in trials if t.get("read"))
    return {
        "position": v["position"], "targetability": v["targetability"],
        "confidence": v["confidence"], "outcome_state": v.get("outcome_state"),
        "consensus": v.get("outcome_consensus"), "split": v.get("outcome_split"),
        "provider": v.get("outcome_provider"),
        "trials": len(trials), "readable": readable,
        "excluded": len(v.get("outcome_excluded") or []),
        "could_not_check": len(a["tier_profile"]["could_not_check"]),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--provider", default=None, help="default: whatever ships")
    args = ap.parse_args()
    _load_env()

    cfg = load_config(strict=True)
    results: dict[str, list[dict]] = {f"{g} x {c}": [] for g, c in ORDER}

    async with OpenTargetsClient() as ot, ClinicalTrialsClient() as ct:
        http = httpx.AsyncClient(timeout=120)
        for r in range(args.runs):
            prov = build_outcome_provider(args.provider, ot=ot, http=http)
            print(f"\n--- run {r + 1} of {args.runs}, provider {prov.name} ---")
            for g, c in ORDER:
                try:
                    got = await one(g, c, cfg, ot, ct, prov)
                except Exception as exc:
                    got = {"error": f"{type(exc).__name__}: {exc}"}
                results[f"{g} x {c}"].append(got)
                print(f"  {g:8} x {c:22} {got.get('position','ERR'):16} "
                      f"{str(got.get('targetability')):15} {str(got.get('confidence')):9} "
                      f"{str(got.get('outcome_state'))}")
        await http.aclose()

    print(f"\n{'=' * 100}")
    print(f"{'CASE':34}{'VERDICT':10}{'CONF':8}{'TRIAL SET':12}{'what moved'}")
    print("-" * 100)
    moved_any = []
    for case, runs in results.items():
        ok = [r for r in runs if "error" not in r]
        if not ok:
            print(f"{case:34}ERROR")
            continue
        verdicts = {(r["position"], r["targetability"]) for r in ok}
        confs = {r["confidence"] for r in ok}
        sets = {tuple(r["readable"]) for r in ok}
        notes = []
        if len(verdicts) > 1:
            notes.append("VERDICT: " + " / ".join(f"{p} {t}" for p, t in sorted(verdicts)))
        if len(confs) > 1:
            notes.append("CONFIDENCE: " + ", ".join(str(c) for c in sorted(confs, key=str)))
        if len(sets) > 1:
            union = set().union(*[set(s) for s in sets])
            inter = set.intersection(*[set(s) for s in sets])
            notes.append(f"trial set: {len(inter)} shared of {len(union)}, "
                         f"churn {sorted(union - inter)}")
        if notes:
            moved_any.append(case)
        print(f"{case:34}{'MOVED' if len(verdicts) > 1 else 'same':10}"
              f"{'MOVED' if len(confs) > 1 else 'same':8}"
              f"{'MOVED' if len(sets) > 1 else 'same':12}{'; '.join(notes)}")

    print(f"\n{len(moved_any)}/{len(results)} cases moved in some way.")
    print("Cases where the VERDICT moved: "
          + (", ".join(c for c, r in results.items()
                       if len({(x['position'], x['targetability'])
                               for x in r if 'error' not in x}) > 1) or "none"))
    OUT.write_text(json.dumps({"runs": args.runs, "results": results}, indent=1))
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

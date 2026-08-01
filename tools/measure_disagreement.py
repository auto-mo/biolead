"""Attach enrolment to the target-condition pairs whose readable trials disagree.

The open question this exists to answer, in the owner's words: *is a 700-person trial a
majority against three 30-person trials?* That is answerable from data and must not be
answered by assertion, so nothing here proposes a rule. It measures how often the unit of
counting changes the answer, and stops.

The earlier pass over this question sampled the 40 densest pairs and was run from a
throwaway script, so its numbers survived only in scrollback. This runs the complete
population and writes the per-trial detail to disk.

WHAT A PAIR IS. One gene against one condition, where some drug's ChEMBL mechanism record
names that gene AND NO OTHER, taken over the UNION of the drug's mechanism rows. Both filters
are load-bearing and they catch different shapes. A row pointing at a PROTEIN COMPLEX expands
to every member gene, which is how metformin acquires 51 targets; 63% of targets in these
conditions are reachable only through such an expansion. And a drug may carry several rows
each naming one target, which is how ruxolitinib reaches JAK1, JAK2, JAK3 and TYK2 with every
row looking clean. The first run of this tool applied only the row filter, so its pair
population credited multi-kinase results to single kinases.

WHAT READABLE MEANS. The trial posted results, and `outcome_rule.read_primary_outcome`
resolved it deterministically from the trial's own published statistics. A trial that
completed and posted nothing is unknown rather than failure, and it is the largest single
band in the sweep at 480 of 1,362. Those are excluded here because a pair cannot disagree
with itself over a silence.

PAIRS ARE NOT INDEPENDENT and the output says so. MMP1, MMP7, MMP8 and MMP13 against acne
are four pairs carrying the same doxycycline trials, so counting pairs alone overstates how
much evidence is in view. The signature counts in the summary are the deduplicated figure.

Free: both APIs. Roughly 70 batched ClinicalTrials.gov calls at 1.5/s. Usage:
    python3 tools/measure_disagreement.py
    python3 tools/measure_disagreement.py --top 40     # the earlier sample, for comparison
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.clients.open_targets import OpenTargetsClient  # noqa: E402
from app.core.limits import REGISTRY  # noqa: E402
from app.services.outcome_rule import read_primary_outcome  # noqa: E402

OUT = ROOT / "docs" / "disagreement-pairs.json"

# The same seven that resolve. "photoaging" does not resolve to an ontology term, which is
# the same result the proxy table already records for it.
CONDITIONS = [
    "acne", "androgenetic alopecia", "alopecia areata", "atopic dermatitis",
    "vitiligo", "melasma", "rosacea", "photoaging",
]

Q = """
query D($id:String!){ disease(efoId:$id){ id name
  drugAndClinicalCandidates{ count rows{
    maxClinicalStage
    drug{ id name
      mechanismsOfAction{ rows{ mechanismOfAction actionType targets{ id approvedSymbol } } } }
    clinicalReports{ id source trialPhase trialOverallStatus } } } } }
"""

CT = "https://clinicaltrials.gov/api/v2/studies"
NCT = re.compile(r"^NCT\d{8}$")


async def build_pairs() -> tuple[dict[tuple[str, str], set[str]], dict[str, dict]]:
    """(gene, condition) -> nct ids, plus which drug carried each id into the pair."""
    pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
    provenance: dict[str, dict] = {}

    async with OpenTargetsClient() as ot:
        print(f"open targets data version {await ot.data_version()}\n")
        for term in CONDITIONS:
            d = await ot.resolve_disease(term)
            if d is None:
                print(f"{term:24} did not resolve, skipped")
                continue
            data = await ot._gql(Q, {"id": d.id})
            rows = data["disease"]["drugAndClinicalCandidates"]["rows"]
            before = len(pairs)
            for r in rows:
                drug = r.get("drug") or {}
                moa = (drug.get("mechanismsOfAction") or {}).get("rows") or []
                # The union across every row, not the row. A drug is attributable to one
                # gene only when that gene is the only thing its whole mechanism record
                # names. Same rule as `_attribution_for`, and deliberately duplicated here
                # rather than imported, because this tool must be able to run against a
                # different definition when the question is whether the definition moved.
                named: set[str] = set()
                for m in moa:
                    named |= {t["approvedSymbol"] for t in (m.get("targets") or [])
                              if t.get("approvedSymbol")}
                if len(named) != 1:
                    continue
                sole = {next(iter(named)): moa[0]}
                # Only AACT rows are NCT ids. DailyMed carries uuids, TTD carries strings
                # like "d00uwy/acne vulgaris".
                ncts = {
                    (cr.get("id") or "").upper()
                    for cr in (r.get("clinicalReports") or [])
                    if cr.get("source") == "AACT" and NCT.match((cr.get("id") or "").upper())
                }
                for sym, m in sole.items():
                    pairs[(sym, term)] |= ncts
                    for n in ncts:
                        provenance.setdefault(n, {})[sym] = {
                            "drug": drug.get("name"),
                            "chembl_id": drug.get("id"),
                            "mechanism": m.get("mechanismOfAction"),
                            "action_type": m.get("actionType"),
                        }
            print(f"{term:24} -> {d.name:26} pairs +{len(pairs) - before}")
    return pairs, provenance


async def fetch_full(ids: list[str]) -> dict[str, dict]:
    """Full study records, batched. `filter.ids` accepts a list; adding `fields` returns 400.

    The batch route carries the whole `resultsSection`, analyses included, so this is one
    call per twenty trials rather than one per trial.
    """
    out: dict[str, dict] = {}
    failed: list[str] = []
    lim = REGISTRY.get("clinicaltrials")
    # 40-id batches at 5/s return 429. 20 at 1.5/s complete cleanly. Measured, not assumed.
    lim.rate_per_second = 1.5
    lim.burst = 3

    async with httpx.AsyncClient(timeout=90) as client:
        for i in range(0, len(ids), 20):
            chunk = ids[i : i + 20]

            async def go(c=chunk):
                r = await client.get(CT, params={"filter.ids": ",".join(c),
                                                 "pageSize": len(c), "format": "json"})
                r.raise_for_status()
                return r.json()

            try:
                data = await lim.run(go)
            except Exception:
                await asyncio.sleep(20)
                try:
                    data = await lim.run(go)
                except Exception as exc:
                    # Unchecked is not absent. A 429 read as "no results posted" is how a
                    # transport problem becomes a scientific claim, and it has already cost
                    # this project 80 trials once.
                    failed.extend(chunk)
                    print(f"  chunk unchecked after backoff ({type(exc).__name__}), "
                          f"{len(chunk)} ids")
                    continue

            for s in data.get("studies", []):
                p = s.get("protocolSection", {}) or {}
                nct = (p.get("identificationModule", {}) or {}).get("nctId")
                if not nct:
                    continue
                design = p.get("designModule", {}) or {}
                status = p.get("statusModule", {}) or {}
                oms = ((s.get("resultsSection", {}) or {})
                       .get("outcomeMeasuresModule", {}) or {}).get("outcomeMeasures", []) or []
                out[nct] = {
                    "status": status.get("overallStatus"),
                    "why_stopped": status.get("whyStopped"),
                    "has_results": bool(s.get("resultsSection")),
                    "phases": design.get("phases") or [],
                    "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
                    "enrollment_type": (design.get("enrollmentInfo") or {}).get("type"),
                    "brief_title": (p.get("identificationModule", {}) or {}).get("briefTitle"),
                    "primary_outcomes": [
                        {"title": om.get("title"), "analyses": om.get("analyses") or []}
                        for om in oms if om.get("type") == "PRIMARY"
                    ],
                }
            print(f"  {min(i + 20, len(ids)):5}/{len(ids)} fetched", end="\r")

    print(f"  {len(out)}/{len(ids)} fetched          ")
    if failed:
        print(f"  {len(failed)} ids never checked: absence for these is not a finding")
    return out


def vote(trials: list[dict], key) -> tuple[str | None, dict[str, float]]:
    """Winner under one unit of counting, and the full tally. None when tied."""
    tally: dict[str, float] = defaultdict(float)
    for t in trials:
        tally[t["read"]] += key(t)
    if not tally:
        return None, {}
    top = max(tally.values())
    winners = [k for k, v in tally.items() if v == top]
    return (winners[0] if len(winners) == 1 else None), dict(tally)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=0,
                    help="restrict to the N densest pairs by trial count (0 = all)")
    args = ap.parse_args()

    pairs, provenance = asyncio.run(build_pairs())
    ranked = sorted(pairs.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    if args.top:
        ranked = ranked[: args.top]
        print(f"\nrestricted to the {args.top} densest pairs")

    all_ncts = sorted({n for _, v in ranked for n in v})
    print(f"\nfetching {len(all_ncts)} distinct trials from ClinicalTrials.gov")
    trials = asyncio.run(fetch_full(all_ncts))

    # -- read every trial once -----------------------------------------------------------
    reads: dict[str, dict] = {}
    for nct, rec in trials.items():
        if not rec["has_results"]:
            continue
        v = read_primary_outcome(nct, rec["primary_outcomes"])
        reads[nct] = {
            "read": v.read, "path": v.path, "reason": v.reason,
            "enrollment": rec["enrollment"], "enrollment_type": rec["enrollment_type"],
            "status": rec["status"], "phases": rec["phases"],
            "brief_title": rec["brief_title"],
            "primary_titles": [o["title"] for o in rec["primary_outcomes"]],
        }

    deterministic = {n: r for n, r in reads.items() if r["path"] == "DETERMINISTIC"}
    print(f"\n{len(trials)} trials, {len(reads)} posted results, "
          f"{len(deterministic)} resolve deterministically")

    # Enrolment is missing or zero on a few records. A trial with no enrolment figure
    # cannot vote by enrolment, and silently treating it as 0 would delete it from one unit
    # and not the other. It is reported instead.
    no_enrol = [n for n, r in deterministic.items() if not r["enrollment"]]
    if no_enrol:
        print(f"{len(no_enrol)} deterministic trials carry no enrolment count: "
              f"{', '.join(sorted(no_enrol)[:6])}")

    # -- assemble the pairs --------------------------------------------------------------
    out_pairs = []
    for (sym, cond), ncts in ranked:
        rows = [{"nct_id": n, **deterministic[n]} for n in sorted(ncts) if n in deterministic]
        if len(rows) < 2:
            continue
        for r in rows:
            r["drug"] = (provenance.get(r["nct_id"], {}).get(sym) or {}).get("drug")
        kinds = {r["read"] for r in rows}
        by_count, count_tally = vote(rows, lambda t: 1)
        # Trials with no enrolment figure are excluded from the enrolment vote rather than
        # counted as zero.
        enrolled = [r for r in rows if r["enrollment"]]
        by_enrol, enrol_tally = vote(enrolled, lambda t: t["enrollment"])
        total_n = sum(r["enrollment"] or 0 for r in rows)

        out_pairs.append({
            "gene": sym, "condition": cond,
            "trials_linked": len(ncts),
            "trials_readable": len(rows),
            "reads": sorted(kinds),
            "disagrees": len(kinds) > 1,
            "by_count": by_count, "count_tally": count_tally,
            "by_enrollment": by_enrol, "enrollment_tally": enrol_tally,
            "units_agree": (by_count == by_enrol) if (by_count and by_enrol) else None,
            "total_enrollment": total_n,
            "winner_enrollment_share": (
                round(max(enrol_tally.values()) / sum(enrol_tally.values()), 3)
                if enrol_tally and sum(enrol_tally.values()) else None),
            "trials": rows,
            # Four MMP pairs against acne carry the same doxycycline trials. The signature
            # is what makes that visible in the summary instead of counting it four times.
            "signature": "|".join(sorted(r["nct_id"] for r in rows)),
        })

    multi = out_pairs
    dis = [p for p in multi if p["disagrees"]]
    sig_multi = {p["signature"] for p in multi}
    sig_dis = {p["signature"] for p in dis}
    flipped = [p for p in dis if p["units_agree"] is False]
    count_tied = [p for p in dis if p["by_count"] is None]
    enrol_tied = [p for p in dis if p["by_enrollment"] is None]

    # -- report --------------------------------------------------------------------------
    print("\n" + "=" * 78)
    print(f"pairs considered                                  {len(ranked)}")
    print(f"multi-readable (>=2 deterministic trials)         {len(multi)}"
          f"   {len(sig_multi)} distinct trial sets")
    print(f"  of those, disagreeing                           {len(dis)}"
          f"   {len(sig_dis)} distinct trial sets")
    print(f"\nON THE DISAGREEING PAIRS, does the unit change the answer")
    print(f"  count winner == enrolment winner                {len(dis) - len(flipped)}")
    print(f"  count winner != enrolment winner                {len(flipped)}")
    print(f"  tied on trial count                             {len(count_tied)}")
    print(f"  tied on enrolment                               {len(enrol_tied)}")
    print("=" * 78)

    print(f"\n{'gene':10} {'condition':22} {'reads':28} {'by count':12} {'by enrol':12} share")
    for p in sorted(dis, key=lambda p: (p["condition"], p["gene"])):
        tally = ", ".join(f"{k} {int(v)}" for k, v in sorted(p["count_tally"].items()))
        mark = "  <-- FLIPS" if p["units_agree"] is False else ""
        print(f"{p['gene']:10} {p['condition']:22} {tally:28} "
              f"{str(p['by_count']):12} {str(p['by_enrollment']):12} "
              f"{p['winner_enrollment_share']}{mark}")

    print("\nper-trial detail on the disagreeing pairs, smallest enrolment first")
    for p in sorted(dis, key=lambda p: (p["condition"], p["gene"])):
        print(f"\n  {p['gene']} x {p['condition']}   "
              f"n={p['total_enrollment']} across {p['trials_readable']} trials")
        for r in sorted(p["trials"], key=lambda r: r["enrollment"] or 0):
            print(f"    {r['nct_id']}  {r['read']:11} n={str(r['enrollment'] or '?'):>6}  "
                  f"{(r['drug'] or '')[:22]:22} {(r['brief_title'] or '')[:44]}")

    OUT.write_text(json.dumps({
        "generated_for": "open item 1, enrolment on the disagreeing pairs",
        "conditions": CONDITIONS,
        "pairs_considered": len(ranked),
        "trials_fetched": len(trials),
        "trials_with_results": len(reads),
        "trials_deterministic": len(deterministic),
        "deterministic_without_enrollment": sorted(no_enrol),
        "summary": {
            "multi_readable_pairs": len(multi),
            "multi_readable_signatures": len(sig_multi),
            "disagreeing_pairs": len(dis),
            "disagreeing_signatures": len(sig_dis),
            "units_disagree": len(flipped),
            "tied_on_count": len(count_tied),
            "tied_on_enrollment": len(enrol_tied),
        },
        "pairs": multi,
    }, indent=1))
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    print(REGISTRY.get("clinicaltrials").snapshot())
    return 0


if __name__ == "__main__":
    sys.exit(main())

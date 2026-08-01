"""Enumerate every target with a drug in seven skin and hair conditions, and grade the trials.

Open Targets gives target-drug-disease-trial rows; ClinicalTrials.gov is then asked about
each trial id, because Open Targets records that a trial exists and not what it showed.

SHAPES VERIFIED LIVE, because two of them are not what the name suggests:

  1. `knownDrugs` DOES NOT EXIST in data version 26.06. The field is
     `Disease.drugAndClinicalCandidates`, returning `clinicalIndicationsFromDiseaseImp`
     with `count` and `rows`. Querying `knownDrugs` is a GraphQL error rather than an empty
     result, so this one at least fails loudly.
  2. The indication row carries NO TARGET. The target is reached through
     `drug.mechanismsOfAction.rows[].targets[]`, which is the ChEMBL mechanism record. A
     drug with no mechanism record yields no target, and `mechanismsOfAction` is null for
     many of them (coal tar, icariin), so target counts are counts of drugs with a curated
     mechanism rather than of drugs.
  3. `clinicalReports[].id` is a LOWERCASE nct id for rows sourced from AACT, and is not an
     NCT id at all for other sources: DailyMed rows carry a uuid, TTD rows carry strings
     like `d00uwy/acne vulgaris`. Filtering on `source == "AACT"` and upper-casing is what
     makes the ClinicalTrials.gov lookup work.
  4. ClinicalTrials.gov v2 accepts `filter.ids` with a comma-separated list, but adding
     `fields=` alongside it returns 400.

Free: both APIs. Usage:
    python3 tools/sweep_known_drugs.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.clients.open_targets import OpenTargetsClient  # noqa: E402
from app.core.limits import REGISTRY  # noqa: E402

OUT = ROOT / "docs" / "known-drugs-sweep.json"

# "pigmentation disorders" is not one ontology term. It resolves to "skin pigmentation
# disorder", which carries zero drug rows, so the two disorders that actually have drug
# programmes are named directly. "photoaging" does not resolve at all, which is the same
# result the proxy table already records for it.
CONDITIONS = [
    "acne", "androgenetic alopecia", "alopecia areata", "atopic dermatitis",
    "vitiligo", "melasma", "rosacea", "photoaging",
]

Q = """
query D($id:String!){ disease(efoId:$id){ id name
  drugAndClinicalCandidates{ count rows{
    maxClinicalStage
    drug{ id name drugType
      mechanismsOfAction{ rows{ mechanismOfAction actionType targets{ id approvedSymbol } } } }
    clinicalReports{ id source url trialPhase trialOverallStatus trialNumberOfArms
                     trialPrimaryPurpose trialOfficialTitle } } } } }
"""

CT = "https://clinicaltrials.gov/api/v2/studies"
NCT = re.compile(r"^NCT\d{8}$")
DOSE_TEXT = re.compile(r"dose[- ]?(rang|find|escalat|response|titrat)", re.I)
DOSE_ARM = re.compile(r"\b\d+(\.\d+)?\s?(mg|mcg|µg|%|g|iu|ug)\b", re.I)
PLACEBO = re.compile(r"\b(placebo|vehicle|sham)\b", re.I)


async def fetch_trials(ids: list[str]) -> dict[str, dict]:
    """ClinicalTrials.gov v2, batched. `filter.ids` accepts a list; `fields` breaks it."""
    out: dict[str, dict] = {}
    failed: list[str] = []
    lim = REGISTRY.get("clinicaltrials")
    # ClinicalTrials.gov 429s on 40-id batches at 5/s. Measured, not assumed.
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
            except Exception as exc:
                # A 429 here is a transport failure, not an absence of trials. Retrying
                # once more slowly is the difference between "80 unchecked" and a number.
                await asyncio.sleep(20)
                try:
                    data = await lim.run(go)
                except Exception as exc2:
                    failed.extend(chunk)
                    print(f"  chunk still failing after backoff ({type(exc2).__name__}); "
                          f"{len(chunk)} ids unchecked")
                    continue
            for s in data.get("studies", []):
                p = s.get("protocolSection", {})
                nct = p.get("identificationModule", {}).get("nctId")
                if not nct:
                    continue
                design = p.get("designModule", {}) or {}
                arms_mod = p.get("armsInterventionsModule", {}) or {}
                arms = arms_mod.get("armGroups", []) or []
                interventions = arms_mod.get("interventions", []) or []
                title = " ".join(filter(None, [
                    p.get("identificationModule", {}).get("briefTitle"),
                    p.get("identificationModule", {}).get("officialTitle"),
                    (p.get("descriptionModule", {}) or {}).get("briefSummary", "")[:1500],
                ]))
                arm_labels = " ".join(
                    f"{a.get('label','')} {a.get('type','')} {a.get('description','')}"
                    for a in arms)
                iv_names = " ".join(i.get("name", "") for i in interventions)

                has_placebo = bool(
                    any(a.get("type") == "PLACEBO_COMPARATOR" for a in arms)
                    or PLACEBO.search(arm_labels) or PLACEBO.search(iv_names))
                # Dose ranging: the phrase, or two or more arms each naming a dose.
                dose_arms = sum(1 for a in arms if DOSE_ARM.search(
                    f"{a.get('label','')} {a.get('description','')}"))
                dose_ranging = bool(DOSE_TEXT.search(title)) or dose_arms >= 2

                out[nct] = {
                    "status": (p.get("statusModule", {}) or {}).get("overallStatus"),
                    "has_results": bool(s.get("resultsSection")),
                    "phases": design.get("phases") or [],
                    "n_arms": len(arms),
                    "enrollment": ((design.get("enrollmentInfo") or {}).get("count")),
                    "placebo_or_vehicle": has_placebo,
                    "dose_ranging": dose_ranging,
                    "primary_purpose": (design.get("designInfo") or {}).get("primaryPurpose"),
                }
    if failed:
        print(f"  {len(failed)} ids never checked: absence for these is not a finding")
    return out


async def main() -> int:
    per_disease: dict[str, dict] = {}
    drug_targets: dict[str, set[str]] = defaultdict(set)   # chembl id -> target symbols
    drug_names: dict[str, str] = {}
    target_trials: dict[str, set[str]] = defaultdict(set)  # symbol -> nct ids
    target_any_drug: set[str] = set()
    all_ncts: set[str] = set()
    non_nct_sources: Counter = Counter()

    async with OpenTargetsClient() as ot:
        print(f"data version {await ot.data_version()}\n")
        for term in CONDITIONS:
            d = await ot.resolve_disease(term)
            if d is None:
                print(f"{term!r}: did not resolve, skipped")
                per_disease[term] = {"resolved": None}
                continue
            data = await ot._gql(Q, {"id": d.id})
            dc = data["disease"]["drugAndClinicalCandidates"]
            rows = dc["rows"]
            ncts_here: set[str] = set()
            targets_here: set[str] = set()

            for r in rows:
                drug = r["drug"] or {}
                cid = drug.get("id")
                if cid:
                    drug_names[cid] = drug.get("name") or cid
                moa = (drug.get("mechanismsOfAction") or {}).get("rows") or []
                tsyms = {t["approvedSymbol"] for m in moa for t in (m.get("targets") or [])}
                if cid:
                    drug_targets[cid] |= tsyms
                targets_here |= tsyms
                target_any_drug |= tsyms

                for cr in (r.get("clinicalReports") or []):
                    src = cr.get("source")
                    rid = (cr.get("id") or "").upper()
                    if src == "AACT" and NCT.match(rid):
                        ncts_here.add(rid)
                        for s in tsyms:
                            target_trials[s].add(rid)
                    else:
                        non_nct_sources[src or "unknown"] += 1

            all_ncts |= ncts_here
            per_disease[term] = {
                "resolved": d.name, "id": d.id, "indication_rows": dc["count"],
                "distinct_drugs": len({r["drug"]["id"] for r in rows if r.get("drug")}),
                "distinct_targets": len(targets_here),
                "distinct_ncts": len(ncts_here),
            }
            print(f"{term:24} -> {d.name:26} rows={dc['count']:4} "
                  f"targets={len(targets_here):4} nct={len(ncts_here):4}")

    print(f"\nfetching {len(all_ncts)} distinct trials from ClinicalTrials.gov")
    trials = await fetch_trials(sorted(all_ncts))
    print(f"  {len(trials)} returned, {len(all_ncts) - len(trials)} not found\n")

    # -- the numbers ------------------------------------------------------------------
    targets_with_trial = {s for s, n in target_trials.items() if n}
    got = [t for t in trials.values()]
    completed = [t for t in got if t["status"] == "COMPLETED"]
    with_results = [t for t in got if t["has_results"]]
    placebo = [t for t in got if t["placebo_or_vehicle"]]
    dosing = [t for t in got if t["dose_ranging"]]
    single = {c for c, s in drug_targets.items() if len(s) == 1}
    multi = {c for c, s in drug_targets.items() if len(s) > 1}
    nomech = {c for c in drug_names if not drug_targets.get(c)}

    def pc(n: int, d: int) -> str:
        return f"{n/d*100:5.1f}%" if d else "    n/a"

    print("=" * 74)
    print("TARGETS")
    print(f"  with a drug of any kind                 {len(target_any_drug):5}")
    print(f"  with at least one registered trial      {len(targets_with_trial):5}"
          f"  {pc(len(targets_with_trial), len(target_any_drug))} of the above")
    print("\nTRIALS  (distinct NCT ids, ClinicalTrials.gov)")
    print(f"  reached from these seven conditions     {len(all_ncts):5}")
    print(f"  found on ClinicalTrials.gov             {len(got):5}")
    print(f"  completed                               {len(completed):5}  {pc(len(completed), len(got))}")
    print(f"  posted results                          {len(with_results):5}  {pc(len(with_results), len(got))}")
    print(f"  completed AND posted results            "
          f"{len([t for t in got if t['status']=='COMPLETED' and t['has_results']]):5}"
          f"  {pc(len([t for t in got if t['status']=='COMPLETED' and t['has_results']]), len(got))}")
    print(f"  placebo or vehicle comparator           {len(placebo):5}  {pc(len(placebo), len(got))}")
    print(f"  dose ranging                            {len(dosing):5}  {pc(len(dosing), len(got))}")
    print("\nDRUGS  (ChEMBL mechanism records)")
    print(f"  distinct drugs                          {len(drug_names):5}")
    print(f"  with no mechanism record at all         {len(nomech):5}  {pc(len(nomech), len(drug_names))}")
    print(f"  hitting exactly one target              {len(single):5}  {pc(len(single), len(drug_names))}")
    print(f"  hitting more than one target            {len(multi):5}  {pc(len(multi), len(drug_names))}")
    if multi:
        worst = sorted(drug_targets.items(), key=lambda kv: -len(kv[1]))[:8]
        print("  widest:")
        for c, s in worst:
            print(f"    {drug_names[c][:26]:26} {len(s):3} targets")
    print("\nTRIAL IDS THAT ARE NOT NCTs, by source")
    for s, n in non_nct_sources.most_common():
        print(f"  {s:14} {n:5}")
    print("=" * 74)

    OUT.write_text(json.dumps({
        "per_disease": per_disease,
        "totals": {
            "targets_with_any_drug": len(target_any_drug),
            "targets_with_trial": len(targets_with_trial),
            "ncts_reached": len(all_ncts), "ncts_found": len(got),
            "completed": len(completed), "with_results": len(with_results),
            "placebo_or_vehicle": len(placebo), "dose_ranging": len(dosing),
            "drugs": len(drug_names), "drugs_single_target": len(single),
            "drugs_multi_target": len(multi), "drugs_no_mechanism": len(nomech),
        },
        "target_trial_counts": {s: len(n) for s, n in sorted(target_trials.items())},
        "trials": trials,
    }, indent=1))
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

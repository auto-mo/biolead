"""Build `data/trial_snapshot.json`: the trial records every demo case touches.

A completed trial's registry record is settled, so this is a pinned copy of stable facts
rather than a stub. Records served from it are marked SNAPSHOT and carry this fetch date, so
a reader is never left guessing whether they are looking at today's registry or July's.

It exists because the in-memory cache is not a fallback. Failed fetches are deliberately not
cached and successful ones only exist if a fetch already succeeded in that process, so a
process starting during an outage has nothing. ClinicalTrials.gov returned sustained 429s to
sustained load, and under a full outage the demo loses its two best cases.

The id list is DISCOVERED, not typed. Every demo case is run through both outcome providers
and every NCT id either of them reaches is collected, so a case that starts touching a new
trial is covered without anyone remembering to add it.

Usage:
    python3 tools/snapshot_trials.py            # discover, fetch, write
    python3 tools/snapshot_trials.py --verify   # check the file against live, no write
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.clients.clinicaltrials import ClinicalTrialsClient  # noqa: E402
from app.clients.open_targets import OpenTargetsClient  # noqa: E402
from app.core.config import load_config  # noqa: E402
from app.services.outcome import build_outcome_provider  # noqa: E402
from app.services.outcome_tools import read_trial  # noqa: E402

OUT = ROOT / "data" / "trial_snapshot.json"

# The chips the interface offers, and only those.
#
# SCOPED DELIBERATELY. Discovering every trial every case can reach would be broader, which
# for IL4R in atopic dermatitis is 74 dupilumab trials, and fetching them all triggered the
# 429s this file exists to survive. The snapshot holds the trials whose ABSENCE CHANGES A
# DEMO ANSWER, which is a much smaller set: the four the curated file names, and the three
# clascoterone trials behind AR's tested-and-silent state.
#
# RARG and IL4R are cold cases for the plan, not demo chips, and they are not pinned.
DEMO_CASES = [
    ("SRD5A2", "androgenetic alopecia"), ("SRD5A2", "hair thinning"),
    ("AR", "androgenetic alopecia"), ("AR", "hair thinning"),
    ("SRD5A1", "hair thinning"), ("IL17A", "oily skin"),
    ("IL1RL2", "oily skin"), ("PTGDR2", "hair thinning"),
    ("FLG", "atopic dermatitis"), ("FLG", "cosmetic dry skin"),
    ("FASN", "oily skin"), ("AR", "rosacea"),
]

# A case whose outcome rests on one drug's trials pins them; a case reaching dozens does not
# need dozens pinned to keep its answer. Above this the case is left to live fetch.
MAX_TRIALS_PER_CASE = 8

# Fields that belong to the trial rather than to how it was read.
#
# `primary_outcomes` AND THE ARM FIELDS ARE PINNED TOO, AND THE DEMO DEPENDS ON IT. The first
# version of this file pinned metadata only, on the reasoning that a completed trial's status
# is settled and its results are bulky. That was fine while the outcome came from a curated
# fact and the fetch only PROMOTED it from asserted to retrieved. Under the graph provider
# there is no curated fact behind it: if the posted results cannot be read, there is no
# outcome at all. Measured with the source refusing every call, SRD5A2 x androgenetic
# alopecia went from UPSTREAM/ACTIONABLE/HIGH to UPSTREAM/UNKNOWN/MODERATE and IL17A reported
# NO_DRUG, which is a false claim rather than a degraded one.
#
# The arm fields travel because the attribution cap reads them: a search-derived link is
# checked against the trial's experimental arms, and without them a pinned trial is excluded
# by the cap it should pass.
KEEP = ("nct_id", "exists", "status", "has_results", "title", "enrollment", "why_stopped",
        "primary_outcomes", "conditions", "interventions", "experimental_interventions")


async def discover(cfg, ot, http, ct, searches: dict) -> set[str]:
    """Every NCT id the demo reaches, through both providers."""
    ids: set[str] = set()

    # The curated file names its trials directly.
    for f in cfg.clinical_facts:
        if f.nct_id:
            ids.add(f.nct_id.upper())

    # Record what each drug-and-condition search returned, not only the ids it produced.
    # A search that returns nothing under outage silently shortens the trial list.
    from app.core.trial_snapshot import search_key
    from app.services.outcome_tools import find_drugs_for_target, search_trials

    retrieved = build_outcome_provider("retrieved", ot=ot, http=http)
    for gene, cond in DEMO_CASES:
        proxy = cfg.lookup_proxy(cond)
        try:
            r = await retrieved.outcome_for(gene, cond, proxy=proxy, config=cfg, ct=ct)
        except Exception as exc:
            print(f"  {gene} x {cond}: discovery failed, {exc}")
            continue
        found = {t.nct_id.upper() for t in r.trials}
        if len(found) > MAX_TRIALS_PER_CASE:
            print(f"  {gene:8} x {cond:24} {len(found):3} trials, over the cap, not pinned")
            continue
        ids |= found
        term = (proxy.search_term if proxy and proxy.search_term else cond)
        disease = await ot.resolve_disease(term)
        if disease is not None:
            for d in await find_drugs_for_target(ot, symbol=gene, disease_id=disease.id):
                try:
                    got = await search_trials(http, drug=d.name, condition=term)
                except Exception:
                    continue
                if len(got) <= MAX_TRIALS_PER_CASE:
                    searches[search_key(d.name, term)] = got
                    ids |= {n.upper() for n in got}
        print(f"  {gene:8} x {cond:24} {len(found):3} trial(s)  state={r.state}")
    return ids


async def main() -> int:
    verify = "--verify" in sys.argv
    cfg = load_config(strict=True)

    async with OpenTargetsClient() as ot, httpx.AsyncClient(timeout=60) as http, \
            ClinicalTrialsClient() as ct:
        print("discovering the trials the demo touches")
        searches: dict[str, list[str]] = {}
        ids = await discover(cfg, ot, http, ct, searches)
        print(f"\n{len(ids)} distinct trials\nfetching")

        records: dict[str, dict] = {}
        failed: list[str] = []
        for n in sorted(ids):
            rec = await read_trial(http, n)
            if not rec.found:
                failed.append(n)
                print(f"  {n}  FAILED  {rec.note}")
                continue
            # Only settled records are pinned. An ongoing trial's fields still move, and a
            # snapshot of a moving target is a stub wearing a cache's clothes.
            if (rec.status or "").upper() not in ("COMPLETED", "TERMINATED", "WITHDRAWN",
                                                  "SUSPENDED", "UNKNOWN"):
                print(f"  {n}  skipped, status {rec.status} is not settled")
                continue
            records[n] = {k: getattr(rec, k, None) for k in KEEP}
            records[n]["nct_id"] = n
            records[n]["exists"] = True
            print(f"  {n}  {rec.status:12} results={str(rec.has_results):5} "
                  f"n={str(rec.enrollment):>6}")

    if failed:
        print(f"\n{len(failed)} could not be fetched: {', '.join(failed)}")
        if not verify:
            print("Refusing to write a partial snapshot. A snapshot missing the trial a case "
                  "depends on is worse than none, because it looks complete.")
            return 1

    if verify:
        existing = json.loads(OUT.read_text()) if OUT.exists() else {"trials": {}}
        old = existing.get("trials", {})
        drift = [n for n in records if n in old and old[n] != records[n]]
        missing = [n for n in records if n not in old]
        print(f"\nverify: {len(old)} pinned, {len(drift)} drifted, {len(missing)} missing")
        for n in drift[:10]:
            print(f"  DRIFT {n}: {old[n]} -> {records[n]}")
        return 1 if (drift or missing) else 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "fetched_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source": "https://clinicaltrials.gov/api/v2/studies",
        "note": ("Settled trial records only. A completed trial's registry entry does not "
                 "change, so this is a pinned copy of stable facts. Records served from here "
                 "are marked SNAPSHOT and carry fetched_on. Rebuild with "
                 "tools/snapshot_trials.py."),
        "trials": records,
        "searches": searches,
    }, indent=1) + "\n")
    print(f"\nwrote {OUT.relative_to(ROOT)}  {len(records)} trials, "
          f"{len(searches)} pinned searches")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

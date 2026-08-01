"""The four retrieval tools the outcome agent calls.

Plain async functions. They are written and tested standalone so that when the graph
arrives it wraps something already known to work, and so a failure at that point is a graph
failure rather than a retrieval one.

Each returns a record carrying its own identifier, because the cite guardrail is enforced
against identifiers fetched in the run rather than against the model's word for it.

WHAT THESE DO NOT DO. They do not decide anything. `read_trial` returns the trial's own
numbers and does not compute whether the trial succeeded; there is no structured field on
ClinicalTrials.gov saying whether a primary endpoint was met, only 26% of primary outcomes
carry a p-value at all, and NCT02998671 reports a ratio of 1.18 with a 90% interval of 0.79
to 1.81 which is a failure that nothing in the API states. Reading that is comprehension,
which is the agent's job.

THE FAMILY FILTER, which is the load-bearing line in this module. A ChEMBL mechanism record
may name a PROTEIN COMPLEX or PROTEIN FAMILY, which Open Targets expands to every member
gene. Metformin is attributed 51 targets that way, gabapentin 26, pentoxifylline 25. A
mechanism row naming exactly one target is a single protein; a row naming several is an
expansion. Measured over the seven conditions: 395 single-target rows against 91 expansions,
and 175 of 279 targets are reachable only through an expansion.

THAT FILTER CATCHES ONE SHAPE OF THE PROBLEM AND THERE ARE TWO. A drug may also carry
SEVERAL rows each naming ONE target: ruxolitinib has six across JAK1, JAK2, JAK3 and TYK2.
Every row passes a per-row filter, and the drug still hits four kinases. Attribution is
therefore computed over the union of the drug's rows, never row by row. See
`_attribution_for`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.clients.clinicaltrials import BASE as CT_BASE
from app.clients.open_targets import OpenTargetsClient
from app.core import trial_snapshot
from app.core.cache import CACHE
from app.core.limits import REGISTRY

NCT_RE = re.compile(r"^NCT\d{8}$")

# Settled trial records, for the life of the process. Only successful reads land here.
_TRIAL_CACHE: dict[str, "TrialRecord"] = {}

# One query per disease, reused for every gene assessed against it.
_INDICATIONS = """
query D($id:String!){ disease(efoId:$id){ id name
  drugAndClinicalCandidates{ count rows{
    maxClinicalStage
    drug{ id name drugType
      mechanismsOfAction{ rows{ mechanismOfAction actionType targetName
                                targets{ id approvedSymbol } } } }
    clinicalReports{ id source url trialPhase trialOverallStatus } } } } }
"""


@dataclass
class DrugHit:
    chembl_id: str
    name: str
    drug_type: str | None
    mechanism: str | None
    action_type: str | None
    max_stage: str | None
    # How this drug's mechanism names our target. See `assess_attribution`.
    attribution: str = "NOT_NAMED"
    family_size: int = 0
    # Every target the drug's mechanism record names, so a reader can see the other three
    # kinases rather than take the label's word for it.
    named_targets: list[str] = field(default_factory=list)
    nct_ids: list[str] = field(default_factory=list)

    @property
    def record_id(self) -> str:
        return self.chembl_id


@dataclass
class TrialRecord:
    nct_id: str
    found: bool
    status: str | None = None
    has_results: bool = False
    phases: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    enrollment: int | None = None
    brief_title: str | None = None
    why_stopped: str | None = None
    provenance: str = "LIVE"
    snapshot_date: str | None = None
    arms: list[dict] = field(default_factory=list)
    # Intervention names as the registry lists them. Needed because a drug appearing in a
    # trial is not the same as a trial being about that drug: NCT02781311 tested setipiprant
    # with a finasteride comparator arm, and a name search for finasteride returns it.
    interventions: list[str] = field(default_factory=list)
    # Interventions that sit in an arm the trial is actually testing, as opposed to one it
    # is testing against. A drug present only as a comparator is not what the trial is about.
    experimental_interventions: list[str] = field(default_factory=list)
    has_comparator: bool = False
    primary_outcomes: list[dict] = field(default_factory=list)
    note: str | None = None

    @property
    def record_id(self) -> str:
        return self.nct_id

    @property
    def readable(self) -> bool:
        """A trial the agent can actually reason from.

        Completed with nothing posted is the largest band in the sweep at 480 of 1,362, and
        it is unknown rather than failure.
        """
        return self.found and self.has_results


# ---------------------------------------------------------------------------------------
# 1. Which drugs hit this target in this indication
# ---------------------------------------------------------------------------------------


async def _indications(ot: OpenTargetsClient, disease_id: str) -> list[dict]:
    version = await ot.data_version()

    async def go() -> list[dict]:
        d = await ot._gql(_INDICATIONS, {"id": disease_id})
        disease = d.get("disease") or {}
        return (disease.get("drugAndClinicalCandidates") or {}).get("rows") or []

    return await CACHE.get_or_set(("ot.indications", version, disease_id), go)


def _attribution_for(
    moa_rows: list[dict], symbol: str
) -> tuple[str, int, dict | None, list[str]]:
    """Three states, no score, computed over the drug's WHOLE mechanism record.

    Affinity cannot support a selectivity ratio: tofacitinib's rank order across the JAKs
    flips between papers, and metformin has zero affinity measurements against any of the 51
    targets attributed to it. So this reports how the mechanism NAMES the target and stops
    there.

    THE UNION IS THE QUESTION, NOT THE ROW. Asking whether any ONE
    row named the symbol alone, and never asked what the drug's other rows named.
    Ruxolitinib carries six rows naming JAK1, JAK2, JAK3 and TYK2 one at a time, so it
    returned SOLE_NAMED_TARGET on all four and the caller printed that a result on it was
    attributable to each. That is the family expansion arriving from the opposite direction:
    one row naming many targets and many rows naming one target each are the same drug
    hitting the same several proteins, and only the first shape was being caught.

    Measured over the seven conditions: 21 drugs carry several single-target rows
    (ruxolitinib, upadacitinib, doxycycline across four MMPs, cyproterone acetate,
    etrasimod), and 5 more carry a lone row beside a family row (ritlecitinib names JAK3
    alone in one row and 6 targets in total).
    """
    named: set[str] = set()
    matched: dict | None = None
    for m in moa_rows or []:
        syms = {t.get("approvedSymbol") for t in (m.get("targets") or [])
                if t.get("approvedSymbol")}
        named |= syms
        # The row that names our symbol, kept for its mechanism text. Which row it is does
        # not change the verdict any more; the union does.
        if matched is None and symbol in syms:
            matched = m
    if symbol not in named:
        return ("NOT_NAMED", 0, None, [])
    if named == {symbol}:
        return ("SOLE_NAMED_TARGET", 1, matched, [symbol])
    return ("ONE_OF_A_FAMILY", len(named), matched, sorted(named))


async def find_drugs_for_target(
    ot: OpenTargetsClient, *, symbol: str, disease_id: str
) -> list[DrugHit]:
    """Drugs whose ChEMBL mechanism names this gene, in this indication."""
    hits: list[DrugHit] = []
    for row in await _indications(ot, disease_id):
        drug = row.get("drug") or {}
        moa = (drug.get("mechanismsOfAction") or {}).get("rows") or []
        attribution, size, matched, named = _attribution_for(moa, symbol)
        if attribution == "NOT_NAMED":
            continue
        ncts = sorted({
            (cr.get("id") or "").upper()
            for cr in (row.get("clinicalReports") or [])
            # Only AACT rows are NCT ids. DailyMed carries uuids and TTD carries strings
            # like "d00uwy/acne vulgaris".
            if cr.get("source") == "AACT" and NCT_RE.match((cr.get("id") or "").upper())
        })
        hits.append(DrugHit(
            chembl_id=drug.get("id") or "",
            name=drug.get("name") or "",
            drug_type=drug.get("drugType"),
            mechanism=(matched or {}).get("mechanismOfAction"),
            action_type=(matched or {}).get("actionType"),
            max_stage=row.get("maxClinicalStage"),
            attribution=attribution,
            family_size=size,
            named_targets=named,
            nct_ids=ncts,
        ))
    # Sole-named first: those are the only ones that can carry an unqualified claim.
    hits.sort(key=lambda h: (h.attribution != "SOLE_NAMED_TARGET", -len(h.nct_ids)))
    return hits


# ---------------------------------------------------------------------------------------
# 2. Which trials tested that drug here
# ---------------------------------------------------------------------------------------


async def list_trials_for_drug(
    ot: OpenTargetsClient, *, chembl_id: str, disease_id: str
) -> list[str]:
    out: list[str] = []
    for row in await _indications(ot, disease_id):
        if (row.get("drug") or {}).get("id") != chembl_id:
            continue
        out.extend(
            (cr.get("id") or "").upper()
            for cr in (row.get("clinicalReports") or [])
            if cr.get("source") == "AACT" and NCT_RE.match((cr.get("id") or "").upper())
        )
    return sorted(set(out))


# ---------------------------------------------------------------------------------------
# 2b. Trials the indication row did not link
# ---------------------------------------------------------------------------------------


async def search_trials(client, *, drug: str, condition: str, limit: int = 40) -> list[str]:
    """Query ClinicalTrials.gov directly by intervention and condition.

    Open Targets' linking is incomplete. Its CJM-112 row for acne carries one clinical
    report, `d0t5tn/acne vulgaris` from TTD, and no AACT row, so NCT02998671 is unreachable
    through it even though the trial exists and posted results. 776 of the trial ids in the
    sweep were non-NCT, 167 of them TTD.

    Depending on one source's curation to decide whether a drug was ever tested is the same
    shape of failure this project documents three times over, so there is a second route.
    """
    if not drug or not condition:
        return []

    async def go() -> list[str]:
        r = await client.get(
            "https://clinicaltrials.gov/api/v2/studies",
            params={"query.intr": drug, "query.cond": condition,
                    "pageSize": min(limit, 100), "format": "json"},
        )
        r.raise_for_status()
        return [
            s["protocolSection"]["identificationModule"]["nctId"]
            for s in r.json().get("studies", [])
        ]

    try:
        return await REGISTRY.get("clinicaltrials").run(go)
    except Exception:
        pinned = trial_snapshot.get_search(drug, condition)
        if pinned is not None:
            return list(pinned)
        # An empty list here would read as "never tested", which is a claim. The caller
        # distinguishes a failed search from an empty one by catching this.
        raise


# ---------------------------------------------------------------------------------------
# 3. What the trial says. Returned, not judged.
# ---------------------------------------------------------------------------------------

_PLACEBO = re.compile(r"\b(placebo|vehicle|sham)\b", re.I)


# Arm types that mean "this is what the trial is testing against", not "this is what the
# trial is testing".
_COMPARATOR_ARMS = {"ACTIVE_COMPARATOR", "PLACEBO_COMPARATOR", "SHAM_COMPARATOR",
                    "NO_INTERVENTION"}


def _experimental_interventions(arms: list[dict], interventions: list[dict]) -> list[str]:
    """Intervention names appearing in at least one arm that is not a comparator.

    A trial listing a drug is not a trial of that drug. NCT02781311 tested setipiprant with
    an active finasteride comparator, so a name search for finasteride returns it, and
    crediting its result to SRD5A2 would attribute a setipiprant failure to the wrong gene.
    Trials reached through Open Targets' indication rows are exempt from this check upstream,
    because that curation is an explicit statement that the drug was studied for the
    indication. A bare name match is not.
    """
    non_comparator = {a.get("label") for a in arms
                      if (a.get("type") or "").upper() not in _COMPARATOR_ARMS}
    out: list[str] = []
    for iv in interventions:
        name = iv.get("name")
        if not name:
            continue
        labels = iv.get("armGroupLabels") or []
        # No arm mapping at all: keep it rather than drop it. Absence of the mapping is not
        # evidence the drug was a comparator.
        if not labels or any(l in non_comparator for l in labels):
            out.append(name)
    return out


def _reconcile_with_snapshot(rec: TrialRecord) -> TrialRecord:
    """A live record that has LOST its results falls back to the pinned copy.

    THE FAILURE THIS CATCHES IS SILENT AND IS THIS PROJECT'S OWN DOCUMENTED SHAPE. A trial
    that posted results is settled: it does not stop having them. But a loaded or throttled
    response can come back HTTP 200 with the protocol section and no `resultsSection`, and
    nothing in it says so. Downstream that reads as "completed, nothing posted", which is a
    legitimate state, so it never reaches `could_not_check` and never trips the degraded-run
    guard. The verdict just quietly loses a trial.

    That is what made SRD5A2, the demo opener, return ACTIONABLE/HIGH standalone and
    UNKNOWN/MODERATE inside a full benchmark run with `could_not_check` empty. It is the same
    class as `associatedTargets` truncating to the page size and a 429 reading as zero
    associations, arriving through a third route.

    Only the demo trials are pinned, so this guard covers the cases whose absence would
    change a demo answer and nothing else. It never invents results: it only refuses to
    believe that a trial known to have posted them has stopped.
    """
    if rec.has_results or not rec.found:
        return rec
    snap = trial_snapshot.get(rec.nct_id)
    if not snap or not snap.get("has_results"):
        return rec
    return TrialRecord(
        nct_id=rec.nct_id, found=True, status=snap.get("status"), has_results=True,
        enrollment=snap.get("enrollment"), brief_title=snap.get("title"),
        why_stopped=snap.get("why_stopped"),
        conditions=snap.get("conditions") or rec.conditions,
        interventions=snap.get("interventions") or rec.interventions,
        experimental_interventions=(snap.get("experimental_interventions")
                                    or rec.experimental_interventions),
        primary_outcomes=snap.get("primary_outcomes") or [],
        provenance="SNAPSHOT", snapshot_date=trial_snapshot.fetched_on(),
        note="The live record came back without its posted results, which a settled trial "
             "does not lose. Served from the pinned copy.")


def _record_from_study(data: dict) -> TrialRecord | None:
    """One study record into a `TrialRecord`.

    Shared by the single-id route and the batch route so the two cannot drift. The batch
    route returns the same study shape, `resultsSection` included, which is why one call can
    replace twenty.
    """
    p = data.get("protocolSection", {}) or {}
    nct_id = (p.get("identificationModule", {}) or {}).get("nctId")
    if not nct_id:
        return None
    status_mod = p.get("statusModule", {}) or {}
    design = p.get("designModule", {}) or {}
    arms_mod = p.get("armsInterventionsModule", {}) or {}
    arms = arms_mod.get("armGroups", []) or []
    interventions = arms_mod.get("interventions", []) or []

    blob = " ".join(
        f"{a.get('label','')} {a.get('type','')} {a.get('description','')}" for a in arms
    ) + " " + " ".join(i.get("name", "") for i in interventions)

    primary: list[dict] = []
    for om in ((data.get("resultsSection", {}) or {}).get("outcomeMeasuresModule", {}) or {}
               ).get("outcomeMeasures", []) or []:
        if om.get("type") != "PRIMARY":
            continue
        primary.append({
            "title": om.get("title"),
            "param_type": om.get("paramType"),
            "unit": om.get("unitOfMeasure"),
            "groups": [g.get("title") for g in (om.get("groups") or [])],
            # Values and analyses verbatim. `pValue` is a string and is often "<0.001",
            # so it is never coerced here.
            "classes": om.get("classes"),
            "analyses": [
                {k: a.get(k) for k in (
                    "pValue", "statisticalMethod", "paramType", "paramValue",
                    "ciPctValue", "ciLowerLimit", "ciUpperLimit",
                    "nonInferiorityType", "estimateComment", "groupIds")}
                for a in (om.get("analyses") or [])
            ],
        })

    return TrialRecord(
        nct_id=nct_id,
        found=True,
        status=status_mod.get("overallStatus"),
        has_results=bool(data.get("resultsSection")),
        phases=design.get("phases") or [],
        conditions=(p.get("conditionsModule", {}) or {}).get("conditions", []) or [],
        enrollment=(design.get("enrollmentInfo") or {}).get("count"),
        brief_title=(p.get("identificationModule", {}) or {}).get("briefTitle"),
        why_stopped=status_mod.get("whyStopped"),
        arms=[{"label": a.get("label"), "type": a.get("type")} for a in arms],
        interventions=[i.get("name", "") for i in interventions if i.get("name")],
        experimental_interventions=_experimental_interventions(arms, interventions),
        has_comparator=bool(
            any(a.get("type") == "PLACEBO_COMPARATOR" for a in arms) or _PLACEBO.search(blob)
        ),
        primary_outcomes=primary,
    )


async def read_trial(client, nct_id: str) -> TrialRecord:
    """Full record, with the outcome numbers surfaced and no verdict attached."""
    if not NCT_RE.match(nct_id):
        return TrialRecord(nct_id, found=False, note="malformed NCT id")

    async def go() -> dict | None:
        r = await client.get(f"{CT_BASE}/{nct_id}", params={"format": "json"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    try:
        data = await REGISTRY.get("clinicaltrials").run(go)
    except Exception as exc:
        snap = trial_snapshot.get(nct_id)
        if snap is not None:
            # The snapshot is written in the client's vocabulary, where presence is `exists`
            # and the title is `title`. This record calls those `found` and `brief_title`.
            # Mapping rather than sharing a shape, because the two carry different things:
            # the client never holds outcome measures and this one does.
            # Posted results ARE pinned now. Without them a pinned trial is present but
            # unreadable, which under the graph provider means the outcome disappears
            # entirely rather than degrading, and the demo opener loses its band.
            return TrialRecord(
                nct_id=nct_id, found=True,
                status=snap.get("status"), has_results=snap.get("has_results", False),
                enrollment=snap.get("enrollment"), brief_title=snap.get("title"),
                why_stopped=snap.get("why_stopped"),
                conditions=snap.get("conditions") or [],
                interventions=snap.get("interventions") or [],
                experimental_interventions=snap.get("experimental_interventions") or [],
                primary_outcomes=snap.get("primary_outcomes") or [],
                provenance="SNAPSHOT", snapshot_date=trial_snapshot.fetched_on(),
                note="Served from the pinned snapshot, fetched "
                     f"{trial_snapshot.fetched_on()}.")
        # Not cached, and not an absence. A transport failure that becomes "no trial" is
        # how a rate limit turns into a scientific claim, which happened on this project's
        # own sweep and cost 80 trials before it was caught.
        return TrialRecord(nct_id, found=False, note=f"could not be read: {exc}")
    if data is None:
        return TrialRecord(nct_id, found=False, note="not found on ClinicalTrials.gov")
    rec = _record_from_study(data)
    if rec is None:
        return TrialRecord(nct_id, found=False, note="record carried no NCT id")
    return _reconcile_with_snapshot(rec)


# ---------------------------------------------------------------------------------------
# 4. What the result licenses about this gene
# ---------------------------------------------------------------------------------------


@dataclass
class Attribution:
    verdict: str          # SOLE_NAMED_TARGET | ONE_OF_A_FAMILY | NOT_NAMED
    family_size: int
    mechanism: str | None
    explanation: str
    # The members, not just the count. A reader told "one of 4" and not told which four
    # cannot check the claim, and this is the field the whole tool exists to be honest in.
    named_targets: list[str] = field(default_factory=list)


async def assess_attribution(
    ot: OpenTargetsClient, *, symbol: str, chembl_id: str, disease_id: str
) -> Attribution:
    """Whether a result on this drug may be attributed to this gene.

    Not a selectivity score, because selectivity cannot be computed. Tofacitinib is the
    densest case available and its rank order across the JAKs flips between papers, with
    JAK1-versus-JAK2 ratios spanning 0.1x to 162x. Metformin has no affinity measurement
    against any of its 51 attributed targets. So this reports naming, which is checkable.
    """
    for row in await _indications(ot, disease_id):
        drug = row.get("drug") or {}
        if drug.get("id") != chembl_id:
            continue
        moa = (drug.get("mechanismsOfAction") or {}).get("rows") or []
        verdict, size, matched, named = _attribution_for(moa, symbol)
        if verdict == "SOLE_NAMED_TARGET":
            expl = (f"Across every mechanism row for {drug.get('name')}, {symbol} is the only "
                    f"target named, so a result on this drug is attributable to {symbol}.")
        elif verdict == "ONE_OF_A_FAMILY":
            others = ", ".join(s for s in named if s != symbol)
            expl = (f"The mechanism record for {drug.get('name')} names {size} targets, "
                    f"{symbol} among them, alongside {others}. A result on this drug does not "
                    f"isolate {symbol}, and no affinity data exists that would.")
        else:
            expl = f"No mechanism record for {drug.get('name')} names {symbol}."
        return Attribution(verdict, size, (matched or {}).get("mechanismOfAction"), expl,
                           named_targets=named)
    return Attribution("NOT_NAMED", 0, None,
                       f"{chembl_id} is not an indication row for this disease.")


async def read_trials(client, nct_ids: list[str]) -> list[TrialRecord]:
    """Many trials in one call, through the `filter.ids` batch route.

    ADDED AT STAGE 4 BECAUSE ONE CALL PER TRIAL PRODUCED A WRONG ANSWER, not because it was
    slow. RARG in acne reaches 11 trifarotene trials. Reading them one at a time exhausted
    the agent's tool-call budget before it reached the trial carrying the result, and the
    run returned "tested, and the result was never published" about a trial that published
    one. That is a starved fetch becoming a scientific claim, which is the failure this
    project exists to prevent, arriving this time through a budget rather than a rate limit.

    Falls back to the single-record route per id when the batch fails, because that path
    carries the pinned-snapshot fallback and a batch 429 must not become an absence.
    """
    ids = [n for n in dict.fromkeys(nct_ids) if NCT_RE.match(n)]
    bad = [n for n in nct_ids if not NCT_RE.match(n)]
    out: list[TrialRecord] = [TrialRecord(n, found=False, note="malformed NCT id")
                              for n in bad]

    # PROCESS CACHE ON SETTLED RECORDS. A completed trial's record does not change during a
    # run, and the same trials are reached over and over: the eval fetches every case twice,
    # once single and once through batch, and neighbouring genes share drugs and therefore
    # trials. Uncached, a fifteen-case eval is several hundred fetches in a few minutes and
    # trips the source, which then arrives as a verdict rather than as an error.
    #
    # FAILURES ARE NOT CACHED, here or anywhere. "Could not check" must stay distinguishable
    # from "checked and absent", and a cached failure erases that distinction for the rest of
    # the process.
    cached = {n: _TRIAL_CACHE[n] for n in ids if n in _TRIAL_CACHE}
    out.extend(cached.values())
    ids = [n for n in ids if n not in cached]

    for i in range(0, len(ids), 20):
        chunk = ids[i : i + 20]

        async def go(c=chunk) -> dict:
            r = await client.get(CT_BASE, params={"filter.ids": ",".join(c),
                                                  "pageSize": len(c), "format": "json"})
            r.raise_for_status()
            return r.json()

        try:
            data = await REGISTRY.get("clinicaltrials").run(go)
        except Exception:
            for n in chunk:
                rec = await read_trial(client, n)
                if rec.found:
                    _TRIAL_CACHE[n] = rec
                out.append(rec)
            continue

        seen = set()
        for s in data.get("studies", []):
            rec = _record_from_study(s)
            if rec is not None:
                rec = _reconcile_with_snapshot(rec)
                seen.add(rec.nct_id)
                _TRIAL_CACHE[rec.nct_id] = rec
                out.append(rec)
        # An id the batch did not return is genuinely absent from the registry, because the
        # request succeeded. Distinct from the failure path above, which retries per id.
        for n in chunk:
            if n not in seen:
                out.append(TrialRecord(n, found=False,
                                       note="not found on ClinicalTrials.gov"))
    order = {n: i for i, n in enumerate(nct_ids)}
    return sorted(out, key=lambda r: order.get(r.nct_id, len(order)))

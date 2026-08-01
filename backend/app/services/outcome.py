"""The outcome axis, behind one interface.

The question *"did modulating this target move this endpoint in humans"* has one named seam,
so implementations can sit beside each other and be switched by a flag. `pipeline.py` and
`batch.py` both ask a provider rather than reaching into the configuration directly.

WHAT THE PROVIDER OWNS. Everything that fills the directional tier 1 slots that
`rules.derive_targetability` and `rules.derive_confidence` read: `tier_1_positive`,
`tier_1_negative`, `has_directional_tier_1` and `directional_tier_1_asserted_only`. It owns
nothing else. Position, mode, abstention and the whole tier 2 to 5 profile are computed
outside it and stay deterministic.

WHAT IT RETURNS. An `OutcomeResult`, not a mutated profile. The provider says what it found
and the caller folds it in, so a provider cannot quietly rewrite tiers it does not own.

The events are part of the interface, not decoration. The trace is the product, so a
provider that fetches something has to be able to say so mid-flight; `FileOutcomeProvider`
emits the trial fetches the pipeline used to emit inline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.clients.clinicaltrials import ClinicalTrialsClient
from app.core.config import Config
from app.models.contracts import ProxyRow, TierProfile
from app.services.tiers import add_clinical_facts


@dataclass
class OutcomeResult:
    """What a provider found about the outcome question.

    `events` are (name, payload) pairs the caller forwards onto its own stream. Returning
    them rather than yielding keeps the provider callable from both an async generator and
    a plain await, which is what lets batch and single-gene share it.
    """

    facts: list[Any] = field(default_factory=list)
    # Evidence a provider RETRIEVED rather than read out of a curated file. Kept separate
    # from `facts` because a `ClinicalFact` is by construction an asserted row, `retrieved`
    # pinned to False, and dressing a retrieved trial up as one would put the wrong
    # provenance on the item that decides whether a case may reach HIGH.
    evidence: list[Any] = field(default_factory=list)
    trial_records: dict[str, Any] = field(default_factory=dict)
    events: list[tuple[str, dict]] = field(default_factory=list)

    # Set by a provider that could not answer for a reason worth recording. Distinct from
    # finding nothing: "no drug exists against this target" is an answer, "the source was
    # unreachable" is not, and the profile keeps those apart.
    could_not_check: list[str] = field(default_factory=list)

    # Which of the three states of not-knowing this is, and the trials behind it. The file
    # provider can only ever say NOT_ASSESSED or TESTED_REPORTED, because a curated file has
    # no way to know that a drug exists whose trial posted nothing. That blindness is the
    # rebuild's whole argument, so the field lives on the shared result rather than on the
    # provider that can populate it.
    state: str = "NOT_ASSESSED"
    trials: list = field(default_factory=list)
    read: str = "UNDETERMINED"
    path: str = "NONE"
    reason: str = ""

    # Which provider answered, so nothing downstream has to guess. Set by each provider.
    provider: str = "file"
    # The split, always, plus what the attribution cap set aside. Empty on providers that
    # have neither concept, which is itself informative: `file` and `retrieved` do not apply
    # the disagreement rule or the cap, and a blank here means it was never asked.
    consensus: str | None = None
    split: str | None = None
    minority: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    measured_disease: str | None = None
    borrowed: bool = False

    def apply(self, profile: TierProfile) -> TierProfile:
        """Fold the result into a profile. The provider never mutates one directly."""
        if self.facts:
            profile = add_clinical_facts(profile, self.facts, self.trial_records)
        for item in self.evidence:
            profile.tiers.setdefault(item.tier, []).append(item)
        profile.could_not_check.extend(self.could_not_check)
        return profile


class OutcomeProvider(Protocol):
    """One question, one method.

    `gene` and `condition` are as typed. `proxy` is the borrow row, which the file provider
    needs because a fact written against a disease has to be reachable from an endpoint that
    borrows it.
    """

    name: str

    async def outcome_for(
        self,
        gene: str,
        condition: str,
        *,
        proxy: ProxyRow | None,
        config: Config,
        ct: ClinicalTrialsClient | None,
    ) -> OutcomeResult: ...


class FileOutcomeProvider:
    """The current behaviour, unchanged: `config/clinical_facts.yaml`.

    This is the implementation the rebuild replaces. It stays runnable until the agentic
    provider passes the same cases, which is what makes the fallback
    reversible.

    Its limitation is the reason for the rebuild and is worth stating where it lives: the
    answer is bounded by how many rows a human typed, so it cannot distinguish "nobody has
    tried this" from "nobody curated this". Four facts carry a direction.

    RETIRED AT STAGE 7 AND KEPT SELECTABLE. It is no longer the default and its data lives in
    `config/archive/clinical_facts.yaml`. It exists so the demo has something to fall back to
    if the graph proves unstable on stage, which is a real risk: see plan section 0.6 for
    everything measured moving under the graph.
    """

    name = "file"

    async def outcome_for(
        self,
        gene: str,
        condition: str,
        *,
        proxy: ProxyRow | None,
        config: Config,
        ct: ClinicalTrialsClient | None,
    ) -> OutcomeResult:
        # A fact is written against the disease it concerns (acne_vulgaris) while a query
        # arrives against an endpoint (oily_skin) that borrows it, so every key the endpoint
        # could be known by is offered. Matching on one key only once cost TNF its entire
        # tier 1b layer.
        keys = [condition]
        if proxy is not None:
            keys += [proxy.endpoint, proxy.borrowed_from or "", proxy.search_term or ""]

        facts = config.clinical_facts_for(gene, *keys)
        result = OutcomeResult(facts=facts, provider=self.name)
        # A curated file cannot tell "nobody tried" from "nobody typed it in", so it never
        # claims NO_DRUG. Saying NOT_ASSESSED is the honest limit of this provider and it is
        # the sentence the rebuild exists to replace.
        result.state = "TESTED_REPORTED" if facts else "NOT_ASSESSED"
        result.reason = (
            "From the curated file. It cannot distinguish an untested target from an "
            "uncurated one." if facts else
            "No curated fact covers this target and condition. That is a gap in curation "
            "effort, not a retrieved fact about the world."
        )
        if not facts or ct is None:
            return result

        for f in facts:
            if not f.nct_id:
                continue
            rec = await ct.fetch(f.nct_id)
            result.trial_records[f.nct_id] = rec
            result.events.append((
                "trial",
                {"nct_id": f.nct_id, "exists": rec.exists, "status": rec.status,
                 "has_results": rec.has_results, "title": rec.title,
                 "enrollment": rec.enrollment, "usable_as_tier_1a": rec.usable_as_tier_1a},
            ))
        return result


class RetrievedOutcomeProvider:
    """The outcome answered from records, into three states rather than one.

        NO_DRUG            no mechanism record names this target in this indication
        TESTED_UNREPORTED  a drug was tested and the result was never published
        TESTED_REPORTED    a drug was tested and the trial says what happened

    The middle state is the largest band in the retrievable set: 480 of 1,362 trials, 35%,
    completed and posted nothing. Larger than the 287 with a readable result. Collapsing it
    into "untested" hid the biggest thing in the data.

    A trial id that came from a source which named it, and then fails to fetch, is recorded
    as `could_not_check` and never as an absence.
    """

    name = "retrieved"

    def __init__(self, ot, http) -> None:
        self._ot, self._http = ot, http

    async def outcome_for(
        self, gene, condition, *, proxy, config, ct,
    ) -> OutcomeResult:
        from app.models.contracts import OutcomeTrial
        from app.services.outcome_rule import read_primary_outcome
        from app.services.outcome_tools import (
            find_drugs_for_target, read_trial, search_trials,
        )

        term = (proxy.search_term if proxy and proxy.search_term else condition)
        disease = await self._ot.resolve_disease(term)
        if disease is None:
            return OutcomeResult(state="NOT_ASSESSED", provider=self.name,
                                 could_not_check=[f"{term!r} did not resolve"])

        out = OutcomeResult(provider=self.name)
        drugs = await find_drugs_for_target(self._ot, symbol=gene, disease_id=disease.id)
        out.events.append(("outcome_drugs", {
            "gene": gene, "disease": disease.name,
            "drugs": [{"name": d.name, "chembl_id": d.chembl_id,
                       "attribution": d.attribution, "family_size": d.family_size,
                       "named_targets": d.named_targets}
                      for d in drugs[:6]]}))
        if not drugs:
            out.state = "NO_DRUG"
            out.reason = (f"No drug in {disease.name} has a mechanism record naming {gene}. "
                          f"That is retrieved, not a gap in curation.")
            return out

        pairs = []
        for d in drugs:
            ids = list(d.nct_ids)
            try:
                ids += [n for n in await search_trials(
                    self._http, drug=d.name, condition=term) if n not in ids]
            except Exception as exc:
                out.could_not_check.append(
                    f"clinicaltrials/search({d.name}): {exc}. Trials may exist unseen.")
            pairs += [(n, d) for n in ids]

        if not pairs:
            out.state = "NO_DRUG"
            out.reason = (f"{len(drugs)} drug(s) name {gene}, and no human trial in "
                          f"{disease.name} was found for any of them.")
            return out

        records = []
        for nct, drug in pairs:
            rec = await read_trial(self._http, nct)
            if not rec.found:
                out.could_not_check.append(
                    f"clinicaltrials/{nct}: {rec.note or 'could not be read'}")
                continue
            t = OutcomeTrial(nct_id=nct, drug=drug.name, status=rec.status,
                             has_results=rec.has_results, enrollment=rec.enrollment,
                             title=rec.brief_title)
            v = read_primary_outcome(nct, rec.primary_outcomes) if rec.has_results else None
            records.append((t, v))
            out.events.append(("outcome_trial", {
                "nct_id": nct, "drug": drug.name, "status": rec.status,
                "has_results": rec.has_results, "enrollment": rec.enrollment,
                "read": (v.read if v else None), "path": (v.path if v else None)}))

        out.trials = [t for t, _ in records]
        decided = [(t, v) for t, v in records if v and v.path == "DETERMINISTIC"]

        # KNOWN GAP, and it is deliberately loud rather than arbitrary.
        #
        # SRD5A2 in androgenetic alopecia reaches five readable trials. NCT03004469 (n=458)
        # reads BENEFIT; another reads NO_BENEFIT. Taking whichever resolved first made the
        # answer depend on iteration order, which is not a decision procedure. Which trial
        # speaks when several disagree is judgment about design, power and population, and
        # it is one of the things the agent is for.
        #
        # Without that, disagreement abstains and says so.
        kinds = {v.read for _, v in decided}
        if len(kinds) > 1:
            out.state, out.path = "TESTED_REPORTED", "NONE"
            out.reason = (
                f"{len(decided)} trials resolve and they disagree: "
                + ", ".join(f"{t.nct_id} {v.read} (n={t.enrollment})" for t, v in decided)
                + ". Which one speaks needs a judgment this layer does not make yet."
            )
            return out
        if decided:
            t, v = decided[0]
            out.state, out.read, out.path, out.reason = "TESTED_REPORTED", v.read, v.path, v.reason
            return out
        posted = [(t, v) for t, v in records if t.has_results]
        if posted:
            t, v = posted[0]
            out.state, out.path = "TESTED_REPORTED", "MODEL"
            out.reason = v.reason if v else "Results posted with no structured analysis."
            return out
        if records:
            done = [t for t, _ in records if (t.status or "").upper() == "COMPLETED"]
            out.state = "TESTED_UNREPORTED"
            out.reason = (f"{len(done)} completed trial(s) against {gene} posted no results. "
                          f"Tested, and the result was never published.")
            return out
        out.state = "NOT_ASSESSED"
        out.reason = "Trials were named but none could be read."
        return out


def build_outcome_provider(kind: str | None = None, *, ot=None, http=None) -> OutcomeProvider:
    """Select a provider. One implementation today; the flag exists so adding the second
    is a registration rather than an edit to two call sites."""
    import os

    # STAGE 7: THE DEFAULT IS THE SUBGRAPH. `file` stays selectable, by flag or by
    # environment variable, as the demo fallback. It reads `config/archive/`.
    kind = kind or os.environ.get("BIOLEAD_OUTCOME_PROVIDER", "graph")
    if kind == "graph":
        return GraphOutcomeProvider(ot=ot, http=http)
    if kind == "graph-rules":
        # The subgraph with the agent switched off. Same gathering, same arithmetic, same
        # attribution cap and same disagreement rule; no model and no model cost.
        return GraphOutcomeProvider(ot=ot, http=http, allow_model=False)
    if kind == "file":
        return FileOutcomeProvider()
    if kind == "retrieved":
        if ot is None or http is None:
            raise ValueError("the retrieved provider needs an Open Targets client and an "
                             "http client")
        return RetrievedOutcomeProvider(ot, http)
    raise ValueError(f"unknown outcome provider {kind!r}")


class GraphOutcomeProvider:
    """The outcome subgraph, behind the same interface as the curated file.

    The graph answers whether modulating this gene moved this endpoint in humans. This maps
    that answer onto the profile the rules engine reads, and it is the only place where the
    two vocabularies meet.

    WHAT IT WILL AND WILL NOT ASSERT. Directional tier 1 evidence is emitted only when the
    consensus rule actually decided. `BALANCED`, `NO_ATTRIBUTABLE_DRUG`, `NO_DRUG` and an
    unresolved read all emit NOTHING directional, so a case the graph refused to call cannot
    lift a confidence cap through the back door. The refusal is carried as `reason` and shown.

    TIER 1a, NOT 1b. The trials behind the call were fetched this run and carry their own
    NCT ids, so the evidence is retrieved and may reach HIGH. That is the whole difference
    between this provider and the file it replaces: `directional_tier_1_asserted_only` is
    False here, and it is that property, not the direction, that the HIGH band turns on.
    """

    name = "graph"

    def __init__(self, ot=None, http=None, model_name: str | None = None,
                 allow_model: bool = True) -> None:
        self._ot, self._http, self._model = ot, http, model_name
        # False is the batch gate: gather and do the arithmetic, never call the model.
        self._allow_model = allow_model
        # Owned only when nobody handed one in. The API selects this provider from an
        # environment variable and has no client to pass at construction time, so the
        # provider has to be able to stand one up rather than fail at the first fetch.
        self._owns_http = False

    async def outcome_for(
        self, gene, condition, *, proxy, config, ct,
    ) -> OutcomeResult:
        from app.models.contracts import EvidenceItem, ExcludedTrial, OutcomeTrial
        from app.services.outcome_graph import DEFAULT_MODEL, run_outcome

        # THE CEILING GATES THE AGENT, NOT THE ANSWER. Over the daily ceiling the subgraph
        # runs with the model off: the gathering, the arithmetic, the attribution cap and the
        # disagreement rule all still run, and an ambiguous trial comes back undetermined
        # instead of read. Nothing raises and the assessment still returns.
        from app.core.gate import SPEND_CAP

        allow_model = self._allow_model and SPEND_CAP.allowed()

        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=90)
            self._owns_http = True
        if self._ot is None:
            from app.clients.open_targets import OpenTargetsClient
            self._ot = await OpenTargetsClient().__aenter__()

        term = (proxy.search_term if proxy and proxy.search_term else condition)
        disease = await self._ot.resolve_disease(term)
        if disease is None:
            return OutcomeResult(state="NOT_ASSESSED", provider=self.name,
                                 could_not_check=[f"{term!r} did not resolve"])

        try:
            g = await run_outcome(
                gene=gene, condition=condition, disease_id=disease.id,
                disease_name=disease.name, search_term=term,
                ot=self._ot, http=self._http, allow_model=allow_model,
                model_name=self._model or DEFAULT_MODEL)
        except Exception as exc:
            # A graph failure is not an absence of evidence. It goes to could_not_check so
            # the eval's degraded-run guard fails the case rather than passing it quietly.
            return OutcomeResult(
                state="NOT_ASSESSED", provider=self.name,
                could_not_check=[f"outcome_graph({gene}): {exc}"],
                reason="The outcome layer could not complete.")

        out = OutcomeResult(state=g.get("state", "NOT_ASSESSED"),
                            read=g.get("read") or "UNDETERMINED",
                            reason=g.get("reason", ""), provider=self.name)
        out.consensus = g.get("consensus")
        out.split = g.get("split")
        out.measured_disease = g.get("measured_disease")
        out.borrowed = bool(g.get("borrowed"))
        out.minority = [
            OutcomeTrial(nct_id=m["nct_id"], drug=m.get("drug"), status=m.get("status"),
                         has_results=True, enrollment=m.get("enrollment"),
                         title=m.get("title"), read=m.get("read"), path=m.get("path"))
            for m in (g.get("minority") or [])
        ]
        out.excluded = [
            ExcludedTrial(nct_id=e["nct_id"], drug=e.get("drug"),
                          named_targets=e.get("named_targets") or [], why=e.get("why", ""))
            for e in (g.get("excluded_by_attribution") or [])
        ]
        out.could_not_check.extend(g.get("could_not_check") or [])
        # Charged after the fact from real usage, the same way the adjudicator is.
        if g.get("input_tokens") or g.get("output_tokens"):
            SPEND_CAP.charge(g.get("model") or "", g.get("input_tokens") or 0,
                             g.get("output_tokens") or 0)
        if not allow_model and self._allow_model:
            out.reason = (out.reason + " Daily model ceiling reached, so ambiguous trials "
                          "were left unread rather than interpreted.").strip()
        out.trials = [
            OutcomeTrial(nct_id=t["nct_id"], drug=t.get("drug"), status=t.get("status"),
                         has_results=bool(t.get("has_results")),
                         enrollment=t.get("enrollment"), title=t.get("title"),
                         read=t.get("read"), path=t.get("path"))
            for t in (g.get("trials") or [])
        ]
        out.events.append(("outcome_graph", {
            "gene": gene, "disease": disease.name, "state": g.get("state"),
            "consensus": g.get("consensus"), "read": g.get("read"),
            "split": g.get("split"), "minority": g.get("minority"),
            "measured_disease": g.get("measured_disease"), "borrowed": g.get("borrowed"),
            "excluded_by_attribution": g.get("excluded_by_attribution"),
            "rule_model_disagreements": g.get("rule_model_disagreements"),
            "tool_calls_made": g.get("tool_calls_made"),
            "input_tokens": g.get("input_tokens"), "output_tokens": g.get("output_tokens"),
        }))

        supports = {"BENEFIT": "TARGETABILITY_POSITIVE",
                    "NO_BENEFIT": "TARGETABILITY_NEGATIVE",
                    "WORSE": "TARGETABILITY_NEGATIVE"}.get(g.get("read") or "")
        if supports and g.get("consensus") in ("DECIDED", "UNANIMOUS"):
            decided = [t for t in (g.get("trials") or []) if t.get("read")]
            out.evidence.append(EvidenceItem(
                tier="1a",
                source="clinicaltrials_gov",
                datasource_id=(decided[0]["nct_id"] if decided else None),
                # The split travels with the item, so a reader who sees the row that earned
                # the call also sees what it was decided over.
                summary=(f"{g.get('reason', '')} {g.get('split', '')}".strip()),
                raw={"nct_ids": [t["nct_id"] for t in decided],
                     "minority": g.get("minority") or [],
                     "measured_disease": g.get("measured_disease"),
                     "borrowed": bool(g.get("borrowed")),
                     "consensus": g.get("consensus")},
                supports=supports,
                provenance="RETRIEVED",
            ))
        return out

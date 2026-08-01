"""The assessment pipeline.

Deterministic: the same input produces the same sequence of stages. The execution graph is
written in code, not decided by a model. That is a design position, not an accident, and it
is why there is no agent framework here.

The pipeline is an async generator of events. The SSE endpoint streams them and the eval
harness drains them, so there is exactly one code path and the demo cannot diverge from the
thing being tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Protocol

from app.clients.clinicaltrials import ClinicalTrialsClient
from app.clients.hpa import HpaClient
from app.clients.open_targets import OpenTargetsClient
from app.core.config import Config
from app.models.contracts import Assessment, TierProfile, Verdict
from app.services import rules
from app.services.reach import assess_reachability
from app.services.outcome import OutcomeProvider, build_outcome_provider
from app.services.tiers import map_lookup_to_profile

STAGES = [
    "RESOLVE_GENE",
    "LOOKUP_PROXY",
    "RESOLVE_CONDITION",
    "DETERMINE_MODE",
    "FETCH_EVIDENCE",
    "MAP_TIERS",
    "DETECT_CONFLICTS",
    "RULE_VERDICT",
    # Runs AFTER the verdict, and reads none of it. Ordering is the design: reachability
    # cannot influence a causal call if it is computed once the causal call is already made.
    "REACHABILITY",
    "MODEL_ADJUDICATE",
    "RECONCILE",
    "SELECT_EXPERIMENT",
    "RENDER",
]


@dataclass
class Event:
    event: str
    data: dict


class Adjudicator(Protocol):
    """The model's independent second read.

    It receives the same evidence packet the rules received and NEVER the rule verdict,
    because seeing it would anchor the model and destroy the independence the mechanism
    depends on.
    """

    async def adjudicate(self, packet: dict) -> Verdict | None: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _select_gap(profile: TierProfile, condition: str) -> str:
    """Pick the gap type that most limits this assessment.

    Templated, not generated. The model may later phrase the sentence but can only choose
    from config/experiments.yaml, every entry of which is an assay from ODDITY's own patent.
    """
    if not (profile.has("1a") or profile.has("1b")):
        return "no_human_endpoint_measurement"
    if not profile.has("3"):
        c = condition.lower()
        if any(k in c for k in ("pigment", "melasma", "freckle", "tone", "vitiligo")):
            return "no_functional_evidence_pigment_cell"
        if any(k in c for k in ("hair", "alopecia", "thinning")):
            return "no_functional_evidence_follicle"
        if any(k in c for k in ("dry", "xerosis", "barrier", "eczema", "dermatitis")):
            return "no_barrier_or_matrix_evidence"
        if any(k in c for k in ("acne", "sebum", "oily")):
            return "no_keratinocyte_evidence"
        return "no_barrier_or_matrix_evidence"
    return "no_cytotoxicity_baseline"


def _detect_conflicts(profile: TierProfile) -> list[str]:
    out: list[str] = []
    if profile.has("2") and profile.has("5") and not profile.has("3"):
        out.append(
            "Genetic association and literature volume are both present but there is no "
            "functional evidence in a relevant human cell type. The genetics is not "
            "downgraded by that absence; the gap is recorded and drives the resolving "
            "experiment."
        )
    if profile.tier_1_positive and profile.tier_1_negative:
        out.append(
            "Tier 1 evidence points both ways. Shown rather than resolved: a failed "
            "intervention can be wrong modality, dose, population or endpoint rather than a "
            "wrong target."
        )
    if profile.has("4") and not profile.has("2") and not profile.has("3"):
        out.append(
            "The only interventional evidence is an animal model. Mouse skin and hair "
            "biology diverges substantially from human, so this is secondary support and "
            "not a tiebreaker."
        )
    if profile.could_not_check:
        out.append(
            "Part of the evidence base could not be read, so absence here is not a finding: "
            + "; ".join(profile.could_not_check)
        )
    return out


async def run_assessment(
    gene: str,
    condition: str,
    *,
    config: Config,
    ot: OpenTargetsClient,
    ct: ClinicalTrialsClient | None = None,
    hpa: HpaClient | None = None,
    adjudicator: Adjudicator | None = None,
    outcome_provider: OutcomeProvider | None = None,
) -> AsyncIterator[Event]:
    """Yield stage events, then a final `assessment` event."""

    def stage(name: str, **data) -> Event:
        return Event("stage", {"stage": name, "index": STAGES.index(name), **data})

    # The clients this generator already holds are handed to the provider, so selecting
    # `graph` from an environment variable works without the API knowing what a subgraph is.
    outcome_provider = outcome_provider or build_outcome_provider(ot=ot)
    data_version = await ot.data_version()
    yield Event("start", {"gene": gene, "condition": condition,
                          "data_version": data_version,
                          "outcome_provider": outcome_provider.name})

    # 1. RESOLVE_GENE - exact symbol match only.
    yield stage("RESOLVE_GENE")
    target = await ot.resolve_target(gene)
    yield stage(
        "RESOLVE_GENE",
        done=True,
        resolved=target.ensembl_id if target else None,
        note=None if target else "no exact symbol match; ranked search would risk the wrong gene",
    )

    # 2. LOOKUP_PROXY - exact match plus synonyms, no fuzzy matching.
    yield stage("LOOKUP_PROXY")
    proxy = config.lookup_proxy(condition)
    yield stage(
        "LOOKUP_PROXY",
        done=True,
        proxy=proxy.endpoint if proxy else None,
        rating=proxy.rating if proxy else None,
        borrowed_from=proxy.borrowed_from if proxy else None,
    )
    if proxy is not None:
        yield Event(
            "proxy",
            {
                "endpoint": proxy.display_name,
                "borrowed_from": proxy.borrowed_from,
                "rating": proxy.rating,
                "rationale": proxy.rationale.strip(),
                "what_it_misses": proxy.what_it_misses.strip(),
                "population_caveat": (proxy.population_caveat or "").strip() or None,
                "refuse": proxy.refuse,
            },
        )

    # 3. RESOLVE_CONDITION - and check whether the source answered about something else.
    yield stage("RESOLVE_CONDITION")
    search_term = (proxy.search_term if proxy and proxy.search_term else condition)
    disease = await ot.resolve_disease(search_term) if search_term else None
    undeclared = False
    if disease is not None:
        undeclared = ot.substitution_occurred(
            search_term, disease.name, proxy.expected_resolved_name if proxy else None
        )
    yield stage(
        "RESOLVE_CONDITION",
        done=True,
        searched=search_term,
        resolved=disease.name if disease else None,
        resolved_id=disease.id if disease else None,
        undeclared_substitution=undeclared,
    )
    if disease is not None and disease.name.lower() != (search_term or "").lower():
        yield Event(
            "substitution",
            {
                "searched": search_term,
                "resolved": disease.name,
                "declared": not undeclared,
                "note": (
                    "Declared in the proxy table, so this is a documented borrow."
                    if not undeclared
                    else "NOT declared. The source answered about a different disease."
                ),
            },
        )

    # 5. FETCH_EVIDENCE - cross-route, so a zero is corroborated before it becomes a finding.
    yield stage("FETCH_EVIDENCE")
    profile = TierProfile()
    if target is not None and disease is not None:
        lookup = await ot.lookup_evidence(target.ensembl_id, disease.id)
        yield Event("source_status", {"source": "open_targets", "status": lookup.status,
                                      "note": lookup.note})
        profile = map_lookup_to_profile(lookup, data_version=data_version)
    else:
        profile.could_not_check.append("open_targets: gene or condition did not resolve")
    yield stage("FETCH_EVIDENCE", done=True)

    # 6. MAP_TIERS - fold in the outcome axis, whoever answered it.
    #
    # The provider owns the directional tier 1 slots and nothing else. Today that is the
    # curated file; after the rebuild it is the agentic subgraph, selected by a flag rather
    # than by an edit here.
    yield stage("MAP_TIERS")
    outcome = await outcome_provider.outcome_for(
        gene, condition, proxy=proxy, config=config, ct=ct
    )
    for name, payload in outcome.events:
        yield Event(name, payload)
    profile = outcome.apply(profile)
    for tier, items in sorted(profile.tiers.items()):
        for item in items:
            yield Event(
                "evidence",
                {
                    "tier": tier,
                    "source": item.source,
                    "summary": item.summary,
                    "supports": item.supports,
                    "provenance": item.provenance,
                },
            )
    for empty in profile.checked_and_empty:
        yield Event("evidence_absent", {"kind": "checked_and_empty", "detail": empty})
    for unknown in profile.could_not_check:
        yield Event("evidence_absent", {"kind": "could_not_check", "detail": unknown})
    yield stage("MAP_TIERS", done=True, tiers={k: len(v) for k, v in profile.tiers.items()})

    # 4. DETERMINE_MODE - announced before the verdict so the caveat lands before the answer.
    yield stage("DETERMINE_MODE")
    mode, mode_reason = rules.determine_mode(profile, proxy)
    yield Event("mode", {"mode": mode, "reason": mode_reason})
    yield stage("DETERMINE_MODE", done=True, mode=mode)

    # 7. DETECT_CONFLICTS
    yield stage("DETECT_CONFLICTS")
    conflicts = _detect_conflicts(profile)
    for c in conflicts:
        yield Event("conflict", {"description": c})
    yield stage("DETECT_CONFLICTS", done=True, count=len(conflicts))

    # 8. RULE_VERDICT - the verdict of record, because it is reproducible.
    yield stage("RULE_VERDICT")
    abstention = rules.check_abstention(
        profile,
        proxy,
        gene_resolved=target is not None,
        condition_resolved=disease is not None,
        undeclared_substitution=undeclared,
        requested_condition=search_term or condition,
        resolved_condition=disease.name if disease else "",
    )
    rule_v = rules.rule_verdict(profile, proxy, mode, abstention)
    # The provider answered the outcome question; the rule engine derived a verdict from
    # what it found. Carrying the state through means "untested" and "tested and silent"
    # stay different on the wire, which is the whole point of having three states.
    rule_v.outcome_state = outcome.state
    rule_v.outcome_trials = list(outcome.trials)
    rule_v.outcome_path = outcome.path
    rule_v.outcome_reason = outcome.reason
    # Three providers exist and they disagree. An output that does not name the one that
    # produced it can be quoted as the rebuild's answer when it came from the curated file,
    # so the name travels with the verdict and is rendered rather than living in a doc.
    rule_v.outcome_provider = outcome.provider
    # The split, unconditionally, and what the cap set aside. Both are required renders.
    rule_v.outcome_consensus = outcome.consensus
    rule_v.outcome_split = outcome.split
    rule_v.outcome_minority = list(outcome.minority)
    rule_v.outcome_excluded = list(outcome.excluded)
    rule_v.outcome_measured_disease = outcome.measured_disease
    rule_v.outcome_borrowed = outcome.borrowed
    yield Event("rule_verdict", rule_v.model_dump())
    yield stage("RULE_VERDICT", done=True)

    # 8b. REACHABILITY - a gate, not a verdict field. Computed after the verdict and given
    #     none of it, so it cannot bend a causal call toward a commercial one.
    yield stage("REACHABILITY")
    reachability = None
    if target is not None:
        hpa_record = await hpa.fetch(target.ensembl_id) if hpa is not None else None
        tract = await ot.tractability([target.ensembl_id])
        reachability = assess_reachability(
            hpa_record,
            tract.get(target.ensembl_id),
            tractability_assessed=target.ensembl_id in tract,
        )
        yield Event("reachability", reachability.model_dump())
    yield stage(
        "REACHABILITY", done=True,
        verdict=reachability.verdict if reachability else None,
    )

    # 9/10. MODEL_ADJUDICATE + RECONCILE - independent second read, never merged in.
    model_v: Verdict | None = None
    agreement: bool | None = None
    yield stage("MODEL_ADJUDICATE")
    if adjudicator is not None:
        packet = {
            "gene": gene,
            "condition": condition,
            "mode": mode,
            "proxy": proxy.model_dump() if proxy else None,
            "tiers": {k: [i.model_dump() for i in v] for k, v in profile.tiers.items()},
            "checked_and_empty": profile.checked_and_empty,
            "could_not_check": profile.could_not_check,
        }
        model_v = await adjudicator.adjudicate(packet)
    yield stage("MODEL_ADJUDICATE", done=True, ran=adjudicator is not None)

    yield stage("RECONCILE")
    if model_v is not None:
        agreement = (
            model_v.position == rule_v.position
            and model_v.targetability == rule_v.targetability
        )
        yield Event(
            "agreement",
            {
                "agreement": agreement,
                "rule": [rule_v.position, rule_v.targetability],
                "model": [model_v.position, model_v.targetability],
                "note": (
                    "Both are shown. A disagreement is a signal about the case,  to "
                    "resolve, and the rule verdict remains the verdict of record because it is "
                    "reproducible."
                    if agreement is False
                    else None
                ),
            },
        )
    yield stage("RECONCILE", done=True)

    # 11. SELECT_EXPERIMENT - templated lookup, ODDITY assays only.
    yield stage("SELECT_EXPERIMENT")
    experiment = config.experiment_for(_select_gap(profile, condition))
    if experiment is not None:
        yield Event("experiment", experiment.model_dump())
    yield stage("SELECT_EXPERIMENT", done=True)

    # 12. RENDER
    yield stage("RENDER")
    limitations: list[str] = []
    if proxy is not None and proxy.borrow_type == "DISEASE_BORROW":
        limitations.append(f"Evidence is borrowed from {proxy.borrowed_from}. "
                           f"{proxy.what_it_misses.strip()}")
    if proxy is not None and proxy.population_caveat:
        limitations.append(proxy.population_caveat.strip())
    if profile.directional_tier_1_asserted_only:
        limitations.append(
            "The clinical evidence here was asserted from a curated file rather than "
            "retrieved, so confidence is capped at Moderate."
        )
    limitations.extend(profile.could_not_check)

    assessment = Assessment(
        gene=gene,
        ensembl_id=target.ensembl_id if target else None,
        condition_as_typed=condition,
        resolved_disease_id=disease.id if disease else None,
        resolved_disease_name=disease.name if disease else None,
        term_substituted=undeclared,
        mode=mode,
        mode_reason=mode_reason,
        proxy=proxy,
        tier_profile=profile,
        conflicts=conflicts,
        rule_verdict=rule_v,
        model_verdict=model_v,
        agreement=agreement,
        final_verdict=rule_v,
        limitations=limitations,
        resolving_experiment=experiment,
        reachability=reachability,
        # Both registers come from the one adjudication call, so they describe the same
        # read. They are the MODEL's read, not the rule verdict, and the UI labels them
        # that way; on disagreement the dual-read section already shows both calls.
        text_technical=model_v.text_technical if model_v else "",
        text_plain=model_v.text_plain if model_v else "",
        adjudicator_is_stub=bool(getattr(adjudicator, "is_stub", True)),
        data_version=data_version,
        assessed_at=_now(),
    )
    yield Event("assessment", assessment.model_dump())
    yield stage("RENDER", done=True)
    yield Event("done", {"gene": gene, "condition": condition})

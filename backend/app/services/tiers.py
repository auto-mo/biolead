"""Map Open Targets evidence onto the tier hierarchy.

THE ASSOCIATION ROUTE IS PRIMARY, NOT THE ROW-LEVEL `evidences()` ROUTE. Against live data
the row-level route is the unstable one:

    AR x androgenetic alopecia
      association route : genetic_association 0.60, clinical 0.46, literature 0.32
      evidence route    : 0 rows

The row-level route is the unstable one. If tier mapping depended on it, the core positive
case in the whole evaluation set would silently produce an empty profile and abstain.

So the ASSOCIATION route is primary for tiering, because it is stable and its datatype
scores map cleanly onto tiers. The row-level route is ENRICHMENT: it supplies datasource
granularity for citation and display. When it is unavailable the profile is still complete,
and the assessment records that its provenance is degraded.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.clients.open_targets import EvidenceLookup
from app.models.contracts import EvidenceItem, TierProfile

# Open Targets datatype id -> our tier. Ordering of the hierarchy lives in tiers.yaml;
# this is only the mapping.
DATATYPE_TO_TIER: dict[str, str] = {
    "clinical": "1a",              # known drug / clinical precedent, retrieved
    "genetic_association": "2",
    "genetic_literature": "2",
    "known_drug": "1a",
    "somatic_mutation": "3",
    "affected_pathway": "3",
    "rna_expression": "5",
    "literature": "5",
    "animal_model": "4",
}

# Which direction a populated datatype pushes. Tier 5 is INVERTED: heavy literature with no
# genetics is the passenger fingerprint, so it never supports an upstream call.
DATATYPE_SUPPORTS: dict[str, str] = {
    # PRECEDENCE, NOT OUTCOME. Open Targets' clinical datatype means a drug against this
    # target reached clinical development for this disease. It does not say the drug worked,
    # and it happily counts failures: for IL1RL2 x acne it is picking up imsidolimab, the
    # trial that missed its primary endpoint. Treating precedence as efficacy would be the
    # fire-truck error applied to drug programmes instead of to expression.
    "clinical": "NEUTRAL",
    "known_drug": "NEUTRAL",
    "genetic_association": "UPSTREAM",
    "genetic_literature": "UPSTREAM",
    "somatic_mutation": "NEUTRAL",
    "affected_pathway": "NEUTRAL",
    "animal_model": "NEUTRAL",
    "rna_expression": "DOWNSTREAM",
    "literature": "DOWNSTREAM",
}

# Below this an Open Targets datatype score is treated as noise rather than evidence.
SCORE_FLOOR = 0.05

# ...EXCEPT for tier 5, which has no floor, because tier 5 is an INVERTED signal.
#
# Found by running PTGDR2 x androgenetic alopecia. Open Targets scores its literature at
# 0.04, below the floor, so the fire truck was being discarded as noise -- while Europe PMC
# returns 239 papers for "PGD2 AND alopecia" and the nominating paper has 202 citations.
#
# The lesson is about the score, not the floor: Open Targets' literature datatype score is
# NOT a measure of literature volume. For a normal tier a low score means weak evidence and
# filtering it is right. For tier 5 the meaningful fact is "there is literature here and
# nothing else", and a low score does not weaken that reading at all.
#
# The proper source for tier 5 volume and trajectory is Europe PMC with its year facet
# (verified working: MMP1 x skin aging runs 16 papers in 2005 to 206 in 2024). That client
# is not built yet, so the floor exemption is the interim behaviour rather than a
# workaround.
NO_FLOOR_TIERS = {"5"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def map_lookup_to_profile(
    lookup: EvidenceLookup,
    *,
    data_version: str,
    source_label: str = "open_targets",
) -> TierProfile:
    """Build a TierProfile from one reconciled gene x disease lookup."""
    profile = TierProfile()

    if lookup.status == "COULD_NOT_CHECK" and not lookup.datatype_scores:
        # Nothing usable from either route.
        profile.could_not_check.append(f"{source_label}: {lookup.note or 'unavailable'}")
        return profile

    if lookup.status == "CHECKED_AND_EMPTY":
        profile.checked_and_empty.append(
            f"{source_label}: both routes agree there is no association"
        )
        return profile

    # FOUND, or COULD_NOT_CHECK with usable association scores. Tier from the stable route.
    row_counts: dict[str, int] = {}
    for row in lookup.rows:
        row_counts[row.get("datatypeId", "")] = row_counts.get(row.get("datatypeId", ""), 0) + 1

    for datatype, score in sorted(lookup.datatype_scores.items(), key=lambda kv: -kv[1]):
        tier_for_floor = DATATYPE_TO_TIER.get(datatype)
        if score < SCORE_FLOOR and tier_for_floor not in NO_FLOOR_TIERS:
            profile.checked_and_empty.append(
                f"{source_label}/{datatype}: present but below the {SCORE_FLOOR} floor "
                f"(score {score:.3f})"
            )
            continue

        tier = DATATYPE_TO_TIER.get(datatype)
        if tier is None:
            continue

        n = row_counts.get(datatype, 0)
        detail = f"{n} supporting row(s)" if n else "row detail unavailable"
        profile.tiers.setdefault(tier, []).append(
            EvidenceItem(
                tier=tier,
                source=source_label,
                datasource_id=datatype,
                summary=f"{datatype.replace('_', ' ')} score {score:.2f} ({detail})",
                raw={"datatype": datatype, "score": score, "row_count": n},
                supports=DATATYPE_SUPPORTS.get(datatype, "NEUTRAL"),
                provenance="RETRIEVED",
                retrieved_at=_now(),
                data_version=data_version,
            )
        )

    # Record degraded provenance explicitly. The tiers are populated, but the citation-level
    # detail is missing, and the user should be told which of those two things is true.
    if lookup.status == "COULD_NOT_CHECK":
        profile.could_not_check.append(
            f"{source_label}/row-detail: {lookup.note or 'evidence route returned nothing'}"
        )

    # Absent datatypes are a finding, not silence. Only assert this when at least one route
    # actually answered.
    for datatype, tier in (("genetic_association", "2"), ("clinical", "1a")):
        if datatype not in lookup.datatype_scores:
            profile.checked_and_empty.append(
                f"{source_label}/{datatype}: checked, no association reported (tier {tier})"
            )

    return profile


def add_clinical_facts(
    profile: TierProfile, facts: list, trial_records: dict | None = None
) -> TierProfile:
    """Fold curated tier 1b facts into a profile, marked ASSERTED.

    Only facts that survived the loader's sources rule ever reach here.
    """
    for fact in facts:
        supports = {
            "TARGETABILITY_NEGATIVE": "TARGETABILITY_NEGATIVE",
            "TARGETABILITY_POSITIVE": "TARGETABILITY_POSITIVE",
            "TARGETABILITY_UNKNOWN": "TARGETABILITY_UNKNOWN",
        }.get(fact.direction, "NEUTRAL")

        rec = (trial_records or {}).get(fact.nct_id) if fact.nct_id else None
        verified_trial = bool(rec and rec.usable_as_tier_1a)
        tier = "1a" if verified_trial else "1b"
        if fact.nct_id and not verified_trial:
            profile.could_not_check.append(
                f"clinicaltrials/{fact.nct_id}: named by a curated fact but could not be "
                f"verified as a completed trial with posted results"
                + (f" ({rec.error})" if rec and rec.error else "")
                + ". Evidence degraded to asserted."
            )
        profile.tiers.setdefault(tier, []).append(
            EvidenceItem(
                tier=tier,
                source=("clinicaltrials_gov" if verified_trial else "curated_clinical_facts"),
                datasource_id=fact.id,
                summary=fact.claim.strip(),
                # nct_id travels with the item so the renderer can link the trial. Without
                # it the row that earns an ACTIONABLE call cites a trial the reader cannot
                # reach, because the claim text does not always name the identifier.
                raw={"sources": fact.sources, "caveats": fact.caveats,
                     "nct_id": fact.nct_id},
                supports=supports,
                provenance=("RETRIEVED" if verified_trial else "ASSERTED"),
                retrieved_at=None,
                data_version=None,
            )
        )
    return profile

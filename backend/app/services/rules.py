"""The rule engine. Pure functions, no I/O, fully testable in isolation.

This is the verdict of record, because it is reproducible. The model adjudicates separately
and its answer is carried alongside, never merged in.

BOTH verdict fields are derived here:

  position      - upstream vs downstream, from the tier profile. Germline variation exists
                  before the phenotype does, so tier 2 presence is temporal evidence of
                  upstream position. Its absence alongside tier 3/5 evidence supports
                  downstream.
  targetability - essentially a direct readout of tier 1.

No curated component, no model input, no provenance flag. A third position value splitting
DOWNSTREAM by pathway redundancy was designed and cut: it is not derivable, and it would
have reintroduced the only non-deterministic path in the verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.contracts import Confidence, Mode, ProxyRow, TierProfile, Verdict

_CONFIDENCE_ORDER: list[Confidence] = ["LOW", "MODERATE", "HIGH"]


def _cap(current: Confidence, ceiling: Confidence) -> Confidence:
    return _CONFIDENCE_ORDER[min(_CONFIDENCE_ORDER.index(current), _CONFIDENCE_ORDER.index(ceiling))]


@dataclass
class Abstention:
    trigger: int
    name: str
    explanation: str


# --------------------------------------------------------------------------------------
# Mode
# --------------------------------------------------------------------------------------


def determine_mode(profile: TierProfile, proxy: ProxyRow | None) -> tuple[Mode, str]:
    """Genetics mode requires qualifying genetic evidence AND a proxy that is not LOW.

    A LOW-rated proxy routes to mechanism mode even when it returns data. Low-rated
    genetics is worse than no genetics, because it carries the authority of genetics
    without the applicability.
    """
    if not profile.has("2"):
        return "MECHANISM", "No qualifying human genetic evidence returned for this endpoint."

    if (proxy is not None and proxy.borrow_type == "DISEASE_BORROW"
            and proxy.rating in ("LOW", "NONE")):
        return (
            "MECHANISM",
            f"Genetic evidence returned, but it is borrowed from {proxy.borrowed_from} at a "
            f"{proxy.rating} rating. Low-rated genetics carries the authority of genetics "
            f"without the applicability, so it does not qualify for genetics mode.",
        )

    if proxy is not None and proxy.borrow_type == "DISEASE_BORROW":
        return (
            "GENETICS",
            f"Human genetic evidence available via a {proxy.rating}-rated borrow from "
            f"{proxy.borrowed_from}.",
        )

    return "GENETICS", "Human genetic evidence available for the endpoint itself."


# --------------------------------------------------------------------------------------
# Abstention. Seven triggers, evaluated after the tier 1 check.
# --------------------------------------------------------------------------------------


def check_abstention(
    profile: TierProfile,
    proxy: ProxyRow | None,
    *,
    gene_resolved: bool,
    condition_resolved: bool,
    undeclared_substitution: bool,
    requested_condition: str = "",
    resolved_condition: str = "",
) -> Abstention | None:
    """Return the first firing trigger, or None.

    Tier 1 evidence is mode-independent and takes precedence over triggers 1 and 2: a
    target with a human intervention outcome can be assessed even with no usable proxy.
    """
    if not gene_resolved:
        return Abstention(
            4,
            "gene_unresolved",
            "The gene symbol did not resolve to an exact match. Symbol search is ranked "
            "rather than exact, so accepting the top hit would risk answering about a "
            "different gene.",
        )

    if undeclared_substitution:
        return Abstention(
            7,
            "condition_substituted",
            f"The source answered about {resolved_condition!r} when asked about "
            f"{requested_condition!r}, and that substitution is not declared in the proxy "
            f"table. Any evidence returned describes a different disease.",
        )

    if not condition_resolved:
        return Abstention(
            1,
            "condition_unresolved",
            "The condition did not resolve to any term in the source, and no curated proxy "
            "supplies one.",
        )

    has_tier_1 = profile.has("1a") or profile.has("1b")

    if proxy is not None and proxy.refuse and not has_tier_1:
        return Abstention(
            2,
            "proxy_refused",
            f"The only available borrow is from {proxy.borrowed_from}, which the curated "
            f"table marks as one to refuse. {proxy.what_it_misses.strip()}",
        )

    if not has_tier_1:
        if proxy is None:
            return Abstention(
                1,
                "no_proxy",
                "No curated proxy exists for this endpoint and no clinical outcome evidence "
                "is available.",
            )
        if proxy.borrow_type == "NONE":   # NO_BORROW_NEEDED excluded
            return Abstention(
                1,
                "no_proxy",
                f"No defensible borrow exists for this endpoint. {proxy.rationale.strip()}",
            )

    # Trigger 6 outranks trigger 5: if a tier the verdict would have rested on could not be
    # read, that is not the same as it being absent.
    decisive_missing = profile.could_not_check and not (profile.has("2") or has_tier_1)
    if decisive_missing:
        return Abstention(
            6,
            "source_degraded",
            "A source that the verdict would have depended on could not be read: "
            + "; ".join(profile.could_not_check),
        )

    only_tier_5 = profile.has("5") and not any(profile.has(t) for t in ("1a", "1b", "2", "3", "4"))
    if only_tier_5:
        return Abstention(
            5,
            "literature_only",
            "The only evidence available is expression correlation and literature volume. "
            "That establishes the gene is involved, which is the starting question rather "
            "than the answer, and heavy literature with absent genetics is the classic "
            "passenger fingerprint.",
        )

    if not profile.tiers:
        return Abstention(
            5,
            "no_evidence",
            "No evidence was returned at any tier.",
        )

    return None


# --------------------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------------------


def derive_position(profile: TierProfile) -> tuple[str, str]:
    if profile.has("2"):
        return (
            "UPSTREAM_DRIVER",
            "POS_UPSTREAM_GENETIC",
        )
    if profile.has("3") or profile.has("5"):
        return "DOWNSTREAM", "POS_DOWNSTREAM_NO_GENETICS"
    return "INSUFFICIENT", "POS_INSUFFICIENT"


def derive_targetability(profile: TierProfile) -> tuple[str, str]:
    pos, neg = profile.tier_1_positive, profile.tier_1_negative
    if pos and neg:
        # Do not pick. A failed trial can be wrong modality, dose, population or endpoint
        # rather than a wrong target, and a success can be off-target. The conflict is
        # surfaced to the user instead of being resolved silently.
        return "UNKNOWN", "TGT_CONFLICT_UNRESOLVED"
    if pos:
        return "ACTIONABLE", "TGT_ACTIONABLE"
    if neg:
        return "NOT_ACTIONABLE", "TGT_NOT_ACTIONABLE"
    return "UNKNOWN", "TGT_UNKNOWN"


def derive_confidence(
    profile: TierProfile, proxy: ProxyRow | None, mode: Mode
) -> tuple[Confidence, list[str]]:
    """Start from evidence breadth, then apply every cap that applies. Caps are recorded."""
    applied: list[str] = []

    if (profile.has_directional_tier_1 or profile.has("2")) and (
        profile.has("3") or profile.has_directional_tier_1):
        conf: Confidence = "HIGH"
    elif profile.has("2") or profile.has("1a") or profile.has("1b"):
        conf = "MODERATE"
    else:
        conf = "LOW"

    if mode == "MECHANISM" and not profile.has_directional_tier_1:
        new = _cap(conf, "MODERATE")
        if new != conf:
            applied.append("mechanism mode caps at MODERATE: the natural experiment human "
                          "genetics provides was never run")
            conf = new

    if profile.directional_tier_1_asserted_only:
        new = _cap(conf, "MODERATE")
        if new != conf:
            applied.append("tier 1b only: the clinical evidence was asserted from a curated "
                          "file rather than retrieved, and asserted evidence cannot reach HIGH")
            conf = new

    # The borrow cap applies whether or not directional tier 1 evidence exists.
    #
    # It used to carry `and not profile.has_directional_tier_1`, which lifted the cap the
    # moment a human outcome appeared. That was safe only while outcomes came from a curated
    # file keyed to the endpoint. They do not: NCT01231607 measured androgenetic alopecia and
    # NCT02781311 measured it too, so the old condition removed the cap at exactly the moment
    # the borrow was being crossed. A borrowed outcome may not lift a cap and may not reach
    # HIGH.
    if proxy is not None and proxy.borrow_type == "DISEASE_BORROW":
        ceiling: Confidence = "LOW" if proxy.rating in ("LOW", "MODERATE_LOW") else "MODERATE"
        new = _cap(conf, ceiling)
        if new != conf:
            applied.append(
                f"evidence is borrowed from {proxy.borrowed_from} at a {proxy.rating} rating, "
                f"which caps confidence at {ceiling}"
            )
            conf = new

    return conf, applied


def rule_verdict(
    profile: TierProfile,
    proxy: ProxyRow | None,
    mode: Mode,
    abstention: Abstention | None = None,
) -> Verdict:
    """The verdict of record."""
    if abstention is not None:
        return Verdict(
            position="INSUFFICIENT",
            targetability="UNKNOWN",
            confidence=None,
            rule_fired=f"ABSTAIN_{abstention.trigger}_{abstention.name}",
            reasoning=abstention.explanation,
            cited_tiers=sorted(profile.tiers.keys()),  # type: ignore[arg-type]
        )

    position, pos_rule = derive_position(profile)
    targetability, tgt_rule = derive_targetability(profile)

    if position == "INSUFFICIENT":
        return Verdict(
            position="INSUFFICIENT",
            targetability=targetability,
            confidence=None,
            rule_fired=pos_rule,
            reasoning="The evidence profile does not support a position call.",
            cited_tiers=sorted(profile.tiers.keys()),  # type: ignore[arg-type]
        )

    confidence, caps = derive_confidence(profile, proxy, mode)

    bits = [
        "Tier 2 human genetic evidence is present, which is temporal evidence of upstream "
        "position because germline variation exists before the phenotype does."
        if position == "UPSTREAM_DRIVER"
        else "No tier 2 human genetic evidence, alongside expression or functional evidence, "
        "which places the gene downstream of whatever is driving the process.",
    ]
    if targetability == "ACTIONABLE":
        bits.append("Tier 1 evidence shows modulating the target moved a human endpoint.")
    elif targetability == "NOT_ACTIONABLE":
        bits.append("Tier 1 evidence shows modulating the target did not move the endpoint.")
    else:
        bits.append(
            "No tier 1 human intervention evidence either way, so targetability is unknown "
            "rather than negative. Absence of evidence is not evidence of absence."
        )
    bits.extend(f"Confidence capped: {c}." for c in caps)

    # Name the disease the outcome was measured on, when a directional call was reached
    # through a borrow. The matrix reads this before anyone reads the prose.
    measured_on = None
    if (targetability in ("ACTIONABLE", "NOT_ACTIONABLE")
            and proxy is not None and proxy.borrow_type == "DISEASE_BORROW"):
        measured_on = proxy.borrowed_from

    return Verdict(
        position=position,  # type: ignore[arg-type]
        targetability=targetability,  # type: ignore[arg-type]
        confidence=confidence,
        rule_fired=f"{pos_rule}+{tgt_rule}",
        reasoning=" ".join(bits),
        cited_tiers=sorted(profile.tiers.keys()),  # type: ignore[arg-type]
        outcome_measured_on=measured_on,
    )

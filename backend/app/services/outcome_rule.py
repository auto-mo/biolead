"""The deterministic half of the outcome read.

Where a trial publishes a structured statistical analysis, deciding whether the effect is
distinguishable from no effect is arithmetic. Where it publishes only per-arm numbers, it is
comprehension. This module does the first and refuses the second, and every answer records
which path produced it.

Measured over the 287 completed-with-results-and-comparator trials in the sweep:

    carries a CI or a p-value on a primary outcome   180   62.7%   deterministic
    raw per-arm values only                          107   37.3%   model

THE ASYMMETRY THAT MAKES THIS WORK. Deciding *no benefit* needs no direction. If the
interval covers the null, or p is not significant, the trial did not distinguish the arms,
and that holds whether the measure counts lesions or hairs. Direction is only needed to
separate *benefit* from *worse than comparator*, which is the rarer branch.

That matters because the passenger call is the one the project cares most about getting
right, and it is the branch that needs no inference at all. NCT02998671 reports 1.18 with a
90% interval of 0.79 to 1.81, and NCT02781311 reports p=0.9239. Both resolve here with no
lexicon consulted and no model called.

WHERE IT REFUSES, and the refusal goes to the model rather than to a guess:
  - `nonInferiorityType` set. Crossing the null is the goal in a non-inferiority design, so
    the rule reads backwards.
  - `paramType` not recognisable as a difference or a ratio, so the null value is unknown.
  - A significant effect whose measure has no determinable polarity: mRNA abundance,
    cytokine levels, biomarkers.
  - A significant effect where the sign convention depends on which arm came first and
    `groupIds` does not resolve it.
  - More than one primary outcome disagreeing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

OutcomeRead = Literal["BENEFIT", "NO_BENEFIT", "WORSE", "UNDETERMINED"]
ReadPath = Literal["DETERMINISTIC", "MODEL", "NONE"]

# WHY the rule refused, as a code rather than as prose, because something downstream has to
# act on the difference. Two of these mean THE TRIAL PUBLISHED NO COMPARISON AT ALL, and a
# model asked to read one of those has nothing to read: any answer other than UNDETERMINED
# is a comparison it computed itself. The rest mean a comparison exists and the rule declined
# to interpret it, which is exactly what the model is for.
Refusal = Literal[
    "NO_PRIMARY",          # results posted, no primary outcome measure
    "NO_ANALYSIS",         # per-arm values only. NO COMPARISON PUBLISHED
    "NO_USABLE_STAT",      # an analysis exists but carries no interval or p. NO COMPARISON
    "COPRIMARY_DISAGREE",  # comparisons exist and point different ways
    "NO_POLARITY",         # a real effect, but the measure has no better direction
    "NO_POINT_ESTIMATE",   # a real effect, but no estimate to take a sign from
    "NON_INFERIORITY",     # a comparison exists and the significance rule reads backwards
]

# The refusals where nothing was published to read. A model read on one of these is the
# forbidden operation, and `outcome_graph.validate` rejects it.
NO_COMPARISON_PUBLISHED = ("NO_ANALYSIS", "NO_USABLE_STAT", "NO_PRIMARY")

# A refusal where a comparison EXISTS but cannot support the word benefit. In a
# non-inferiority or equivalence design an interval covering the null is the intended
# result: it means "not worse", never "better". NCT05550337 compares two trifarotene
# formulations and reports a ratio of 0.998 with a 90% interval of [0.927, 1.076]; the model
# read that as BENEFIT. Equivalence between two formulations of the same drug cannot show
# the drug works at all. NO_BENEFIT and UNDETERMINED stay available.
NO_BENEFIT_READ_AVAILABLE = ("NON_INFERIORITY",)

# paramType is free text and arrives in case variants: "LS Mean Difference",
# "Least squares mean difference", "Least Squares Mean Difference". Normalised, then matched
# on substring, because the tail varies ("Mean Difference (Net)", "(Final Values)").
_RATIO = ("odds ratio", "risk ratio", "hazard ratio", "rate ratio", "relative risk",
          "incidence rate ratio", "ratio")
_DIFF = ("difference", "mean difference", "risk difference", "ls mean", "least squares",
         "net change", "change difference")

# Direction. Success-family is checked first and wins outright, because "IGA Success"
# contains both a severity index and a success word and means the second.
_HIGHER_BETTER = re.compile(
    r"\b(success|successful|achiev\w*|clear\w*|improve\w*|respon\w*|reduction|"
    r"hair count|hair growth|hair density|regrowth|resolution)\b", re.I)
_LOWER_BETTER = re.compile(
    r"\b(lesion count\w*|lesion\w*|severity|easi|scorad|iga|isga|index|burden|"
    r"pruritus|itch\w*|symptom score)\b", re.I)
# No inherent polarity. Present in the real title set and must not be guessed at.
_NO_POLARITY = re.compile(
    r"\b(abundance|mrna|expression|concentration|level\w*|biomarker|pharmacokinet\w*|"
    r"auc|cmax|titer|titre|count of cells)\b", re.I)


@dataclass
class OutcomeVerdict:
    read: OutcomeRead
    path: ReadPath
    reason: str
    nct_id: str | None = None
    # Everything the arithmetic used, so a reader can redo it.
    evidence: dict[str, Any] = field(default_factory=dict)
    needs_model: bool = False
    refusal: str | None = None


def _null_value(param_type: str | None) -> float | None:
    if not param_type:
        return None
    p = param_type.strip().lower()
    if any(k in p for k in _RATIO):
        return 1.0
    if any(k in p for k in _DIFF):
        return 0.0
    return None


def _polarity(title: str | None) -> str | None:
    """HIGHER, LOWER, or None when the title does not settle it."""
    t = title or ""
    if _NO_POLARITY.search(t):
        return None
    if _HIGHER_BETTER.search(t):
        return "HIGHER"
    if _LOWER_BETTER.search(t):
        return "LOWER"
    return None


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def _p_significant(raw: Any) -> bool | None:
    """`pValue` is a string and is often '<0.001' or '>0.05'. Never coerced blindly."""
    if raw in (None, ""):
        return None
    s = str(raw).strip().replace(" ", "")
    m = re.match(r"^([<>]=?)?(\d*\.?\d+(?:[eE][-+]?\d+)?)$", s)
    if not m:
        return None
    op, val = m.group(1), float(m.group(2))
    if op in ("<", "<="):
        return val <= 0.05
    if op in (">", ">="):
        return False if val >= 0.05 else None
    return val < 0.05


def read_primary_outcome(nct_id: str, primary_outcomes: list[dict]) -> OutcomeVerdict:
    """Decide from the trial's own published statistics, or hand over."""
    if not primary_outcomes:
        return OutcomeVerdict("UNDETERMINED", "NONE",
                              "The trial posted results with no primary outcome measure.",
                              nct_id, needs_model=False, refusal="NO_PRIMARY")

    reads: list[OutcomeVerdict] = []
    for om in primary_outcomes:
        title = om.get("title")
        for an in om.get("analyses") or []:
            # The field is present on most analyses and usually reads SUPERIORITY, so its
            # mere presence means nothing. Observed values: SUPERIORITY,
            # SUPERIORITY_OR_OTHER, NON_INFERIORITY_OR_EQUIVALENCE. Only the last inverts
            # the rule. Treating presence as truthy abstained on 287 of 287 trials.
            if (an.get("nonInferiorityType") or "").upper().startswith(
                    ("NON_INFERIORITY", "EQUIVALENCE")):
                reads.append(OutcomeVerdict(
                    "UNDETERMINED", "NONE",
                    "Non-inferiority design: an interval covering the null is the intended "
                    "result, so the significance rule reads backwards.",
                    nct_id, needs_model=True, refusal="NON_INFERIORITY"))
                continue

            null = _null_value(an.get("paramType"))
            lo, hi = _num(an.get("ciLowerLimit")), _num(an.get("ciUpperLimit"))
            sig_p = _p_significant(an.get("pValue"))
            ev = {"param_type": an.get("paramType"), "param_value": an.get("paramValue"),
                  "ci": [an.get("ciLowerLimit"), an.get("ciUpperLimit")],
                  "ci_pct": an.get("ciPctValue"), "p_value": an.get("pValue"),
                  "null_value": null, "outcome_title": title}

            excludes_null = None
            if null is not None and lo is not None and hi is not None:
                excludes_null = not (lo <= null <= hi)

            # No effect distinguishable from null. Needs no direction, which is why the
            # passenger call is the branch that never guesses.
            if excludes_null is False or sig_p is False:
                reads.append(OutcomeVerdict(
                    "NO_BENEFIT", "DETERMINISTIC",
                    (f"The {an.get('ciPctValue') or ''}% interval "
                     f"[{an.get('ciLowerLimit')}, {an.get('ciUpperLimit')}] covers the null "
                     f"value {null:g}." if excludes_null is False else
                     f"p = {an.get('pValue')} does not reach 0.05."),
                    nct_id, ev))
                continue

            if excludes_null is True or sig_p is True:
                pol = _polarity(title)
                if pol is None:
                    reads.append(OutcomeVerdict(
                        "UNDETERMINED", "NONE",
                        f"A real effect, but the measure {title!r} does not state which "
                        f"direction is better.", nct_id, ev, needs_model=True,
                        refusal="NO_POLARITY"))
                    continue
                val = _num(an.get("paramValue"))
                if val is None:
                    reads.append(OutcomeVerdict(
                        "UNDETERMINED", "NONE",
                        "A real effect, but no point estimate to take a sign from.",
                        nct_id, ev, needs_model=True, refusal="NO_POINT_ESTIMATE"))
                    continue
                base = null if null is not None else 0.0
                favourable = (val > base) if pol == "HIGHER" else (val < base)
                reads.append(OutcomeVerdict(
                    "BENEFIT" if favourable else "WORSE", "DETERMINISTIC",
                    (f"The effect {an.get('paramValue')} excludes the null value {base:g}, "
                     f"and for {title!r} "
                     f"{'higher' if pol == 'HIGHER' else 'lower'} is better."),
                    nct_id, ev))
                continue

            reads.append(OutcomeVerdict(
                "UNDETERMINED", "NONE",
                "The analysis carries no usable interval or p-value.",
                nct_id, ev, needs_model=True, refusal="NO_USABLE_STAT"))

    decided = [r for r in reads if r.path == "DETERMINISTIC"]
    if not decided:
        if reads:
            return reads[0]
        return OutcomeVerdict(
            "UNDETERMINED", "NONE",
            "No structured analysis on any primary outcome; only per-arm values.",
            nct_id, needs_model=True, refusal="NO_ANALYSIS")

    kinds = {r.read for r in decided}
    if len(kinds) > 1:
        return OutcomeVerdict(
            "UNDETERMINED", "NONE",
            "Primary outcomes disagree: " + ", ".join(sorted(kinds)) + ".",
            nct_id, needs_model=True, refusal="COPRIMARY_DISAGREE")

    # A benefit on any primary is reported as benefit; agreement is already established.
    return decided[0]

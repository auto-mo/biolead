"""What to do when the readable trials on one target disagree.

This is a rule and not a judgment, so it lives here and not in the
agent's head: the model is asked to comprehend trial records, never to decide which of them
wins.

THE SAMPLE THIS RESTS ON, because it belongs next to the rule rather than in a document
nobody opens. Seven skin and hair conditions swept under union attribution: 131
gene-condition pairs, 576 trials, 59 resolving deterministically. Ten pairs carry two or
more readable trials and FIVE OF THOSE DISAGREE. All five are atopic dermatitis, which is
the only condition in the set with a dense modern trial record. **The rule is calibrated on
one disease and generalises on assumption.** `tools/measure_disagreement.py` reproduces the
measurement; `docs/disagreement-pairs.json` is its output.

THE UNIT IS TRIAL COUNT. ENROLMENT CHECKS RATHER THAN WEIGHS. Summed enrolment named the
same winner as trial count on every disagreeing pair, ten of ten before the attribution fix
and five of five after it. Weighting by enrolment would add a mechanism that changes nothing
observable while making the output harder to explain. It is on every record anyway, so
checking it is free, and the two units diverging is the signature of the case nobody has
seen yet: one large trial against several small ones pointing the other way. That case does
not occur anywhere in the measured population, which is a reason to detect it rather than a
reason to assume it away.

THE TWO-THIRDS THRESHOLD IS A CHOSEN PRINCIPLE, NOT A FIGURE READ OFF THE DATA. It means AT
LEAST TWICE AS MANY TRIALS ONE WAY AS THE OTHER, which can be stated before looking at a
single trial. All five observed cases happen to clear it, at 2/3, 2/3, 5/6, 10/11 and 12/14,
and two sit exactly on it. That is a property of the sample and not evidence for the
threshold; had the sample come out otherwise the threshold would be the same and more pairs
would be BALANCED. The first thing it refuses is a 3-2 split, which has not occurred, so the
refusal is untested.

This is also why enrolment carries no threshold of its own. A two-thirds floor on enrolment
share has no meaning: nothing about "two thirds of enrolled patients" says a result is
settled. Its weakest observed case cleared it by 1.3 points, 0.680 against 0.667, which is
coincidence wearing the clothes of calibration. It was dropped for that reason.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

# At least twice as many trials one way as the other.
MAJORITY_FRACTION = 2 / 3

Consensus = Literal["DECIDED", "UNANIMOUS", "BALANCED", "NONE"]


@dataclass
class ConsensusResult:
    status: Consensus
    read: str | None
    # Every trial that did not read the way the verdict did. Never empty when the verdict is
    # DECIDED, and never dropped from the rendering. See `summary`.
    minority: list[dict] = field(default_factory=list)
    majority: list[dict] = field(default_factory=list)
    count_tally: dict[str, int] = field(default_factory=dict)
    enrollment_tally: dict[str, int] = field(default_factory=dict)
    by_count: str | None = None
    by_enrollment: str | None = None
    reason: str = ""

    @property
    def summary(self) -> str:
        """The split, always, including when the majority was taken.

        A majority read never renders alone. Twelve trials reading benefit with two reading
        worse is one fact, not a fact and a footnote, and a reader who is shown only the
        winner cannot see what the rule decided over.
        """
        if not self.count_tally:
            return "No trial resolved."
        parts = ", ".join(f"{n} {read}" for read, n in
                          sorted(self.count_tally.items(), key=lambda kv: -kv[1]))
        total = sum(self.count_tally.values())
        return f"{total} readable trial(s): {parts}."


def _tally(trials: list[dict], key) -> tuple[str | None, dict[str, int]]:
    """Winner under one unit, and the whole tally. None when tied."""
    t: dict[str, int] = defaultdict(int)
    for x in trials:
        t[x["read"]] += key(x)
    if not t:
        return None, {}
    top = max(t.values())
    winners = [k for k, v in t.items() if v == top]
    return (winners[0] if len(winners) == 1 else None), dict(t)


def decide(trials: list[dict[str, Any]]) -> ConsensusResult:
    """Apply the rule to the deterministically-read trials on one gene and condition.

    Each trial is a dict carrying at least `read` and `nct_id`, and `enrollment` where the
    registry published one. A trial with no enrolment figure is excluded from the enrolment
    check rather than counted as zero: treating a missing number as nothing would delete the
    trial from one unit and not the other, which is how the two units come to disagree for a
    reason that has nothing to do with the trials.
    """
    readable = [t for t in trials if t.get("read") in ("BENEFIT", "NO_BENEFIT", "WORSE")]
    if not readable:
        return ConsensusResult("NONE", None, reason="No trial resolved deterministically.")

    by_count, count_tally = _tally(readable, lambda t: 1)
    enrolled = [t for t in readable if t.get("enrollment")]
    by_enrol, enrol_tally = _tally(enrolled, lambda t: int(t["enrollment"]))

    if len(count_tally) == 1:
        read = next(iter(count_tally))
        return ConsensusResult(
            "UNANIMOUS", read, minority=[], majority=readable,
            count_tally=count_tally, enrollment_tally=enrol_tally,
            by_count=by_count, by_enrollment=by_enrol,
            reason=f"All {len(readable)} readable trial(s) read {read}.")

    def split(read: str | None) -> tuple[list, list]:
        return ([t for t in readable if t["read"] == read],
                [t for t in readable if t["read"] != read])

    # The tripwire. Not a weight: the only thing enrolment may do is refuse.
    if by_count is None or by_enrol is None or by_count != by_enrol:
        maj, mino = split(by_count)
        why = ("The trials are evenly split by count."
               if by_count is None else
               "The trials are evenly split by enrolment."
               if by_enrol is None else
               f"Counting trials favours {by_count} and summed enrolment favours "
               f"{by_enrol}. The two units disagree.")
        return ConsensusResult(
            "BALANCED", None, minority=mino, majority=maj,
            count_tally=count_tally, enrollment_tally=enrol_tally,
            by_count=by_count, by_enrollment=by_enrol,
            reason=why + " No verdict is taken from a balanced split.")

    share = count_tally[by_count] / sum(count_tally.values())
    maj, mino = split(by_count)
    if share < MAJORITY_FRACTION:
        return ConsensusResult(
            "BALANCED", None, minority=mino, majority=maj,
            count_tally=count_tally, enrollment_tally=enrol_tally,
            by_count=by_count, by_enrollment=by_enrol,
            reason=(f"{count_tally[by_count]} of {sum(count_tally.values())} trials read "
                    f"{by_count}, short of twice as many one way as the other. "
                    f"No verdict is taken from a balanced split."))

    return ConsensusResult(
        "DECIDED", by_count, minority=mino, majority=maj,
        count_tally=count_tally, enrollment_tally=enrol_tally,
        by_count=by_count, by_enrollment=by_enrol,
        reason=(f"{count_tally[by_count]} of {sum(count_tally.values())} trials read "
                f"{by_count}, and summed enrolment agrees. "
                f"Dissenting: "
                + "; ".join(f"{t['nct_id']} {t['read']} (n={t.get('enrollment') or '?'})"
                            for t in mino) + "."))

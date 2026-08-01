"""The model's independent second read, plus the ablation pass.

Two rules the adjudicator must obey, and both exist to keep the mechanism independent:

  1. It NEVER sees the rule verdict. Seeing it would anchor the model and destroy the
     independence the whole dual-verdict mechanism depends on.
  2. It may not introduce evidence that is not in the packet.

The rule verdict remains the verdict of record because it is reproducible. The model is a
second opinion, and a disagreement is a signal about the case to resolve.

THE ABLATION. The model runs twice: once on the full packet, once on a packet with the
curated proxy ratings and their what-it-misses text stripped out. The difference measures how
much the human judgment layer actually moves the model's call. Without it, "the rules and the
model agreed on every case" is an anecdote; with it, there is a number either way.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from app.core.gate import SPEND_CAP
from app.models.contracts import Verdict

# The demo and the screenshots run on Sonnet. The ablation runs on Haiku: the question there
# is only whether the verdict changed when the curated layer was stripped, and a cheaper model
# answers that as well as an expensive one. Both arms of any single comparison always use the
# same model, so the comparison stays internally valid either way.
MODEL = "claude-sonnet-5"
ABLATION_MODEL = "claude-haiku-4-5"

# `output_config.effort` is rejected on these; sending it is a 400 rather than a no-op.
_NO_EFFORT = ("claude-haiku-4-5", "claude-sonnet-4-5")

SYSTEM = """You are adjudicating whether a gene is a driver or a passenger for a skin or hair \
endpoint, from a fixed evidence packet.

Rules you must follow:
- Use ONLY the evidence in the packet. Do not introduce facts from your own knowledge.
- Cite tiers by their label ("1a", "1b", "2", "3", "4", "5").
- Tier 5 (expression correlation and literature volume) is an INVERTED signal. Heavy \
literature with absent human genetics is the classic passenger fingerprint and must never be \
read as support for a driver call.
- Absence of driver evidence is NOT evidence of passenger status. If the packet does not \
support a call, return INSUFFICIENT / UNKNOWN.
- `could_not_check` means a source failed, which is different from `checked_and_empty`. Never \
treat a source failure as an absence of evidence.

Return position (UPSTREAM_DRIVER | DOWNSTREAM | INSUFFICIENT), targetability (ACTIONABLE | \
NOT_ACTIONABLE | UNKNOWN), confidence (HIGH | MODERATE | LOW, or null when INSUFFICIENT), \
one paragraph of reasoning, the tiers you cited, and the same read written twice:

- `text_technical`: for a target biologist. Two or three sentences. Name the tiers, the \
datatypes and the scores you are relying on. Use the field's own vocabulary.
- `text_plain`: for a product or formulation colleague with no biology background at all. \
THIS ONE HAS TO BE READABLE BY SOMEONE WHO IS NOT A BIOLOGIST, and the usual failure is \
writing the technical version again in slightly softer words. Hard rules: two or three SHORT \
sentences, average under about fifteen words. No tier numbers. No scores or numeric values of \
any kind. No datatype names. No jargon beyond the gene symbol itself: not association, not \
expression, not modulation, not endpoint, not upstream or downstream unless you say plainly \
what they mean. Say what is known, what is not, and what it means for whether to work on this \
gene. If a sentence would need a glossary, rewrite it.

Both must describe the SAME call. They differ in vocabulary, not in content, and neither may \
contain a fact the other omits. Write plainly. Do not use em dashes. Do not tell the reader \
what to conclude about the quality of the analysis."""


def ablate(packet: dict) -> dict:
    """Strip the curated judgment layer, keeping the raw evidence intact.

    Removed: the proxy rating, its rationale, its what-it-misses text and its population
    caveat. Kept: every tier, every score, every source. So the model sees the same
    evidence and none of the human's opinion about how far it travels.
    """
    out = copy.deepcopy(packet)
    if out.get("proxy"):
        for key in ("rating", "rationale", "what_it_misses", "population_caveat", "refuse"):
            out["proxy"].pop(key, None)
        out["proxy"]["_ablated"] = True
    return out


class StubAdjudicator:
    """Deterministic stand-in so the pipeline and eval run with no API key.

    Applies the same tier logic the prompt describes. Useful for wiring, useless as a second
    opinion, and it must never be presented as one: if the stub is in use, the agreement flag
    is meaningless and the eval marks it as such.
    """

    is_stub = True

    async def adjudicate(self, packet: dict) -> Verdict | None:
        tiers = packet.get("tiers", {})
        has = lambda t: bool(tiers.get(t))  # noqa: E731

        def direction(kinds: set[str]) -> bool:
            return any(
                i.get("supports") in kinds for t in ("1a", "1b") for i in tiers.get(t, [])
            )

        pos = "UPSTREAM_DRIVER" if has("2") else (
            "DOWNSTREAM" if (has("3") or has("5")) else "INSUFFICIENT"
        )
        neg, positive = direction({"TARGETABILITY_NEGATIVE"}), direction({"TARGETABILITY_POSITIVE"})
        tgt = "UNKNOWN" if (neg and positive) else (
            "NOT_ACTIONABLE" if neg else ("ACTIONABLE" if positive else "UNKNOWN")
        )
        conf = None if pos == "INSUFFICIENT" else ("HIGH" if (has("2") and has("1a")) else "MODERATE")
        technical, plain = _stub_registers(packet, tiers, pos, tgt, conf)
        return Verdict(
            position=pos, targetability=tgt, confidence=conf,
            rule_fired="STUB", reasoning="Deterministic stub. Not a model opinion.",
            cited_tiers=sorted(tiers.keys()),
            text_technical=technical, text_plain=plain,
        )


def _stub_registers(
    packet: dict, tiers: dict, pos: str, tgt: str, conf: str | None
) -> tuple[str, str]:
    """Both registers, assembled from the packet rather than written by a model.

    Templated on purpose. The stub exists so the dual-register path is exercisable with no
    API key, and templated sentences make it obvious in the UI that no model wrote them.
    """
    gene = packet.get("gene", "the gene")
    condition = packet.get("condition", "this endpoint")
    proxy = packet.get("proxy") or {}
    borrowed = proxy.get("borrowed_from") if proxy.get("borrow_type") == "DISEASE_BORROW" else None

    present = sorted(t for t in ("1a", "1b", "2", "3", "4", "5") if tiers.get(t))
    tech = [
        f"Tiers present for {gene} against {condition}: "
        + (", ".join(present) if present else "none")
        + "."
    ]
    if borrowed:
        tech.append(f"Evidence is borrowed from {borrowed} at rating {proxy.get('rating')}.")
    tech.append(
        f"Position {pos}, modulation outcome {tgt}"
        + (f", confidence {conf}." if conf else ".")
    )
    if packet.get("could_not_check"):
        tech.append("One or more sources could not be read on this run.")

    POS_PLAIN = {
        "UPSTREAM_DRIVER": f"Human genetics places {gene} early in what causes {condition}.",
        "DOWNSTREAM": f"{gene} changes alongside {condition} without evidence that it causes it.",
        "INSUFFICIENT": f"The evidence does not place {gene} in the chain that causes {condition}.",
    }
    TGT_PLAIN = {
        "ACTIONABLE": "Changing it has already moved the endpoint in people.",
        "NOT_ACTIONABLE": "It was changed in people and the endpoint did not move.",
        "UNKNOWN": "Nobody has tested whether changing it moves the endpoint.",
    }
    lay = [POS_PLAIN[pos], TGT_PLAIN[tgt]]
    if borrowed:
        lay.append(
            f"None of this was measured on {condition} directly. It comes from {borrowed}."
        )
    if packet.get("could_not_check"):
        lay.append("Part of the evidence could not be read this run, so some of it is missing.")

    return " ".join(tech), " ".join(lay)


class AnthropicAdjudicator:
    """The real second read."""

    is_stub = False

    def __init__(self, model: str = MODEL):
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic()
        self._model = model
        self._send_effort = not model.startswith(_NO_EFFORT)

    async def adjudicate(self, packet: dict) -> Verdict | None:
        # The daily ceiling is checked before the call, not after. Returning None here
        # degrades the app to the rule verdict alone, which is the verdict of record, so a
        # spent budget costs the second read and never the answer.
        if not SPEND_CAP.allowed():
            return None

        # Note: no temperature. Sampling parameters are rejected on current models, and the
        # determinism claim rests on the rule verdict rather than on the model anyway.
        #
        # max_tokens covers thinking AND response text. Adaptive thinking is on by default
        # on this model, so the 2000 this started at truncated the JSON mid-object and
        # surfaced as a parse error rather than as the cap it was.
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=8000,
                system=SYSTEM,
                messages=[{"role": "user", "content": json.dumps(packet, default=str)}],
                output_config={
                    **({"effort": "high"} if self._send_effort else {}),
                    "format": {
                        "type": "json_schema",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "position": {"type": "string",
                                             "enum": ["UPSTREAM_DRIVER", "DOWNSTREAM",
                                                      "INSUFFICIENT"]},
                                "targetability": {"type": "string",
                                                  "enum": ["ACTIONABLE", "NOT_ACTIONABLE",
                                                           "UNKNOWN"]},
                                # A nullable enum has to be an anyOf. Written as
                                # {"type": ["string","null"], "enum": [..., None]} the API
                                # rejects the schema outright: "Enum value 'HIGH' does not
                                # match declared type".
                                "confidence": {
                                    "anyOf": [
                                        {"type": "string",
                                         "enum": ["HIGH", "MODERATE", "LOW"]},
                                        {"type": "null"},
                                    ]
                                },
                                "reasoning": {"type": "string"},
                                "cited_tiers": {"type": "array", "items": {"type": "string"}},
                                "text_technical": {"type": "string"},
                                "text_plain": {"type": "string"},
                            },
                            "required": ["position", "targetability", "reasoning",
                                         "cited_tiers", "text_technical", "text_plain"],
                            "additionalProperties": False,
                        },
                    },
                },
            )
        except Exception as exc:
            # A parse or transport failure is a caught error with a visible event, never a
            # silent fallback to the rule verdict.
            raise RuntimeError(f"adjudication failed: {type(exc).__name__}: {exc}") from exc

        # Both of these arrive as HTTP 200 with unparseable content. Naming them here means
        # a truncated or declined adjudication reads as what it is rather than as bad JSON.
        if resp.stop_reason == "refusal":
            raise RuntimeError(
                "adjudication refused by the model's safety classifiers "
                f"(category: {getattr(resp.stop_details, 'category', None)})"
            )
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(
                "adjudication hit max_tokens; the structured output is truncated"
            )

        usage = getattr(resp, "usage", None)
        if usage is not None:
            SPEND_CAP.charge(self._model,
                             getattr(usage, "input_tokens", 0) or 0,
                             getattr(usage, "output_tokens", 0) or 0)

        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        data: dict[str, Any] = json.loads(text)
        if data.get("position") == "INSUFFICIENT":
            data["confidence"] = None
        return Verdict(
            position=data["position"],
            targetability=data["targetability"],
            confidence=data.get("confidence"),
            rule_fired=None,
            reasoning=data.get("reasoning", ""),
            cited_tiers=[t for t in data.get("cited_tiers", [])
                         if t in ("1a", "1b", "2", "3", "4", "5")],
            text_technical=data.get("text_technical", ""),
            text_plain=data.get("text_plain", ""),
        )


def _load_env() -> None:
    """Read `biolead/.env` if the key is not already in the environment.

    Hand-parsed rather than pulling in a dependency for four lines. An exported variable
    always wins, so CI or a shell export overrides the file. The file is gitignored and
    chmod 600; it is the only place the key exists on disk.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    path = Path(__file__).resolve().parents[3] / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def build_adjudicator(model: str | None = None):
    """Real adjudicator when a key is present, deterministic stub otherwise.

    `model` lets the eval harness run the ablation on a cheaper model than the demo uses.
    """
    _load_env()
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return AnthropicAdjudicator(model or MODEL)
        except Exception:
            return StubAdjudicator()
    return StubAdjudicator()

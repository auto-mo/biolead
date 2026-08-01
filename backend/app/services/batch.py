"""Batch triage. One condition, many genes.

Why this exists at all. One gene at a time is a lookup. The brief asks for scientists to
spend their time on the leads that matter, and a lead only becomes a lead relative to the
list it came from, so the unit of work is a list being narrowed. A differential expression
list is the list that actually lands on a desk.

TWO DESIGN POSITIONS, both of which cost something.

1. THE VERDICT CODE IS SHARED, NOT REIMPLEMENTED. Batch fetches differently and decides
   identically. `map_lookup_to_profile`, `rules.determine_mode`, `rules.check_abstention`
   and `rules.rule_verdict` are the same functions the single-gene pipeline calls, given
   the same shapes. Only the transport differs: the single-gene path asks the association
   route from the target side, batch asks it from the disease side, and both are the same
   route. `eval/run_eval.py` asserts a gene assessed both ways lands on the same verdict
   rather than trusting this paragraph.

2. NO MODEL ADJUDICATION PER ROW. The rule verdict is already the verdict of record because
   it is reproducible; the model is a second read. At the measured $0.018 per call a 506
   gene list is $9.11 of adjudication to triage a list, and the point of triage is to work
   out which few genes are worth spending on. So batch is deterministic and free, and the
   model read is what you spend on a row once you open it.

WHAT BATCH DOES NOT FETCH. Row-level datasource detail. The single-gene path fetches it as
enrichment for citation display; tiering has come from the association route since the
Phase 4 correction, so its absence changes no verdict. It is recorded as a run-level
limitation rather than repeated onto every row.
"""

from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator

import asyncio

from app.clients.clinicaltrials import ClinicalTrialsClient
from app.clients.hpa import HpaClient
from app.clients.open_targets import EvidenceLookup, OpenTargetsClient
from app.core.cache import CACHE
from app.core.config import Config
from app.core.limits import REGISTRY
from app.models.contracts import (
    BatchResult,
    BatchRow,
    BatchSummary,
    EvidenceClass,
    TierProfile,
)
from app.services import rules
from app.services.pipeline import Event
from app.services.reach import assess_reachability
from app.services.outcome import OutcomeProvider, build_outcome_provider

# Above this many genes the outcome axis runs without the model. See the gate in run_batch.
BATCH_MODEL_LIMIT = 25
from app.services.tiers import map_lookup_to_profile

# A symbol is conservative on purpose. Anything outside this is not sent to the source; it
# is reported back as a line we refused to interpret, which is preferable to guessing that
# `IFI27 (ISG12)` means IFI27.
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-.@_/]{0,31}$")

# Header names that mean "this column holds the gene symbol", lowercased and stripped of
# punctuation. Checked in order; the first match wins.
_GENE_HEADERS = (
    "gene", "genes", "symbol", "genesymbol", "genename", "geneid",
    "hgncsymbol", "id", "name", "feature",
)

MAX_GENES = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", h.strip().lower())


@dataclass
class ParsedList:
    symbols: list[str] = field(default_factory=list)
    input_fields: dict[str, dict[str, str]] = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    detected: str = ""
    truncated_at: int | None = None


def parse_gene_list(text: str) -> ParsedList:
    """Accept what people actually paste.

    A pasted column of symbols, a CSV or TSV with a header, a comma-separated line, a
    spreadsheet column with quotes still on it. The gene column is found by header name
    and falls back to the first column, which is where every differential expression table
    in this project's sources happens to put it.

    Everything refused is reported. A parser that silently drops the lines it did not
    understand would make the denominator of the headline number wrong, and the headline
    number is the whole point.
    """
    out = ParsedList()
    raw = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return out

    lines = [ln for ln in raw.split("\n") if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return out

    # Delimiter. A single line with commas is a comma-separated list, not a one-row CSV.
    sample = "\n".join(lines[:20])
    if "\t" in sample:
        delim, out.detected = "\t", "tab-separated"
    elif "," in sample:
        delim, out.detected = ",", "comma-separated"
    else:
        delim, out.detected = "", "one symbol per line"

    rows: list[list[str]]
    if delim:
        rows = [[c.strip().strip('"').strip("'") for c in r]
                for r in csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)]
    else:
        rows = [[ln.strip().strip('"').strip("'")] for ln in lines]

    header: list[str] | None = None
    col = 0
    if rows and len(rows[0]) > 1:
        normed = [_norm_header(c) for c in rows[0]]
        for want in _GENE_HEADERS:
            if want in normed:
                header, col = rows[0], normed.index(want)
                out.detected += f", header row, gene column {rows[0][col]!r}"
                break
        else:
            # No recognisable header. If the first cell of row 0 is not a plausible symbol
            # it is still a header, just an unfamiliar one, so drop it and use column 0.
            if rows[0] and not _SYMBOL.match(rows[0][0]):
                header = rows[0]
                out.detected += ", unrecognised header row dropped"
    body = rows[1:] if header is not None else rows

    seen: set[str] = set()
    for row in body:
        if col >= len(row):
            out.rejected.append(delim.join(row) if delim else "".join(row))
            continue
        sym = row[col].strip()
        if not sym:
            continue
        if not _SYMBOL.match(sym):
            out.rejected.append(sym)
            continue
        key = sym.upper()
        if key in seen:
            out.duplicates.append(sym)
            continue
        seen.add(key)
        out.symbols.append(sym)
        if header is not None:
            out.input_fields[sym] = {
                header[i].strip(): row[i].strip()
                for i in range(min(len(header), len(row)))
                if i != col and header[i].strip() and row[i].strip()
            }

    if len(out.symbols) > MAX_GENES:
        out.truncated_at = MAX_GENES
        dropped = out.symbols[MAX_GENES:]
        out.symbols = out.symbols[:MAX_GENES]
        out.rejected.extend(dropped)

    return out


# --------------------------------------------------------------------------------------
# Classification and ranking
# --------------------------------------------------------------------------------------

_TIER_1 = ("1a", "1b")


def classify(profile: TierProfile) -> EvidenceClass:
    """Which evidence class a gene falls in. This is what the headline counts."""
    if any(profile.has(t) for t in _TIER_1) or profile.has("2"):
        return "TIER_1_OR_2"
    if profile.has("3") or profile.has("4"):
        return "OTHER_EVIDENCE_ONLY"
    if profile.has("5"):
        return "EXPRESSION_OR_LITERATURE_ONLY"
    if profile.could_not_check:
        return "COULD_NOT_CHECK"
    return "NO_ASSOCIATION"


_POSITION_RANK = {"UPSTREAM_DRIVER": 0, "DOWNSTREAM": 1, "INSUFFICIENT": 2}
_TARGETABILITY_RANK = {"ACTIONABLE": 0, "UNKNOWN": 1, "NOT_ACTIONABLE": 2}
_CONFIDENCE_RANK = {"HIGH": 0, "MODERATE": 1, "LOW": 2, None: 3}


# Reachability is the SECOND sort key, applied inside a verdict band and never across one.
# A reachable passenger must never outrank an unreachable driver: causality decides what is
# worth understanding, reachability decides what is worth formulating, and letting the
# commercial axis reorder the causal one would undo the split it exists beside.
_REACH_RANK = {"REACHABLE": 0, "HARD_TO_REACH": 1, "UNKNOWN": 2, "OUT_OF_REACH": 3}


def sort_key(row: BatchRow):
    """Verdict, then reachability, then confidence, abstentions grouped at the bottom.

    Grouped rather than hidden: an abstention is a statement about the evidence and a
    reader scanning for what to work on next needs to see that the tool declined on a gene
    they care about. Hiding them would make the list look more decided than it is.

    The final term is the strongest genetic association score, which breaks ties inside a
    band without ever crossing one. Nothing from the input file ranks anything: a gene at
    the top of the paper's table by fold change ranks here on its evidence alone.
    """
    group = 1 if row.abstained else 0
    reach = _REACH_RANK.get(row.reachability.verdict if row.reachability else "", 2)
    v = row.verdict
    if v is None:
        return (group, 9, 9, reach, 9, 0.0, row.gene)
    genetic = max(
        (s for k, s in row.datatype_scores.items() if k.startswith("genetic")), default=0.0
    )
    return (
        group,
        _POSITION_RANK.get(v.position, 9),
        _TARGETABILITY_RANK.get(v.targetability, 9),
        reach,
        _CONFIDENCE_RANK.get(v.confidence, 9),
        -genetic,
        row.gene,
    )


# --------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------


async def run_batch(
    symbols: list[str],
    condition: str,
    *,
    config: Config,
    ot: OpenTargetsClient,
    ct: ClinicalTrialsClient | None = None,
    hpa: HpaClient | None = None,
    input_fields: dict[str, dict[str, str]] | None = None,
    source: str | None = None,
    progress_every: int = 25,
    outcome_provider: OutcomeProvider | None = None,
) -> AsyncIterator[Event]:
    """Yield progress events, then one `batch_result`.

    Same event-generator shape as `run_assessment`, for the same reason: the SSE endpoint
    and any harness drain the identical generator, so what is demonstrated is what is
    tested.
    """
    # THE BATCH GATE, ADDED AT STAGE 7 WHEN THE DEFAULT BECAME THE SUBGRAPH.
    #
    # Single-gene and batch must decide identically, so they share a provider. But a 506-gene
    # list through the agent is roughly $64 at the measured $0.126 mean, against a $25 daily
    # ceiling, and hours of calls. Above BATCH_MODEL_LIMIT the same provider runs with the
    # agent switched off: same gathering, same arithmetic, same attribution cap, same
    # disagreement rule, no model.
    #
    # WHAT THAT COSTS IS ON THE ROW, NOT HIDDEN. A gene whose outcome needed a model read of
    # an ambiguous trial comes back undetermined in a large batch rather than guessed, and
    # `limitations` says so. Opening the row runs the full single-gene path.
    gated = outcome_provider is None and len(symbols) > BATCH_MODEL_LIMIT
    if outcome_provider is None:
        outcome_provider = build_outcome_provider("graph-rules" if gated else None)
    started = time.monotonic()
    calls_before = sum(lim.stats.calls for lim in REGISTRY._limiters.values())
    input_fields = input_fields or {}
    limitations: list[str] = []
    if gated:
        limitations.append(
            f"This list is longer than {BATCH_MODEL_LIMIT} genes, so the outcome axis ran "
            f"the arithmetic only. Trials that publish no readable comparison are left "
            f"undetermined here rather than read by a model. Open a row for the full read.")

    data_version = await ot.data_version()
    yield Event("batch_start", {
        "genes": len(symbols), "condition": condition,
        "data_version": data_version, "source": source,
    })

    # 1. Resolve the borrow and the condition ONCE. Both are properties of the condition,
    #    not of any gene, so doing this per gene would be N identical calls to a question
    #    whose answer cannot change inside a run.
    yield Event("batch_stage", {"stage": "RESOLVE_CONDITION", "done": False})
    proxy = config.lookup_proxy(condition)
    search_term = (proxy.search_term if proxy and proxy.search_term else condition)
    disease = await ot.resolve_disease(search_term) if search_term else None
    undeclared = bool(disease) and ot.substitution_occurred(
        search_term, disease.name, proxy.expected_resolved_name if proxy else None
    )
    yield Event("batch_stage", {
        "stage": "RESOLVE_CONDITION", "done": True,
        "searched": search_term,
        "resolved": disease.name if disease else None,
        "resolved_id": disease.id if disease else None,
        "proxy": proxy.display_name if proxy else None,
        "rating": proxy.rating if proxy else None,
        "borrowed_from": proxy.borrowed_from if proxy else None,
        "undeclared_substitution": undeclared,
        "resolved_once_for": len(symbols),
    })

    # 2. Resolve every symbol in one call, then keep the three outcomes apart.
    yield Event("batch_stage", {"stage": "RESOLVE_GENES", "done": False})
    resolution = await ot.resolve_targets_bulk(symbols)
    yield Event("batch_stage", {
        "stage": "RESOLVE_GENES", "done": True,
        "resolved": len(resolution.resolved),
        "unresolved": len(resolution.unresolved),
        "ambiguous": len(resolution.ambiguous),
        "calls": 1,
    })
    if resolution.ambiguous:
        limitations.append(
            f"{len(resolution.ambiguous)} symbol(s) map to more than one Ensembl gene and "
            f"were not assessed: {', '.join(sorted(resolution.ambiguous))}. Choosing one "
            f"would be a guess presented as a resolution."
        )

    # 3. One association call for the whole resolved set.
    yield Event("batch_stage", {"stage": "FETCH_EVIDENCE", "done": False})
    ids = [t.ensembl_id for t in resolution.resolved.values()]
    assoc = None
    if disease is not None and ids:
        assoc = await ot.association_scores_bulk(disease.id, ids)
    yield Event("batch_stage", {
        "stage": "FETCH_EVIDENCE", "done": True,
        "targets_checked": len(ids),
        "targets_with_association": len(assoc.scores) if assoc else 0,
        "trustworthy": bool(assoc and assoc.trustworthy),
        "calls": max(1, -(-len(ids) // 400)) if ids else 0,
    })
    if assoc and not assoc.trustworthy:
        limitations.append(
            "The association fetch returned fewer rows than it reported, so genes may be "
            "counted as having no association when they have one: "
            + "; ".join(assoc.truncated)
        )
    limitations.append(
        "Batch mode reads the association route only. Row-level datasource detail, which "
        "the single-gene view fetches for citation, is not retrieved. Tiering has come "
        "from the association route, so no verdict depends on it."
    )

    # 3b. Reachability inputs. Tractability batches; HPA does not, and its absence of a
    #     batch route is the reason the limiter exists. One request per gene, bounded by
    #     the hpa limiter's concurrency cap and token bucket, gathered rather than serial.
    tract: dict[str, dict[str, list[str]]] = {}
    hpa_records: dict[str, object] = {}
    if ids:
        yield Event("batch_stage", {"stage": "REACHABILITY", "done": False,
                                    "targets": len(ids)})
        tract = await ot.tractability(ids)
        if hpa is not None:
            # asyncio.gather fans out all of them; the limiter is what stops that from
            # being 474 simultaneous requests. Exceptions are returned, not raised, so one
            # unreachable gene cannot abort the run.
            got = await asyncio.gather(
                *(hpa.fetch(i) for i in ids), return_exceptions=True
            )
            hpa_records = {
                i: r for i, r in zip(ids, got) if not isinstance(r, BaseException)
            }
        lim = REGISTRY.get("hpa").stats
        yield Event("batch_stage", {
            "stage": "REACHABILITY", "done": True,
            "tractability_assessed": len(tract),
            "hpa_fetched": len(hpa_records),
            "hpa_calls": lim.calls,
            "hpa_peak_concurrency": lim.max_observed_concurrency,
            "hpa_throttled_seconds": round(lim.throttled_seconds, 2),
        })

    # 4. Per gene: build a profile through the SAME mapper and rule engine as single-gene.
    yield Event("batch_stage", {"stage": "ASSESS", "done": False, "total": len(symbols)})
    rows: list[BatchRow] = []
    counts: dict[str, int] = {}

    for i, symbol in enumerate(symbols, 1):
        target = resolution.resolved.get(symbol)

        if symbol in resolution.ambiguous:
            ids_ = resolution.ambiguous[symbol]
            # Trigger 4 is "the symbol did not resolve to an exact match". Two exact
            # matches is a failure to resolve to ONE, so it belongs to the same trigger
            # under its own name rather than becoming an eighth.
            row = BatchRow(
                gene=symbol, evidence_class="NOT_ASSESSABLE",
                input_fields=input_fields.get(symbol, {}),
                verdict=rules.rule_verdict(
                    TierProfile(), proxy, "MECHANISM",
                    rules.Abstention(
                        4, "symbol_ambiguous",
                        f"The symbol {symbol} maps to {len(ids_)} Ensembl genes "
                        f"({', '.join(ids_)}). Choosing one would be a guess presented "
                        f"as a resolution.",
                    ),
                ),
                mode="MECHANISM",
                note=f"{len(ids_)} Ensembl genes share this symbol.",
            )
        elif target is None:
            # Routed through the real abstention check rather than hand-built, so an
            # unresolved gene lands on the same verdict it would in single-gene mode.
            empty = TierProfile()
            row = BatchRow(
                gene=symbol, evidence_class="NOT_ASSESSABLE",
                input_fields=input_fields.get(symbol, {}),
                verdict=rules.rule_verdict(
                    empty, proxy, "MECHANISM",
                    rules.check_abstention(
                        empty, proxy,
                        gene_resolved=False,
                        condition_resolved=disease is not None,
                        undeclared_substitution=undeclared,
                        requested_condition=search_term or condition,
                        resolved_condition=disease.name if disease else "",
                    ),
                ),
                mode="MECHANISM",
                note="No exact symbol match in the source. Ranked search would risk the wrong gene.",
            )
        else:
            scores = (assoc.scores.get(target.ensembl_id, {}) if assoc else {})
            if assoc is None:
                lookup = EvidenceLookup(
                    "COULD_NOT_CHECK",
                    note="the condition did not resolve, so no evidence route could be queried",
                )
            elif scores:
                lookup = EvidenceLookup("FOUND", datatype_scores=scores, rows=[])
            else:
                lookup = EvidenceLookup(
                    "CHECKED_AND_EMPTY",
                    note="the association route reports no association with this disease",
                )

            profile = map_lookup_to_profile(lookup, data_version=data_version)

            # The outcome axis, through the same provider the single-gene path uses, so
            # the two cannot diverge on the question that decides the modulation call.
            outcome = await outcome_provider.outcome_for(
                symbol, condition, proxy=proxy, config=config, ct=ct
            )
            profile = outcome.apply(profile)

            mode, _ = rules.determine_mode(profile, proxy)
            abstention = rules.check_abstention(
                profile, proxy,
                gene_resolved=True,
                condition_resolved=disease is not None,
                undeclared_substitution=undeclared,
                requested_condition=search_term or condition,
                resolved_condition=disease.name if disease else "",
            )
            verdict = rules.rule_verdict(profile, proxy, mode, abstention)

            row = BatchRow(
                gene=symbol,
                ensembl_id=target.ensembl_id,
                evidence_class=classify(profile),
                verdict=verdict,
                mode=mode,
                tier_counts={k: len(v) for k, v in profile.tiers.items()},
                datatype_scores=scores,
                input_fields=input_fields.get(symbol, {}),
                reachability=assess_reachability(
                    hpa_records.get(target.ensembl_id),  # type: ignore[arg-type]
                    tract.get(target.ensembl_id),
                    tractability_assessed=target.ensembl_id in tract,
                ),
            )

        rows.append(row)
        counts[row.evidence_class] = counts.get(row.evidence_class, 0) + 1
        if i % progress_every == 0 or i == len(symbols):
            yield Event("batch_progress", {
                "done": i, "total": len(symbols), "gene": symbol,
                "counts": dict(counts),
                "elapsed": round(time.monotonic() - started, 2),
            })

    yield Event("batch_stage", {"stage": "ASSESS", "done": True})

    # 5. Rank and summarise.
    rows.sort(key=sort_key)

    reach_counts: dict[str, int] = {}
    for r in rows:
        if r.reachability:
            reach_counts[r.reachability.verdict] = reach_counts.get(r.reachability.verdict, 0) + 1

    summary = BatchSummary(
        condition_as_typed=condition,
        resolved_disease_name=disease.name if disease else None,
        resolved_disease_id=disease.id if disease else None,
        proxy=proxy,
        input_count=len(symbols),
        assessable_count=len(symbols) - counts.get("NOT_ASSESSABLE", 0),
        tier_1_or_2=counts.get("TIER_1_OR_2", 0),
        expression_or_literature_only=counts.get("EXPRESSION_OR_LITERATURE_ONLY", 0),
        no_association=counts.get("NO_ASSOCIATION", 0),
        other_evidence_only=counts.get("OTHER_EVIDENCE_ONLY", 0),
        not_assessable=counts.get("NOT_ASSESSABLE", 0),
        could_not_check=counts.get("COULD_NOT_CHECK", 0),
        reach_reachable=reach_counts.get("REACHABLE", 0),
        reach_hard=reach_counts.get("HARD_TO_REACH", 0),
        reach_out=reach_counts.get("OUT_OF_REACH", 0),
        reach_unknown=reach_counts.get("UNKNOWN", 0),
        counts_trustworthy=bool(assoc and assoc.trustworthy) if ids else False,
        trust_note=(
            None if (assoc and assoc.trustworthy)
            else "The evidence fetch did not complete cleanly; the counts below are a floor."
        ),
    )

    calls = sum(lim.stats.calls for lim in REGISTRY._limiters.values()) - calls_before
    result = BatchResult(
        summary=summary,
        rows=rows,
        source=source,
        limitations=limitations,
        limiter_stats=REGISTRY.snapshot(),
        cache_stats=CACHE.as_dict(),
        call_count=calls,
        elapsed_seconds=round(time.monotonic() - started, 2),
        data_version=data_version,
        assessed_at=_now(),
    )
    yield Event("batch_result", result.model_dump())
    yield Event("done", {"genes": len(symbols), "condition": condition})

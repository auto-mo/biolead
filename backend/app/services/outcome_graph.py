"""The outcome subgraph. Agent, ToolNode, validator, invoked directly.

The one agentic section in an otherwise deterministic spine. Everything outside it is plain
async Python. Everything inside answers one question: DID MODULATING THIS GENE MOVE THIS
ENDPOINT IN HUMANS, AND WHAT DOES THE EVIDENCE LICENSE US TO SAY ABOUT THIS GENE
SPECIFICALLY.

WHAT THE MODEL DOES AND DOES NOT DO. It retrieves, and it comprehends a trial that published
no usable statistic. It does not decide. Three things are taken out of its hands and
computed:

  1. Whether a trial separated its arms, where the trial published an interval or a p-value.
     `outcome_rule.read_primary_outcome`. Arithmetic, ~35% of readable trials.
  2. Whether a result may be attributed to this gene at all. `assess_attribution` plus the
     cap below. A drug whose mechanism record names four kinases cannot answer for one.
  3. Which read wins when trials disagree. `outcome_consensus.decide`. A rule with a stated
     sample, not a feel.

So the model's output is a READ PER TRIAL for the trials arithmetic could not settle, plus
what it could not determine. The verdict is assembled from those reads by code.

THE ATTRIBUTION CAP, which is the thing that makes the union fix bite. Until this node
existed, `assess_attribution` was computed correctly and consumed by nothing, so a
multi-target drug's result still answered for a single gene while being labelled honestly.
Here, a trial reached only through a drug that is not `SOLE_NAMED_TARGET` is EXCLUDED from
the consensus and reported as the reason, per section 3.3 of the revamp plan.

GUARDRAILS, and one of them is not optional. `DEFAULT_RECURSION_LIMIT` is 10007 in
langgraph 1.2.10, not the 25 that older knowledge assumes and not the 1000 the docs claim.
Anyone treating the old default as an implicit runaway guard has no guard at all, so
`recursion_limit` is set explicitly at every invocation and a tool-call cap is enforced in
the router besides.
"""

from __future__ import annotations

import json
import operator
from dataclasses import asdict
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy

from app.core.cache import CACHE
from app.services import outcome_consensus
from app.services.outcome_rule import (
    NO_BENEFIT_READ_AVAILABLE,
    NO_COMPARISON_PUBLISHED,
    read_primary_outcome,
)
from app.services.outcome_tools import (
    assess_attribution,
    find_drugs_for_target,
    read_trials,
    search_trials,
)

# Comprehension of a trial record, not generation. Sonnet reads the app; the caller may pass
# Haiku for a cheaper sweep. Both are exercised.
DEFAULT_MODEL = "claude-sonnet-5"

# Bounds, all explicit. See the module docstring on why the last one is load-bearing.
MAX_TOOL_CALLS = 24
RECURSION_LIMIT = 40
NODE_TIMEOUT_S = 120.0
MAX_VALIDATION_RETRIES = 2

READS = ("BENEFIT", "NO_BENEFIT", "WORSE", "UNDETERMINED")

GOAL = """You read clinical trial results that a deterministic rule could not settle.

Gene: {gene}
Endpoint asked about: {condition}
Disease actually queried: {disease_name}

THE EVIDENCE HAS ALREADY BEEN GATHERED. Every trial reachable for this gene has been found and
fetched, and every trial publishing a usable interval or p-value has already been read by
arithmetic. You are shown ONLY the trials the arithmetic refused, with their published numbers
verbatim. You do not choose which trials matter and you cannot request more; that is settled.

For each trial below, return a read: BENEFIT, NO_BENEFIT, WORSE or UNDETERMINED.

Rules you are held to, and the validator will reject a submission that breaks them.
- Report only the trials shown to you, by their exact identifiers.
- A completed trial that posted no results is UNKNOWN, not failure. Do not read silence as a
  negative result.
- A trial stopped for any reason other than a stated efficacy or futility reason is not efficacy
  evidence. Roughly 82% of stops are operational.
- Read a trial only from what it published. If it reports per-arm numbers with no comparison, say
  UNDETERMINED and say why. Do not compute a difference yourself.
- If you cannot support a read, say UNDETERMINED. That is a real answer here.

THE ENDPOINT AND THE DISEASE MAY DIFFER, AND THAT IS NOT A REASON TO WITHHOLD A READ. Where the
endpoint asked about is not the disease queried, you are reading trials that enrolled and measured
the queried disease. Read them for what they measured. Do not refuse a read because the trial
measured acne lesion counts when the question named oily skin: the difference is recorded on the
record as `measured_disease` and shown to the reader, and it is not yours to absorb by abstaining.
State what the trial measured and let the borrow be visible."""


class OutcomeState(TypedDict):
    gene: str
    condition: str
    disease_id: str
    disease_name: str
    # Everything the tools can reach, gathered deterministically before the model runs.
    gathered: NotRequired[dict]
    messages: Annotated[list, add_messages]
    tool_calls_made: Annotated[int, operator.add]
    input_tokens: Annotated[int, operator.add]
    output_tokens: Annotated[int, operator.add]
    rejections: Annotated[list[str], operator.add]
    submitted: NotRequired[dict]
    outcome: NotRequired[dict]


# ---------------------------------------------------------------------------------------
# Tools. Thin wrappers over the retrieval layer, each returning a record with its own id.
# ---------------------------------------------------------------------------------------


def _build_tools() -> list[StructuredTool]:
    """One tool. The retrieval tools are gone from the agent's reach on purpose.

    They still exist and are still used; `gather_evidence` calls them directly. What changed
    is that the model no longer decides which of their results to look at, because that is
    enumeration and not judgment, and letting it decide produced three different trial sets
    for the same gene on the same day.
    """

    async def _submit(trial_reads: list[dict], could_not_determine: list[str]) -> str:
        return json.dumps({"received": len(trial_reads)})

    return [StructuredTool.from_function(
        coroutine=_submit, name="submit_outcome",
        description="Return your reads. trial_reads is a list of {nct_id, read, why} "
                    "covering the trials shown to you; read is one of BENEFIT, NO_BENEFIT, "
                    "WORSE, UNDETERMINED. could_not_determine is a list of plain statements "
                    "about what you could not establish.")]


def _unresolved_brief(gathered: dict) -> str:
    """The trials the arithmetic refused, with their published numbers verbatim.

    Each carries why the rule refused it. Where the refusal means no comparison was
    published, the brief says so in the record itself rather than only in the system prompt,
    because the instruction has been measured failing at volume and a per-record marker is
    harder to lose in a long prompt than a rule stated once at the top.
    """
    lines = []
    refusals = gathered.get("refusals") or {}
    for nct in gathered["unresolved"]:
        rec = gathered["records"][nct]
        why = refusals.get(nct)
        entry = {
            "nct_id": nct,
            "title": rec.get("title"),
            "status": rec.get("status"),
            "enrollment": rec.get("enrollment"),
            "why_stopped": rec.get("why_stopped"),
            "has_comparator": rec.get("has_comparator"),
            "rule_refused_because": why,
            "primary_outcomes": rec.get("primary_outcomes"),
        }
        if why in NO_COMPARISON_PUBLISHED:
            entry["THE_ONLY_LEGAL_READ_HERE_IS_UNDETERMINED"] = (
                "This trial published no comparison between its arms. Any other read would "
                "be a comparison you computed. Return UNDETERMINED.")
        lines.append(json.dumps(entry, indent=1))
    return "\n\n".join(lines)


_SYNONYMS_Q = """
query D($id:String!){ disease(efoId:$id){ id name synonyms{ relation terms } } }
"""


async def disease_terms(ot, disease_id: str) -> list[str]:
    """The disease's own name plus every ontology synonym, lowercased.

    Used to decide whether a trial found by NAME SEARCH is actually in this disease.
    Drawn from the ontology rather than invented, because the alternative is a hand-written
    matcher and this project has a rule about hand-written substitutes for retrieval.
    """
    version = await ot.data_version()

    async def go() -> list[str]:
        d = (await ot._gql(_SYNONYMS_Q, {"id": disease_id})).get("disease") or {}
        terms = {(d.get("name") or "").strip().lower()}
        for s in d.get("synonyms") or []:
            terms |= {t.strip().lower() for t in (s.get("terms") or []) if t}
        return sorted(t for t in terms if t)

    return await CACHE.get_or_set(("ot.synonyms", version, disease_id), go)


def _in_disease(conditions: list[str], terms: list[str]) -> bool:
    """Does this trial's OWN condition list name this disease?

    HEAD MATCH, not containment, and the difference is a real trial. NCT02421172 is
    "Hidradenitis Suppurativa (Acne Inversa)". A containment test on "acne" accepts it, and
    a CJM-112 search for acne returns it, so the agent read a hidradenitis suppurativa trial
    as evidence about acne. The word `acne` is inside a DIFFERENT disease's name.

    A condition matches when the condition string starts with one of the disease's terms, or
    a term starts with the condition string. "Acne Vulgaris" head-matches "acne"; "Atopic
    Dermatitis" head-matches the synonym "atopic dermatitis"; "Hidradenitis Suppurativa
    (Acne Inversa)" head-matches neither.
    """
    for c in conditions or []:
        cl = "".join(ch for ch in c.lower() if ch.isalnum() or ch.isspace()).strip()
        for t in terms:
            tl = "".join(ch for ch in t.lower() if ch.isalnum() or ch.isspace()).strip()
            if not tl or not cl:
                continue
            if cl.startswith(tl) or tl.startswith(cl):
                return True
    return False


# ---------------------------------------------------------------------------------------
# Gathering. Deterministic, exhaustive, and the agent is not consulted.
# ---------------------------------------------------------------------------------------


async def gather_evidence(ot, http, *, gene: str, disease_id: str,
                          disease_name: str, search_term: str | None = None) -> dict:
    """Every trial the tools can reach for this gene, read, with arithmetic applied.

    THE AGENT USED TO DO THIS AND IT SHOULD NOT HAVE. Measured over three runs of IL4R
    against atopic dermatitis, whose reachable set is 85 trials: the agent cited 40, then
    74, then 72, three different subsets, with no tool-call cap hit and no fetch failure. It
    was choosing when it had seen enough. That made the readable count 7, 14 and 15 and the
    consensus UNANIMOUS once and DECIDED twice, on the same gene, the same day, against the
    same registry.

    Enumerating a database is not judgment. Reading an ambiguous trial is. Only the second
    belongs to the model, so gathering happens here, exhaustively, before the model is
    invoked at all. AHR and TNFRSF4 were already stable across runs for the uninteresting
    reason that their reachable sets are 12 and 16, small enough that the agent took all of
    them; stability that depends on a set being small is not stability.
    """
    # THE TRIAL SEARCH USES THE PROXY'S SEARCH TERM, NOT THE ONTOLOGY'S LABEL, and the two
    # differ. Open Targets resolves acne to the label "acne"; the registry and the pinned
    # snapshot both key on "acne vulgaris". Searching with the ontology label missed the
    # pinned entry entirely, so under an outage IL17A reported NO_DRUG about a gene with a
    # drug and two trials.
    term = (search_term or disease_name)
    drugs = await find_drugs_for_target(ot, symbol=gene, disease_id=disease_id)
    attribution = {
        d.chembl_id: {"attribution": d.attribution, "named_targets": d.named_targets,
                      "drug": d.name, "linked_trials": d.nct_ids}
        for d in drugs
    }
    could_not_check: list[str] = []

    # Both routes, for every drug that could answer for this gene. Open Targets' linking is
    # incomplete and the direct search is unverified, so each is kept with its provenance:
    # the attribution cap treats them differently downstream.
    links: dict[str, dict[str, str]] = {}
    for d in drugs:
        for n in d.nct_ids:
            links.setdefault(n, {})[d.chembl_id] = "indication"
        if d.attribution != "SOLE_NAMED_TARGET":
            # A drug that cannot answer for this gene does not need its trials searched for.
            continue
        try:
            for n in await search_trials(http, drug=d.name, condition=term):
                links.setdefault(n, {}).setdefault(d.chembl_id, "search")
        except Exception as exc:
            # A failed search is not an empty one. Recorded so the eval's degraded-run guard
            # can fail a case whose evidence was gathered over a hole.
            could_not_check.append(
                f"clinicaltrials/search({d.name}): {exc}. Trials may exist unseen.")

    reachable = sorted(links)
    terms = await disease_terms(ot, disease_id)
    records: dict[str, dict] = {}
    reads: dict[str, dict] = {}
    unresolved: list[str] = []
    refusals: dict[str, str | None] = {}
    excluded_wrong_disease: list[dict] = []
    not_under_test: list[dict] = []

    # Batched, every one of them, in id order. No sampling and no early exit.
    for i in range(0, len(reachable), 20):
        chunk = reachable[i : i + 20]
        for rec in await read_trials(http, chunk):
            if not rec.found:
                could_not_check.append(f"clinicaltrials/{rec.nct_id}: "
                                       f"{rec.note or 'could not be read'}")
                continue
            # A trial reached ONLY by name search must be in this disease by its own
            # condition list. Open Targets indication rows are exempt: that curation already
            # says the drug was studied for this disease.
            vias = set(links.get(rec.nct_id, {}).values())
            if vias == {"search"} and not _in_disease(rec.conditions, terms):
                excluded_wrong_disease.append(
                    {"nct_id": rec.nct_id, "conditions": rec.conditions})
                continue

            # THE DRUG MUST BE WHAT THE TRIAL IS TESTING, AND THIS IS CHECKED DURING
            # GATHERING RATHER THAN AT ATTRIBUTION TIME.
            #
            # A name search returns every trial whose record mentions the drug, comparator
            # arms included. Searching finasteride in androgenetic alopecia returns ten
            # trials where finasteride is not the drug under test, among them NCT02781311,
            # which is the setipiprant trial that is PTGDR2's own benchmark case.
            #
            # These used to be fetched, read, admitted as evidence, and then discarded by
            # the attribution cap, which put them in the set-aside list beside genuine
            # multi-target exclusions. They are not the same thing. A multi-target drug is
            # a judgment about what a result licenses; a comparator arm was never a
            # candidate. Dropping them here keeps the set-aside list to exclusions that
            # carry a judgment.
            #
            # The fetch still happens, and cannot be avoided: which arm a drug sits in is
            # only in the trial record. What is avoided is the trial entering the evidence
            # set at all.
            if vias == {"search"}:
                searched = [c for c, via in links[rec.nct_id].items() if via == "search"]
                under_test = [c for c in searched
                              if _names_match(attribution.get(c, {}).get("drug") or "",
                                              rec.experimental_interventions or [])]
                if not under_test:
                    names = ", ".join(
                        str(attribution.get(c, {}).get("drug")) for c in searched)
                    not_under_test.append({
                        "nct_id": rec.nct_id, "drug": names,
                        "experimental_interventions": rec.experimental_interventions,
                    })
                    links.pop(rec.nct_id, None)
                    continue
            payload = {
                "record_id": rec.nct_id, "found": True, "status": rec.status,
                "has_results": rec.has_results, "enrollment": rec.enrollment,
                "phases": rec.phases, "title": rec.brief_title,
                "why_stopped": rec.why_stopped, "has_comparator": rec.has_comparator,
                "provenance": rec.provenance,
                "interventions": rec.interventions,
                "experimental_interventions": rec.experimental_interventions,
                "primary_outcomes": rec.primary_outcomes,
            }
            records[rec.nct_id] = payload
            if not rec.has_results:
                continue
            v = read_primary_outcome(rec.nct_id, rec.primary_outcomes)
            if v.path == "DETERMINISTIC":
                reads[rec.nct_id] = {"read": v.read, "path": "DETERMINISTIC",
                                     "why": v.reason}
            else:
                # The 65% the arithmetic refuses. This, and only this, is what the model is
                # shown, and WHY it was refused travels with it: two of the refusal codes
                # mean the trial published no comparison at all, and a read on one of those
                # would be a comparison the model computed itself.
                unresolved.append(rec.nct_id)
                refusals[rec.nct_id] = v.refusal

    return {"drugs": attribution, "links": links, "records": records, "reads": reads,
            "unresolved": unresolved, "refusals": refusals,
            "reachable": reachable,
            "wrong_disease": excluded_wrong_disease,
            "not_under_test": not_under_test,
            "could_not_check": could_not_check}


# ---------------------------------------------------------------------------------------
# The cited ledger. Now the gathered set, because gathering is deterministic.
# ---------------------------------------------------------------------------------------


def evidence_cited(gathered: dict) -> set[str]:
    """Every record identifier fetched this run.

    Taken from `gather_evidence`, which fetched them, rather than from the transcript. When
    the agent did the gathering this had to be reconstructed from tool messages; now the set
    is known before the model is called, which is also what makes the cite guardrail a
    membership test rather than a parse.
    """
    return set(gathered.get("records", {})) | set(gathered.get("drugs", {}))


def _names_match(drug: str, interventions: list[str]) -> bool:
    """Loose on purpose: registries write 'Finasteride', '1mg Finasteride active', 'P-3074'."""
    d = _norm_drug(drug)
    if not d:
        return False
    return any(d in _norm_drug(iv) or _norm_drug(iv) in d for iv in interventions if iv)


def _norm_drug(name: str) -> str:
    """Letters and digits only.

    ChEMBL writes CJM-112 and the registry writes CJM112. A substring test on the raw
    strings fails on the hyphen and excluded the acne trial that is IL17A's whole case, so
    the punctuation comes out before anything is compared.
    """
    return "".join(ch for ch in (name or "").upper() if ch.isalnum())


# ---------------------------------------------------------------------------------------
# Assembly. The verdict is computed from the reads, never taken from the model whole.
# ---------------------------------------------------------------------------------------


def assemble(state: OutcomeState) -> dict:
    """Turn the gathered evidence plus the model's reads into one outcome record."""
    gathered = state.get("gathered") or {"records": {}, "drugs": {}, "links": {},
                                         "reads": {}, "unresolved": [],
                                         "could_not_check": []}
    submitted = state.get("submitted") or {"trial_reads": [], "could_not_determine": []}
    records = gathered["records"]
    attribution = gathered["drugs"]
    links = gathered["links"]

    model_reads = {r["nct_id"]: r for r in submitted.get("trial_reads", [])
                   if isinstance(r, dict) and r.get("nct_id")}

    trials: list[dict] = []
    excluded: list[dict] = []
    rule_model_disagreements: list[dict] = []
    could_not_check: list[str] = list(gathered.get("could_not_check") or [])

    for nct in sorted(records):
        rec = records[nct]

        # THE ATTRIBUTION CAP. A trial reached only through a drug whose mechanism record
        # names more than this gene cannot license a claim about this gene.
        cids = links.get(nct, {})
        sole, comparator_only = [], []
        for c, via in cids.items():
            if attribution.get(c, {}).get("attribution") != "SOLE_NAMED_TARGET":
                continue
            if via == "search" and not _names_match(
                    attribution.get(c, {}).get("drug") or "",
                    rec.get("experimental_interventions") or []):
                comparator_only.append(c)
                continue
            sole.append(c)

        # DEFAULT DENY. Absence of an attributable link is not permission.
        if not sole:
            multi = [attribution.get(c, {}) for c in cids
                     if attribution.get(c, {}).get("attribution") == "ONE_OF_A_FAMILY"]
            if multi:
                worst = max(multi, key=lambda a: len(a.get("named_targets") or []))
                why = (f"Reached only through {worst.get('drug')}, whose mechanism record "
                       f"names {', '.join(worst.get('named_targets') or [])}. A result on it "
                       f"does not isolate {state['gene']}.")
            elif comparator_only:
                names = ", ".join(
                    str(attribution.get(c, {}).get("drug")) for c in comparator_only)
                worst = attribution.get(comparator_only[0], {})
                why = (f"{names} appears in this trial only as a comparator, not in an arm "
                       f"the trial is testing.")
            else:
                worst = {}
                why = (f"No drug naming {state['gene']} was linked to this trial, so nothing "
                       f"attributes its result to {state['gene']}.")
            excluded.append({"nct_id": nct, "drug": worst.get("drug"),
                             "named_targets": worst.get("named_targets"), "why": why})
            continue

        drug_name = attribution.get(sole[0], {}).get("drug")
        if not rec.get("has_results"):
            trials.append({"nct_id": nct, "read": None, "path": "NONE",
                           "has_results": False,
                           "status": rec.get("status"), "enrollment": rec.get("enrollment"),
                           "drug": drug_name, "title": rec.get("title"),
                           "provenance": rec.get("provenance")})
            continue

        det = gathered["reads"].get(nct)
        mr = model_reads.get(nct)
        if det:
            read, path, why = det["read"], "DETERMINISTIC", det["why"]
            # Instrumented rather than silenced. The rule wins, and how often the model would
            # have said otherwise is a number worth having.
            if mr and mr.get("read") in READS and mr["read"] != read:
                rule_model_disagreements.append(
                    {"nct_id": nct, "rule": read, "model": mr["read"],
                     "model_why": mr.get("why")})
        elif mr and mr.get("read") in READS:
            read, path, why = mr["read"], "MODEL", (mr.get("why") or "")
        else:
            read, path, why = "UNDETERMINED", "NONE", "No read was produced for this trial."

        trials.append({
            "nct_id": nct, "read": None if read == "UNDETERMINED" else read,
            "path": path, "why": why, "has_results": True, "status": rec.get("status"),
            "enrollment": rec.get("enrollment"), "title": rec.get("title"),
            "drug": drug_name, "provenance": rec.get("provenance"),
        })

    consensus = outcome_consensus.decide(trials)

    # A FAILED LOOKUP IS NEVER "NO DRUG EXISTS". `NO_DRUG` is a claim about the world and
    # `could_not_check` says the world could not be read, so the two must not collapse. Under
    # a simulated ClinicalTrials.gov outage this branch reported NO_DRUG for IL17A, a gene
    # with a drug and two trials, which is the project's own documented failure shape
    # appearing in its own assembly.
    if could_not_check and not trials and not excluded:
        state_name, reason = "NOT_ASSESSED", (
            f"The outcome question could not be answered for {state['gene']}: "
            + could_not_check[0])
    elif not attribution:
        state_name, reason = "NO_DRUG", (
            f"No drug in {state['disease_name']} has a mechanism record naming "
            f"{state['gene']}. That is retrieved, not a gap in curation.")
    elif not trials and excluded:
        state_name, reason = "NO_ATTRIBUTABLE_DRUG", (
            f"{len(excluded)} trial(s) were found and none of them can answer for "
            f"{state['gene']}. " + excluded[0]["why"])
    elif not trials:
        state_name, reason = "NO_DRUG", (
            f"{len(attribution)} drug(s) name {state['gene']} and no readable human trial "
            f"in {state['disease_name']} was found for any of them.")
    elif consensus.status in ("DECIDED", "UNANIMOUS", "BALANCED"):
        state_name, reason = "TESTED_REPORTED", consensus.reason
    elif any(t.get("has_results") for t in trials):
        # PUBLISHED BUT UNRESOLVABLE IS NOT UNPUBLISHED.
        posted = [t for t in trials if t.get("has_results")]
        state_name, reason = "TESTED_REPORTED", (
            f"{len(posted)} trial(s) against {state['gene']} posted results and none of "
            f"them could be resolved. "
            + "; ".join(f"{t['nct_id']}: {(t.get('why') or '')[:90]}" for t in posted[:3]))
    elif any((t.get("status") or "").upper() == "COMPLETED" for t in trials):
        state_name, reason = "TESTED_UNREPORTED", (
            f"{sum(1 for t in trials if (t.get('status') or '').upper() == 'COMPLETED')} "
            f"completed trial(s) against {state['gene']} posted nothing at all. "
            f"Tested, and the result was never published.")
    else:
        state_name, reason = "NOT_ASSESSED", "Trials were named but none could be read."

    borrowed = (state["disease_name"] or "").strip().lower() != (
        state["condition"] or "").strip().lower()

    return {"outcome": {
        "state": state_name,
        "read": consensus.read,
        "measured_disease": state["disease_name"],
        "endpoint_asked": state["condition"],
        "borrowed": borrowed,
        "consensus": consensus.status,
        "split": consensus.summary,
        "minority": consensus.minority,
        "count_tally": consensus.count_tally,
        "enrollment_tally": consensus.enrollment_tally,
        "reason": reason,
        "trials": trials,
        "excluded_by_attribution": excluded,
        "rule_model_disagreements": rule_model_disagreements,
        "could_not_determine": submitted.get("could_not_determine", []),
        "could_not_check": could_not_check,
        "evidence_cited": sorted(evidence_cited(gathered)),
        "reachable_trials": len(gathered.get("reachable") or []),
        "unresolved_sent_to_model": len(gathered.get("unresolved") or []),
    }}


# ---------------------------------------------------------------------------------------
# Nodes and routing
# ---------------------------------------------------------------------------------------


def _last_submit(messages: list) -> tuple[dict, str] | None:
    """The last submission and the id of the tool call that carried it."""
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            for tc in (m.tool_calls or []):
                if tc["name"] == "submit_outcome":
                    return (tc.get("args") or {}), tc.get("id") or ""
            return None
    return None


def _last_submit_args(messages: list) -> dict | None:
    got = _last_submit(messages)
    return got[0] if got else None


def _make_gather(ot, http, search_term=None):
    async def gather(state: OutcomeState) -> dict:
        g = await gather_evidence(ot, http, gene=state["gene"],
                                  disease_id=state["disease_id"],
                                  disease_name=state["disease_name"],
                                  search_term=search_term)
        return {"gathered": g}

    return gather


def _make_agent(model, tools):
    bound = model.bind_tools(tools, tool_choice="submit_outcome")

    async def agent(state: OutcomeState) -> dict:
        gathered = state["gathered"]
        prompt = (
            f"{len(gathered['unresolved'])} trial(s) could not be settled by arithmetic. "
            f"Read each one and return a read for it.\n\n"
            + _unresolved_brief(gathered)
        )
        msgs = list(state["messages"]) + [HumanMessage(content=prompt)]
        resp = await bound.ainvoke(msgs)
        u = getattr(resp, "usage_metadata", None) or {}
        return {"messages": [HumanMessage(content=prompt), resp],
                "input_tokens": int(u.get("input_tokens") or 0),
                "output_tokens": int(u.get("output_tokens") or 0)}

    return agent


def validate(state: OutcomeState) -> dict:
    """The cite guardrail, now a membership test against the gathered set.

    A claim naming a trial nobody fetched is rejected, not softened. Gathering is
    deterministic, so the legal set is known before the model is called.
    """
    args = _last_submit_args(state["messages"]) or {}
    gathered = state["gathered"]
    legal = set(gathered["unresolved"])
    refusals = gathered.get("refusals") or {}
    reads = [r for r in (args.get("trial_reads") or []) if isinstance(r, dict)]

    unfetched = [r.get("nct_id") for r in reads if r.get("nct_id") not in legal]
    bad_read = [f"{r.get('nct_id')}={r.get('read')}" for r in reads
                if r.get("read") not in READS]

    # THE PROHIBITION, ENFORCED RATHER THAN INSTRUCTED. Where the rule refused a trial
    # because it published no comparison, the only read available from the record is
    # UNDETERMINED; anything else is a comparison the model computed from per-arm numbers.
    # This was a sentence in the goal statement and nothing else, and it was measured
    # holding at 2 unresolved trials and failing at 75, with RARG returning BENEFIT on
    # reasons like "255 participants with IGA success vs placebo 157".
    computed = [r.get("nct_id") for r in reads
                if refusals.get(r.get("nct_id")) in NO_COMPARISON_PUBLISHED
                and r.get("read") not in (None, "UNDETERMINED")]
    # An equivalence interval covering the null means "not worse", never "better".
    equivalence = [r.get("nct_id") for r in reads
                   if refusals.get(r.get("nct_id")) in NO_BENEFIT_READ_AVAILABLE
                   and r.get("read") == "BENEFIT"]

    if ((unfetched or bad_read or computed or equivalence)
            and len(state.get("rejections", [])) < MAX_VALIDATION_RETRIES):
        problems = []
        if unfetched:
            problems.append(
                f"These are not among the trials you were shown, so no claim may name them: "
                f"{', '.join(str(x) for x in unfetched)}.")
        if bad_read:
            problems.append(
                f"These reads are not one of {', '.join(READS)}: {', '.join(bad_read)}.")
        if computed:
            problems.append(
                f"These trials published no comparison between their arms, so the only read "
                f"available is UNDETERMINED. You returned a directional read, which means you "
                f"computed a comparison the trial did not publish: "
                f"{', '.join(str(x) for x in computed)}.")
        if equivalence:
            problems.append(
                f"These are non-inferiority or equivalence designs. An interval covering the "
                f"null there means not worse, never better, so BENEFIT is not available: "
                f"{', '.join(str(x) for x in equivalence)}.")
        msg = " ".join(problems)
        # THE TOOL CALL MUST BE ANSWERED BEFORE THE REJECTION. ToolNode used to do this;
        # once the retrieval tools were removed the graph went agent -> validate directly,
        # and appending a plain message after an unanswered `tool_use` is a 400 from the
        # API rather than a retry.
        got = _last_submit(state["messages"])
        out = []
        if got and got[1]:
            out.append(ToolMessage(content="rejected", tool_call_id=got[1]))
        out.append(HumanMessage(content="Submission rejected. " + msg))
        return {"messages": out, "rejections": [msg]}

    # Retries exhausted. A read that would have been rejected is FORCED to UNDETERMINED
    # rather than kept: the guardrail must not be defeated by persistence.
    clean = []
    forced: list[str] = []
    for r in reads:
        if r.get("nct_id") not in legal or r.get("read") not in READS:
            continue
        if (refusals.get(r.get("nct_id")) in NO_COMPARISON_PUBLISHED
                and r.get("read") != "UNDETERMINED"):
            forced.append(r["nct_id"])
            clean.append({**r, "read": "UNDETERMINED",
                          "why": "No comparison was published by this trial."})
            continue
        if (refusals.get(r.get("nct_id")) in NO_BENEFIT_READ_AVAILABLE
                and r.get("read") == "BENEFIT"):
            forced.append(r["nct_id"])
            clean.append({**r, "read": "UNDETERMINED",
                          "why": "Equivalence design: an interval covering the null means "
                                 "not worse, not better."})
            continue
        clean.append(r)
    dropped = [r.get("nct_id") for r in reads
               if r.get("nct_id") not in legal or r.get("read") not in READS]
    cnd = list(args.get("could_not_determine") or [])
    if dropped:
        cnd.append(f"Claims naming trials outside the gathered set were dropped: "
                   f"{', '.join(str(d) for d in dropped)}.")
    if forced:
        cnd.append(f"These trials published no comparison, so a directional read was "
                   f"refused and recorded as undetermined: {', '.join(forced)}.")
    return {"submitted": {"trial_reads": clean, "could_not_determine": cnd}}


def _make_route_after_gather(allow_model: bool):
    def route_after_gather(state: OutcomeState) -> Literal["agent", "assemble"]:
        # No ambiguous trial means nothing for the model to read, and the whole verdict
        # comes off arithmetic. That path costs nothing and is the common one.
        #
        # `allow_model=False` is the batch gate. A 506-gene list through the agent is about
        # $64 at the measured mean and hours of calls, against a $25 daily ceiling, so a
        # large list gets the arithmetic and nothing else. What that costs is stated on the
        # row rather than hidden: a batch verdict that would have needed a model read comes
        # back undetermined instead of guessed.
        if not allow_model:
            return "assemble"
        return "agent" if state["gathered"]["unresolved"] else "assemble"

    return route_after_gather


def route_after_agent(state: OutcomeState) -> Literal["validate", "assemble"]:
    return "validate" if _last_submit_args(state["messages"]) is not None else "assemble"


def route_after_validate(state: OutcomeState) -> Literal["agent", "assemble"]:
    return "assemble" if state.get("submitted") is not None else "agent"


def build_graph(model, tools, ot, http, search_term=None, allow_model=True):
    g = StateGraph(OutcomeState)
    g.add_node("gather", _make_gather(ot, http, search_term), timeout=NODE_TIMEOUT_S)
    g.add_node("agent", _make_agent(model, tools), timeout=NODE_TIMEOUT_S,
               # Transport only. A schema rejection is a decision and must never be retried
               # into a different answer.
               retry_policy=RetryPolicy(max_attempts=3,
                                        retry_on=(ConnectionError, TimeoutError)))
    g.add_node("validate", validate)
    g.add_node("assemble", assemble)
    g.add_edge(START, "gather")
    g.add_conditional_edges("gather", _make_route_after_gather(allow_model))
    g.add_conditional_edges("agent", route_after_agent)
    g.add_conditional_edges("validate", route_after_validate)
    g.add_edge("assemble", END)
    return g.compile()


async def run_outcome(
    *, gene: str, condition: str, disease_id: str, disease_name: str,
    ot, http, model_name: str = DEFAULT_MODEL, search_term: str | None = None,
    allow_model: bool = True,
) -> dict:
    """One assessment through the subgraph."""
    from langchain_anthropic import ChatAnthropic

    tools = _build_tools()
    model = ChatAnthropic(model=model_name, max_tokens=8000, timeout=120)
    graph = build_graph(model, tools, ot, http, search_term, allow_model)

    init: OutcomeState = {
        "gene": gene, "condition": condition,
        "disease_id": disease_id, "disease_name": disease_name,
        "messages": [SystemMessage(content=GOAL.format(
            gene=gene, condition=condition, disease_name=disease_name))],
        "tool_calls_made": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "rejections": [],
    }
    # Explicit, because the default is 10007 and would not stop a runaway.
    out = await graph.ainvoke(init, config={"recursion_limit": RECURSION_LIMIT})
    result = out.get("outcome") or {}
    result["rejections"] = out.get("rejections", [])
    result["model"] = model_name
    result["input_tokens"] = out.get("input_tokens", 0)
    result["output_tokens"] = out.get("output_tokens", 0)
    result["tool_calls_made"] = out.get("tool_calls_made", 0)
    result["model_allowed"] = allow_model
    return result

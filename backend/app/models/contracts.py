"""Data contracts for BioLead.

Both verdict fields are DERIVED from the evidence profile. There is no curated or
model-supplied component in the verdict itself; the model adjudicates independently and
its answer is carried alongside, never merged in.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------------------
# Enums, as Literals so they serialise straight to the frontend types.
# --------------------------------------------------------------------------------------

Position = Literal["UPSTREAM_DRIVER", "DOWNSTREAM", "INSUFFICIENT"]
Targetability = Literal["ACTIONABLE", "NOT_ACTIONABLE", "UNKNOWN"]
Confidence = Literal["HIGH", "MODERATE", "LOW"]
Rating = Literal["HIGH", "MODERATE", "MODERATE_LOW", "LOW", "NONE"]
# NO_BORROW_NEEDED and NONE are NOT the same thing, and conflating them made the lead
# demo case abstain. NO_BORROW_NEEDED = the query is against a real disease with its own
# ontology term, so nothing is borrowed. NONE = no defensible borrow exists at all.
BorrowType = Literal["NO_BORROW_NEEDED", "DISEASE_BORROW", "NONE"]
Mode = Literal["GENETICS", "MECHANISM"]
Provenance = Literal["RETRIEVED", "ASSERTED"]
Tier = Literal["1a", "1b", "2", "3", "4", "5"]


class ProxyRow(BaseModel):
    """One row of the curated borrow table.

    `what_it_misses` is rendered verbatim in the output, never paraphrased.
    """

    endpoint: str
    display_name: str
    formal_name: str | None = None   # what a scientist would write
    plain_name: str | None = None    # what a consumer would say
    synonyms: list[str] = Field(default_factory=list)
    borrow_type: BorrowType
    borrowed_from: str | None = None
    search_term: str | None = None
    # What the source is EXPECTED to resolve search_term to. Abstention trigger 7 fires
    # when the actual resolution differs from this, i.e. an UNDECLARED substitution.
    expected_resolved_name: str | None = None
    restricted_to_genes: list[str] | None = None
    rating: Rating
    refuse: bool = False
    rationale: str
    what_it_misses: str
    population_caveat: str | None = None


class EvidenceItem(BaseModel):
    tier: Tier
    source: str
    datasource_id: str | None = None
    summary: str
    raw: dict = Field(default_factory=dict)

    # Which way this single row points. Not the verdict enum: one row
    # supports a direction, it does not make a call.
    supports: Literal[
        "UPSTREAM",
        "DOWNSTREAM",
        "TARGETABILITY_POSITIVE",
        "TARGETABILITY_NEGATIVE",
        "TARGETABILITY_UNKNOWN",
        "NEUTRAL",
    ] = "NEUTRAL"

    provenance: Provenance = "RETRIEVED"
    retrieved_at: str | None = None
    data_version: str | None = None


class TierProfile(BaseModel):
    """What was found, what was checked and empty, and what could not be checked.

    The last two are separate fields on purpose. "We looked and there was nothing" and
    "we could not look" are different findings, and the API cannot reliably tell them
    apart, so the pipeline has to.
    """

    tiers: dict[str, list[EvidenceItem]] = Field(default_factory=dict)
    checked_and_empty: list[str] = Field(default_factory=list)
    could_not_check: list[str] = Field(default_factory=list)

    def has(self, tier: str) -> bool:
        return bool(self.tiers.get(tier))

    @property
    def tier_1_positive(self) -> bool:
        return any(
            i.supports == "TARGETABILITY_POSITIVE"
            for t in ("1a", "1b")
            for i in self.tiers.get(t, [])
        )

    @property
    def tier_1_negative(self) -> bool:
        return any(
            i.supports == "TARGETABILITY_NEGATIVE"
            for t in ("1a", "1b")
            for i in self.tiers.get(t, [])
        )

    @property
    def has_directional_tier_1(self) -> bool:
        """Tier 1 evidence that actually points somewhere.

        Clinical PRECEDENCE sits in 1a but is NEUTRAL: it says a drug programme existed,
        not that it worked. Only directional evidence may lift a confidence cap.
        """
        return self.tier_1_positive or self.tier_1_negative

    @property
    def directional_tier_1_asserted_only(self) -> bool:
        """True when every tier 1 item carrying a direction came from the curated file."""
        retrieved = any(
            i.supports in ("TARGETABILITY_POSITIVE", "TARGETABILITY_NEGATIVE")
            for i in self.tiers.get("1a", [])
        )
        asserted = any(
            i.supports in ("TARGETABILITY_POSITIVE", "TARGETABILITY_NEGATIVE")
            for i in self.tiers.get("1b", [])
        )
        return asserted and not retrieved


class Experiment(BaseModel):
    gap: str
    label: str
    assay: str
    what_a_positive_would_change: str


class Verdict(BaseModel):
    position: Position
    targetability: Targetability
    confidence: Confidence | None = None
    rule_fired: str | None = None
    reasoning: str = ""
    cited_tiers: list[Tier] = Field(default_factory=list)

    # The disease the outcome evidence was actually measured on, when that is not the
    # endpoint asked about.
    #
    # Every directional outcome in the current build crosses a borrow: NCT01231607 measured
    # androgenetic alopecia, not non-clinical hair thinning; NCT02998671 measured acne, not
    # sebum production. Rendering those as a bare "benefit shown" states a result about an
    # endpoint no trial measured. The matrix is read before the prose, so the qualification
    # has to travel as data rather than sit in a paragraph underneath.
    outcome_measured_on: str | None = None

    # Which of the three states of not-knowing this is. See OutcomeState.
    outcome_state: OutcomeState = "NOT_ASSESSED"
    # The trials behind that state, listed so the reader can check the absence themselves.
    outcome_trials: list[OutcomeTrial] = Field(default_factory=list)
    # Which path produced the read: DETERMINISTIC, MODEL, or NONE.
    outcome_path: str | None = None
    outcome_reason: str | None = None

    # WHICH PROVIDER ANSWERED. Three exist and they disagree: `file` is the curated default,
    # `retrieved` applies neither the attribution cap nor the disagreement
    # rule, `graph` is the subgraph and applies both. An output that does not name its
    # provider can be quoted as the rebuild's answer when it is not, so this travels with
    # every verdict and is rendered.
    outcome_provider: str = "file"

    # THE SPLIT, SHOWN UNCONDITIONALLY. Whether the majority was taken or the case abstained,
    # a rule that decides must show what it decided over. `outcome_split` is the tally as a
    # sentence; `outcome_minority` names every dissenting trial with its id and enrolment.
    outcome_consensus: str | None = None
    outcome_split: str | None = None
    outcome_minority: list[OutcomeTrial] = Field(default_factory=list)

    # What the attribution cap set aside, and why.
    outcome_excluded: list[ExcludedTrial] = Field(default_factory=list)

    # The borrow, on the record rather than in prose underneath it.
    outcome_measured_disease: str | None = None
    outcome_borrowed: bool = False

    # The same read written twice, for the two audiences that have to act on it. Produced in
    # ONE call, so the registers cannot drift apart the way two calls would let them.
    text_technical: str = ""
    text_plain: str = ""

    @model_validator(mode="after")
    def _confidence_only_when_called(self) -> "Verdict":
        # Insufficient carries no confidence, because there is nothing to be confident about.
        if self.position == "INSUFFICIENT" and self.confidence is not None:
            raise ValueError("INSUFFICIENT position must not carry a confidence")
        return self


# --------------------------------------------------------------------------------------
# The outcome axis has three states of not-knowing, not one.
# --------------------------------------------------------------------------------------
#
# UNKNOWN used to cover all of them, which made the largest band in the whole retrievable
# set invisible. Of 1,362 trials reachable from drug-linked indication rows, 480 (35%)
# completed and posted nothing. That is the single biggest band, larger than the 287 with a
# readable result, and it is a different fact about the world from "nobody tried".
#
# AR is the case: clascoterone is a genuine androgen receptor antagonist, and both of its
# androgenetic alopecia trials completed with no results posted, n=95 and n=762. Rendering
# that as "untested" is false. Somebody tested it and did not say what happened.
OutcomeState = Literal[
    "NO_DRUG",             # nothing has ever been made against this target here
    "TESTED_UNREPORTED",   # tested in humans, the result was never published
    "TESTED_REPORTED",     # tested, and the trial says what happened
    "NOT_ASSESSED",        # the outcome layer did not run for this query
]


class OutcomeTrial(BaseModel):
    """One trial behind an outcome state, so a reader can confirm it."""

    nct_id: str
    drug: str | None = None
    status: str | None = None
    has_results: bool = False
    enrollment: int | None = None
    title: str | None = None
    # BENEFIT / NO_BENEFIT / WORSE, or None when nothing resolved it. Carried so a minority
    # trial can be rendered saying what it actually said, rather than only that it dissented.
    read: str | None = None
    # DETERMINISTIC, MODEL or NONE.
    path: str | None = None


class ExcludedTrial(BaseModel):
    """A trial the attribution cap set aside, and the reason.

    Rendered, not merely recorded. SRD5A2 rests on finasteride BECAUSE six dutasteride
    trials were excluded, so the exclusion is doing real work in the verdict and a reader who
    cannot see it cannot check the call. Same principle as the proxy table printing
    `what_it_misses` verbatim.
    """

    nct_id: str
    drug: str | None = None
    named_targets: list[str] = Field(default_factory=list)
    why: str = ""


class ClinicalFact(BaseModel):
    """A tier 1b asserted fact. See config/clinical_facts.yaml for the two hard rules."""

    id: str
    gene: str
    condition: str
    # When present, the pipeline FETCHES this trial and the evidence becomes tier 1a
    # retrieved rather than 1b asserted. That is what lets a passenger call reach HIGH.
    nct_id: str | None = None
    claim: str
    evidence_type: str
    direction: str
    strength: Confidence
    retrieved: Literal[False] = False
    verified: bool = False
    verified_on: str | None = None
    sources: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    note: str | None = None


# --------------------------------------------------------------------------------------
# Topical reachability. A GATE, not a verdict field.
# --------------------------------------------------------------------------------------
#
# Its own vocabulary. Nothing here shares a value with Position or
# Targetability, so a reader cannot mistake one for the other and a template cannot
# accidentally render one in the other's slot. The question is commercial and
# formulational; the verdict's question is causal. Keeping the words disjoint is how that
# distinction survives contact with a UI.
Reach = Literal["REACHABLE", "HARD_TO_REACH", "OUT_OF_REACH", "UNKNOWN"]
Depth = Literal["EPIDERMAL", "APPENDAGEAL", "DERMAL"]


class Reachability(BaseModel):
    verdict: Reach
    rule_fired: str

    depth: Depth | None = None
    in_skin: bool | None = None          # None means neither assay answered
    skin_ntpm: float | None = None
    skin_ih: str | None = None
    compartments: dict[str, str] = Field(default_factory=dict)
    subcellular: list[str] = Field(default_factory=list)
    secretome_location: str | None = None

    small_molecule_buckets: list[str] = Field(default_factory=list)
    antibody_buckets: list[str] = Field(default_factory=list)

    # The buckets split by what they actually assert, because they do not all assert the
    # same thing and the renderer must not re-derive that split its own way. Clinical means
    # a molecule exists; structural means one is plausible; location-only asserts where the
    # protein sits and says nothing about tractability at all.
    sm_clinical: list[str] = Field(default_factory=list)
    sm_structural: list[str] = Field(default_factory=list)
    ab_clinical: list[str] = Field(default_factory=list)
    ab_location_only: list[str] = Field(default_factory=list)

    # Three lists rather than one paragraph, because they are read differently: supports
    # and blockers are findings, unknowns are gaps. Collapsing them would let a gap read
    # as a negative, which is the error this project exists to avoid.
    supports: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Batch. One condition, many genes.
# --------------------------------------------------------------------------------------

# What class of evidence exists for a gene, which is the axis the headline number is built
# on. Not the verdict: a gene can be UPSTREAM_DRIVER on tier 2 alone, and the
# question the headline asks is what kind of evidence put it there, not what it concluded.
#
# NO_ASSOCIATION is not a separate kind of thing from EXPRESSION_OR_LITERATURE_ONLY, it is
# the extreme of it. Every gene in an input list is there because its expression changed,
# so a gene with no external association has exactly one piece of evidence behind it: the
# measurement that put it on the list. The two are reported together as the fire-truck
# population and broken apart underneath.
EvidenceClass = Literal[
    "TIER_1_OR_2",                   # human genetics or a human intervention outcome
    "EXPRESSION_OR_LITERATURE_ONLY",  # tier 5 in the source, plus the input measurement
    "NO_ASSOCIATION",                 # checked, nothing at all: the input list is the case
    "OTHER_EVIDENCE_ONLY",            # animal model or pathway annotation only
    "NOT_ASSESSABLE",                 # symbol did not resolve, or resolved two ways
    "COULD_NOT_CHECK",                # a source failed; absence here is not a finding
]


class BatchRow(BaseModel):
    gene: str
    ensembl_id: str | None = None
    evidence_class: EvidenceClass
    verdict: Verdict | None = None
    mode: Mode | None = None
    tier_counts: dict[str, int] = Field(default_factory=dict)
    datatype_scores: dict[str, float] = Field(default_factory=dict)
    # Whatever the input file carried alongside the symbol: log2 fold change, adjusted p.
    # Passed through untouched and never used to rank, so a reader can see a gene at the
    # top of the paper's table sitting at the bottom of this one.
    input_fields: dict[str, str] = Field(default_factory=dict)
    note: str = ""
    # Second sort key in batch, and never part of the verdict.
    reachability: Reachability | None = None

    @property
    def abstained(self) -> bool:
        return self.verdict is None or bool(
            self.verdict.rule_fired and self.verdict.rule_fired.startswith("ABSTAIN_")
        )


class BatchSummary(BaseModel):
    """The headline. One line, two numbers, and the arithmetic that produced them."""

    condition_as_typed: str
    resolved_disease_name: str | None = None
    resolved_disease_id: str | None = None
    proxy: ProxyRow | None = None

    input_count: int = 0
    assessable_count: int = 0

    tier_1_or_2: int = 0
    expression_or_literature_only: int = 0
    no_association: int = 0
    other_evidence_only: int = 0
    not_assessable: int = 0
    could_not_check: int = 0

    @property
    def fire_truck_population(self) -> int:
        """Everything assessable that tier 1 and tier 2 did not reach."""
        return (
            self.expression_or_literature_only
            + self.no_association
            + self.other_evidence_only
        )

    # The reachability gate, counted separately from the causal classes above and never
    # summed with them. A gene appears in exactly one evidence class and exactly one reach
    # bucket, and the two are independent questions about it.
    reach_reachable: int = 0
    reach_hard: int = 0
    reach_out: int = 0
    reach_unknown: int = 0

    # Set false when a source truncated or failed in a way that makes the counts
    # unreliable. A headline number computed over a partial fetch is worse than none.
    counts_trustworthy: bool = True
    trust_note: str | None = None

    @model_validator(mode="after")
    def _classes_partition_the_input(self) -> "BatchSummary":
        """The six evidence classes must sum to the input count.

        A write-up quoted "2 have tier 1 or 2, 47 have expression or literature only" over a
        50-gene list. Both numbers were right and one gene was missing from the sentence:
        SOX18, whose only evidence is a mouse model, belongs to neither. The classes are a
        partition, so the arithmetic can be checked, and now is.
        """
        parts = (
            self.tier_1_or_2
            + self.expression_or_literature_only
            + self.no_association
            + self.other_evidence_only
            + self.not_assessable
            + self.could_not_check
        )
        if parts != self.input_count:
            raise ValueError(
                f"evidence classes sum to {parts} over {self.input_count} input genes. "
                "Every gene lands in exactly one class, so a mismatch means a class is "
                "missing from the summary or a row was counted twice."
            )
        return self


class BatchResult(BaseModel):
    summary: BatchSummary
    rows: list[BatchRow] = Field(default_factory=list)
    source: str | None = None          # provenance of the input list
    limitations: list[str] = Field(default_factory=list)
    limiter_stats: list[dict] = Field(default_factory=list)
    cache_stats: dict = Field(default_factory=dict)
    call_count: int = 0
    elapsed_seconds: float | None = None
    data_version: str | None = None
    assessed_at: str | None = None


class Assessment(BaseModel):
    gene: str
    ensembl_id: str | None = None
    condition_as_typed: str
    resolved_disease_id: str | None = None
    resolved_disease_name: str | None = None

    # Set when the source answered about a different disease than the one requested.
    # This is abstention trigger 7 and it fires on 6 of 12 lookups against real conditions.
    term_substituted: bool = False

    mode: Mode
    mode_reason: str
    proxy: ProxyRow | None = None

    tier_profile: TierProfile
    conflicts: list[str] = Field(default_factory=list)

    rule_verdict: Verdict
    model_verdict: Verdict | None = None
    agreement: bool | None = None

    # The rule verdict is the verdict of record, because it is reproducible.
    final_verdict: Verdict

    # Ablation: the model run again with the curated ratings stripped from the packet.
    # Populated only when the ablation pass is enabled.
    model_verdict_ablated: Verdict | None = None
    curation_changed_model_call: bool | None = None

    limitations: list[str] = Field(default_factory=list)
    resolving_experiment: Experiment | None = None

    # The topical gate. Sits alongside the verdict and never inside it.
    reachability: Reachability | None = None

    # Lifted from the model verdict so the renderer has one place to read them from.
    text_technical: str = ""
    text_plain: str = ""

    # True when the second read came from the deterministic stub rather than a model. The
    # UI has to say so: stub prose presented as a model opinion would be a false claim.
    adjudicator_is_stub: bool = True

    # Provenance. Determinism is claimed only against a pinned snapshot, so the snapshot
    # has to be on the record.
    data_version: str | None = None
    assessed_at: str | None = None

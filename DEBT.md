# DEBT

What is wrong with this, what is missing, and what would mislead you if you did not know it.

Ordered by how badly it would bite someone picking this up.

---

## 1. Things that would mislead you if you read the output at face value

**An instruction is not a guardrail, and this one is enforced in code because of it.** The agent
is told: *if a trial reports per-arm numbers with no comparison, say UNDETERMINED. Do not compute
a difference yourself.* Shown two unresolved trials a model obeys that. Shown seventy-five it
does not, and the reads it returns state the forbidden operation in their own reasons
(*"255 vs placebo 157"*, *"-69.1 vs -48.1"*). The instruction is identical in both cases; only the
volume differs. So trials that published no comparison at all are now set aside mechanically
before the model sees them, and the validator rejects a directional read on one of them the same
way it rejects an uncited trial. Treat any prompt-only prohibition in this codebase as untested
until something enforces it.

**RARG against oily skin is not reproducible.** Around seventy-five ambiguous trials go to the
model and it has returned both ACTIONABLE and UNKNOWN on different runs. The benchmark expects
its dominant value, so **expect that case to go red occasionally, and expect batch and single to
disagree on it.** This is not the rule failing: the prohibition holds and the surviving reads
cite published statistics. It is a model reading a large ambiguous set.

**Attribution is computed over the union of a drug's mechanism rows, not row by row.** Asking
whether any one row names the queried gene alone is wrong: ruxolitinib carries six rows across
JAK1, JAK2, JAK3 and TYK2, so every one of the four came back as a sole named target and the tool
stated that a result on it was attributable to each. Anything short of a sole named target now
caps the outcome claim and excludes the trial with the reason shown. **This applies under the
`graph` and `graph-rules` providers only**; `retrieved` applies neither the attribution cap nor
the disagreement rule, so do not quote its output.

**The ablation number is a range, not a value.** Five keyed runs of the same eleven cases put
"curation changed the model's call" at 1, 1, 4, 3 and 1 of 11. Two cases flipped in **both
directions** across runs, and a curation effect cannot have two signs. The harness compares one
draw of the full packet against one draw of the ablated packet per case, so effect and sampling
variance are confounded by construction. **No single number from that range is reportable.**
Fixing it needs k draws per condition with a paired comparison, which is a harness change and not
more runs.

**The MMP1 publication trajectory is hand-gathered.** The tool does not fetch it. The Europe PMC
client is designed and not implemented, so tier 5 volume and trajectory are not retrieved by
anything. Say so out loud whenever that series appears.

**Confidence is categorical, and not a probability.** GWAS, Mendelian randomization and
colocalization all derive from the same underlying genetic data, so multiplying them as
independent likelihoods triple-counts one fact and manufactures confidence.

**A HIGH-confidence passenger call is nearly unreachable, by design.** It needs a failed human
intervention; that evidence is almost always tier 1b asserted; and 1b caps at MODERATE. Reachable
in principle through a tier 1a efficacy failure, but only about 6% of terminated skin trials stop
for efficacy or futility, and 18% over the drug-linked set. Different populations, so quote the
population with the number. Proving a passenger is harder than proving a driver, and for a triage
decision MODERATE and HIGH produce the same action.

**Cost per assessment is $0.00 to $0.615, mean $0.132.** Two of six measured cases cost nothing
because every trial resolved by arithmetic. RARG costs three times the next most expensive. A
batch above the model gate costs nothing.

---

## 2. Not built, and the reason is a real constraint

**Prompt caching on the outcome subgraph. Designed, not implemented.** Measured across six cases,
the spend driver is **input, not output**, which is the opposite of the adjudicator where output
dominates. A trial record is large, the agent is handed several, and every tool result is resent
on every turn, so input grows with the number of turns. That is exactly the shape caching
addresses, and it is why the lever here is caching rather than reasoning effort.

Not built because a half-built optimisation is worse than a measured decision not to build one.
The mean sits inside the range this was scoped against, the subgraph only fires on genes that
reach a drug at all, and a full batch run stays under a dollar.

**No Europe PMC client.** Tier 5 volume and trajectory are the one place where a low Open Targets
literature score is known to be wrong: PTGDR2 scores 0.04 while Europe PMC returns 239 papers.
The interim fix is a floor exemption, not a measurement.

**Pathway databases (Reactome, KEGG, INDRA) rejected, not deferred.** Pathway membership is
annotation, not endpoint-specific causal evidence. The arrows in those graphs come from many
experiments in many systems, none of them this endpoint. Consuming that as settled direction
would be a silent borrow.

**Mendelian randomization and colocalization are consumed, never computed.** Both need raw GWAS
summary statistics and statistical machinery that could not be validated in the window. Open
Targets already computes them.

**No auth, no multi-tenancy, no audit log persistence.** A single trusted audience is assumed.
The deployment diagram names all three.

**No queue, no worker pool, no resumable run state.** Concurrency here is three people.

**Batch tops out at URL length.** The gene list travels in the query string because `EventSource`
cannot send a body. `POST /api/parse_genes` exists and returns what the parser understood, but
nothing hands a token back to the stream, so a very long paste fails on the URL length. Presets
and any realistic paste are unaffected.

---

## 3. Known sharp edges in the code

**`uvicorn --reload` does not watch YAML.** Any change to `config/*.yaml` needs a restart, not a
reload.

**Bare modifier class names in `styles.css` collide.** A generic `.borrow` rule inherited a chip's
padding, a bare `.r-low` painted a rating scale solid red, and a bare `.prov` block inherited the
provenance chip's mono font, size and centring. **Scope new modifiers to their block, and grep the
file for the class name before adding it.** Check the token names too: this stylesheet defines
`--surface-*`, `--on-surface-*` and `--outline*`, and `--md-sys-color-*` names appear only in
generated ramp comments, so writing against them fails silently.

**The deterministic stub hides defects in the paths only it exercises.** An invalid
structured-output schema, a `max_tokens` too small for adaptive thinking, and evidence links
pointing at site roots all survived every stub run and surfaced the moment a real key and a real
reader were involved. Do not read "the eval passes" as "the model path works."

**Batch and single-gene may report different abstention triggers.** One-way. Single-gene also
queries the unstable row-level evidence route, so when that route flakes it fires trigger 6 where
batch fires trigger 5. Reproduces on roughly 1 run in 5 for FASN. Both abstain, the verdict fields
are identical, and `make eval` asserts exactly that.

**Batch does not fetch row-level datasource detail**, so a batch row cannot deep-link a citation
the way the single-gene view can. No verdict depends on it.

**The single-flight cache path is barely exercised.** The startup warm normally completes before
any user request, so `single_flight_joins` is usually 0. It is untested under real contention.

**Running the whole benchmark is itself a sweep against ClinicalTrials.gov.** Leave a minute
between full runs, or the source starts refusing and the refusals arrive as verdicts. The eval's
degraded-run guard exists because a starved fetch silently moved a case from HIGH to MODERATE with
every other assertion green.

---

## 4. Data and scope limits worth stating before someone asks

**Eight endpoints in the borrow table.** Everything outside them abstains on trigger 1. That is a
hard ceiling on what the tool can answer.

**The FLG barrier borrow covers a slice of the trait, not the trait.** FLG null carriage is about
5.8% of the general population, 7.7% European ancestry, 3.0% Asian, near-absent in African. Around
94% of people with dry skin carry no FLG null at all. The output states it. Correcting for it
needs cohort data this build does not consume, which is also why **ancestry-stratified proxies are
not built**.

**The eval set is fifteen cases.** It is a deterministic regression check, not an accuracy
benchmark. At that size a single miss moves measured accuracy by about seven points, which makes
calibration metrics meaningless. It asserts position, modulation outcome, mode, confidence band,
cited tiers and which provider answered, so a correct call citing the wrong tiers fails.

**The case set contains no confirmed passenger from the field's own consensus.** Three of the
field's confident answers failed checking against primary sources. The tool declines to label
passengers on thin evidence.

**Reachability is not a permeation model.** Nothing in it accounts for the stratum corneum, log P,
molecular weight or vehicle. It reads where the protein is and whether any modality has ever been
made against it. It narrows a list. It does not settle a formulation.

**Two skin donors, and they disagree.** The Human Protein Atlas reports skin as "skin 1" and
"skin 2" and the calls differ between them. The tool folds both into a compartment and takes the
strongest call, and the donor count travels with the answer.

---

## 5. Security and operations

**Rotate `ANTHROPIC_API_KEY` before any public exposure.** It lives in `.env`, chmod 600,
gitignored, with `.env.example` alongside. It is not in this repository and the history was
checked before publishing.

**Nothing bounds inbound requests in the default configuration.** `core/limits.py` bounds
*outbound* calls to the four data sources, which is a different problem and does not cover this
one. `core/gate.py` provides a shared-secret gate, a per-IP limiter and a daily spend ceiling, all
off unless their environment variables are set.

**The daily spend ceiling degrades rather than errors.** On reaching it the adjudicator is skipped
and the outcome subgraph runs with its agent off: same gathering, same arithmetic, same
attribution cap, same disagreement rule. The rule verdict is the verdict of record, so answers
keep appearing and only the model read is missing.

---

## 6. If you are picking this up cold

Read in this order: `README.md`, then `config/proxies.yaml` because it holds the judgments, then
`backend/app/services/rules.py` because it is the verdict.

Three things will bite you first. `enableIndirect` must be inlined into the Open Targets query
string and never passed as a GraphQL variable. Never take `hits[0]` from a gene symbol search.
And a zero is not a finding until a second route agrees with it.

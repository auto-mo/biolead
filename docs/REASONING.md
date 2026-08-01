# How the agent reasons

Every number here was measured while building this, or comes from a paper I have named. The last
section says which, and what I left out because I could not source it.

---

## 1. The position I took before building

A differential expression list contains a few hundred genes that are all genuinely different in
affected tissue, and most of them are different because the condition is happening. Modulating those
changes nothing. Sorting them is the job, and getting it wrong commits assay time, formulation work
and a claim strategy to a target the next experiment would have ruled out anyway.

Two decisions came before any code, and everything else follows from them.

**The translation from a cosmetic endpoint to a disease is part of the system, not something
someone does in their head.** Cosmetic endpoints have no genetics of their own, so the evidence has
to be borrowed from a disease. If that borrow is not written down, nothing in the question, the
answer or the score records that it happened.

**The verdict is two fields, because one field cannot express both.** Whether a gene causes the
condition and whether modulating it moves the endpoint are different questions with different
evidence behind them. Collapsing them gives the same recommendation to genes that need opposite
actions.

---

## 2. The reasoning

**Precedence is what genetics buys.** Your genes are fixed at conception, so they exist before the
condition does. When a naturally occurring variant associates with a trait across a population,
nobody chose who received which version, and the gene came first. Nelson and colleagues measured
what that is worth in 2015: targets with human genetic support succeed through development at
roughly twice the rate of targets without it.

**Open Targets answers questions about diseases, and these endpoints are not diseases.** Sebum
production in skin with no acne diagnosis. Tone evenness. Hair density in thinning never diagnosed
as alopecia. Firmness. None of them has an entry, because none of them is a pathology, so any
question about one has to be asked using the name of something else.

**The source also substitutes on its own, without saying so.** I ran twelve condition lookups in the
first hour of building, and six came back about a different condition than the one I asked for. Two
were harmless synonyms. Two were not: xerosis returned dry skin, and melasma returned freckles, as a
single result, with nothing in the answer indicating a swap. The trial registry does the same thing:
searching for acne returns hidradenitis suppurativa trials, because that disease is also called acne
inversa. The agent read one of those as evidence about acne and flipped a verdict, until I added a
check that the returned name starts with the name I asked about.

**So the borrow is written down.** Eight endpoints, one row each, hand written and version
controlled, and every row prints in full whenever it is used. Three of them, to show what a row
carries:

| Endpoint | Borrowed from | Rating | What it misses |
|---|---|---|---|
| Hair thinning | Androgenetic alopecia | Moderate | A threshold effect at clinical disease. Most women with female-pattern loss have normal circulating androgens |
| Oily skin | Acne vulgaris | Low | The field's own review finds no causal role for sebum composition in acne, and no genetic study of sebum production exists, so there is nothing to check a sebum proxy against |
| Hyperpigmentation | Vitiligo | Low, refused | The direction is inverted |

I did not have a model choose the borrow at runtime. Asked which disease stands in for sebum
production, a model will always produce a fluently justified answer, including when the honest
answer is that no good one exists, and there is no record to argue with afterwards. Vitiligo is the
case that made the decision for me. It genuinely shares pigmentation genes with hyperpigmentation,
so any automated check for shared significant genes would pass that borrow, and it is still wrong,
because the variant in question protects against vitiligo while raising melanoma risk. An endpoint
not in the table gets nothing.

**Two questions, and the grid they produce.** Position asks whether the evidence places the gene
before or after the process starts, and comes from whether human genetic association exists, not
from where the gene sits in a pathway diagram. Modulation outcome asks whether modulating the gene
moved the endpoint in people, not whether it can be drugged. Both definitions print on every
assessment, because both terms mean something else in ordinary drug discovery usage.

| | Benefit shown | No benefit | Untested |
|---|---|---|---|
| **Upstream** | Causal, and hitting it worked | Causal, but the drug failed | Causal, never tested |
| **Downstream** | Downstream, but it worked | **Downstream, tried and failed** | Downstream, never tested |

Downstream spans three cells and only the bold one is a passenger. IL17A against oily skin sits in
it: thousands of papers, no genetics, and a completed trial in fifty-two people where the drug did
worse than placebo. MMP1 against photoaging has the same evidence shape and does not sit in it,
because no MMP inhibitor has ever been trialled against a photoaging, wrinkle or elasticity
endpoint. Under a single driver-or-passenger label those two come out the same, and one of those
recommendations is wrong.

**The evidence hierarchy.** A human trial retrieved during the run and cited by its registry number
outranks everything, because it records what happened instead of predicting it. Human genetic
association comes next, then perturbation experiments in a relevant human cell type, then animal
models, then expression change and publication volume. Each tier carries what it cannot prove: a
drug hits more than its intended target, so a success does not cleanly implicate the one you asked
about, and a genetic association is not an effect size in a treated adult.

Two parts do unusual work. A trial claim typed into a file by a person is held apart from one
retrieved during the run: it will not load without sources attached and cannot reach high confidence
alone. And publication volume is used with the sign inverted, because heavy literature with absent
genetics is the passenger fingerprint. Papers on MMP1 in skin aging run from sixteen in 2005 to two
hundred and six in 2024, against a genetic association of zero and no trial against any skin
endpoint.

---

## 3. How that became an agent

The assessment runs in three parts.

**The first part is fixed, and no model is involved.** Resolve the gene symbol to an exact match.
Look up the borrow. Resolve the condition. Fetch the genetic, functional and literature evidence
and sort it into tiers. Letting a model choose these steps costs reproducibility and buys nothing,
because there are four sources and all four are relevant to every question. Four of the six reasons
to abstain are decidable here, and when one fires the rest never runs. On a published list of five
hundred and six genes, four hundred and eighty-nine stop at this point.

**Then a gate.** Has any drug whose mechanism names this gene alone been tested for this condition?
If not, the outcome is unknown, the reason is that no drug exists, and the agent never starts. This
is a lookup, not a judgment, and it is why running a five hundred gene list is affordable.

**The second part gathers every trial, settles what it can by arithmetic, and gives the agent only
what is left.**

Gathering is exhaustive and it is not the agent's decision. Every trial both retrieval routes reach
is enumerated and read. When the agent chose its own subset, three runs of the same question read
forty, then seventy-four, then seventy-two out of eighty-five available trials, with no limit hit
and no request failed. It was deciding when it had seen enough.

Arithmetic then takes about a third of the work away. A trial registry has no field saying whether a
trial met its endpoint, but where a trial publishes a proper statistical comparison the answer is
mechanical: if the confidence interval covers the value that means no difference, the two arms did
not separate. That needs no knowledge of which direction is good, because it is a statement about
whether the arms differ at all, and it holds whether the measurement counts inflammatory lesions or
counts hairs. So the passenger call infers nothing. I swept thirteen hundred and sixty-two
drug-linked trials across the seven conditions in scope, and of the two hundred and eighty-seven
that completed with posted results and a comparison group, one hundred resolve this way.

What reaches the agent is what the arithmetic refused: co-primary measures that disagree with each
other, biomarker endpoints with no inherently better direction, and equivalence designs where
covering the null is the intended result.

**A drug that names more than one target does not isolate any of them.** Drug records sometimes
point at a whole protein family, which expands to every member gene, so metformin arrives attributed
to fifty-one targets. Filtering to records naming exactly one gene removes sixty-three percent of
the targets. The subtler case is a drug with several single-gene records: ruxolitinib
has six, naming four related kinases one at a time, and my first attempt asked whether any single
record named the gene in question. It answered yes for all four, which licenses a confident claim on
a false premise. Attribution is now computed across all of a drug's records at once, and one gene
went from a confident no-benefit verdict to no attributable drug, with every excluded trial listed.

**When readable trials disagree, the unit is the trial count.** A majority is taken when one reading
holds at least two thirds of them, and only when the total number of participants points the same
way. Two thirds is a chosen principle rather than a number read off the data: it means at least
twice as many trials one way as the other, which can be stated before looking at a single trial. Of
a hundred and thirty-one gene-condition pairs in that sweep, ten have more than one readable trial
and five disagree, all five atopic dermatitis, so the rule is calibrated on one disease area. The
split is always shown, with every dissenting trial named.

**What the agent is not allowed to do.** Every claim must cite a record it fetched in that run, and
a verdict naming a trial that is not on the list is rejected. A trial that published no comparison
cannot be given a direction. A completed trial with no posted results is unknown and never failure.
A trial stopped early is not efficacy evidence unless the stated reason was efficacy.

I replaced an instruction with those checks, and here is why. The instruction was one line: do not
compute a difference yourself, only read what the trial published. It held while the agent was
seeing two ambiguous trials. When gathering became exhaustive and it was seeing seventy-five, five
of its six readings stated the forbidden operation in their own words. The instruction had not
changed. The volume had. So trials with nothing to compare are now set aside mechanically and never
reach the model at all.

**The third part is fixed again**: the rules produce the verdict, place it on the grid, run a
separate check on whether a topical product could reach the target, and name the experiment that
would resolve what is still open, chosen from a fixed list of assays this lab already runs.

---

## 4. Abstention

Six reasons to refuse, in the order they are checked. The gene symbol does not resolve to an exact
match, and symbol search is fuzzy enough that asking for AR returns five other genes behind it. The
condition came back different from the one asked about and the substitution is not one the borrow
table declares. The condition does not resolve at all, or no borrow exists for it. The only
available borrow is one the table refuses. A source the verdict depended on could not be read, which
is a different thing from the source returning nothing. The only evidence is expression change and
publication count.

**Passenger cannot be proven by absence.** Calling a gene a passenger with confidence requires an
expression change, no human genetic support, and a drug that was tried in people and did not work.
Anything softer resolves to insufficient evidence, which most tools would call a passenger. That
makes a high-confidence passenger call nearly unreachable, and I would rather say so than have it
look like a defect. Of eighty-six terminated skin trials in the first survey, about six percent
stopped for lack of efficacy; of a hundred and five stopped drug-linked trials in the later one,
eighteen percent. For a triage decision, moderate and high confidence produce the same action
anyway.

**Untested and unreported are different facts**, so there are three outcome states rather than two:
no drug has ever been tested against this gene for this condition, a drug was tested and the result
was never published, or a drug was tested and the trial published something readable. AR against
androgenetic alopecia is the case that needs the second: tested, and never published. Clascoterone is a genuine androgen receptor blocker, and
three completed trials covering fifteen hundred and sixty people posted no results at all. Collapsed
into "untested", that reads as nobody having tried. It is also the most common thing the outcome
layer finds: of the thirteen hundred and sixty-two trials swept, four hundred and eighty completed
and published nothing.

---

## 5. What I got wrong

**MMP inhibitors failed in humans for photoaging.** The original design used MMP1 as its clean
passenger on this basis, and no such trial has ever existed. The calibration is what makes the
negative credible: a hundred and eighty-one trials mention photoaging, eleven hundred mention
wrinkles, a hundred and eleven mention MMP inhibitors, and the overlap is zero. Six apparent hits
were read in full and all six measure MMP as a biomarker. MMP1 moved to downstream with an unknown
outcome, and photoaging became an endpoint the table refuses.

**Finasteride is evidence about AR.** Finasteride lowers DHT and DHT acts on the androgen receptor,
so attributing the finasteride result to AR felt like a lookup. No database records that link:
finasteride's mechanism names the enzyme, not the receptor. My own file asserted the connection in
its verdict and contradicted it in its own footnote. Once the tool retrieved instead of reading the
file, the evidence moved to SRD5A2, and AR became the tested-and-never-reported case above.

**A drug naming a gene is evidence about that gene.** The ruxolitinib case. After the fix, the set
of disagreeing cases halved, because two of them had been the same twelve trials counted twice.

---

## 6. Limits

**Eight endpoints is the whole scope**, and everything outside it abstains. The table is small
because rows kept failing review. The photoaging borrow was built and then reverted when four of its
five named genes could not be traced to any study of skin ageing. The acne sub-pathway split was
designed in full and cut when no published analysis produced that partition. The pigmentation row
was downgraded when the endpoint I expected to have its own genetics returned no association at all.

**Fifteen test cases, passing all fifteen, and that is a regression check.** At that sample size a
single wrong call moves measured accuracy by seven points, so it is not a claim about accuracy. It
checks the position, the outcome, the confidence band and which evidence tiers were cited, so a
right answer reached for the wrong reason fails.

**Three separate claims about determinism.** The evidence path is deterministic: the same question
fetches the same records in the same order. The trial set is deterministic: every reachable trial is
read and the model cannot ask for more, with the measured exception that the registry's own search
returns slightly different sets between calls. The reading of an ambiguous trial is not
deterministic, and roughly two thirds of readable trials do not resolve by arithmetic. Across three
runs of seven cases no verdict moved. One case outside that set is not reproducible at all.

**One source for genetics.** A second would need its own way of assigning variants to genes and its
own way of mapping traits to endpoints, and that second mapping is the borrow problem again with no
written row behind it. So the tool inherits one aggregator's judgment calls, including gene
assignments that are inferences: one acne association credited to a gene sits sixty-one thousand
bases away inside a different gene. What the system enforces is narrower: a result of zero is not a
finding until a second route inside the same source agrees, a rule that exists because the same
query returned a hundred and sixty-five results and then none, ten minutes apart, reporting success
both times.

**Designed and not built, none of it for time.** Pathway databases as evidence of direction, because
pathway membership records that a gene sits in a curated map of a process, not that it drives this
endpoint, and consuming those arrows as settled direction is a borrow with nothing recording how far
it travelled. Splitting downstream further by whether a gene is the only route or one of several,
because that cannot be derived from evidence and no case here is a confirmed example. Response
caching, ancestry-stratified borrows, and computing the genetic statistics instead of consuming
them.

**It triages known evidence and does not discover.** Every verdict describes what could be retrieved
when it ran, so it cannot tell you that a gene nobody has studied is a driver. What it does is take
a list of genes that all look equally interesting and separate the ones with a human experiment
behind them, the ones with an experiment that failed, the ones whose result was never published, and
the ones carrying only the measurement that put them on the list. On a published androgenetic
alopecia expression list it places two of the paper's own top fifty and declines forty-eight. On an
acne list it places none of seventy-seven.

---

## Where the numbers came from

Everything above was measured while building this and the runs are recorded, except for two papers:
Nelson and colleagues in *Nature Genetics*, 2015, for the doubled success rate, and Jin and
colleagues in *Nature Genetics*, 2016, for the vitiligo and melanoma direction.

**What I left out because I could not source it.** The cost of a failed target in money or months.
Any percentage breakdown of atopic dermatitis genetics by immune pathway, which no paper provides
and which the one study that tested it contradicts. A figure for how many women with diffuse
thinning have normal androgens, which spans roughly sixteen to seventy-four percent across cohorts
depending on which hormone is measured. A figure for how much of dry skin is explained by the
barrier gene genotype, so carrier frequency is used instead. And how often my curated ratings
changed the model's answer: five repeat runs gave one, one, four, three and one out of eleven, with
two cases flipping in both directions, and an effect cannot have two signs, so the measurement is
confounded and no number in that range is reportable.

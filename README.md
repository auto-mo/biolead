# BioLead

Takes a gene and a skin or hair endpoint, and returns whether human evidence places the gene
upstream of the endpoint or alongside it. Or takes a differential expression list and ranks it.

The problem it exists for: **a gene going up in affected tissue is the observation that makes it
look like a target, and it is not the observation that makes it one.** Fire trucks are at fires.

Run the published androgenetic alopecia list through it and the tool places two of the paper's
own top 50 genes and declines the other forty-eight. Forty-seven have nothing beyond the
expression measurement that put them on the list; one has a mouse model and nothing human.

---

## Two minute quickstart

Needs Python 3.11+ and Node 20+. No API key required: without one the second read falls back to a
deterministic stub, and the app says so on screen.

```bash
git clone https://github.com/auto-mo/biolead.git && cd biolead
make venv                      # own interpreter, exact pins
cd frontend && npm install && cd ..

make api    # terminal 1, port 8931
make web    # terminal 2, port 5173
```

The project owns its interpreter. Versions are pinned exactly in `requirements.txt`, and
`tools/` needs `requirements-dev.txt` plus `playwright install chromium`.

Open **http://localhost:5173** (Vite binds IPv6 only, so `localhost` and not `127.0.0.1`).

Try these in order:

| Query | Shows |
|---|---|
| `AR` x hair thinning | Driver with a clinical outcome behind it |
| `IL17A` x oily skin | Passenger. Heavy literature, no genetics, a trial that lost to placebo |
| `FLG` x atopic dermatitis, then `FLG` x cosmetic dry skin | The same gene with the borrow made visible |
| **Assess a list** > the 50-gene preset | The headline count, the matrix, the ranked table |

Then `make eval` for the regression check. It runs against live APIs and takes about a minute.

Optional, for the model-written second read:

```bash
cp .env.example .env && echo "ANTHROPIC_API_KEY=sk-ant-xxx" > .env && chmod 600 .env
```

---

## What it does

**One gene.** Resolves the gene and the endpoint, works out which disease the evidence has to be
borrowed from, fetches it, maps it onto a six-tier hierarchy, and derives a verdict. Every step
is shown with its source and its timing.

**A list.** Same decision code, one condition, ranked output. The count comes first, then the
table.

**The verdict is two fields, not one.**

- **Position**: upstream driver, downstream, or insufficient. Derived from whether human genetic
  association exists, because germline variation is dated relative to the phenotype.
- **Modulation outcome**: benefit shown, no benefit, untested. Close to a direct readout of
  tier 1, and the code says so.

Those two axes make a grid, and **downstream spans three cells of which only one is a passenger**.
A gene can sit downstream of the driver and still be worth working on.

**Then a separate gate: could a topical reach it.** Location from the Human Protein Atlas,
modality from Open Targets tractability. It is not part of the verdict. FLG is the case it was
built for: the cleanest driver call the tool makes, and nothing tractable in any
modality to formulate against.

**It abstains.** Seven triggers. Abstention is the common outcome.

---

## The design position


**1. The borrow is curated, not inferred.** Cosmetic endpoints have no ontology terms, so any
query about them has to name a disease instead. That translation is a scientific judgment, it
lives in [`config/proxies.yaml`](config/proxies.yaml) under version control, and a scientist who
disagrees can open the file and change it. A model asked to pick the proxy would always produce a
fluently justified answer, including for the borrow that is upside down.

**2. The execution graph is code, not a model.** The pipeline is an async generator of stages
written in Python. That is why there is no agent framework here. A model adjudicates *separately*,
never sees the rule verdict, and its answer is carried alongside. The rule verdict is the verdict
of record because it is reproducible.

**3. A zero is not a finding until a second route agrees with it.** The same query returned 165
rows and then 0 within one session, HTTP 200 both times. `checked_and_empty` and `could_not_check`
are different states and the APIs will not distinguish them for you.

---

## Layout

```
backend/app/
  clients/       open_targets.py, clinicaltrials.py, hpa.py
  core/          config.py (loader + tier 1b rules), limits.py, cache.py
  services/      pipeline.py, batch.py, rules.py, tiers.py, reach.py, adjudicate.py
  models/        contracts.py
  api/routes.py  SSE endpoints
config/          proxies.yaml, tiers.yaml, experiments.yaml
config/archive/  clinical_facts.yaml, read only by --provider file
data/de_lists/   published DE tables + SOURCE.md
eval/            run_eval.py, benchmark_cases.json
frontend/src/    React 19 + Vite, no component library
tools/           the scripts that generate and verify everything in docs/
docs/            architecture-aws-{full,slide}.{excalidraw,png}, DEPLOYMENT.md, demo/, batch/, ui/
```

**The four config files hold the judgments**: which borrow, rated how, what it misses, which
clinical facts are admissible and with what sources. Start with `config/proxies.yaml`.

---

## Evidence hierarchy

| Tier | What |
|---|---|
| **1a** | Human intervention outcome, retrieved at runtime from ClinicalTrials.gov |
| **1b** | Human intervention outcome, asserted from a curated file. Cannot reach HIGH confidence, and does not load at all without sources |
| **2** | Human genetic association |
| **3** | Functional evidence in a relevant human cell type |
| **4** | Animal model |
| **5** | Expression correlation and literature volume. **Inverted**: heavy literature with absent genetics is the passenger fingerprint |

---

## Verification

The design documents were fact-checked against primary sources before any code was written, and
most of their supporting facts did not survive. The API shapes were probed before the clients were
written, and two of four assumed shapes were wrong.

Six independent sources have returned failures that read as findings, all HTTP 200. The clients
are shaped around that.

---

## Deployment

One set of infrastructure serving two request shapes.
[`architecture-aws-full.png`](docs/architecture-aws-full.png) carries the trade-offs;
[`architecture-aws-slide.png`](docs/architecture-aws-slide.png) is the same deployment sized for
projection.

- **`/api/assess`** holds an SSE stream open for the whole run. One gene, 2 to 4 seconds.
- **`/api/batch`** starts a background task on the same container, which writes progress and the
  result to DynamoDB while the client polls. 50 genes 7.5s, 506 genes 81s.

ALB and not API Gateway: SSE is the whole interaction, and API Gateway's REST integration caps at
29 seconds.

Beyond this, a queue and worker pool. Not triggered: that needs several full-list runs in flight
at once, or runs long enough that losing one to a task restart matters.

The 81-second breakdown and the cost model are in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

## Known gaps

[`DEBT.md`](DEBT.md). Read it before trusting anything here beyond what it claims.

## Licence and data

Code MIT. `data/de_lists/` redistributes a CC BY-NC supplementary table with attribution; see
[`data/de_lists/SOURCE.md`](data/de_lists/SOURCE.md).

Built as a take-home assessment. Not a medical device, not clinical advice, and every verdict is
a statement about the evidence that was retrievable.


---

## The outcome axis

**The default is retrieval.** For a gene and an endpoint the system finds the drugs whose
ChEMBL mechanism record names that gene, enumerates every trial both routes can reach, reads
all of them, and decides from what they published. `config/clinical_facts.yaml`, the four
hand-written drug facts that used to answer this question, is kept in `config/archive/` and is
read only by `--provider file`.

**Three providers, and they do not agree. Every verdict names the one that produced it**, the
interface renders it, and the eval prints it per row and fails a case whose verdict names a
different provider than the run asked for.

| `--provider` | What it is | Attribution cap | Disagreement rule | Model cost |
|---|---|---|---|---|
| `graph` | the subgraph. **The shipping default** | yes | yes | $0.00 to $0.62 per assessment |
| `graph-rules` | the same, agent off. What batch uses above 25 genes | yes | yes | none |
| `file` | the retired curated file, kept as the demo fallback | no | no | none |
| `retrieved` | retrieval without the caps below | **no** | **no** | none |

**What the model does and does not do.** Three things are taken out of its hands and computed:
whether a trial separated its arms, whether a result may be attributed to this gene, and which
read wins when trials disagree. It reads the trials the arithmetic refused, and a trial that
published no comparison at all never reaches it.

```
make eval          # 15 cases against the shipping default
make eval-graph    # the same, explicit
make demo          # the demo running order, three times, diffed
```

**Running the whole benchmark is itself a sweep against ClinicalTrials.gov.** Leave a minute
between full runs or the source starts refusing and the refusals arrive as verdicts.

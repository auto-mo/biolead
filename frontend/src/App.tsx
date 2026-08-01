import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import Batch from "./Batch";
import EndpointMenu, { matchEndpoint, type EndpointEvidence } from "./EndpointMenu";
import GeneField from "./GeneField";
import Help, { TIERS } from "./Help";
import Matrix from "./Matrix";
import OutcomeState from "./OutcomeState";
import OutcomeSplit from "./OutcomeSplit";
import ProviderBadge from "./ProviderBadge";
import Reach from "./Reach";
import type { Assessment, EvidenceItem, ProxyRow } from "./types";
import { API } from "./apiBase";

const TIER_LABEL: Record<string, string> = Object.fromEntries(
  TIERS.map(([t, name]) => [t, name]),
);

/** One per matrix cell that has a case, chosen from the sweep in docs/matrix-sweep.json
 *  (1,672 gene x endpoint pairs across the seven endpoints).
 *
 *  The first three are the argument. SRD5A2 twice is the same finasteride trial with and
 *  without a borrow, HIGH against MODERATE, and the second one's outcome cell says which
 *  disease it was measured on. AR is the case those two used to occupy: two clascoterone
 *  trials in androgenetic alopecia, both completed, neither with results posted.
 *
 *  Two cells have no case and no chip: upstream with no benefit, and downstream with
 *  benefit shown. The matrix draws them empty rather than hiding them. */
const PRESETS: [string, string, string][] = [
  ["SRD5A2", "androgenetic alopecia", "upstream, benefit shown. No borrow, so it reaches HIGH"],
  ["SRD5A2", "hair thinning", "the same trial through a borrow. MODERATE, and the cell says why"],
  ["AR", "androgenetic alopecia", "tested and silent. Two trials, neither posted results"],
  ["SRD5A1", "hair thinning", "downstream, untested. Same family, no genetics of its own"],
  ["IL17A", "oily skin", "downstream, no benefit. Trial lost to placebo"],
  ["IL1RL2", "oily skin", "downstream, no benefit. A second failed trial"],
  ["FLG", "atopic dermatitis", "upstream, untested. Control, nothing borrowed"],
  ["FLG", "cosmetic dry skin", "the same gene with the borrow made visible"],
  ["FASN", "oily skin", "abstains. Real gene, real condition, thin evidence"],
  ["AR", "rosacea", "abstains. No borrow is curated for rosacea"],
];

const OT = "https://platform.opentargets.org";

/** Resolve a source label to the page that actually shows the evidence being cited.
 *
 *  Every one of these used to point at a site root. A chip that reads `open_targets`, sits
 *  next to a score, and links to the Open Targets homepage looks like a citation and is not
 *  one, the reader cannot get from it to the number. Where the identifiers are known this
 *  now deep-links to the gene x disease evidence page; where they are not it returns "" and
 *  the chip renders as a plain label. No link is better than a link that implies a source
 *  it does not reach. */
function sourceHref(
  source: string | undefined,
  ids: { gene?: string | null; disease?: string | null },
): string {
  if (!source) return "";
  if (source === "open_targets" || source === "opentargets") {
    if (ids.gene && ids.disease) return `${OT}/evidence/${ids.gene}/${ids.disease}`;
    if (ids.gene) return `${OT}/target/${ids.gene}`;
    if (ids.disease) return `${OT}/disease/${ids.disease}`;
    return "";
  }
  // clinicaltrials.gov rows carry their NCT id in the summary and are linked from there;
  // curated_clinical_facts is a file in this repo, not a URL.
  return "";
}

const reduced = () =>
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const ExtIcon = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
    <path d="M6.5 3.5H3.5v9h9v-3M9.5 3.5h3v3M12.5 3.5L7 9" strokeLinecap="round" />
  </svg>
);
const InfoIcon = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
    <circle cx="8" cy="8" r="6.2" />
    <path d="M8 7.2v4M8 4.9v.6" strokeLinecap="round" />
  </svg>
);

function Ref({ href, children }: { href: string; children: React.ReactNode }) {
  if (!href) return <span className="t-mono">{children}</span>;
  return (
    <a className="ref" href={href} target="_blank" rel="noreferrer noopener">
      <ExtIcon />
      {children}
    </a>
  );
}

/** Turn an identifier in a claim into a link. Only exact known shapes are linked. */
function linkFor(text: string): { label: string; href: string } | null {
  const nct = text.match(/\bNCT\d{8}\b/);
  if (nct) return { label: nct[0], href: `https://clinicaltrials.gov/study/${nct[0]}` };
  const pmid = text.match(/\bPMID\s*(\d{6,8})\b/);
  if (pmid) return { label: `PMID ${pmid[1]}`, href: `https://pubmed.ncbi.nlm.nih.gov/${pmid[1]}/` };
  return null;
}

/** The only question a scientist actually has: do I work on this gene.
 *  Derived from the verdict, so it cannot disagree with the fields below it. */
function recommendation(
  position: string,
  targetability: string,
  confidence: string | null,
): { verb: string; because: string } {
  if (position === "INSUFFICIENT")
    return {
      verb: "Nothing is established about this gene here",
      because:
        "The sources were queried and came back without the evidence a call needs. That is " +
        "a statement about the literature, not about the gene, and not a failure of the " +
        "lookup. What was checked and what would change it are below.",
    };
  if (targetability === "NOT_ACTIONABLE")
    return {
      verb: "Do not prioritise",
      because:
        "A drug against this target was tested in people and did not beat placebo.",
    };
  if (position === "UPSTREAM_DRIVER" && targetability === "ACTIONABLE")
    return {
      verb: "Prioritise",
      because:
        "Human genetics places this gene upstream, and modulating it has already moved the endpoint in people.",
    };
  if (position === "UPSTREAM_DRIVER")
    return {
      verb: confidence === "HIGH" ? "Worth pursuing" : "Worth pursuing, with a caveat",
      because:
        "Human genetics places this gene upstream, but nobody has shown that modulating it moves the endpoint.",
    };
  // DOWNSTREAM AND ACTIONABLE. This branch did not exist until the rebuild, and the reason is
  // the reason it matters: under four curated facts the cell was empty, so the fall-through
  // below caught it and told the reader "no intervention has been tested" about a gene whose
  // drug worked. AHR, IL31RA, JAK1 and NR3C1 all land here now.
  if (targetability === "ACTIONABLE")
    return {
      verb: "Worth working on, not as a root cause",
      because:
        "Modulating this gene has already moved the endpoint in people. Human genetics does " +
        "not place it upstream, so it acts on the process rather than on what starts it.",
    };
  return {
    verb: "Deprioritise for now",
    because:
      "The gene sits downstream of whatever drives the process, and no intervention has been tested.",
  };
}

const TGT_LABEL: Record<string, string> = {
  ACTIONABLE: "endpoint benefit shown",
  NOT_ACTIONABLE: "no endpoint benefit",
  UNKNOWN: "untested",
};

interface Step {
  label: string;
  source?: string;
  href?: string;
  detail?: string;
  more?: string;
  tone?: "warn" | "good";
  ms: number;
}

export default function App() {
  // Single gene is the landing state and the default. Batch is a second mode reached
  // because a list is what you bring once you already know the question.
  const [mode, setMode] = useState<"single" | "list">("single");
  const [gene, setGene] = useState("");
  const [condition, setCondition] = useState("");
  const [endpoints, setEndpoints] = useState<ProxyRow[]>([]);
  const [epEvidence, setEpEvidence] = useState<EndpointEvidence[]>([]);
  const [epEvidenceGene, setEpEvidenceGene] = useState<string>("");
  const [outage, setOutage] = useState(false);
  const [menu, setMenu] = useState(false);
  const [examplesOpen, setExamplesOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [running, setRunning] = useState(false);
  const [started, setStarted] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [traceOpen, setTraceOpen] = useState(true);
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const [dualInfo, setDualInfo] = useState(false);
  const [register, setRegister] = useState<"technical" | "plain">("technical");
  // Definitions are collapsed by default: they never change between assessments.
  const [defs, setDefs] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const t0 = useRef(0);
  const last = useRef(0);
  // Identifiers as the trace resolves them, so a step logged after resolution can link to
  // the exact evidence page rather than to a site root.
  const ids = useRef<{ gene?: string | null; disease?: string | null }>({});

  // Container transform. The launch block is one DOM node that changes grid placement, so
  // its position can be measured before and after and the difference played back as a
  // translate. Transform and opacity only, 160ms, same easing as the trace step entry.
  const launchRef = useRef<HTMLDivElement>(null);
  const firstRect = useRef<DOMRect | null>(null);

  useLayoutEffect(() => {
    const el = launchRef.current;
    const first = firstRect.current;
    firstRect.current = null;
    if (!el || !first || !started || reduced()) return;
    const now = el.getBoundingClientRect();
    const dx = first.left - now.left;
    const dy = first.top - now.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    el.animate(
      [
        { transform: `translate(${dx}px, ${dy}px)`, opacity: 0.55 },
        { transform: "translate(0px, 0px)", opacity: 1 },
      ],
      { duration: 160, easing: "cubic-bezier(0.2, 0, 0, 1)" },
    );
  }, [started]);

  useEffect(() => {
    fetch(`${API}/proxies`)
.then((r) => r.json())
.then(setEndpoints)
.catch(() => setEndpoints([]));
  }, []);

  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setElapsed(Date.now() - t0.current), 100);
    return () => clearInterval(id);
  }, [running]);

  // Once a gene resolves, ask what it has against each borrow so the endpoint list can be
  // ordered by it. Debounced, aborted in flight, and failure is silent: the menu falls back
  // to the curated file's own order, which is a human's and perfectly usable.
  useEffect(() => {
    const g = gene.trim();
    if (g.length < 2) {
      setEpEvidence([]);
      setEpEvidenceGene("");
      return;
    }
    const ac = new AbortController();
    const t = setTimeout(() => {
      fetch(`${API}/endpoint_evidence?gene=${encodeURIComponent(g)}`, { signal: ac.signal })
.then((r) => r.json())
.then((d) => {
          if (d.ok) {
            setEpEvidence(d.endpoints ?? []);
            setEpEvidenceGene(d.gene ?? g);
          } else {
            setEpEvidence([]);
            setEpEvidenceGene("");
          }
        })
.catch(() => undefined);
    }, 350);
    return () => {
      clearTimeout(t);
      ac.abort();
    };
  }, [gene]);

  const run = useCallback(
    (g: string, c: string) => {
      if (!g.trim() || !c.trim()) return;
      if (!started) firstRect.current = launchRef.current?.getBoundingClientRect() ?? null;
      esRef.current?.close();
      t0.current = Date.now();
      last.current = Date.now();
      setSteps([]);
      setAssessment(null);
      setOpen({});
      setElapsed(0);
      setRunning(true);
      setStarted(true);
      setHelpOpen(false);
      setExamplesOpen(false);
      setTraceOpen(true);

      const url =
        `${API}/assess?gene=${encodeURIComponent(g)}&condition=${encodeURIComponent(c)}` +
        (outage ? "&simulate_outage=open_targets" : "");
      const es = new EventSource(url);
      esRef.current = es;

      ids.current = {};
      const push = (s: Omit<Step, "ms">) => {
        const now = Date.now();
        const ms = now - last.current;
        last.current = now;
        setSteps((p) => [
          ...p,
          { ...s, ms, href: s.href ?? sourceHref(s.source, ids.current) },
        ]);
      };
      const on = (n: string, fn: (d: any) => void) =>
        es.addEventListener(n, (e) => fn(JSON.parse((e as MessageEvent).data)));

      on("start", (d) =>
        push({ label: "open stream", source: d.data_version, detail: `${d.gene} × ${d.condition}` }),
      );
      on("stage", (d) => {
        if (!d.done) return;
        // Record the ids before pushing, so this step is itself linkable.
        if (d.stage === "RESOLVE_GENE") ids.current.gene = d.resolved ?? null;
        if (d.stage === "RESOLVE_CONDITION") ids.current.disease = d.resolved_id ?? null;
        push({
          label: d.stage.toLowerCase().replace(/_/g, " "),
          source: STAGE_SOURCE[d.stage],
          detail: summarise(d),
        });
      });
      on("proxy", (d) =>
        push({
          label: "borrow selected",
          source: "config/proxies.yaml",
          detail: `${d.borrowed_from ?? "none"} · rating ${d.rating}`,
          more: `${d.rationale}\n\nMisses: ${d.what_it_misses}`,
          tone: d.refuse || d.rating === "LOW" ? "warn" : undefined,
        }),
      );
      on("substitution", (d) =>
        push({
          label: "term substituted",
          source: "opentargets/search",
          detail: `“${d.searched}” → “${d.resolved}”`,
          tone: d.declared ? undefined : "warn",
        }),
      );
      on("trial", (d) =>
        push({
          label: `trial ${d.nct_id}`,
          source: "clinicaltrials.gov",
          href: `https://clinicaltrials.gov/study/${d.nct_id}`,
          detail: `${d.status} · results ${d.has_results ? "posted" : "absent"}`,
          more: d.title ?? undefined,
          tone: d.usable_as_tier_1a ? "good" : "warn",
        }),
      );
      on("source_status", (d) =>
        push({
          label: d.status.toLowerCase().replace(/_/g, " "),
          source: d.source,
          detail: d.note || undefined,
          tone: d.status === "COULD_NOT_CHECK" ? "warn" : undefined,
        }),
      );
      on("evidence", (d) => push({ label: `tier ${d.tier}`, source: d.source, detail: d.summary }));
      on("evidence_absent", (d) =>
        push({
          label: d.kind === "could_not_check" ? "could not check" : "checked, empty",
          detail: d.detail,
          tone: d.kind === "could_not_check" ? "warn" : undefined,
        }),
      );
      on("mode", (d) => push({ label: `${d.mode.toLowerCase()} mode`, detail: d.reason }));
      on("conflict", (d) => push({ label: "conflict", detail: d.description, tone: "warn" }));
      on("rule_verdict", (d) =>
        push({
          label: "rule verdict",
          source: "deterministic",
          detail: `${d.position} · ${d.targetability}`,
          more: d.reasoning,
          tone: "good",
        }),
      );
      on("agreement", (d) =>
        push({
          label: d.agreement ? "model agrees" : "model disagrees",
          source: "adjudicator",
          detail: `rule ${d.rule.join("/")} · model ${d.model.join("/")}`,
          tone: d.agreement ? undefined : "warn",
        }),
      );
      on("reachability", (d) =>
        push({
          label: "topical reachability",
          source: "hpa + opentargets",
          href: ids.current.gene
            ? `https://www.proteinatlas.org/${ids.current.gene}`
            : "",
          detail: `${d.verdict.replace(/_/g, " ").toLowerCase()} · ${d.rule_fired}`,
          tone: d.verdict === "OUT_OF_REACH" ? "warn" : d.verdict === "REACHABLE" ? "good" : undefined,
        }),
      );
      on("experiment", (d) =>
        push({ label: "resolving experiment", source: "config/experiments.yaml", detail: d.assay }),
      );
      on("assessment", (d) => setAssessment(d as Assessment));
      on("done", () => {
        setRunning(false);
        setTraceOpen(false); // collapses to a summary line once complete
        es.close();
      });
      on("error", (d) => {
        push({ label: "error", detail: d.error, tone: "warn" });
        setRunning(false);
        es.close();
      });
      es.onerror = () => {
        setRunning(false);
        es.close();
      };
    },
    [outage, started],
  );

  const v = assessment?.final_verdict;
  const mv = assessment?.model_verdict;
  // Count real evidence sources only. "deterministic" and "adjudicator" are internal
  // labels, and the data-version string is provenance, not a source.
  const NOT_A_SOURCE = new Set(["deterministic", "adjudicator"]);
  const sources = new Set(
    steps
.map((s) => s.source)
.filter((x): x is string => !!x && !NOT_A_SOURCE.has(x) && !x.startsWith("opentargets-")),
  );

  const selectedEndpoint = matchEndpoint(endpoints, condition);
  const registerText =
    assessment && (register === "technical" ? assessment.text_technical : assessment.text_plain);

  const launch = (
    <div className={`launch ${started ? "compact" : ""}`} ref={launchRef}>
      {!started && (
        <p className="launch-line t-body">
          Takes a gene and a skin or hair endpoint, and returns whether human evidence places
          the gene upstream of the endpoint or alongside it.
        </p>
      )}
      <form
        className="qform"
        onSubmit={(e) => {
          e.preventDefault();
          run(gene, condition);
        }}
      >
        <div className="qfields">
          <GeneField value={gene} onChange={setGene} compact={started} />
          <EndpointMenu
            endpoints={endpoints}
            value={condition}
            onChange={setCondition}
            compact={started}
            evidence={epEvidence}
            evidenceGene={epEvidenceGene}
          />
        </div>
        <div className="qactions">
          <button className="btn-filled" disabled={running}>
            {running ? "Running" : "Assess"}
          </button>
          <button
            type="button"
            className="btn-outline"
            aria-expanded={helpOpen}
            onClick={() => setHelpOpen((h) => !h)}
          >
            How this works
          </button>
          <div className="pop-wrap pop-examples">
            <button
              type="button"
              className="btn-outline"
              aria-expanded={examplesOpen}
              aria-haspopup="menu"
              onClick={() => setExamplesOpen((x) => !x)}
            >
              Examples
              <svg className="epick-caret" viewBox="0 0 16 16" aria-hidden>
                <path d="M4 6.2 8 10.2l4-4" fill="none" stroke="currentColor" strokeWidth="1.6"
                  strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            {examplesOpen && (
              <div className="menu wide" role="menu">
                {PRESETS.map(([g, c, why]) => (
                  <button
                    key={g + c}
                    role="menuitem"
                    className="menu-row"
                    onClick={() => {
                      setGene(g);
                      setCondition(c);
                      setExamplesOpen(false);
                      run(g, c);
                    }}
                  >
                    <span className="menu-row-main t-mono">{g} · {c}</span>
                    <span className="menu-row-sub t-body-s">{why}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="pop-wrap pop-more">
            <button
              type="button"
              className="icon-btn"
              aria-label="More options"
              aria-expanded={menu}
              onClick={() => setMenu((m) => !m)}
            >
              <svg viewBox="0 0 16 16" aria-hidden fill="currentColor">
                <circle cx="3" cy="8" r="1.3" /><circle cx="8" cy="8" r="1.3" />
                <circle cx="13" cy="8" r="1.3" />
              </svg>
            </button>
            {menu && (
              <div className="menu" role="menu">
                <label>
                  <input
                    type="checkbox"
                    checked={outage}
                    onChange={(e) => setOutage(e.target.checked)}
                  />
                  <span>
                    <span className="t-body-s">Simulate source outage (demo)</span>
                    <span className="hint t-body-s">
                      Forces the evidence source to fail for the next run.
                    </span>
                  </span>
                </label>
              </div>
            )}
          </div>
        </div>
      </form>
      {selectedEndpoint?.refuse && (
        <p className="launch-flag t-body-s">
          This borrow is marked refuse in the curated table. The run will stop without a call
          unless human outcome evidence exists for the gene.
        </p>
      )}
    </div>
  );

  return (
    <>
      <header className="topbar">
        <div className="topbar-in">
          <span className="wordmark">
            BIO<span>LEAD</span>
          </span>
          <div className="seg modeseg" role="group" aria-label="Mode">
            <button
              className={mode === "single" ? "on" : ""}
              aria-pressed={mode === "single"}
              onClick={() => setMode("single")}
            >
              One gene
            </button>
            <button
              className={mode === "list" ? "on" : ""}
              aria-pressed={mode === "list"}
              onClick={() => setMode("list")}
            >
              Assess a list
            </button>
          </div>
          {started && mode === "single" && (
            <span className="tagline t-body-s">
              Driver or passenger, with the borrowed evidence shown.
            </span>
          )}
          {started && mode === "single" && (
            <button className="btn-ghost" onClick={() => { setStarted(false); setAssessment(null); setSteps([]); }}>
              New query
            </button>
          )}
        </div>
      </header>

      {mode === "list" ? (
        <main className="shell batchshell">
          <Batch
            endpoints={endpoints}
            onOpenGene={(g, c) => {
              // A row in the triage table is a hand-off, not a dead end: opening one
              // switches to single mode and runs the full assessment, model read included.
              setMode("single");
              setGene(g);
              setCondition(c);
              run(g, c);
            }}
          />
        </main>
      ) : (
      <main className={`shell ${started ? "result" : "landing"}`}>
        {launch}

        <div className="column">
          {helpOpen && <Help onClose={() => setHelpOpen(false)} />}

          {started && (
            <div className="trace enter">
              <button className="trace-bar" onClick={() => setTraceOpen((o) => !o)} aria-expanded={traceOpen}>
                <span className="t-label">Reasoning trace</span>
                <span className="trace-sum t-body-s num">
                  {steps.length} steps · {(elapsed / 1000).toFixed(1)}s · {sources.size} sources
                </span>
                <span className={`caret ${traceOpen ? "open" : ""}`}>
                  <svg viewBox="0 0 16 16" aria-hidden>
                    <path d="M4 6.2 8 10.2l4-4" fill="none" stroke="currentColor" strokeWidth="1.6"
                      strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
              </button>
              {running && (
                <div className="progress" role="progressbar" aria-label="Assessing">
                  <i />
                </div>
              )}
              {traceOpen && (
                <ol className="steps">
                  {steps.map((s, i) => (
                    <li key={i} className={`step ${s.tone ?? ""}`}>
                      <div className="step-top">
                        <span className="step-label">{s.label}</span>
                        {s.source && <Ref href={s.href ?? ""}>{s.source}</Ref>}
                        <span className="step-ms">{s.ms < 1 ? "<1ms" : `${s.ms}ms`}</span>
                      </div>
                      {s.detail && <p className="step-detail t-body-s">{s.detail}</p>}
                      {s.more && (
                        <>
                          <button className="step-more" onClick={() => setOpen((o) => ({ ...o, [i]: !o[i] }))}>
                            {open[i] ? "hide detail" : "show detail"}
                          </button>
                          {open[i] && <p className="step-detail t-body-s">{s.more}</p>}
                        </>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}

          {assessment && v && (
            <article className="assessment enter">
              <p className="eyebrow t-mono">
                {assessment.gene}
                {assessment.ensembl_id && (
                  <Ref href={`https://ensembl.org/Homo_sapiens/Gene/Summary?g=${assessment.ensembl_id}`}>
                    {assessment.ensembl_id}
                  </Ref>
                )}
              </p>
              <h2 className="t-display">{assessment.condition_as_typed}</h2>
              <p className="mode t-body">
                <strong>{assessment.mode} mode.</strong> {assessment.mode_reason}
              </p>

              {assessment.resolved_disease_name &&
                assessment.resolved_disease_name.toLowerCase() !==
                  assessment.condition_as_typed.toLowerCase() && (
                  <div className={`banner ${assessment.term_substituted ? "warn" : "note"}`}>
                    <strong className="t-body">
                      Asked about “{assessment.condition_as_typed}”. The source answered about “
                      {assessment.resolved_disease_name}”.
                    </strong>
                    <span className="t-body-s">
                      {assessment.term_substituted
                        ? "Not declared in the borrow table."
                        : "Declared in the borrow table."}
                    </span>
                  </div>
                )}

              <div className="verdict-wrap">
              <section className="verdict">
                {(() => {
                  const r = recommendation(v.position, v.targetability, v.confidence);
                  return (
                    <div className="rec">
                      <p className="rec-verb">{r.verb}.</p>
                      <p className="rec-why t-body">{r.because}</p>
                    </div>
                  );
                })()}
                <div>
                  <span className="vlabel t-label">
                    Position
                    <button
                      type="button"
                      className="vdef-btn"
                      aria-expanded={defs}
                      onClick={() => setDefs((d) => !d)}
                      title="What these three fields mean"
                    >
                      ⓘ
                    </button>
                  </span>
                  <span className={`vvalue pos-${v.position.toLowerCase()}`}>
                    {v.position.replace(/_/g, " ").toLowerCase()}
                  </span>
                  {defs && (
                    <span className="vdef vdef-open t-body-s">
                      Whether the evidence places this gene before or after the process starts.
                      Derived from whether human genetic association exists, not from pathway
                      topology.
                    </span>
                  )}
                </div>
                <div>
                  <span className="vlabel t-label">Modulation outcome</span>
                  <span className={`vvalue tgt-${v.targetability.toLowerCase()}`}>
                    {TGT_LABEL[v.targetability]}
                  </span>
                  {defs && (
                    <span className="vdef vdef-open t-body-s">
                      Whether modulating this gene moves the endpoint. Not whether it can be
                      drugged.
                    </span>
                  )}
                </div>
                <div>
                  <span className="vlabel t-label">Confidence</span>
                  <span className="vvalue">{v.confidence ?? "n/a"}</span>
                  {defs && (
                    <span className="vdef vdef-open t-body-s">
                      How much the evidence behind this call can carry. Capped by the mode, by
                      the borrow, and by whether the clinical evidence was retrieved or
                      asserted.
                    </span>
                  )}
                </div>
                <p className="reasoning t-body-s">{v.reasoning}</p>
                {v.rule_fired && <p className="rule">{v.rule_fired}</p>}

                {/* Untested and tested-but-silent are different facts and used to render
                    identically. The second is the largest band in the retrievable set. */}
                <OutcomeState
                  state={v.outcome_state}
                  trials={v.outcome_trials ?? []}
                  reason={v.outcome_reason}
                  path={v.outcome_path}
                />


                {/* An abstention is the tool's most common output, so it has to read as a
                    result. Without this the screen shows three greyed fields and looks
                    broken, and a visitor concludes the site failed rather than that
                    nothing is known. Everything here is already on the wire. */}
                {v.position === "INSUFFICIENT" && (
                  <div className="insuf">
                    <h3 className="insuf-h t-label">What was checked</h3>
                    <ul className="insuf-list t-body-s">
                      <li>
                        <b>Human genetics</b> for this endpoint:{" "}
                        {assessment.tier_profile.tiers["2"]?.length
                          ? "found"
                          : "queried, nothing returned"}
                      </li>
                      <li>
                        <b>Human intervention outcome</b>:{" "}
                        {assessment.tier_profile.tiers["1a"]?.length ||
                        assessment.tier_profile.tiers["1b"]?.length
                          ? "found"
                          : "queried, nothing returned"}
                      </li>
                      <li>
                        <b>Functional and expression evidence</b>:{" "}
                        {assessment.tier_profile.tiers["3"]?.length ||
                        assessment.tier_profile.tiers["5"]?.length
                          ? "found"
                          : "queried, nothing returned"}
                      </li>
                      {assessment.tier_profile.could_not_check.length > 0 && (
                        <li className="insuf-warn">
                          <b>Could not be read</b>, so absence here is not a finding:{" "}
                          {assessment.tier_profile.could_not_check.join("; ")}
                        </li>
                      )}
                    </ul>
                    {assessment.resolving_experiment && (
                      <>
                        <h3 className="insuf-h t-label">What would change this</h3>
                        <p className="t-body-s">
                          <b>{assessment.resolving_experiment.label}.</b>{" "}
                          {assessment.resolving_experiment.assay}{" "}
                          {assessment.resolving_experiment.what_a_positive_would_change}
                        </p>
                      </>
                    )}
                    <p className="insuf-foot t-body-s">
                      Abstaining is the common outcome. Of the 50 genes in the differential
                      expression preset, the tool places two.
                    </p>
                  </div>
                )}
              </section>
              {/* Locator and legend, not a second verdict. It shows where this call sits
                  among the calls the tool can make, which is the part the two fields above
                  cannot show on their own. */}
              <aside className="verdict-matrix" aria-label="Where this call sits">
                <h3 className="t-label">Where this sits</h3>
                <Matrix
                  variant="locator"
                  active={{
                    position: v.position,
                    targetability: v.targetability,
                    outcomeMeasuredOn: v.outcome_measured_on,
                    outcomeState: v.outcome_state,
                  }}
                />
              </aside>
              </div>

              {/* OUTSIDE `verdict-wrap` ON PURPOSE. These went inside the verdict panel
                  first and rendered as a one-word-wide ribbon down its narrow column: the
                  panel is a fixed narrow card and these are prose plus trial lists. They
                  belong in the full-width flow with the write-up. */}
              <OutcomeSplit
                consensus={v.outcome_consensus ?? null}
                split={v.outcome_split ?? null}
                minority={v.outcome_minority ?? []}
                excluded={v.outcome_excluded ?? []}
              />

              {/* Three outcome providers exist and they disagree. Naming the one that
                  answered is the difference between a reader quoting the rebuild and a
                  reader quoting the curated file believing it was the rebuild. */}
              <ProviderBadge provider={v.outcome_provider} />

              {registerText && (
                <section className="register">
                  <div className="register-head">
                    <h3 className="t-title">Written up</h3>
                    <div className="seg" role="group" aria-label="Register">
                      <button
                        className={register === "technical" ? "on" : ""}
                        aria-pressed={register === "technical"}
                        onClick={() => setRegister("technical")}
                      >
                        Technical
                      </button>
                      <button
                        className={register === "plain" ? "on" : ""}
                        aria-pressed={register === "plain"}
                        onClick={() => setRegister("plain")}
                      >
                        Plain language
                      </button>
                    </div>
                  </div>
                  <p className="register-text t-body" aria-live="polite">{registerText}</p>
                  <p className="register-src t-body-s">
                    {assessment.adjudicator_is_stub
                      ? "Written by the deterministic stub, not a model. No API key is set."
                      : ""}
                    {assessment.agreement === false &&
                      " That read differs from the verdict above. The rule verdict is the verdict of record."}
                  </p>
                </section>
              )}

              {mv && (
                <section className="dual">
                  <div className="dual-head">
                    <h3 className="t-title">Two independent reads</h3>
                    <span className={`state-chip ${assessment.agreement ? "agree" : "disagree"}`}>
                      {assessment.agreement ? "agree" : "disagree"}
                    </span>
                    <button className="info" aria-label="About the two reads" onClick={() => setDualInfo((x) => !x)}>
                      <InfoIcon />
                    </button>
                  </div>
                  <div className="dual-cols">
                    <div className="dual-card">
                      <h4 className="t-label">Rules, verdict of record</h4>
                      <span className="dual-v">
                        {v.position.replace(/_/g, " ").toLowerCase()} · {TGT_LABEL[v.targetability]}
                      </span>
                      <p className="t-body-s">Deterministic against {assessment.data_version}.</p>
                    </div>
                    <div className="dual-card">
                      <h4 className="t-label">
                        {assessment.adjudicator_is_stub ? "Stub, second read" : "Model, second read"}
                      </h4>
                      <span className="dual-v">
                        {mv.position.replace(/_/g, " ").toLowerCase()} · {TGT_LABEL[mv.targetability]}
                      </span>
                      {/* TWO SENTENCES, THE REST BEHIND A DISCLOSURE. The full reasoning
                          restated the technical write-up almost verbatim, so the page said
                          the same thing three times: write-up, this column, and the trace.
                          What this column is for is whether the second read AGREES, which is
                          the verdict line above. */}
                      {(() => {
                        const full = (mv.reasoning || "").trim();
                        const bits = full.split(/(?<=\.)\s+/);
                        const head = bits.slice(0, 2).join(" ");
                        const rest = bits.slice(2).join(" ");
                        return (
                          <>
                            <p className="t-body-s">{head}</p>
                            {rest && (
                              <details className="disclose">
                                <summary>The rest of the second read</summary>
                                <p className="disclose-body t-body-s">{rest}</p>
                              </details>
                            )}
                          </>
                        );
                      })()}
                    </div>
                  </div>
                  {dualInfo && (
                    <p className="dual-info t-body-s">
                      The model never sees the rule verdict. The rule verdict is the verdict of
                      record. On disagreement both are shown.
                    </p>
                  )}
                </section>
              )}

              {assessment.reachability && <Reach r={assessment.reachability} />}

              {assessment.proxy && assessment.proxy.borrow_type === "DISEASE_BORROW" && (
                <section className="borrow">
                  <h3 className="t-title">
                    Where this evidence comes from
                    <span className={`rating r-${assessment.proxy.rating.toLowerCase()}`}>
                      {assessment.proxy.rating}
                    </span>
                  </h3>
                  <p className="t-body-s">
                    <strong>{assessment.proxy.display_name}</strong> has no genetics of its own.
                    Borrowed from <strong>{assessment.proxy.borrowed_from}</strong>.
                  </p>
                  <p className="t-body-s">{assessment.proxy.rationale}</p>
                  <p className="misses t-body-s">
                    <strong>Misses.</strong> {assessment.proxy.what_it_misses}
                  </p>
                  {assessment.proxy.population_caveat && (
                    <p className="caveat t-body-s">
                      <strong>Coverage.</strong> {assessment.proxy.population_caveat}
                    </p>
                  )}
                </section>
              )}

              <section className="ledger">
                <h3 className="t-title">What was found</h3>
                {Object.entries(assessment.tier_profile.tiers)
.sort()
.map(([tier, items]) => (
                    <div className="tier-card" key={tier}>
                      <div className="thead">
                        <span className="assist-chip">tier {tier}</span>
                        <span className="t-label thead-name">{TIER_LABEL[tier] ?? tier}</span>
                      </div>
                      <div className="rows">
                        {(items as EvidenceItem[]).map((it, i) => {
                          // Prefer the identifier the pipeline actually fetched over one
                          // parsed out of the prose; the claim text does not always name it.
                          const nct = it.raw?.nct_id;
                          const link = nct
                            ? { label: nct, href: `https://clinicaltrials.gov/study/${nct}` }
                            : linkFor(it.summary);
                          return (
                            <div className="row" key={i}>
                              <span className={`prov ${it.provenance.toLowerCase()}`}>
                                {it.provenance === "RETRIEVED" ? "fetched" : "asserted"}
                              </span>
                              <span className="sum t-body-s">
                                {it.summary}
                                <span className="refs">
                                  {link && <Ref href={link.href}>{link.label}</Ref>}
                                  <Ref
                                    href={sourceHref(it.source, {
                                      gene: assessment.ensembl_id,
                                      disease: assessment.resolved_disease_id,
                                    })}
                                  >
                                    {it.source}
                                  </Ref>
                                </span>
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}

                {(assessment.tier_profile.checked_and_empty.length > 0 ||
                  assessment.tier_profile.could_not_check.length > 0) && (
                  <div className="absent-grid">
                    {assessment.tier_profile.checked_and_empty.length > 0 && (
                      <div className="absent-card empty">
                        <h4 className="t-label">Checked and empty</h4>
                        {assessment.tier_profile.checked_and_empty.map((s, i) => (
                          <p key={i} className="t-body-s">{s}</p>
                        ))}
                      </div>
                    )}
                    {assessment.tier_profile.could_not_check.length > 0 && (
                      <div className="absent-card unknown">
                        <h4 className="t-label">Could not check</h4>
                        {assessment.tier_profile.could_not_check.map((s, i) => (
                          <p key={i} className="t-body-s">{s}</p>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </section>

              {assessment.conflicts.length > 0 && (
                <section className="conflicts">
                  <h3 className="t-title">Conflicts</h3>
                  {assessment.conflicts.map((c, i) => (
                    <p key={i} className="t-body-s">{c}</p>
                  ))}
                </section>
              )}

              {assessment.limitations.length > 0 && (
                <section className="limits">
                  <h3 className="t-title">Limitations</h3>
                  <ul>
                    {assessment.limitations.map((l, i) => (
                      <li key={i} className="t-body-s">{l}</li>
                    ))}
                  </ul>
                </section>
              )}

              {assessment.resolving_experiment && (
                <section className="experiment">
                  <h3 className="t-title">Resolving experiment</h3>
                  <p className="assay">{assessment.resolving_experiment.assay}</p>
                  <p className="gap">{assessment.resolving_experiment.label}</p>
                  <p className="t-body-s">
                    {assessment.resolving_experiment.what_a_positive_would_change}
                  </p>
                  <p className="refs">
                    <Ref href="https://patents.google.com/patent/US12564547B2/en">US 12,564,547 B2</Ref>
                  </p>
                </section>
              )}

              <footer className="prov-footer t-body-s">
                <span className="t-mono">{assessment.data_version}</span>
                <span className="t-mono num">{assessment.assessed_at}</span>
              </footer>
            </article>
          )}
        </div>
      </main>
      )}
    </>
  );
}

const STAGE_SOURCE: Record<string, string | undefined> = {
  RESOLVE_GENE: "opentargets",
  RESOLVE_CONDITION: "opentargets",
  FETCH_EVIDENCE: "opentargets",
  LOOKUP_PROXY: "config/proxies.yaml",
  MAP_TIERS: "config/tiers.yaml",
  SELECT_EXPERIMENT: "config/experiments.yaml",
  MODEL_ADJUDICATE: "anthropic",
};

function summarise(d: any): string | undefined {
  if (d.stage === "RESOLVE_GENE") return d.resolved ?? d.note ?? undefined;
  if (d.stage === "LOOKUP_PROXY") return d.proxy ? `${d.proxy} (${d.rating})` : "no curated borrow";
  if (d.stage === "RESOLVE_CONDITION") return d.resolved ?? "did not resolve";
  if (d.stage === "MAP_TIERS" && d.tiers) {
    const t = Object.entries(d.tiers).map(([k, n]) => `${k}:${n}`);
    return t.length ? `tiers ${t.join(" ")}` : "nothing";
  }
  if (d.stage === "DETECT_CONFLICTS") return `${d.count} found`;
  if (d.stage === "MODEL_ADJUDICATE") return d.ran ? "adjudicated" : "skipped";
  return undefined;
}

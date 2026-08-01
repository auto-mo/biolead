import { useCallback, useEffect, useRef, useState } from "react";
import Matrix from "./Matrix";
import Reach from "./Reach";
import type { BatchResult, BatchRow, Preset, ProxyRow } from "./types";
import EndpointMenu from "./EndpointMenu";
import { API } from "./apiBase";

/** Batch triage. One condition, many genes.
 *
 *  The summary line leads and the table follows. That ordering is the argument: a reader
 *  who takes one thing from this screen should take the count, not a row. The matrix sits
 *  between them because it is the count broken out by where the genes actually landed.
 *
 *  The table never hides an abstention. They sort to the bottom as a labelled band, so a
 *  scientist looking for a gene they care about finds it and finds out the tool declined,
 *  rather than finding nothing and drawing their own conclusion from the silence.
 */

const CLASS_LABEL: Record<string, string> = {
  TIER_1_OR_2: "Genetics or a human outcome",
  EXPRESSION_OR_LITERATURE_ONLY: "Expression and literature only",
  NO_ASSOCIATION: "Nothing beyond this list",
  OTHER_EVIDENCE_ONLY: "Animal or pathway only",
  NOT_ASSESSABLE: "Not assessable",
  COULD_NOT_CHECK: "Source failed",
};

const TGT_SHORT: Record<string, string> = {
  ACTIONABLE: "benefit shown",
  NOT_ACTIONABLE: "no benefit",
  UNKNOWN: "untested",
};

function Headline({ r }: { r: BatchResult }) {
  const s = r.summary;
  const rest = s.expression_or_literature_only + s.no_association;
  return (
    <div className="bl-headline">
      <p className="bl-line">
        Of <b className="num">{s.input_count}</b> genes,{" "}
        <b className="num bl-hit">{s.tier_1_or_2}</b>{" "}
        {s.tier_1_or_2 === 1 ? "has" : "have"} tier 1 or tier 2 human evidence.{" "}
        <b className="num bl-miss">{rest}</b>{" "}
        {rest === 1 ? "has" : "have"} nothing beyond expression and literature.
      </p>
      <p className="bl-sub t-body-s">
        {s.other_evidence_only > 0 && (
          <>
            <span className="num">{s.other_evidence_only}</span>{" "}
            {s.other_evidence_only === 1 ? "has" : "have"} an animal model or pathway
            annotation and nothing human.{" "}
          </>
        )}
        Of the {rest}, <span className="num">{s.expression_or_literature_only}</span> carry
        literature in the source and <span className="num">{s.no_association}</span> have no
        association at all, so the measurement that put them on the list is the whole case
        for them.
        {s.not_assessable > 0 && (
          <>
            {" "}
            <span className="num">{s.not_assessable}</span> could not be assessed.
          </>
        )}
      </p>
      {!s.counts_trustworthy && s.trust_note && (
        <p className="bl-warn t-body-s">{s.trust_note}</p>
      )}
    </div>
  );
}

export default function Batch({
  endpoints,
  onOpenGene,
}: {
  endpoints: ProxyRow[];
  onOpenGene: (gene: string, condition: string) => void;
}) {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [warm, setWarm] = useState<any>(null);
  const [condition, setCondition] = useState("hair thinning");
  const [text, setText] = useState("");
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [stages, setStages] = useState<string[]>([]);
  const [result, setResult] = useState<BatchResult | null>(null);
  const [parse, setParse] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [detail, setDetail] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    fetch(`${API}/presets`)
.then((r) => r.json())
.then((d) => {
        setPresets(d.presets ?? []);
        setWarm(d.warm ?? null);
      })
.catch(() => setPresets([]));
    return () => esRef.current?.close();
  }, []);

  const start = useCallback((url: string) => {
    esRef.current?.close();
    setRunning(true);
    setResult(null);
    setError(null);
    setParse(null);
    setStages([]);
    setProgress(null);
    setShowAll(false);

    const es = new EventSource(url);
    esRef.current = es;
    const on = (n: string, fn: (d: any) => void) =>
      es.addEventListener(n, (e) => fn(JSON.parse((e as MessageEvent).data)));

    on("parse", setParse);
    on("batch_stage", (d) => {
      if (!d.done) return;
      setStages((p) => [...p, describeStage(d)]);
    });
    on("batch_progress", (d) => setProgress({ done: d.done, total: d.total }));
    on("batch_result", (d) => setResult(d as BatchResult));
    on("error", (d) => {
      setError(d.error);
      setRunning(false);
      es.close();
    });
    on("done", () => {
      setRunning(false);
      es.close();
    });
    es.onerror = () => {
      setRunning(false);
      es.close();
    };
  }, []);

  const runPreset = (p: Preset) => {
    setCondition(p.condition);
    start(`${API}/batch?preset=${encodeURIComponent(p.id)}&condition=${encodeURIComponent(p.condition)}`);
  };

  const runPasted = () => {
    if (!text.trim() || !condition.trim()) return;
    start(
      `${API}/batch?condition=${encodeURIComponent(condition)}&genes=${encodeURIComponent(text)}`,
    );
  };

  const rows = result?.rows ?? [];
  const called = rows.filter((r) => !isAbstained(r));
  const declined = rows.filter((r) => isAbstained(r));
  const shown = showAll ? declined : declined.slice(0, 12);

  return (
    <div className="batch">
      <section className="bl-input">
        <h2 className="t-title">Assess a list</h2>
        <p className="t-body-s bl-intro">
          A differential expression list, one condition, and a ranked table. The count above
          the table is the answer; the table is where it came from.
        </p>

        <div className="bl-presets">
          {presets.map((p) => (
            <button
              key={p.id}
              className="bl-preset"
              disabled={running}
              onClick={() => runPreset(p)}
            >
              <span className="bl-preset-main">
                {p.label} <span className="num bl-preset-n">{p.count} genes</span>
              </span>
              <span className="bl-preset-sub t-body-s">
                {p.truncation_note ?? p.what_it_is}
              </span>
            </button>
          ))}
        </div>
        {/* The citation sits under the list rather than on each card. Both presets are the
            same table, and repeating it made the cards look like two different papers. */}
        {presets.length > 0 && (
          <p className="bl-cite t-body-s">
            {presets[0].citation}{" "}
            <a href={presets[0].url} target="_blank" rel="noreferrer noopener">
              {presets[0].url.replace("https://", "")}
            </a>
          </p>
        )}
        {warm?.ok && (
          <p className="bl-warm t-body-s">
            Cache warm for the {warm.genes}-gene list, prepared in{" "}
            <span className="num">{warm.seconds}s</span> at startup.
          </p>
        )}

        <details className="bl-paste">
          <summary className="t-label">Or paste a list</summary>
          <div className="bl-paste-in">
            <EndpointMenu
              endpoints={endpoints}
              value={condition}
              onChange={setCondition}
              compact
            />
            <textarea
              className="bl-textarea t-mono"
              value={text}
              rows={7}
              spellCheck={false}
              placeholder={"One symbol per line, or paste a CSV or TSV column.\nFLG\nAR\nTYR"}
              onChange={(e) => setText(e.target.value)}
            />
            <button className="btn-filled" disabled={running || !text.trim()} onClick={runPasted}>
              {running ? "Running" : "Assess list"}
            </button>
          </div>
        </details>
      </section>

      {(running || stages.length > 0) && !result && (
        <section className="bl-run">
          <div className="progress" role="progressbar" aria-label="Assessing list">
            <i />
          </div>
          <ol className="bl-stages">
            {stages.map((s, i) => (
              <li key={i} className="t-body-s">
                {s}
              </li>
            ))}
          </ol>
          {progress && (
            <p className="bl-count t-body-s num">
              {progress.done} of {progress.total}
            </p>
          )}
        </section>
      )}

      {error && <p className="bl-error t-body-s">{error}</p>}

      {parse?.rejected_count > 0 && (
        <p className="bl-parse t-body-s">
          Read <span className="num">{parse.count}</span> symbols ({parse.detected}).
          Refused <span className="num">{parse.rejected_count}</span>:{" "}
          <span className="t-mono">{(parse.rejected ?? []).slice(0, 8).join(", ")}</span>
          {parse.duplicates_dropped > 0 && (
            <>
. Dropped <span className="num">{parse.duplicates_dropped}</span> duplicate
              {parse.duplicates_dropped === 1 ? "" : "s"}
            </>
          )}
.
        </p>
      )}

      {result && (
        <>
          <Headline r={result} />

          <Matrix
            variant="plot"
            items={rows
.filter((r): r is BatchRow & { verdict: NonNullable<BatchRow["verdict"]> } =>
                r.verdict !== null)
.map((r) => ({
                gene: r.gene,
                position: r.verdict.position,
                targetability: r.verdict.targetability,
                outcomeMeasuredOn: r.verdict.outcome_measured_on,
              }))}
            onSelect={(g) => onOpenGene(g, result.summary.condition_as_typed)}
          />

          <section className="bl-table-wrap">
            <h3 className="t-title">Ranked</h3>
            <table className="bl-table">
              <thead>
                <tr>
                  <th>Gene</th>
                  <th>Evidence</th>
                  <th>Position</th>
                  <th>Modulation</th>
                  <th>Conf.</th>
                  <th>Topical</th>
                  <th>Top score</th>
                </tr>
              </thead>
              <tbody>
                {called.map((r) => (
                  <Row key={r.gene} r={r} onOpen={() => onOpenGene(r.gene, result.summary.condition_as_typed)} />
                ))}
              </tbody>
              {declined.length > 0 && (
                <>
                  <tbody className="bl-declined-head">
                    <tr>
                      <td colSpan={7}>
                        <span className="t-label">
                          Abstained · <span className="num">{declined.length}</span>
                        </span>
                        <span className="t-body-s">
                          The tool declined to call these. Shown, not hidden.
                        </span>
                      </td>
                    </tr>
                  </tbody>
                  <tbody className="bl-declined">
                    {shown.map((r) => (
                      <Row
                        key={r.gene}
                        r={r}
                        onOpen={() => onOpenGene(r.gene, result.summary.condition_as_typed)}
                      />
                    ))}
                    {declined.length > shown.length && (
                      <tr>
                        <td colSpan={7}>
                          <button className="btn-ghost" onClick={() => setShowAll(true)}>
                            Show the remaining {declined.length - shown.length}
                          </button>
                        </td>
                      </tr>
                    )}
                  </tbody>
                </>
              )}
            </table>
          </section>

          <section className="bl-meta">
            <button
              className="btn-ghost"
              aria-expanded={detail}
              onClick={() => setDetail((d) => !d)}
            >
              {detail ? "Hide run detail" : "Run detail"}
            </button>
            <span className="t-body-s num">
              {/* Zero calls is the warmed path, not a failure to run. Printing "0 calls"
                  unqualified reads as a bug, which is the opposite of what it means. */}
              {result.call_count === 0
                ? "served from cache, no calls"
                : `${result.call_count} calls`}{" "}
              · {(result.elapsed_seconds ?? 0) < 0.05 ? "<0.1" : result.elapsed_seconds}s ·{" "}
              {result.data_version}
            </span>
            {detail && (
              <div className="bl-meta-in">
                {result.source && <p className="t-body-s">Source: {result.source}</p>}
                <table className="bl-limits">
                  <thead>
                    <tr>
                      <th>Source</th>
                      <th>Calls</th>
                      <th>Peak conc.</th>
                      <th>Cap</th>
                      <th>Rate</th>
                      <th>Retries</th>
                      <th>Throttled</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.limiter_stats.map((l) => (
                      <tr key={l.source}>
                        <td className="t-mono">{l.source}</td>
                        <td className="num">{l.calls}</td>
                        <td className="num">{l.peak_concurrency}</td>
                        <td className="num">{l.max_concurrent}</td>
                        <td className="num">{l.rate_per_second}/s</td>
                        <td className="num">{l.retries}</td>
                        <td className="num">{l.throttled_seconds}s</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="t-body-s num">
                  Cache: {result.cache_stats.hits} hits, {result.cache_stats.misses} misses,{" "}
                  {result.cache_stats.single_flight_joins} single-flight joins.
                </p>
                <ul className="bl-limitations">
                  {result.limitations.map((l, i) => (
                    <li key={i} className="t-body-s">
                      {l}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function isAbstained(r: BatchRow) {
  return !r.verdict || (r.verdict.rule_fired ?? "").startsWith("ABSTAIN_");
}

function Row({ r, onOpen }: { r: BatchRow; onOpen: () => void }) {
  const v = r.verdict;
  const top = Object.entries(r.datatype_scores).sort((a, b) => b[1] - a[1])[0];
  return (
    <tr className={`ec-${r.evidence_class.toLowerCase()}`}>
      <th scope="row">
        <button className="bl-gene t-mono" onClick={onOpen} title="Open the full assessment">
          {r.gene}
        </button>
      </th>
      <td>
        <span className={`ec-chip ec-${r.evidence_class.toLowerCase()}`}>
          {CLASS_LABEL[r.evidence_class]}
        </span>
      </td>
      <td className={v ? `pos-${v.position.toLowerCase()}` : ""}>
        {v ? v.position.replace(/_/g, " ").toLowerCase() : "-"}
      </td>
      <td>{v ? TGT_SHORT[v.targetability] : "-"}</td>
      <td className="num">{v?.confidence ?? "-"}</td>
      <td>{r.reachability ? <Reach r={r.reachability} compact /> : "-"}</td>
      <td className="num">
        {top ? (
          <>
            {top[1].toFixed(2)} <span className="bl-dt t-body-s">{top[0].replace(/_/g, " ")}</span>
          </>
        ) : (
          "-"
        )}
      </td>
    </tr>
  );
}

function describeStage(d: any): string {
  switch (d.stage) {
    case "RESOLVE_CONDITION":
      return `condition resolved once for ${d.resolved_once_for} genes: “${d.searched}” to “${d.resolved}”${d.proxy ? `, borrow ${d.proxy} (${d.rating})` : ""}`;
    case "RESOLVE_GENES":
      return `symbols resolved in ${d.calls} call: ${d.resolved} exact, ${d.unresolved} unresolved, ${d.ambiguous} ambiguous`;
    case "FETCH_EVIDENCE":
      return `evidence for ${d.targets_checked} targets in ${d.calls} call: ${d.targets_with_association} have an association${d.trustworthy ? "" : ", TRUNCATED"}`;
    case "ASSESS":
      return d.done ? "assessed" : "assessing";
    default:
      return d.stage;
  }
}

import type { Reach as ReachValue, Reachability } from "./types";

/** The topical reachability gate.
 *
 *  VISUALLY SEPARATE ON PURPOSE. It sits outside the verdict panel, carries its own
 *  heading naming it as a different question, uses a vocabulary that shares no word with
 *  Position or Modulation outcome, and is drawn on a different surface. The earlier work
 *  split one verdict into two axes that mean different things; rendering a commercial
 *  constraint in the same visual language as a causal one would quietly undo that.
 *
 *  A gene can be causally right and commercially unreachable. FLG is that case: upstream
 *  driver on human genetics, and nothing has ever been made against it in any modality.
 */

const LABEL: Record<ReachValue, string> = {
  REACHABLE: "A topical could plausibly reach this",
  HARD_TO_REACH: "Hard to reach topically",
  OUT_OF_REACH: "Not a topical target",
  UNKNOWN: "Not enough to say",
};

const DEPTH_LABEL: Record<string, string> = {
  EPIDERMAL: "Epidermis",
  APPENDAGEAL: "Follicles and glands",
  DERMAL: "Dermis",
};

const COMPARTMENT_ORDER = ["epidermis", "appendage", "dermis"] as const;
const COMPARTMENT_LABEL: Record<string, string> = {
  epidermis: "Epidermis",
  appendage: "Follicles, glands",
  dermis: "Dermis",
};

/** Depth as a stack, surface at the top. The bar is the immunohistochemistry call, which
 *  is a four-level ordinal, so it is drawn as four steps and never as a percentage. */
const LEVEL_STEPS: Record<string, number> = {
  high: 4,
  medium: 3,
  low: 2,
  "not detected": 0,
};

export default function Reach({ r, compact = false }: { r: Reachability; compact?: boolean }) {
  if (compact) {
    return (
      <span className={`reach-pill rv-${r.verdict.toLowerCase()}`} title={LABEL[r.verdict]}>
        {r.verdict === "REACHABLE"
          ? "reachable"
          : r.verdict === "HARD_TO_REACH"
            ? "hard"
            : r.verdict === "OUT_OF_REACH"
              ? "out of reach"
              : "unknown"}
      </span>
    );
  }

  return (
    <section className="reach">
      <div className="reach-head">
        <h3 className="t-title">Could a topical reach it</h3>
        <span className={`reach-pill rv-${r.verdict.toLowerCase()}`}>{LABEL[r.verdict]}</span>
      </div>
      <p className="reach-def t-body-s">
        A separate question from the verdict above. It does not change the causal call and
        never feeds into it.
      </p>

      {/* COLLAPSED TO ITS CONCLUSION. Expanded, this was the longest block on the page, and
          on most genes it concludes "not enough to say". The working stays one click away. */}
      <details className="disclose">
        <summary>Where in skin, and what it would take</summary>
        <div className="disclose-body">

      <div className="reach-grid">
        <div className="reach-card">
          <h4 className="t-label">Where in skin</h4>
          <div className="reach-depth">
            {COMPARTMENT_ORDER.map((c) => {
              const level = r.compartments[c];
              const steps = level ? (LEVEL_STEPS[level] ?? 0) : -1;
              return (
                <div key={c} className={`rd-row ${steps > 0 ? "on" : ""}`}>
                  <span className="rd-name t-body-s">{COMPARTMENT_LABEL[c]}</span>
                  <span className="rd-bar" aria-hidden>
                    {[1, 2, 3, 4].map((i) => (
                      <i key={i} className={steps >= i ? "on" : ""} />
                    ))}
                  </span>
                  <span className="rd-val t-body-s">
                    {steps < 0 ? "not measured" : level}
                  </span>
                </div>
              );
            })}
          </div>
          <p className="reach-meta t-body-s num">
            {r.skin_ntpm !== null && <>skin RNA {r.skin_ntpm}&nbsp;nTPM</>}
            {r.skin_ih && <> · protein stain {r.skin_ih}</>}
            {r.depth && <> · shallowest compartment {DEPTH_LABEL[r.depth]}</>}
          </p>
        </div>

        <div className="reach-card">
          <h4 className="t-label">What it would take</h4>
          {/* Only buckets that assert a molecule light this up. The localisation buckets
              are listed underneath and explicitly labelled as not tractability: showing
              them as an active modality contradicted the blocker sentence below. */}
          <ul className="reach-mod">
            <li className={r.sm_clinical.length || r.sm_structural.length ? "on" : ""}>
              <span className="t-body-s">Small molecule</span>
              <span className="t-mono">
                {r.sm_clinical.length
                  ? r.sm_clinical.join(", ")
                  : r.sm_structural.length
                    ? `${r.sm_structural.slice(0, 2).join(", ")} (plausible, none in clinic)`
                    : "nothing recorded"}
              </span>
            </li>
            <li className={r.ab_clinical.length ? "on" : ""}>
              <span className="t-body-s">Antibody</span>
              <span className="t-mono">
                {r.ab_clinical.length ? r.ab_clinical.join(", ") : "nothing recorded"}
              </span>
            </li>
          </ul>
          {r.ab_location_only.length > 0 && (
            <p className="reach-aside t-body-s">
              <span className="t-mono">{r.ab_location_only.slice(0, 3).join(", ")}</span> are
              localisation predictions, not evidence that a molecule exists.
            </p>
          )}
          <p className="reach-meta t-body-s">
            {r.secretome_location
              ? `Secreted: ${r.secretome_location}.`
              : r.subcellular.length
                ? `Inside the cell: ${r.subcellular.join(", ")}.`
                : "No subcellular data."}
          </p>
        </div>
      </div>

      {r.blockers.length > 0 && (
        <div className="reach-lines blockers">
          {r.blockers.map((b, i) => (
            <p key={i} className="t-body-s">
              {b}
            </p>
          ))}
        </div>
      )}
      {r.supports.length > 0 && (
        <div className="reach-lines supports">
          {r.supports.map((s, i) => (
            <p key={i} className="t-body-s">
              {s}
            </p>
          ))}
        </div>
      )}
      {r.unknowns.length > 0 && (
        <div className="reach-lines unknowns">
          {r.unknowns.map((u, i) => (
            <p key={i} className="t-body-s">
              {u}
            </p>
          ))}
        </div>
      )}
      <p className="reach-foot t-body-s">
        Location from the Human Protein Atlas, modality from Open Targets tractability.
        Nothing here models the stratum corneum, molecular weight or vehicle, so this
        narrows the list rather than settling a formulation. <span className="t-mono">{r.rule_fired}</span>
      </p>
        </div>
      </details>
    </section>
  );
}

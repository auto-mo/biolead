/* How this works. Collapsed by default, opened from the launch block.
 *
 * Copy rule for this file: state the fact, delete the interpretation. No sentence tells the
 * reader what to conclude about the design. No em dashes.
 */

export const TIERS: [string, string, string][] = [
  ["1a", "Human clinical outcome, retrieved",
   "A trial record fetched from ClinicalTrials.gov at query time, with its identifier shown."],
  ["1b", "Human clinical outcome, asserted",
   "A curated claim from config/clinical_facts.yaml. It will not load without sources, and on its own it cannot reach high confidence."],
  ["2", "Human genetics",
   "Germline variation associated with the disease. Variation exists before the phenotype does, which is what makes it evidence of position rather than of correlation."],
  ["3", "Functional, relevant human cell",
   "A perturbation in the cell type the endpoint lives in."],
  ["4", "Animal model or non-relevant cell",
   "Mouse skin and hair biology diverges substantially from human. Secondary support, never a tiebreaker."],
  ["5", "Expression and literature, inverted",
   "Expression correlation and publication volume. Read as an inverted signal: heavy literature with absent human genetics is the passenger pattern."],
];

const ABSTENTIONS: [string, string][] = [
  ["The gene symbol does not match exactly",
   "Symbol search is ranked and fuzzy. AR returns AR, FDXR, AREG, AKR1B1, ARX. The top hit is not taken."],
  ["No borrow is curated for the endpoint, or none is defensible",
   "Photoaging has no proxy. The row records that one was attempted and rejected."],
  ["The curated table marks the borrow as one to refuse",
   "Hyperpigmentation via vitiligo genetics. Real pigmentation loci appear and the direction is inverted."],
  ["A source that the verdict would have rested on could not be read",
   "Could-not-check is a different state from checked-and-empty, and the API does not distinguish them."],
  ["The only evidence is expression correlation and literature volume",
   "That establishes involvement, which is the starting question rather than the answer."],
  ["The source answered about a disease that was not asked for and not declared",
   "Asking about melasma returns freckles. That substitution is declared in the borrow table, so it passes. An undeclared one stops the run."],
];

export default function Help({ onClose }: { onClose: () => void }) {
  return (
    <section className="help" aria-label="How this works">
      <div className="help-head">
        <h2 className="t-headline">How this works</h2>
        <button className="btn-text" onClick={onClose}>Close</button>
      </div>

      <div className="help-grid">
        <article className="help-card">
          <h3 className="t-title">The fire truck problem</h3>
          <p className="t-body-s">
            Fire trucks are present at large fires and are not the cause of them. A gene whose
            expression rises with a condition may be responding to the condition rather than
            driving it.
          </p>
          <p className="t-body-s">
            Expression correlation and publication volume are recorded at tier 5 and read as an
            inverted signal. A gene with heavy literature and no human genetics is the pattern a
            passenger makes.
          </p>
        </article>

        <article className="help-card">
          <h3 className="t-title">Why a cosmetic endpoint needs a borrow</h3>
          <p className="t-body-s">
            Cosmetic endpoints have no genetics of their own. There is no genome-wide association
            study of sebum production and none of skin firmness, and querying skin pigmentation
            for TYR returns no association at all.
          </p>
          <p className="t-body-s">
            Evidence is therefore borrowed from a related disease that has been studied. Every
            borrow is written down in config/proxies.yaml with a rating and a note on what it
            misses, and both are shown in the endpoint list before the query runs.
          </p>
        </article>

        <article className="help-card">
          <h3 className="t-title">The two modes</h3>
          <p className="t-body-s">
            <strong>Genetics mode.</strong> Human genetic association is present and the call
            rests on it. A borrow rated low does not qualify, because low-rated genetics carries
            the authority of genetics without the applicability.
          </p>
          <p className="t-body-s">
            <strong>Mechanism mode.</strong> No qualifying genetics. The call rests on functional,
            animal or literature evidence, which cannot establish direction on its own.
          </p>
        </article>

        <article className="help-card">
          <h3 className="t-title">What abstention means</h3>
          <p className="t-body-s">
            The run stops and returns no call. Seven conditions trigger it. Tier 1 human outcome
            evidence overrides the first two, because a target with a measured human outcome can
            be assessed without a usable borrow.
          </p>
          <ul className="help-list">
            {ABSTENTIONS.map(([head, body]) => (
              <li key={head}>
                <span className="t-body-s"><strong>{head}.</strong> {body}</span>
              </li>
            ))}
          </ul>
        </article>
      </div>

      <h3 className="t-title help-legend-head">The tiers</h3>
      <p className="t-body-s help-legend-intro">
        Evidence is sorted by what it can establish, not by how much of it there is. Each row in
        an assessment carries its tier and whether it was fetched at query time or asserted from
        the curated file.
      </p>
      <ul className="legend">
        {TIERS.map(([t, name, body]) => (
          <li key={t} className="legend-row">
            <span className="assist-chip">tier {t}</span>
            <span>
              <span className="t-label legend-name">{name}</span>
              <span className="t-body-s">{body}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The split, and what the attribution cap set aside. Both render unconditionally.
 *
 *  THE SPLIT SHOWS WHETHER OR NOT THE MAJORITY WAS TAKEN. A rule that decides must show what
 *  it decided over. Twelve dupilumab trials reading benefit with two reading worse is one
 *  fact, not a fact and a footnote, and a reader shown only the winner cannot check the call.
 *  So the tally appears for a decided case, a unanimous case and an abstention alike, and
 *  every dissenting trial is named with its identifier and its enrolment.
 *
 *  THE EXCLUSIONS ARE DOING REAL WORK AND SO THEY ARE VISIBLE. SRD5A2 rests on finasteride
 *  because six dutasteride trials were set aside, dutasteride naming SRD5A1, SRD5A2 and
 *  SRD5A3. That exclusion is load-bearing in the verdict. Hiding it would be the same error
 *  the proxy table avoids by printing `what_it_misses` verbatim: a tool knowing something the
 *  reader does not.
 *
 *  Nothing here is a verdict field. It is the working behind one.
 */
import type { ExcludedTrial, OutcomeTrial } from "./types";

const CT = "https://clinicaltrials.gov/study/";

const READ_LABEL: Record<string, string> = {
  BENEFIT: "benefit",
  NO_BENEFIT: "no benefit",
  WORSE: "worse than comparator",
};

const CONSENSUS_COPY: Record<string, string> = {
  UNANIMOUS: "Every readable trial agreed.",
  DECIDED:
    "The trials disagreed. The majority was taken because at least twice as many read " +
    "one way as the other, and summed enrolment named the same winner.",
  BALANCED:
    "The trials disagreed and no verdict was taken from them. Either neither side reached " +
    "twice the other, or counting trials and summing enrolment named different winners.",
  NONE: "No trial resolved to a read.",
};

function TrialLine({ t }: { t: OutcomeTrial }) {
  return (
    <li>
      <a
        className="ref t-mono"
        href={`${CT}${t.nct_id}`}
        target="_blank"
        rel="noreferrer noopener"
      >
        {t.nct_id}
      </a>
      {t.read && <span className={`sp-read r-${t.read.toLowerCase()}`}>{READ_LABEL[t.read] ?? t.read.toLowerCase()}</span>}
      {t.enrollment != null && (
        <span className="sp-n num">n = {t.enrollment.toLocaleString()}</span>
      )}
      {t.drug && <span className="sp-drug">{t.drug.toLowerCase()}</span>}
      {t.path === "MODEL" && <span className="sp-path t-label">read by model</span>}
    </li>
  );
}

export default function OutcomeSplit({
  consensus,
  split,
  minority,
  excluded,
}: {
  consensus: string | null;
  split: string | null;
  minority: OutcomeTrial[];
  excluded: ExcludedTrial[];
}) {
  const hasSplit = Boolean(split && consensus);
  if (!hasSplit && excluded.length === 0) return null;

  return (
    <div className="outcome-split">
      {hasSplit && (
        <section className="sp-block">
          <p className="sp-head t-label">What the outcome was decided over</p>
          <p className="sp-tally t-body">{split}</p>
          <p className="sp-body t-body-s">{CONSENSUS_COPY[consensus!] ?? ""}</p>
          {minority.length > 0 && (
            <>
              <p className="sp-sub t-label">
                {minority.length} trial{minority.length === 1 ? "" : "s"} read otherwise
              </p>
              <ul className="sp-trials">
                {minority.map((t) => (
                  <TrialLine key={t.nct_id} t={t} />
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      {excluded.length > 0 && (
        <section className="sp-block sp-excluded">
          <p className="sp-head t-label">
            Set aside: {excluded.length} trial{excluded.length === 1 ? "" : "s"} that cannot
            answer for this gene
          </p>
          <p className="sp-body t-body-s">
            These trials exist and were read. They are excluded because the drug they tested
            does not name this gene alone, so their result cannot be attributed to it.
          </p>
          {/* GROUPED BY REASON, not listed under the first one. Trials get set aside for
              more than one reason in the same run: SRD5A2 drops dutasteride trials because
              the mechanism names three genes, and separately drops trials where finasteride
              is only a comparator arm. Printing one reason over both lists would attribute
              the wrong cause to half of them. */}
          {Array.from(new Set(excluded.map((e) => e.why))).map((why) => {
            const group = excluded.filter((e) => e.why === why);
            return (
              <div className="sp-group" key={why}>
                <ul className="sp-trials">
                  {group.map((e) => (
                    <li key={e.nct_id}>
                      <a
                        className="ref t-mono"
                        href={`${CT}${e.nct_id}`}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {e.nct_id}
                      </a>
                      {e.drug && <span className="sp-drug">{e.drug.toLowerCase()}</span>}
                      {e.named_targets.length > 1 && (
                        <span className="sp-targets t-mono">
                          names {e.named_targets.join(", ")}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
                <p className="sp-why t-body-s">{why}</p>
              </div>
            );
          })}
        </section>
      )}
    </div>
  );
}

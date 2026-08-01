/** The three states of not knowing, rendered as three things rather than as one.
 *
 *  `UNKNOWN` used to cover all of them, which made the largest band in the retrievable set
 *  invisible: 480 of 1,362 trials, 35%, completed and posted nothing. Larger than the 287
 *  with a readable result.
 *
 *  AR is the case. Three completed clascoterone trials in androgenetic alopecia, 1,560
 *  people between them, and not one posted a result. Rendering that as "untested" is false,
 *  and rendering it as a sentence the reader has to take on trust is not much better, so the
 *  trials are listed with their enrolments and their ids linked. A reader can open them and
 *  confirm the absence rather than believe it.
 */
import type { OutcomeTrial } from "./types";

const CT = "https://clinicaltrials.gov/study/";

const COPY: Record<string, { head: string; body: string }> = {
  NO_DRUG: {
    head: "No drug has been made against this target here",
    body:
      "No mechanism record names this gene for this condition. That is a retrieved fact " +
      "about the world, not a gap in what anyone curated.",
  },
  TESTED_UNREPORTED: {
    head: "Tested in people, and the result was never published",
    body:
      "A drug against this target completed human trials and no results were posted. " +
      "Somebody ran the experiment and did not say what happened, which is not the same " +
      "as nobody having tried.",
  },
  TESTED_REPORTED: {
    head: "Tested, and the trial says what happened",
    body: "",
  },
  NOT_ASSESSED: {
    head: "The outcome question was not answered",
    body:
      "The sources that would answer it could not be read. Absence here is a statement " +
      "about the lookup, not about the gene.",
  },
};

export default function OutcomeState({
  state,
  trials,
  reason,
  path,
}: {
  state: string;
  trials: OutcomeTrial[];
  reason?: string | null;
  path?: string | null;
}) {
  // TESTED_REPORTED already renders as the modulation outcome above; repeating it here
  // would say the same thing twice in different words.
  if (state === "TESTED_REPORTED" || state === "NOT_ASSESSED") return null;
  const copy = COPY[state];
  if (!copy) return null;

  const total = trials.reduce((n, t) => n + (t.enrollment ?? 0), 0);

  return (
    <div className={`outcome-state os-${state.toLowerCase().replace(/_/g, "-")}`}>
      <p className="os-head t-title">{copy.head}</p>
      <p className="os-body t-body-s">{reason || copy.body}</p>

      {trials.length > 0 && (
        <>
          <p className="os-count t-label">
            {trials.length} trial{trials.length === 1 ? "" : "s"}
            {total > 0 && (
              <>
                , <span className="num">{total.toLocaleString()}</span> participants
              </>
            )}
          </p>
          <ul className="os-trials">
            {trials.map((t) => (
              <li key={t.nct_id}>
                <a
                  className="ref t-mono"
                  href={`${CT}${t.nct_id}`}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  {t.nct_id}
                </a>
                {t.drug && <span className="os-drug">{t.drug.toLowerCase()}</span>}
                {t.status && <span className="os-status t-label">{t.status.toLowerCase()}</span>}
                {t.enrollment != null && (
                  <span className="os-n num">n = {t.enrollment.toLocaleString()}</span>
                )}
                <span className={`os-res ${t.has_results ? "yes" : "no"}`}>
                  {t.has_results ? "results posted" : "no results posted"}
                </span>
              </li>
            ))}
          </ul>
          <p className="os-confirm t-body-s">
            Open any of them. The absence is on the registry, not asserted here.
          </p>
        </>
      )}
      {path === "NONE" && trials.length === 0 && reason && (
        <p className="os-confirm t-body-s">{reason}</p>
      )}
    </div>
  );
}

/** Which outcome provider answered. Rendered on every result, not documented in a file.
 *
 *  Three exist and they disagree, and only one of them is the rebuild:
 *
 *    file       the curated `clinical_facts.yaml`. Bounded by how many rows a human typed.
 *    retrieved  retrieval only. No attribution cap and NO disagreement rule.
 *    graph      the outcome subgraph. Both applied.
 *
 *  A screenshot of a `file` verdict is indistinguishable from a screenshot of a `graph`
 *  verdict unless the screen says which it is, and the two do not always agree. Flagging
 *  that in a markdown file is not enough when the thing being quoted is a picture.
 */
const COPY: Record<string, { label: string; body: string }> = {
  file: {
    label: "curated file",
    body:
      "The outcome came from config/clinical_facts.yaml, a hand-written table. It cannot " +
      "tell an untested target from an uncurated one, and no attribution cap or " +
      "disagreement rule was applied.",
  },
  retrieved: {
    label: "retrieval, no agent",
    body:
      "The outcome was retrieved without the subgraph. The attribution cap and the " +
      "disagreement rule were NOT applied, so a multi-target drug's result may be " +
      "answering for a single gene here.",
  },
  graph: {
    label: "outcome subgraph",
    body:
      "The outcome was retrieved by the agent. Trials reached only through a drug that " +
      "names more than this gene were excluded, and where trials disagreed the rule " +
      "decided or abstained.",
  },
};

export default function ProviderBadge({ provider }: { provider?: string | null }) {
  const copy = provider ? COPY[provider] : undefined;
  if (!copy || !provider) return null;
  return (
    <div className={`outcome-provider op-${provider}`}>
      <div className="op-line">
        <span className="op-label t-label">Outcome answered by</span>
        <span className="op-name t-mono">{provider}</span>
        <span className="op-sub t-body-s">{copy.label}</span>
      </div>
      <p className="op-body t-body-s">{copy.body}</p>
    </div>
  );
}

/* The endpoint picker.
 *
 * A native <select> cannot do this. The OS draws the popup, which means one line per
 * option, no chips, and none of the app's type scale. This control has to show the borrow
 * and its rating BEFORE the query runs, so the borrow is a thing you choose rather than a
 * thing you discover in the output. That is the whole argument of the tool, so it gets a
 * real component.
 *
 * THE RATING IS DRAWN, NOT WRITTEN. It is the concept this dropdown teaches, and until now
 * it was the last word of a sentence. Four ticks, filled to the rating, so the difference
 * between a moderate borrow and a low one is available at a glance instead of on a read.
 * The word stays next to it: the ticks are the scannable form of the rating, not a
 * replacement that makes the reader decode a scale.
 *
 * ORDER FOLLOWS THE GENE, MEMBERSHIP DOES NOT. Once a gene is chosen the list reorders by
 * whether that gene has evidence against each borrow's disease. Nothing is removed. An
 * endpoint that comes back empty is frequently the interesting answer: FLG against cosmetic
 * dry skin is exactly a case where the restriction doing its work is the finding, and a
 * filter would have deleted it from the screen.
 *
 * Listbox keyboard contract: Down/Up/Home/End move, Enter or Space select, Escape closes
 * and returns focus, typing a letter jumps. Selection is announced via aria-activedescendant.
 */
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ProxyRow } from "./types";

export interface EndpointEvidence {
  endpoint: string;
  state: "tier_1_or_2" | "weak_only" | "checked_and_empty" | "no_disease_term";
  scores: Record<string, number>;
  top_score: number;
}

/** The string sent to the API for a row. Matches what the pipeline's synonym lookup expects. */
export const queryValue = (ep: ProxyRow) => ep.synonyms?.[0] ?? ep.endpoint;

/** Which row a free-text condition corresponds to, if any. Exact, case-insensitive, no fuzzy. */
export function matchEndpoint(endpoints: ProxyRow[], condition: string): ProxyRow | null {
  const c = condition.trim().toLowerCase();
  if (!c) return null;
  return (
    endpoints.find(
      (ep) =>
        ep.endpoint.toLowerCase() === c ||
        ep.synonyms?.some((s) => s.toLowerCase() === c),
    ) ?? null
  );
}

function borrowLine(ep: ProxyRow): { text: string; tone: "b-borrow" | "b-none" | "b-direct" } {
  if (ep.borrow_type === "DISEASE_BORROW")
    return { text: `borrows from ${ep.borrowed_from}`, tone: "b-borrow" };
  if (ep.borrow_type === "NONE") return { text: "no defensible borrow", tone: "b-none" };
  return { text: "own genetics, nothing borrowed", tone: "b-direct" };
}

/* ---- Category icons -------------------------------------------------------------
 * Monochrome, single stroke weight, drawn on the same 16px grid as every other icon in
 * the app. They are a scanning aid for three groups, not illustration: at this size an
 * illustrated icon reads as texture and slows the list down rather than speeding it up.
 */
const HairIcon = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
    <path d="M3 13c0-4.4 2.2-7.5 5-7.5s5 3.1 5 7.5" strokeLinecap="round" />
    <path d="M5.6 13c0-3.3.9-5.6 2.4-5.6s2.4 2.3 2.4 5.6" strokeLinecap="round" />
  </svg>
);
const SkinIcon = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
    <rect x="2.6" y="4.2" width="10.8" height="7.6" rx="2.2" />
    <path d="M2.6 8.6h10.8" strokeLinecap="round" />
  </svg>
);
const PigmentIcon = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
    <circle cx="8" cy="8" r="5.4" />
    <path d="M8 2.6a5.4 5.4 0 0 0 0 10.8z" fill="currentColor" stroke="none" />
  </svg>
);

type Category = "hair" | "pigment" | "skin";

function category(ep: ProxyRow): Category {
  const s = `${ep.endpoint} ${ep.display_name}`.toLowerCase();
  if (/hair|alopecia|follicle|thinning/.test(s)) return "hair";
  if (/pigment|tone|melasma|vitiligo|freckle/.test(s)) return "pigment";
  return "skin";
}

const CATEGORY_ICON: Record<Category, () => React.ReactElement> = {
  hair: HairIcon,
  skin: SkinIcon,
  pigment: PigmentIcon,
};
const CATEGORY_NAME: Record<Category, string> = {
  hair: "Hair",
  skin: "Skin",
  pigment: "Pigment",
};

/** Four ticks. NONE draws four empty ones rather than nothing, so "not rated" and
 *  "rated lowest" are visibly different states instead of both being blank. */
const RATING_STEPS: Record<string, number> = {
  HIGH: 4,
  MODERATE: 3,
  MODERATE_LOW: 2,
  LOW: 1,
  NONE: 0,
};

function RatingScale({ rating }: { rating: string }) {
  const filled = RATING_STEPS[rating] ?? 0;
  const label = rating.replace("_", " ").toLowerCase();
  return (
    <span className={`rscale rs-${rating.toLowerCase()}`} title={`Borrow rating: ${label}`}>
      <span className="rscale-ticks" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <i key={i} className={i < filled ? "on" : ""} />
        ))}
      </span>
      <span className="rscale-word">{rating === "NONE" ? "not rated" : label}</span>
    </span>
  );
}

const EVIDENCE_MARK: Record<EndpointEvidence["state"], { cls: string; text: string }> = {
  tier_1_or_2: { cls: "ev-hit", text: "genetics or outcome" },
  weak_only: { cls: "ev-weak", text: "literature only" },
  checked_and_empty: { cls: "ev-empty", text: "checked, nothing" },
  no_disease_term: { cls: "ev-none", text: "no term to query" },
};

const STATE_ORDER: Record<EndpointEvidence["state"], number> = {
  tier_1_or_2: 0,
  weak_only: 1,
  checked_and_empty: 2,
  no_disease_term: 3,
};

export default function EndpointMenu({
  endpoints,
  value,
  onChange,
  compact = false,
  evidence,
  evidenceGene,
}: {
  endpoints: ProxyRow[];
  value: string;
  onChange: (v: string) => void;
  compact?: boolean;
  evidence?: EndpointEvidence[];
  evidenceGene?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const typed = useRef({ buf: "", at: 0 });
  const id = useId();

  const evidenceBy = useMemo(() => {
    const m: Record<string, EndpointEvidence> = {};
    for (const e of evidence ?? []) m[e.endpoint] = e;
    return m;
  }, [evidence]);

  // Rank, never filter. With no gene chosen the curated file's own order stands, because
  // that order is a human's, and reordering it without a reason would be noise.
  const ordered = useMemo(() => {
    if (!evidence || evidence.length === 0) return endpoints;
    return [...endpoints].sort((a, b) => {
      const ea = evidenceBy[a.endpoint];
      const eb = evidenceBy[b.endpoint];
      const sa = ea ? STATE_ORDER[ea.state] : 3;
      const sb = eb ? STATE_ORDER[eb.state] : 3;
      if (sa !== sb) return sa - sb;
      const ta = ea?.top_score ?? 0;
      const tb = eb?.top_score ?? 0;
      if (ta !== tb) return tb - ta;
      return endpoints.indexOf(a) - endpoints.indexOf(b);
    });
  }, [endpoints, evidence, evidenceBy]);

  const selected = matchEndpoint(ordered, value);
  const custom = !selected && value.trim().length > 0;

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return;
    setActive(Math.max(0, ordered.findIndex((e) => e === selected)));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    listRef.current?.querySelector<HTMLElement>('[data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  const choose = (i: number) => {
    const ep = ordered[i];
    if (!ep) return;
    onChange(queryValue(ep));
    setOpen(false);
    btnRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const n = ordered.length;
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }
    switch (e.key) {
      case "ArrowDown": e.preventDefault(); setActive((a) => (a + 1) % n); break;
      case "ArrowUp": e.preventDefault(); setActive((a) => (a - 1 + n) % n); break;
      case "Home": e.preventDefault(); setActive(0); break;
      case "End": e.preventDefault(); setActive(n - 1); break;
      case "Enter":
      case " ": e.preventDefault(); choose(active); break;
      case "Escape": e.preventDefault(); setOpen(false); btnRef.current?.focus(); break;
      case "Tab": setOpen(false); break;
      default:
        if (e.key.length === 1 && /\S/.test(e.key)) {
          // Type-ahead over the formal name, which is what the first line shows.
          const now = Date.now();
          typed.current.buf = now - typed.current.at > 700 ? e.key : typed.current.buf + e.key;
          typed.current.at = now;
          const q = typed.current.buf.toLowerCase();
          const hit = ordered.findIndex((ep) =>
            (ep.formal_name ?? ep.display_name).toLowerCase().startsWith(q),
          );
          if (hit >= 0) setActive(hit);
        }
    }
  };

  const Face = ({ ep }: { ep: ProxyRow }) => {
    const Icon = CATEGORY_ICON[category(ep)];
    const b = borrowLine(ep);
    return (
      <>
        <span className={`epick-cat cat-${category(ep)}`} title={CATEGORY_NAME[category(ep)]}>
          <Icon />
        </span>
        <span className="epick-text">
          <span className="epick-name">
            {ep.formal_name ?? ep.display_name}
            {ep.plain_name && <em> ({ep.plain_name})</em>}
          </span>
          <span className="epick-meta">
            <span className={`bchip ${b.tone}`}>
              {b.text}
              {ep.refuse && <b> · refused</b>}
            </span>
            {ep.borrow_type === "DISEASE_BORROW" && <RatingScale rating={ep.rating} />}
          </span>
        </span>
      </>
    );
  };

  return (
    <div className={`epick ${compact ? "sm" : ""}`} ref={rootRef}>
      <button
        ref={btnRef}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-activedescendant={open ? `${id}-opt-${active}` : undefined}
        aria-label="Endpoint"
        className={`epick-btn ${open ? "open" : ""}`}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyDown}
      >
        <span className="epick-face">
          {selected ? (
            <Face ep={selected} />
          ) : custom ? (
            <span className="epick-text">
              <span className="epick-name">{value}</span>
              <span className="bchip b-none">not in the borrow table</span>
            </span>
          ) : (
            <span className="epick-text">
              <span className="epick-name muted">Choose an endpoint</span>
            </span>
          )}
        </span>
        <svg className="epick-caret" viewBox="0 0 16 16" aria-hidden>
          <path d="M4 6.2 8 10.2l4-4" fill="none" stroke="currentColor" strokeWidth="1.6"
            strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <ul
          id={`${id}-list`}
          ref={listRef}
          role="listbox"
          aria-label="Endpoint"
          tabIndex={-1}
          className="epick-list"
        >
          {evidenceGene && (
            <li className="epick-rankhead t-body-s" role="presentation">
              Ordered by what <span className="t-mono">{evidenceGene}</span> has against each
              borrow. Every endpoint is still listed.
            </li>
          )}
          {ordered.map((ep, i) => {
            const ev = evidenceBy[ep.endpoint];
            const mark = ev ? EVIDENCE_MARK[ev.state] : null;
            return (
              <li
                key={ep.endpoint}
                id={`${id}-opt-${i}`}
                role="option"
                aria-selected={ep === selected}
                data-active={i === active}
                className={`epick-opt ${i === active ? "active" : ""} ${ep === selected ? "sel" : ""}`}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(i)}
              >
                <Face ep={ep} />
                {mark && (
                  <span className={`evmark ${mark.cls}`}>
                    {mark.text}
                    {ev && ev.top_score > 0 && (
                      <span className="num"> {ev.top_score.toFixed(2)}</span>
                    )}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

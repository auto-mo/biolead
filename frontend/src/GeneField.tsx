/* Gene symbol field with live lookup against Open Targets search.
 *
 * This is a guardrail, not a convenience. Step-zero finding 2: symbol search is fuzzy and
 * ranked, so `AR` returns AR, then FDXR, AREG, AKR1B1, ARX. The pipeline handles that by
 * refusing anything but an exact symbol match, which is safe but silent about what it
 * refused. This field handles the other half: it shows the ranked list and lets the person
 * asking pick the gene they meant.
 *
 * Degradation is explicit. If the lookup times out or fails, the list is dropped and the
 * typed value is submitted as before. The field never blocks on the network.
 */
import { useEffect, useRef, useState } from "react";
import type { GeneHit } from "./types";
import { API } from "./apiBase";

const DEBOUNCE_MS = 200;
const TIMEOUT_MS = 4000;

export default function GeneField({
  value,
  onChange,
  onPick,
  compact = false,
}: {
  value: string;
  onChange: (v: string) => void;
  onPick?: (hit: GeneHit) => void;
  compact?: boolean;
}) {
  const [hits, setHits] = useState<GeneHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [degraded, setDegraded] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Only a keystroke opens the list. Preset buttons set the value programmatically and
  // must not pop a menu over the result the user just asked for.
  const typing = useRef(false);

  useEffect(() => {
    if (!typing.current) return;
    const q = value.trim();
    if (q.length < 1) {
      setHits([]);
      return;
    }
    const ctl = new AbortController();
    const timer = setTimeout(async () => {
      const kill = setTimeout(() => ctl.abort(), TIMEOUT_MS);
      try {
        const r = await fetch(`${API}/genes?q=${encodeURIComponent(q)}&limit=8`, {
          signal: ctl.signal,
        });
        const d = await r.json();
        clearTimeout(kill);
        setDegraded(!d.ok);
        setHits(d.ok ? (d.hits as GeneHit[]) : []);
        setActive(0);
      } catch {
        clearTimeout(kill);
        // Timeout or transport failure. Fall back to the typed value, silently for the
        // keyboard but visibly in the panel, so nobody thinks the gene does not exist.
        setDegraded(true);
        setHits([]);
      }
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctl.abort();
    };
  }, [value]);

  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);

  const pick = (h: GeneHit | undefined) => {
    if (!h) return;
    typing.current = false;
    onChange(h.symbol);
    onPick?.(h);
    setOpen(false);
  };

  const listOpen = open && hits.length > 0;
  const exactTyped = hits.some((h) => h.exact);

  return (
    <div className={`gfield ${compact ? "sm" : ""}`} ref={rootRef}>
      <input
        ref={inputRef}
        value={value}
        role="combobox"
        aria-expanded={listOpen}
        aria-autocomplete="list"
        aria-controls="gene-list"
        aria-activedescendant={listOpen ? `gene-opt-${active}` : undefined}
        aria-label="Gene symbol"
        placeholder="Gene symbol"
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => {
          typing.current = true;
          setOpen(true);
          onChange(e.target.value);
        }}
        onFocus={() => hits.length > 0 && setOpen(true)}
        onKeyDown={(e) => {
          if (!listOpen) return;
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (a + 1) % hits.length); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a - 1 + hits.length) % hits.length); }
          else if (e.key === "Enter") { e.preventDefault(); pick(hits[active]); }
          else if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
          else if (e.key === "Tab") setOpen(false);
        }}
      />

      {listOpen && (
        <ul id="gene-list" role="listbox" aria-label="Gene candidates" className="gfield-list">
          <li className="gfield-note" role="presentation">
            Ranked by the source, not exact.
            {!exactTyped && " No exact symbol match in this list."}
          </li>
          {hits.map((h, i) => (
            <li
              key={h.ensembl_id}
              id={`gene-opt-${i}`}
              role="option"
              aria-selected={i === active}
              className={`gfield-opt ${i === active ? "active" : ""}`}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(h)}
            >
              <span className="gsym">
                {h.symbol}
                {h.exact && <span className="gexact">exact</span>}
              </span>
              <span className="gname">{h.name}</span>
              <span className="gid">{h.ensembl_id}</span>
            </li>
          ))}
        </ul>
      )}

      {degraded && value.trim() !== "" && (
        <p className="gfield-degraded t-body-s">
          Symbol lookup unavailable. The typed symbol is used as it stands.
        </p>
      )}
    </div>
  );
}

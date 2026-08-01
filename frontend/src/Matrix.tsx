import {
  bucket,
  cellKey,
  CELL_LABEL,
  CELL_LABEL_UNREPORTED,
  UNREPORTED_NOTE,
  COLUMNS,
  INSUFFICIENT_LABEL,
  INSUFFICIENT_SUB,
  CELL_LABEL_BORROWED,
  BORROWED_NOTE,
  PASSENGER_CELL,
  ROWS,
  type MatrixItem,
} from "./verdictMatrix";

/** The verdict matrix.
 *
 *  `variant="plot"`  batch, primary. Genes as dots inside their cell.
 *  `variant="locator"` single assessment, secondary. Compact, one cell lit, no dots. It is
 *                      a legend and a locator, not a second verdict, so it never carries a
 *                      value the panel beside it does not already state.
 *
 *  Empty cells render EMPTY rather than collapsing. A cell nobody landed in is a finding:
 *  on the demo list nothing at all reaches "Causal, and hitting it worked", and hiding the
 *  cell would hide that.
 */
export default function Matrix({
  items,
  variant = "plot",
  active,
  onSelect,
  max = 60,
}: {
  items?: MatrixItem[];
  variant?: "plot" | "locator";
  active?: {
    position: string;
    targetability: string;
    outcomeMeasuredOn?: string | null;
    outcomeState?: string | null;
  };
  onSelect?: (gene: string) => void;
  max?: number;
}) {
  const { cells, insufficient } = bucket(items ?? []);
  const activeKey = active
    ? active.position === "INSUFFICIENT"
      ? "INSUFFICIENT"
      : cellKey(active.position, active.targetability)
    : null;

  return (
    <div className={`matrix matrix-${variant}`}>
      {/* The two axis names are separated rather than stacked in the corner. Stacked they
          read as one four-word label belonging to neither axis. */}
      <p className="mx-axis-caption t-label">Modulation outcome</p>
      <div className="matrix-grid" role="table" aria-label="Position against modulation outcome">
        <div className="mx-corner" role="presentation">
          <span className="mx-axis-y t-label">Position</span>
        </div>
        {COLUMNS.map((c) => (
          <div key={c.key} className="mx-colhead t-label" role="columnheader">
            {c.head}
          </div>
        ))}

        {ROWS.map((r) => (
          <div className="mx-row" key={r.key} role="row">
            <div className="mx-rowhead t-label" role="rowheader">
              {r.head}
            </div>
            {COLUMNS.map((c) => {
              const key = cellKey(r.key, c.key);
              const list = cells[key] ?? [];
              const isActive = activeKey === key;
              // In the locator, the borrow belongs to the active call. In the plot it
              // belongs to any gene in the cell that reached it through one.
              const borrowedOn =
                variant === "locator"
                  ? isActive
                    ? active?.outcomeMeasuredOn ?? null
                    : null
                  : list.find((g) => g.outcomeMeasuredOn)?.outcomeMeasuredOn ?? null;
              // Tested and never reported marks the cell it lands in. Locator only: in the
              // plot a cell holds many genes and the mark would belong to none of them.
              const unreported =
                variant === "locator" &&
                isActive &&
                active?.outcomeState === "TESTED_UNREPORTED" &&
                Boolean(CELL_LABEL_UNREPORTED[key]);
              // COMPACT IN THE LOCATOR. Nine cells of prose take a full block to say one
              // thing. Only the occupied cell carries text; the rest are empty positions.
              const compactBlank = variant === "locator" && !isActive;
              return (
                <div
                  key={c.key}
                  role="cell"
                  className={
                    "mx-cell" +
                    (isActive ? " is-active" : "") +
                    (key === PASSENGER_CELL ? " is-passenger" : "") +
                    (borrowedOn ? " is-borrowed" : "") +
                    (unreported ? " is-unreported" : "") +
                    (compactBlank ? " is-blank" : "") +
                    (list.length === 0 && variant === "plot" ? " is-empty" : "")
                  }
                >
                  {!compactBlank && (
                    <p className="mx-label">
                      {unreported
                        ? CELL_LABEL_UNREPORTED[key]
                        : borrowedOn && CELL_LABEL_BORROWED[key]
                          ? CELL_LABEL_BORROWED[key](borrowedOn)
                          : CELL_LABEL[key]}
                    </p>
                  )}
                  {unreported && (
                    <p className="mx-borrowed t-body-s">{UNREPORTED_NOTE}</p>
                  )}
                  {borrowedOn && !compactBlank && (
                    <p className="mx-borrowed t-body-s">{BORROWED_NOTE}</p>
                  )}
                  {variant === "plot" && (
                    <>
                      <p className="mx-count num" aria-hidden={list.length === 0}>
                        {list.length}
                      </p>
                      <div className="mx-dots">
                        {list.slice(0, max).map((g) => (
                          <button
                            key={g.gene}
                            type="button"
                            className="mx-dot"
                            title={g.gene}
                            aria-label={g.gene}
                            onClick={() => onSelect?.(g.gene)}
                          >
                            <span className="mx-dot-name t-mono">{g.gene}</span>
                          </button>
                        ))}
                        {list.length > max && (
                          <span className="mx-more t-body-s num">
                            +{list.length - max}
                          </span>
                        )}
                      </div>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* In the locator this band is the "not placed" row. Showing it while a cell is lit
          says the gene was not placed directly under a highlighted placement. It renders
          only when it IS the answer. In the plot it is a real row and always renders. */}
      {(variant === "plot" || activeKey === "INSUFFICIENT") && (
      <div
        className={
          "mx-band" +
          (activeKey === "INSUFFICIENT" ? " is-active" : "") +
          (variant === "plot" && insufficient.length === 0 ? " is-empty" : "")
        }
      >
        <div className="mx-band-text">
          <p className="mx-label">{INSUFFICIENT_LABEL}</p>
          <p className="mx-band-sub t-body-s">{INSUFFICIENT_SUB}</p>
        </div>
        {variant === "plot" && (
          <>
            <p className="mx-count num">{insufficient.length}</p>
            <div className="mx-dots">
              {insufficient.slice(0, max).map((g) => (
                <button
                  key={g.gene}
                  type="button"
                  className="mx-dot"
                  title={g.gene}
                  aria-label={g.gene}
                  onClick={() => onSelect?.(g.gene)}
                >
                  <span className="mx-dot-name t-mono">{g.gene}</span>
                </button>
              ))}
              {insufficient.length > max && (
                <span className="mx-more t-body-s num">+{insufficient.length - max}</span>
              )}
            </div>
          </>
        )}
      </div>
      )}

      {variant === "plot" && (
        <p className="mx-note t-body-s">
          One of the three downstream cells is a passenger call:{" "}
          <em>{CELL_LABEL[PASSENGER_CELL]}</em> The other two are downstream genes that are
          still worth work. Confidence is not shown here; it is in the table. Not placed
          counts every gene the evidence could not position, which is a wider set than the
          genes the tool abstained on: evidence can exist and still not place a gene.
        </p>
      )}
    </div>
  );
}

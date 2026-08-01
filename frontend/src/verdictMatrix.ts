/** The verdict matrix: position against modulation outcome.
 *
 *  One definition, three renderings. The batch view places every gene as a dot, the
 *  single assessment shows a compact locator with the current cell lit, and slide 6 of the
 *  deck is the same grid drawn by hand. They read from here so the cell wording cannot
 *  drift between the tool and the talk.
 *
 *  WHY THE GRID EARNS ITS SPACE. Downstream spans three cells and only one of them is a
 *  passenger. A gene can sit downstream and still be worth working on, because being
 *  downstream of the driver is not the same as being inert. That distinction is the centre
 *  of the design and until now it was only legible to someone reading the verdict text
 *  carefully.
 *
 *  WHAT IS DELIBERATELY NOT HERE.
 *
 *  Confidence. It is a third dimension and encoding it, as dot size or opacity or a
 *  border, would make two clean axes into three muddy ones. Confidence lives in the table
 *  row and in the single assessment panel.
 *
 *  Insufficient as a peer row. It is not a placement, it is the absence of one, so it sits
 *  as a band under the grid rather than as a third row inside it. A third row would invite
 *  reading it as "upstream, downstream, or this other place", and it is not another place.
 */

export type Position = "UPSTREAM_DRIVER" | "DOWNSTREAM" | "INSUFFICIENT";
export type Targetability = "ACTIONABLE" | "NOT_ACTIONABLE" | "UNKNOWN";

/** Columns, left to right. Order runs from the strongest evidence to the least. */
export const COLUMNS: { key: Targetability; head: string }[] = [
  { key: "ACTIONABLE", head: "Benefit shown" },
  { key: "NOT_ACTIONABLE", head: "No benefit" },
  { key: "UNKNOWN", head: "Untested" },
];

/** Rows, top to bottom. Insufficient is not here; it is the band below. */
export const ROWS: { key: Exclude<Position, "INSUFFICIENT">; head: string }[] = [
  { key: "UPSTREAM_DRIVER", head: "Upstream" },
  { key: "DOWNSTREAM", head: "Downstream" },
];

/** Plain language, not enum names. Short enough to sit inside a cell. */
export const CELL_LABEL: Record<string, string> = {
  "UPSTREAM_DRIVER|ACTIONABLE": "Causal, and hitting it worked.",
  "UPSTREAM_DRIVER|NOT_ACTIONABLE": "Causal, but the drug failed.",
  "UPSTREAM_DRIVER|UNKNOWN": "Causal, never tested.",
  "DOWNSTREAM|ACTIONABLE": "Downstream, but it worked.",
  "DOWNSTREAM|NOT_ACTIONABLE": "Downstream, tried and failed.",
  "DOWNSTREAM|UNKNOWN": "Downstream, never tested.",
};

export const INSUFFICIENT_LABEL = "Not placed.";
export const INSUFFICIENT_SUB =
  "The evidence does not put the gene on the grid either way.";

/** The single cell that is a passenger call. Named so the legend can point at it. */
export const PASSENGER_CELL = "DOWNSTREAM|NOT_ACTIONABLE";

/** Cell wording when the outcome was measured on a borrowed disease.
 *
 *  The matrix is read before the prose under it, so a bare "hitting it worked" states a
 *  result about an endpoint no trial measured. Every directional outcome in the current
 *  build crosses a borrow: NCT01231607 measured androgenetic alopecia rather than
 *  non-clinical hair thinning, NCT02998671 measured acne rather than sebum production.
 *
 *  The cell is marked rather than emptied. Excluding a borrowed result would hide a real
 *  one, which is the opposite of the mistake being fixed. */
export const CELL_LABEL_BORROWED: Record<string, (d: string) => string> = {
  "UPSTREAM_DRIVER|ACTIONABLE": (d) => `Causal, and hitting it worked in ${d}.`,
  "UPSTREAM_DRIVER|NOT_ACTIONABLE": (d) => `Causal, but the drug failed in ${d}.`,
  "DOWNSTREAM|ACTIONABLE": (d) => `Downstream, but it worked in ${d}.`,
  "DOWNSTREAM|NOT_ACTIONABLE": (d) => `Downstream, tried and failed in ${d}.`,
};

/* TESTED AND UNREPORTED IS NOT UNTESTED, and the matrix has three outcome columns for four
   outcome states. AR reaches the UNKNOWN column with three completed trials and 1,560
   participants behind it, and the cell said "never tested", which undoes the distinction the
   state exists to make.

   MARKED RATHER THAN GIVEN A FOURTH COLUMN. A fourth column widens the batch view for a state
   that is common but is not a placement of its own: it is still "we do not know whether
   modulating this moves the endpoint". The borrow already marks a cell without moving the
   result out of it, and this is the same mechanism. */
export const CELL_LABEL_UNREPORTED: Record<string, string> = {
  "UPSTREAM_DRIVER|UNKNOWN": "Causal, tested, never reported.",
  "DOWNSTREAM|UNKNOWN": "Downstream, tested, never reported.",
};

export const UNREPORTED_NOTE =
  "a drug was tested in people and the result was never published";

export const BORROWED_NOTE =
  "measured on the borrowed disease, not on this endpoint";

export const cellKey = (p: string, t: string) => `${p}|${t}`;

export interface MatrixItem {
  gene: string;
  position: string;
  targetability: string;
  /** Set when the outcome was measured on a borrowed disease. */
  outcomeMeasuredOn?: string | null;
}

export interface Bucketed {
  cells: Record<string, MatrixItem[]>;
  insufficient: MatrixItem[];
}

export function bucket(items: MatrixItem[]): Bucketed {
  const cells: Record<string, MatrixItem[]> = {};
  for (const r of ROWS) for (const c of COLUMNS) cells[cellKey(r.key, c.key)] = [];
  const insufficient: MatrixItem[] = [];
  for (const it of items) {
    if (it.position === "INSUFFICIENT") {
      insufficient.push(it);
      continue;
    }
    const k = cellKey(it.position, it.targetability);
    // An unknown combination is dropped into the band rather than silently discarded:
    // a gene that vanishes between the count and the grid would make the grid a lie.
    if (cells[k]) cells[k].push(it);
    else insufficient.push(it);
  }
  return { cells, insufficient };
}

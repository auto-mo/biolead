"""Human Protein Atlas client.

Answers one question: where in skin is this protein, and is it inside the cell or outside
it. That is half of whether a topical could plausibly reach it. The other half is
modality, which comes from Open Targets tractability.

FOUR BEHAVIOURS of this API, all verified live. Every one of them
returns HTTP 200 with a body that parses, which is the same failure shape as the Open
Targets findings: the API does not tell you it answered a different question.

  1. `columns` IS MANDATORY IN PRACTICE. Omit it and the endpoint returns the HPA website's
     HTML homepage with HTTP 200 and `content-type: text/html`. A JSON parser throws on
     what looks like a successful response.

  2. A BAD COLUMN NAME IS SILENTLY IGNORED. `columns=g,NOTACOLUMN` returns `[{"Gene":"AR"}]`
     with no error. A typo produces a missing key, not a failure, so every response is
     checked against the keys we asked for and a missing one degrades to unknown rather
     than to a default.

  3. THERE IS NO BATCH ROUTE, AND MULTI-GENE SYNTAX FAILS SILENTLY. Comma, space, `+OR+`
     and `%20OR%20` all return `[]` with HTTP 200, which is indistinguishable from "gene not
     found". One request per gene, which is why the rate limiter matters here more than
     anywhere else: a 474-gene list is 474 calls against a source that answers in ~0.5s.

  4. SYMBOL SEARCH IS FUZZY, ENSEMBL IS EXACT. `search=AR` returns AR plus ADARB1, BRCA1,
     PPARG, PARP1 and more. This is finding 2 of the Open Targets client again, in a
     different source. Ensembl IDs only, and the returned `Ensembl` field is checked
     against the one requested before the row is used.

Numbers come back as JSON STRINGS ("432.6", "0.0"), never numbers. Arrays come back as
`null` rather than `[]` when empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.cache import CACHE
from app.core.limits import REGISTRY

BASE = "https://www.proteinatlas.org/api/search_download.php"

# Requested specifiers. `secl` expands to two keys and `ct_APE_skin` to 38, so a response
# carries about 48 keys for the eleven asked for.
COLUMNS = "eg,g,scl,scml,secl,pc,rnats,rnatsm,t_RNA_skin_1,t_APE_skin,ct_APE_skin"

# Keys that must be present. Finding 2 means their absence signals a broken query rather
# than a gene with no data, and those two must not be confused.
REQUIRED_KEYS = ("Gene", "Ensembl", "Subcellular location", "Tissue RNA - skin 1 [nTPM]")

_CT_PREFIX = "Tissue Cell type Annotation (IH) - skin"

# HPA reports two skin donors, "skin 1" and "skin 2", and they disagree: for FLG the basal
# layer is `not detected` in one and `low` in the other. Compartments below are keyed on the
# cell-type suffix, so both donors fold into the same compartment and the strongest call
# across them wins. The donor count travels with the answer.
EPIDERMIS = (
    "cells in corneal layer", "cells in granular layer", "cells in spinous layer",
    "cells in basal layer", "keratinocytes", "epidermal cells", "melanocytes",
    "langerhans cells", "langerhans",
)
APPENDAGE = (
    "hair follicles", "sebaceous glands", "sebaceous cells", "eccrine glands",
    "sweat ducts", "secretory cells", "arrector pili muscle cells",
)
DERMIS = (
    "fibroblasts", "extracellular matrix", "endothelial cells", "vascular mural cells",
    "fibrohistiocytic cells", "lymphocytes",
)

_LEVEL = {"high": 3, "medium": 2, "low": 1, "not detected": 0}


@dataclass
class HpaRecord:
    ensembl_id: str
    ok: bool = False
    symbol: str | None = None
    error: str | None = None

    skin_ntpm: float | None = None
    skin_ih: str | None = None            # high | medium | low | not detected | None
    tissue_specificity: str | None = None
    tissue_specific_ntpm: dict[str, str] = field(default_factory=dict)

    subcellular: list[str] = field(default_factory=list)
    subcellular_main: list[str] = field(default_factory=list)
    secretome_location: str | None = None
    protein_class: list[str] = field(default_factory=list)

    # Strongest IH call per compartment, across both donors. Absent key means unmeasured.
    compartments: dict[str, str] = field(default_factory=dict)
    donors_seen: int = 0

    @property
    def subcellular_measured(self) -> bool:
        """31% of genes have no immunofluorescence data at all.

        `null` here is "not measured", not "not localised anywhere", and treating the two
        the same would turn a gap in the atlas into a claim about the protein.
        """
        return bool(self.subcellular or self.subcellular_main)


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [str(v)]


class HpaClient:
    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 30.0):
        self._client = client
        self._owns = client is None
        self._timeout = timeout

    async def __aenter__(self) -> "HpaClient":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns and self._client is not None:
            await self._client.aclose()

    async def _get(self, ensembl_id: str) -> HpaRecord:
        assert self._client is not None, "use as an async context manager"
        resp = await self._client.get(
            BASE,
            params={"search": ensembl_id, "format": "json",
                    "columns": COLUMNS, "compress": "no"},
        )
        resp.raise_for_status()

        # Finding 1: a missing or rejected `columns` yields the website with HTTP 200.
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype:
            return HpaRecord(ensembl_id, error=f"expected JSON, got {ctype!r}")

        rows = resp.json()
        if not rows:
            # Genuinely absent, or a query shape the API refused. Either way this is
            # `could_not_check` territory rather than a finding about the protein.
            return HpaRecord(ensembl_id, error="no row returned")

        row = rows[0]
        # Finding 4: never trust the row without checking it is the gene we asked for.
        if str(row.get("Ensembl", "")).strip() != ensembl_id:
            return HpaRecord(
                ensembl_id,
                error=f"row is for {row.get('Ensembl')!r}, not {ensembl_id!r}",
            )
        # Finding 2: a silently dropped column looks like a gene with no data.
        missing = [k for k in REQUIRED_KEYS if k not in row]
        if missing:
            return HpaRecord(ensembl_id, error=f"columns missing from response: {missing}")

        rec = HpaRecord(
            ensembl_id=ensembl_id,
            ok=True,
            symbol=row.get("Gene"),
            skin_ntpm=_num(row.get("Tissue RNA - skin 1 [nTPM]")),
            skin_ih=row.get("Tissue Annotation (IH) - skin"),
            tissue_specificity=row.get("RNA tissue specificity"),
            tissue_specific_ntpm=row.get("RNA tissue specific nTPM") or {},
            subcellular=_as_list(row.get("Subcellular location")),
            subcellular_main=_as_list(row.get("Subcellular main location")),
            secretome_location=row.get("Secretome location"),
            protein_class=_as_list(row.get("Protein class")),
        )

        donors: set[str] = set()
        best: dict[str, int] = {}
        for key, value in row.items():
            if not key.startswith(_CT_PREFIX) or value is None:
                continue
            tail = key[len(_CT_PREFIX):].strip(" -")
            donor, _, cell = tail.partition(" - ")
            if not cell:
                donor, cell = "", donor
            donors.add(donor or "skin")
            cell = cell.strip().lower()
            group = (
                "epidermis" if cell in EPIDERMIS
                else "appendage" if cell in APPENDAGE
                else "dermis" if cell in DERMIS
                else None
            )
            if group is None:
                continue
            level = _LEVEL.get(str(value).strip().lower())
            if level is None:
                continue
            best[group] = max(best.get(group, 0), level)

        rec.donors_seen = len(donors)
        inv = {v: k for k, v in _LEVEL.items()}
        rec.compartments = {g: inv[lvl] for g, lvl in best.items()}
        return rec

    async def fetch(self, ensembl_id: str) -> HpaRecord:
        """One gene, through the limiter, cached.

        A transport failure is NOT cached: `could_not_check` has to stay distinguishable
        from `checked and empty`, and a cached failure erases that distinction for the
        lifetime of the entry.
        """

        async def go() -> HpaRecord:
            try:
                return await REGISTRY.get("hpa").run(self._get, ensembl_id)
            except httpx.HTTPError as exc:
                return HpaRecord(ensembl_id, error=f"fetch failed: {exc}")

        return await CACHE.get_or_set(
            ("hpa.gene", ensembl_id), go, cacheable=lambda r: r.ok or r.error == "no row returned"
        )

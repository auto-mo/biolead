"""Preset gene lists, read from the published tables in `data/de_lists/`.

A preset exists so the batch demo runs on a click. Pasting 50 symbols in front of an
audience is dead air, and the thing being demonstrated is the answer, not the paste.

THE TRUNCATION IS THE PAPER'S OWN. The 50-gene preset is the first 50 rows of Table S2 as
published. That table is sorted by adjusted p-value ascending and the ordering was checked
to be monotonic, so the preset is the paper's 50 most significant differentially expressed
genes rather than a selection made here. `head -50` of someone else's ranking is a
defensible subset; a hand-picked 50 would not be, and the whole argument of this project is
that the curated layer has to be visible and challengeable.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "de_lists"


@dataclass
class Preset:
    id: str
    label: str
    condition: str
    csv_name: str
    limit: int | None
    citation: str
    url: str
    what_it_is: str
    truncation_note: str | None = None
    symbols: list[str] = field(default_factory=list)
    input_fields: dict[str, dict[str, str]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "condition": self.condition,
            "count": len(self.symbols),
            "citation": self.citation,
            "url": self.url,
            "what_it_is": self.what_it_is,
            "truncation_note": self.truncation_note,
            "symbols": self.symbols,
        }


_LIU_CITATION = (
    "Liu Q, Tang Y, Huang Y, et al. Insights into male androgenetic alopecia using "
    "comparative transcriptome profiling: hypoxia-inducible factor-1 and Wnt/beta-catenin "
    "signalling pathways. Br J Dermatol 2022;187(6):936-947. Supplementary Table S2."
)
_LIU_URL = "https://doi.org/10.1111/bjd.21783"
_LIU_WHAT = (
    "Paired vertex (affected) and occipital (spared) anagen hair follicles from ten male "
    "donors with androgenetic alopecia. RNA-seq, differential expression by DESeq2. Every "
    "gene on the list is differentially expressed in affected tissue."
)

_SENNETT_CITATION = (
    "Sennett ML, Agak GW, Thiboutot DM, Nelson AM. Transcriptomic Analyses Predict "
    "Enhanced Metabolic Activity and Therapeutic Potential of mTOR Inhibitors in "
    "Acne-Prone Skin. JID Innovations 2024;4(6):100306. Supplementary differential "
    "expression results, at the paper's threshold."
)
_SENNETT_URL = "https://doi.org/10.1016/j.xjidi.2024.100306"
_SENNETT_WHAT = (
    "Non-lesional skin from 49 people with acne against skin from 19 healthy controls "
    "with no acne history. RNA-seq, limma. Not lesional versus non-lesional: the question "
    "is what is different about acne-prone skin carrying no lesion."
)

_OT_CITATION = (
    "Open Targets Platform, data version 26.06. Targets associated with androgenetic "
    "alopecia (MONDO_0005339), top 50 by overall association score."
)
_OT_URL = "https://platform.opentargets.org/disease/MONDO_0005339/associations"
_OT_WHAT = (
    "Not a differential expression list. These are the genes a reference database already "
    "links to the disease, best association first. A DE list asks what changed in affected "
    "tissue; this asks what is already associated. The tool answers them differently."
)

_DEFS = [
    Preset(
        id="opentargets_aga_top50",
        label="Androgenetic alopecia, top 50 by Open Targets association",
        condition="hair thinning",
        csv_name="opentargets_aga_top50_by_association.csv",
        limit=None,
        citation=_OT_CITATION,
        url=_OT_URL,
        what_it_is=_OT_WHAT,
        truncation_note=(
            "Top 50 of 964 associated targets, by the platform's own overall association "
            "score. Regenerate with tools/fetch_association_list.py."
        ),
    ),
    Preset(
        id="liu2022_aga_top50",
        label="Androgenetic alopecia, vertex vs occipital hair follicle (top 50)",
        condition="hair thinning",
        csv_name="liu2022_aga_vertex_vs_occipital.csv",
        limit=50,
        citation=_LIU_CITATION,
        url=_LIU_URL,
        what_it_is=_LIU_WHAT,
        truncation_note=(
            "The first 50 rows of Table S2 as published. The table is sorted by adjusted "
            "p-value ascending, so this is the paper's own 50 most significant genes and "
            "not a selection made here."
        ),
    ),
    Preset(
        id="liu2022_aga_full",
        label="Androgenetic alopecia, vertex vs occipital hair follicle (all 506)",
        condition="hair thinning",
        csv_name="liu2022_aga_vertex_vs_occipital.csv",
        limit=None,
        citation=_LIU_CITATION,
        url=_LIU_URL,
        what_it_is=_LIU_WHAT,
    ),
    Preset(
        id="sennett2024_acne_77",
        label="Acne, non-lesional acne-prone skin vs healthy control (all 77)",
        condition="oily skin",
        csv_name="sennett2024_acne_nonlesional_vs_healthy.csv",
        limit=None,
        citation=_SENNETT_CITATION,
        url=_SENNETT_URL,
        what_it_is=_SENNETT_WHAT,
        truncation_note=(
            "Not truncated. All 77 genes the paper reports at its own stated threshold, "
            "absolute log2 fold change above 1 and adjusted P below 0.05, applied to the "
            "full limma output it ships."
        ),
    ),
]


def _load(p: Preset) -> Preset:
    path = DATA_DIR / p.csv_name
    if not path.exists():
        return p
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    if p.limit is not None:
        rows = rows[: p.limit]
    p.symbols = [r["gene"] for r in rows]
    p.input_fields = {
        r["gene"]: {k: v for k, v in r.items() if k != "gene" and v} for r in rows
    }
    return p


PRESETS: dict[str, Preset] = {p.id: _load(p) for p in _DEFS}


def get(preset_id: str) -> Preset | None:
    return PRESETS.get(preset_id)


def listing() -> list[dict]:
    """Without the symbol arrays, which the menu does not need."""
    return [{k: v for k, v in p.as_dict().items() if k != "symbols"} for p in PRESETS.values()]

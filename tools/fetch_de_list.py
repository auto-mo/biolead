"""Fetch the demo differential expression list from its published supplementary table.

The list is NOT assembled here. It is Table S2 of a published paper, downloaded from the
Europe PMC supplementary-files endpoint and transcribed column for column. Nothing is
filtered, reordered or added: if a gene is in the batch demo, it is in that table.

    Liu Q, Tang Y, Huang Y, Wang J, Yang K, Zhang Y, Pu W, Liu J, Shi X, Ma Y, Ni C,
    Zhang Y, Zhu Y, Li H, Wang J, Lin J, Wu W.
    "Insights into male androgenetic alopecia using comparative transcriptome profiling:
    hypoxia-inducible factor-1 and Wnt/beta-catenin signalling pathways."
    British Journal of Dermatology 2022;187(6):936-947.
    doi:10.1111/bjd.21783  PMID 35862273  PMC10087000  CC BY-NC

Paired vertex (affected) and occipital (spared) anagen hair follicles from 10 male donors
with androgenetic alopecia, RNA-seq, DESeq2. Table S2 is the 506 differentially expressed
mRNAs.

Why this paper. The tool's `hair_thinning` endpoint borrows from androgenetic alopecia, so
the query condition and the paper's condition are the same disease, and the borrow being
exercised is the one the curated table actually rates. Vertex-versus-occipital within the
same donor is also the cleanest possible statement of the problem the tool exists for:
every gene on that list is differentially expressed in affected tissue, which is the
observation that makes a gene look like a target and does not make it one.

Two routes that did NOT work, recorded so nobody repeats them:
  - `pmc.ncbi.nlm.nih.gov/articles/instance/<id>/bin/*.docx` returns a JavaScript
    "Preparing to download" interstitial with a 200, not the file. It parses as HTML and
    fails as a docx, which at least fails loudly.
  - The PMC OA package href given by `oa.fcgi` is an `ftp://` URL whose https equivalent
    (`ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/23/31/PMC10087000.tar.gz`) is a 404.

SECOND LIST. Acne, chosen before running anything, for three stated
reasons: acne has real GWAS coverage where androgenetic alopecia has almost none, it is a
live ODDITY programme, and it is one of the two conditions in the September focus groups.

The first of those three did not survive the run and the record should say so. Open Targets
carries 520 targets with a genetic association at or above 0.05 for androgenetic alopecia
and 91 for acne. Male-pattern baldness is one of the most GWAS-powered traits there is,
because it is trivial to phenotype at biobank scale. The selection reason was wrong; the
selection still stands, because it was made in advance and for stated reasons.

    Sennett ML, Agak GW, Thiboutot DM, Nelson AM.
    "Transcriptomic Analyses Predict Enhanced Metabolic Activity and Therapeutic Potential
    of mTOR Inhibitors in Acne-Prone Skin."
    JID Innovations 2024;4(6):100306.
    doi:10.1016/j.xjidi.2024.100306  PMID 39310809  PMC11415809  CC BY-NC-ND

READ THE CONTRAST BEFORE QUOTING THIS ONE. It is NOT lesional versus non-lesional. It is
non-lesional skin from 49 people with acne against skin from 19 healthy controls with no
acne history. So it asks what is different about acne-prone skin that currently has no
lesion on it, which is a narrower question than what is different about a papule.

No lesional-versus-non-lesional acne DE table was found. Twenty-five open-access acne
papers with supplementary files were probed for a machine-readable table carrying gene,
fold change and an adjusted p-value; this was the only hit. The nearest alternative
(Frontiers, PMC8569320, CC BY) carries L1-versus-L-NI fold changes for 67 and 69 genes,
but those genes are selected by trifarotene response and by spontaneous resolution, so the
list is a treatment-response signature wearing a differential expression table's columns.

The 77 genes are the paper's own result at the paper's own stated threshold, |log2FC| > 1
and adjusted P < .05, applied to the full limma output the paper ships. The count is
asserted, and so is the presence of all eight genes the abstract names, so a change in the
table or the threshold fails the run instead of producing a different list quietly.

Usage:
    python3 tools/fetch_de_list.py            # both
    python3 tools/fetch_de_list.py acne       # one
"""

from __future__ import annotations

import csv
import io
import re
import sys
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "de_lists"
OUT_CSV = OUT_DIR / "liu2022_aga_vertex_vs_occipital.csv"

PMCID = "PMC10087000"
SUPP_URL = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{PMCID}/supplementaryFiles"
DOCX_NAME = "BJD-187-936-s002.docx"
TABLE_CAPTION = "Table S2. The list of differentially expressed mRNAs."
EXPECTED_HEADER = ["Gene", "baseMean", "log2FC", "lfcSE", "stat", "pvalue", "padj"]
EXPECTED_ROWS = 506

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _cell_text(el: ET.Element) -> str:
    return "".join(t.text or "" for t in el.iter(f"{W}t")).strip()


def _table_after_caption(body: ET.Element, caption: str) -> ET.Element:
    """Return the first <w:tbl> that follows a paragraph matching `caption`.

    Positional rather than by index: a docx has no table ids, and the supplementary
    document holds ten tables. Keying off the caption means an editorial reordering
    upstream produces a KeyError here rather than a silently different gene list.
    """
    seen = False
    for child in body:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p" and _cell_text(child).startswith(caption[:20]):
            seen = True
        elif tag == "tbl" and seen:
            return child
    raise SystemExit(f"caption not followed by a table: {caption!r}")


# =======================================================================================
# Acne. Sennett 2024, PMC11415809.
# =======================================================================================

ACNE_PMCID = "PMC11415809"
ACNE_SUPP = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{ACNE_PMCID}/supplementaryFiles"
ACNE_XLSX = "mmc1.xlsx"
ACNE_SHEET = "Differential expression results"
ACNE_HEADER = ["genes", "logFC", "AveExpr", "t", "P.Value", "adj.P.Val", "B",
               "symbol", "description", "biotype"]
ACNE_TOTAL_ROWS = 21685          # every gene tested, before the paper's threshold
ACNE_EXPECTED = 77               # the paper's own count at its own threshold
ACNE_OUT = OUT_DIR / "sennett2024_acne_nonlesional_vs_healthy.csv"
# Named in the abstract. If the threshold or the table moves, these stop matching.
ACNE_NAMED = ["KRT6C", "KRT16", "S100A8", "S100A9", "LTF", "LCE4A", "LCE6A", "CTSE"]

X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xlsx_rows(blob: bytes, sheet_name: str):
    """Minimal xlsx reader. Stdlib only, so the repo gains no dependency for one script."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {
        r.get("Id"): r.get("Target")
        for r in rels.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
    }
    target = None
    RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    for sh in wb.find(f"{X}sheets"):
        if sh.get("name") == sheet_name:
            target = rid_to_target[sh.get(RID)]
    if target is None:
        raise SystemExit(f"sheet {sheet_name!r} not in workbook")
    path = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"

    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
            shared.append("".join(t.text or "" for t in si.iter(f"{X}t")))

    for row in ET.fromstring(z.read(path)).find(f"{X}sheetData"):
        out = []
        for c in row:
            v = c.find(f"{X}v")
            raw = v.text if v is not None else None
            if raw is None:
                out.append(None)
            elif c.get("t") == "s":
                out.append(shared[int(raw)])
            else:
                out.append(raw)
        yield out


def fetch_acne() -> None:
    print(f"fetching {ACNE_SUPP}")
    with urllib.request.urlopen(ACNE_SUPP, timeout=180) as resp:
        blob = resp.read()
    print(f"  {len(blob):,} bytes")

    pkg = zipfile.ZipFile(io.BytesIO(blob))
    if ACNE_XLSX not in pkg.namelist():
        raise SystemExit(f"{ACNE_XLSX} not in package: {pkg.namelist()}")

    rows = list(_xlsx_rows(pkg.read(ACNE_XLSX), ACNE_SHEET))
    header = [h for h in rows[0] if h is not None]
    if header != ACNE_HEADER:
        raise SystemExit(f"header changed upstream: {header} != {ACNE_HEADER}")

    body = [r for r in rows[1:] if r and r[0]]
    if len(body) != ACNE_TOTAL_ROWS:
        raise SystemExit(f"row count changed upstream: {len(body)} != {ACNE_TOTAL_ROWS}")

    idx = {h: i for i, h in enumerate(ACNE_HEADER)}
    sel = []
    for r in body:
        try:
            lfc = float(r[idx["logFC"]])
            adj = float(r[idx["adj.P.Val"]])
        except (TypeError, ValueError):
            continue
        if abs(lfc) > 1 and adj < 0.05:
            sel.append(r)

    if len(sel) != ACNE_EXPECTED:
        raise SystemExit(
            f"the paper's threshold now yields {len(sel)} genes, not {ACNE_EXPECTED}. "
            "Either the table or the threshold has moved; do not write a different list."
        )
    symbols = [str(r[idx["symbol"]]) for r in sel]
    missing = [g for g in ACNE_NAMED if g not in symbols]
    if missing:
        raise SystemExit(f"genes named in the abstract are absent from the result: {missing}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    unnamed = 0
    with ACNE_OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gene", "ensembl_id", "log2fc", "ave_expr", "pvalue", "padj"])
        for r in sel:
            # Three of the 77 carry no HGNC symbol, only an Ensembl id. Writing the blank
            # through put three nameless rows into the batch table. The row stays, because
            # it is part of the paper's 77, and the Ensembl id stands in as its label.
            symbol = str(r[idx["symbol"]] or "").strip()
            ensembl = str(r[idx["genes"]] or "").strip()
            if not symbol:
                symbol = ensembl
                unnamed += 1
            if not symbol:
                raise SystemExit(f"row carries neither a symbol nor an Ensembl id: {r}")
            w.writerow([symbol, ensembl, r[idx["logFC"]],
                        r[idx["AveExpr"]], r[idx["P.Value"]], r[idx["adj.P.Val"]]])

    up = sum(1 for r in sel if float(r[idx["logFC"]]) > 0)
    print(f"wrote {ACNE_OUT.relative_to(ROOT)}  {len(sel)} genes  {up} up  {len(sel)-up} down")
    if unnamed:
        print(f"  {unnamed} of {len(sel)} rows carry no HGNC symbol. The Ensembl id stands "
              f"in, and those rows will not resolve to a target.")


def main() -> int:
    which = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    if which in ("acne", "all"):
        fetch_acne()
    if which == "acne":
        return 0
    print(f"fetching {SUPP_URL}")
    with urllib.request.urlopen(SUPP_URL, timeout=120) as resp:
        blob = resp.read()
    print(f"  {len(blob):,} bytes")

    zf = zipfile.ZipFile(io.BytesIO(blob))
    if DOCX_NAME not in zf.namelist():
        raise SystemExit(f"{DOCX_NAME} not in package: {zf.namelist()}")

    doc = zipfile.ZipFile(io.BytesIO(zf.read(DOCX_NAME)))
    body = ET.fromstring(doc.read("word/document.xml")).find(f"{W}body")
    assert body is not None

    tbl = _table_after_caption(body, TABLE_CAPTION)
    rows = [[_cell_text(c) for c in tr.findall(f"{W}tc")] for tr in tbl.findall(f"{W}tr")]

    header, data = rows[0], rows[1:]
    if header != EXPECTED_HEADER:
        raise SystemExit(f"header changed upstream: {header} != {EXPECTED_HEADER}")
    if len(data) != EXPECTED_ROWS:
        raise SystemExit(f"row count changed upstream: {len(data)} != {EXPECTED_ROWS}")

    # Transcribe verbatim. The only normalisation is stripping the non-breaking spaces and
    # soft hyphens Word leaves in cell runs, which would otherwise travel into a gene symbol.
    clean = [[re.sub(r"[ ­​]", "", c).strip() for c in r] for r in data]

    bad = [r[0] for r in clean if not re.fullmatch(r"[A-Za-z0-9\-./]+", r[0])]
    if bad:
        raise SystemExit(f"unexpected symbols: {bad[:10]}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gene", "base_mean", "log2fc", "lfc_se", "stat", "pvalue", "padj"])
        w.writerows(clean)

    up = sum(1 for r in clean if float(r[2]) > 0)
    print(f"wrote {OUT_CSV.relative_to(ROOT)}  {len(clean)} genes  {up} up  {len(clean)-up} down")
    return 0


if __name__ == "__main__":
    sys.exit(main())

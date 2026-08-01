# Differential expression lists used by the batch demo

Each file is a published supplementary table, transcribed column for column by
`tools/fetch_de_list.py`, which re-downloads the source and fails if the header or the row
count has changed upstream.

---

## `liu2022_aga_vertex_vs_occipital.csv`, 506 genes

Liu Q, Tang Y, Huang Y, Wang J, Yang K, Zhang Y, Pu W, Liu J, Shi X, Ma Y, Ni C, Zhang Y,
Zhu Y, Li H, Wang J, Lin J, Wu W. **Insights into male androgenetic alopecia using
comparative transcriptome profiling: hypoxia-inducible factor-1 and Wnt/β-catenin
signalling pathways.** *British Journal of Dermatology* 2022;187(6):936–947.

- doi [10.1111/bjd.21783](https://doi.org/10.1111/bjd.21783)
- PMID [35862273](https://pubmed.ncbi.nlm.nih.gov/35862273/)
- PMC [PMC10087000](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10087000/)
- Licence CC BY-NC. Redistributed here unmodified, with attribution, for non-commercial
  demonstration.

**What the table is.** Supplementary Table S2, "The list of differentially expressed
mRNAs". Paired vertex (affected) and occipital (spared) anagen hair follicles from ten male
donors with androgenetic alopecia, collected by follicular unit extraction, RNA-seq,
differential expression by DESeq2. 506 genes: 308 up in vertex, 198 down.

**Columns**, as published: `gene`, `base_mean`, `log2fc`, `lfc_se`, `stat`, `pvalue`,
`padj`. Only `gene` is read by the tool. The statistics travel with it so a reader can see
that a gene ranked at the bottom of the assessment can sit at the top of the paper.

**Retrieved** 2026-07-31 from the Europe PMC supplementary-files endpoint:

```bash
curl -sL https://www.ebi.ac.uk/europepmc/webservices/rest/PMC10087000/supplementaryFiles -o supp.zip
```

**Why this list and not another.** Three reasons, in order.

1. The condition matches a borrow the curated table already rates. The tool's
   `hair_thinning` endpoint borrows from androgenetic alopecia at MODERATE, so running this
   list exercises the borrow layer on the disease the paper studied.
2. Vertex versus occipital is a within-donor contrast. Both samples come from the same
   scalp, so the usual confounders of a case-control skin study (age, ancestry, treatment,
   sun exposure, biopsy site) are matched by construction. What survives is a within-donor contrast.
3. Every gene on it is differentially expressed in affected tissue, which is the observation
   that makes a gene look like a target and does not make it one.

**Two routes that do not work:**

- `pmc.ncbi.nlm.nih.gov/articles/instance/10087000/bin/BJD-187-936-s002.docx` returns HTTP
  200 with a JavaScript "Preparing to download" interstitial instead of the file. It is
  1,817 bytes of HTML that fails to open as a docx.
- `oa.fcgi` reports the OA package at `ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/23/31/
  PMC10087000.tar.gz`. The https equivalent of that exact path is a 404.

---

## `sennett2024_acne_nonlesional_vs_healthy.csv`, 77 genes

Sennett ML, Agak GW, Thiboutot DM, Nelson AM. **Transcriptomic Analyses Predict Enhanced
Metabolic Activity and Therapeutic Potential of mTOR Inhibitors in Acne-Prone Skin.**
*JID Innovations* 2024;4(6):100306.

- doi [10.1016/j.xjidi.2024.100306](https://doi.org/10.1016/j.xjidi.2024.100306)
- PMID [39310809](https://pubmed.ncbi.nlm.nih.gov/39310809/)
- PMC [PMC11415809](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11415809/)
- Licence **CC BY-NC-ND**. A portion is reproduced here with attribution and without
  adaptation; the xlsx to csv change is a format shift, which CC 4.0 states is not an
  adaptation. Non-commercial use only.

**Read the contrast before quoting this one.** It is **not** lesional versus non-lesional.
It is non-lesional skin from 49 people with acne against skin from 19 healthy controls with
no acne history, so it asks what is different about acne-prone skin currently carrying no
lesion. That is a narrower question than what is different about a papule.

**The 77 genes are the paper's result at the paper's threshold**, absolute log2 fold change
above 1 and adjusted P below 0.05, applied to the full limma output it ships (21,685 genes
tested). `tools/fetch_de_list.py` asserts the sheet name, the header, the 21,685 row count,
the resulting 77, and the presence of all eight genes the abstract names by hand. Any of
those moving fails the run instead of writing a different list.

**Retrieved** 2026-07-31:

```bash
curl -sL https://www.ebi.ac.uk/europepmc/webservices/rest/PMC11415809/supplementaryFiles -o supp.zip
```

**Three of the 77 carry no HGNC symbol**, only an Ensembl id. They are kept, because they
are part of the published 77, and the Ensembl id stands in as the label. They do not
resolve to a target and appear as not assessable.

**Why acne, chosen before running anything.** Acne has real GWAS coverage where
androgenetic alopecia has almost none; it is a live ODDITY programme; and it is one of the
two conditions in the September focus groups.

**The first of those three reasons is wrong, and the record should say so.** Open Targets
carries **520** targets with a genetic association at or above 0.05 for androgenetic
alopecia and **91** for acne. Male-pattern baldness is one of the most GWAS-powered traits
there is, because it is trivial to phenotype at biobank scale. The reason was wrong. The
choice stands, because it was made in advance and for stated reasons, and because the
result is reported unchanged.

**No lesional-versus-non-lesional acne table was found.** Twenty-five open-access acne
papers with supplementary files were probed for a machine-readable table carrying a gene,
a fold change and an adjusted p-value. This was the only hit. The nearest alternative
(PMC8569320, CC BY) carries lesional-versus-non-involved fold changes for 67 and 69 genes,
but those genes are selected by trifarotene response and by spontaneous resolution, so the
list is a treatment-response signature with a differential expression table's columns.

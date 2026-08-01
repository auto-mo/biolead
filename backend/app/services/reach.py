"""Topical reachability. A gate after the verdict, not a third verdict field.

WHY THIS IS SEPARATE, AND WHY THAT SEPARATION IS LOAD BEARING.

Position and modulation outcome are claims about biology: where the gene sits in the causal
chain, and whether moving it moved a human endpoint. Reachability is a claim about product:
whether a thing you rub on skin could plausibly get to the protein. Those are different
questions with different evidence and different failure modes, and a gene can be a perfect
causal target and a hopeless topical one.

IL17A is the case that makes it concrete. It is a real target for its disease, secukinumab
works, and HPA reports its skin expression at 0.0 nTPM with immunohistochemistry not
detected. It is secreted to blood. A topical against IL17A is not a formulation problem, it
is a category error, and none of that is visible anywhere in the causal verdict.

The whole point of the earlier work was splitting one verdict into two axes that mean
different things. Folding a commercial constraint back in as a third field would undo that,
so this renders as a separate gate with its own vocabulary and never touches `position` or
`targetability`.

WHAT THIS IS NOT. It is not a permeation prediction. Nothing here models the stratum
corneum, log P, molecular weight or vehicle. It reads two things a database can actually
answer, where the protein is and whether any modality has ever been made against it, and
refuses on the rest. A real formulation call needs a formulator.

RULES, in the same style as the verdict engine: deterministic, and every outcome records
which fact fired.
"""

from __future__ import annotations

from app.clients.hpa import HpaRecord
from app.models.contracts import Reachability

# Skin RNA below this is treated as not present. Chosen, not derived: HPA's own IH call is
# the stronger signal and this only decides what happens when IH is absent or negative.
# Stated here so it can be argued with, which is the same treatment the borrow ratings get.
NTPM_PRESENT = 1.0

SMALL_MOLECULE = "SM"
ANTIBODY = "AB"

# NOT ALL BUCKETS IN A MODALITY MEAN THE SAME THING, and reading them as if they did
# produced a wrong answer on the first run. Six of the nine antibody buckets are
# LOCALISATION PREDICTIONS -- `GO CC high conf`, `UniProt loc high conf`, `UniProt SigP or
# TMHMM` and friends assert that the protein is extracellular or membrane-bound, which is
# what would make it reachable by an antibody IF one existed. They are not evidence that
# one does.
#
# The first cut of this file treated any true AB bucket as "the only modality here is an
# antibody" and therefore called FLG OUT_OF_REACH. FLG's single true antibody bucket is
# `GO CC high conf`. FLG is a structural protein with no tractable modality at all, which
# is a real and useful finding, and "antibody only" is not it.
#
# So the buckets are split by what they actually assert. Only the clinical ones are
# evidence that a molecule exists; the structural small-molecule ones are evidence that one
# could plausibly be made; the localisation ones say nothing about tractability and are used
# only to corroborate where the protein sits.
SM_CLINICAL = {"Approved Drug", "Advanced Clinical", "Phase 1 Clinical"}
SM_STRUCTURAL = {
    "Structure with Ligand", "High-Quality Ligand", "High-Quality Pocket",
    "Med-Quality Pocket", "Druggable Family",
}
AB_CLINICAL = {"Approved Drug", "Advanced Clinical", "Phase 1 Clinical"}
AB_LOCATION_ONLY = {
    "UniProt loc high conf", "GO CC high conf", "UniProt loc med conf",
    "UniProt SigP or TMHMM", "GO CC med conf", "Human Protein Atlas loc",
}

# Subcellular locations that sit at or outside the cell boundary. A target here needs the
# drug to reach the tissue; one inside the cell needs it to reach the tissue AND get in.
_OUTSIDE = {"Plasma membrane", "Cell Junctions", "Focal adhesion sites"}


def assess_reachability(
    hpa: HpaRecord | None,
    tractability: dict[str, list[str]] | None,
    *,
    tractability_assessed: bool = True,
) -> Reachability:
    """Combine location and modality into one gate.

    `tractability` None means the target was never assessed; an empty dict means it was
    assessed and nothing is tractable. Those are different and the API distinguishes them
    only by presence or absence in the response, so the caller passes the distinction in.
    """
    reasons: list[str] = []
    blockers: list[str] = []
    unknowns: list[str] = []

    # ---- 1. Is the protein in skin at all -------------------------------------------
    in_skin: bool | None = None
    if hpa is None or not hpa.ok:
        unknowns.append(
            "Skin localisation could not be read"
            + (f": {hpa.error}" if hpa and hpa.error else "")
        )
    else:
        ih = (hpa.skin_ih or "").strip().lower()
        ntpm = hpa.skin_ntpm
        if ih in ("high", "medium", "low"):
            in_skin = True
            reasons.append(f"Protein detected in skin by immunohistochemistry at {ih}.")
        elif ih == "not detected" and (ntpm is None or ntpm < NTPM_PRESENT):
            in_skin = False
            blockers.append(
                "Not detected in skin by immunohistochemistry, and skin RNA is "
                f"{ntpm if ntpm is not None else 'unavailable'} nTPM. There is no protein "
                "in the tissue for a topical to act on."
            )
        elif ntpm is not None and ntpm >= NTPM_PRESENT:
            in_skin = True
            reasons.append(
                f"Skin RNA {ntpm:g} nTPM"
                + (
                    ", though immunohistochemistry did not detect the protein, so the two "
                    "assays disagree."
                    if ih == "not detected"
                    else "."
                )
            )
        else:
            unknowns.append("Neither assay places this protein in skin either way.")

    # ---- 2. Which compartment -------------------------------------------------------
    depth: str | None = None
    if hpa is not None and hpa.ok and hpa.compartments:
        present = {k for k, v in hpa.compartments.items() if v != "not detected"}
        donors = f" across {hpa.donors_seen} donors" if hpa.donors_seen > 1 else ""
        if "epidermis" in present:
            depth = "EPIDERMAL"
            reasons.append(
                f"Present in the epidermis ({hpa.compartments['epidermis']}){donors}, which "
                "is the compartment a topical reaches first."
            )
        elif "appendage" in present:
            depth = "APPENDAGEAL"
            reasons.append(
                f"Present in skin appendages ({hpa.compartments['appendage']}){donors} "
                "rather than the epidermis proper. Follicular delivery is a route, not a "
                "given."
            )
        elif "dermis" in present:
            depth = "DERMAL"
            blockers.append(
                f"Annotated in dermal cells ({hpa.compartments['dermis']}) and not in the "
                "epidermis. A topical has to cross the full epidermis to get there."
            )
        else:
            unknowns.append(
                "Immunohistochemistry was run across the skin cell types and detected the "
                "protein in none of them, so depth within skin is unresolved. Where skin "
                "RNA is present this is the two assays disagreeing rather than an answer."
            )
    elif in_skin:
        unknowns.append("No skin cell-type annotation, so depth within skin is unknown.")

    # ---- 3. Secreted or intracellular -----------------------------------------------
    if hpa is not None and hpa.ok:
        if hpa.secretome_location:
            reasons.append(f"Secreted protein ({hpa.secretome_location}).")
        elif not hpa.subcellular_measured:
            # 31% of genes have no immunofluorescence data. Absence is a gap in the atlas.
            unknowns.append(
                "No subcellular immunofluorescence data, so whether the target sits inside "
                "the cell is unmeasured rather than negative."
            )
        else:
            locs = hpa.subcellular_main or hpa.subcellular
            if any(loc in _OUTSIDE for loc in locs):
                reasons.append(f"At the cell surface ({', '.join(locs)}).")
            else:
                reasons.append(
                    f"Intracellular ({', '.join(locs)}), so a topical has to enter the cell "
                    "as well as the tissue."
                )

    # ---- 4. Modality ----------------------------------------------------------------
    sm_buckets = set((tractability or {}).get(SMALL_MOLECULE, []))
    ab_buckets = set((tractability or {}).get(ANTIBODY, []))
    sm = bool(sm_buckets & (SM_CLINICAL | SM_STRUCTURAL))
    ab_clinical = bool(ab_buckets & AB_CLINICAL)
    no_modality = tractability is not None and not sm and not ab_clinical

    if not tractability_assessed or tractability is None:
        unknowns.append("Tractability was not assessed for this target.")
    elif sm:
        clinical = sorted(sm_buckets & SM_CLINICAL)
        reasons.append(
            (
                f"A small molecule against this target has reached the clinic ({', '.join(clinical)})."
                if clinical
                else "Small molecule tractable on structural grounds ("
                + ", ".join(sorted(sm_buckets & SM_STRUCTURAL)[:3])
                + ")."
            )
            + " A small molecule is the modality that crosses intact stratum corneum."
        )
    elif ab_clinical:
        blockers.append(
            "The only modality that has reached the clinic here is an antibody ("
            + ", ".join(sorted(ab_buckets & AB_CLINICAL))
            + "), and no small molecule is tractable. Antibodies do not cross intact "
            "stratum corneum, so this is a systemic target rather than a topical one."
        )
    else:
        blockers.append(
            "No modality is tractable. Nothing has reached the clinic and there is no "
            "ligandable pocket or druggable family, so there is no molecule to formulate "
            "regardless of where the protein sits."
        )

    # The antibody localisation buckets are not tractability. They corroborate the HPA read
    # of where the protein sits, which is the only thing they actually assert.
    if ab_buckets & AB_LOCATION_ONLY:
        reasons.append(
            "Predicted extracellular or membrane-associated ("
            + ", ".join(sorted(ab_buckets & AB_LOCATION_ONLY)[:2])
            + "), which is a location call rather than evidence of a molecule."
        )

    # ---- 5. Combine. The worst constraint governs. -----------------------------------
    if in_skin is False:
        verdict, rule = "OUT_OF_REACH", "REACH_NOT_IN_SKIN"
    elif ab_clinical and not sm and in_skin:
        verdict, rule = "OUT_OF_REACH", "REACH_ANTIBODY_ONLY"
    elif no_modality and in_skin:
        # Present and unformulatable. Distinct from OUT_OF_REACH: the target is right
        # there, and the gap is chemistry rather than delivery.
        verdict, rule = "OUT_OF_REACH", "REACH_NO_TRACTABLE_MODALITY"
    elif in_skin and sm and depth in ("EPIDERMAL", "APPENDAGEAL"):
        verdict, rule = "REACHABLE", "REACH_IN_EPIDERMIS_SM_TRACTABLE"
    elif in_skin and depth == "DERMAL":
        verdict, rule = "HARD_TO_REACH", "REACH_DERMAL_ONLY"
    elif in_skin and sm:
        verdict, rule = "HARD_TO_REACH", "REACH_IN_SKIN_DEPTH_UNKNOWN"
    elif in_skin:
        verdict, rule = "HARD_TO_REACH", "REACH_IN_SKIN_NO_MODALITY"
    else:
        verdict, rule = "UNKNOWN", "REACH_INSUFFICIENT"

    return Reachability(
        verdict=verdict,  # type: ignore[arg-type]
        rule_fired=rule,
        depth=depth,  # type: ignore[arg-type]
        in_skin=in_skin,
        skin_ntpm=hpa.skin_ntpm if hpa and hpa.ok else None,
        skin_ih=hpa.skin_ih if hpa and hpa.ok else None,
        compartments=dict(hpa.compartments) if hpa and hpa.ok else {},
        subcellular=list(hpa.subcellular_main or hpa.subcellular) if hpa and hpa.ok else [],
        secretome_location=hpa.secretome_location if hpa and hpa.ok else None,
        small_molecule_buckets=sorted(sm_buckets),
        antibody_buckets=sorted(ab_buckets),
        sm_clinical=sorted(sm_buckets & SM_CLINICAL),
        sm_structural=sorted(sm_buckets & SM_STRUCTURAL),
        ab_clinical=sorted(ab_buckets & AB_CLINICAL),
        ab_location_only=sorted(ab_buckets & AB_LOCATION_ONLY),
        supports=reasons,
        blockers=blockers,
        unknowns=unknowns,
    )

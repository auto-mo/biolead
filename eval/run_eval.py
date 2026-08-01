"""Evaluation harness.

Not an accuracy benchmark. At n=15 a single miss moves measured accuracy by roughly seven
points, which makes calibration metrics meaningless. This is a DETERMINISTIC REGRESSION
CHECK: it confirms a code change did not break expected behaviour on known cases.

It asserts five things per case, not one:
  - position
  - targetability            (separate field, a case can fail on either)
  - confidence               (asserted, because a confidence band can move unnoticed)
  - mode
  - cited tiers              a correct call citing the wrong tiers is a FAILURE, and that
                             is the check that distinguishes reasoning from a lucky guess

It also runs the ABLATION: the model twice, once with the curated ratings and once without,
reporting how often curation changed its call.

Usage:
    python eval/run_eval.py            # rules only, no model
    python eval/run_eval.py --model    # + adjudication and ablation
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.clients.clinicaltrials import ClinicalTrialsClient  # noqa: E402
from app.clients.open_targets import OpenTargetsClient  # noqa: E402
from app.core.config import load_config  # noqa: E402
from app.services.adjudicate import (  # noqa: E402
    ABLATION_MODEL, _load_env, ablate, build_adjudicator,
)
from app.services.batch import run_batch  # noqa: E402
from app.services.pipeline import run_assessment  # noqa: E402

CASES_PATH = Path(__file__).parent / "benchmark_cases.json"

# RUNNING THE WHOLE BENCHMARK IS ITSELF A SWEEP AGAINST CLINICALTRIALS.GOV, and running two
# of them back to back is a bigger one.
#
# Under the graph provider every case enumerates and reads every trial it can reach, and the
# batch-equivalence pass does it again, so a full run is several hundred fetches. The source
# then starts refusing, and the refusals arrive as VERDICTS rather than as errors: SRD5A2
# comes back UNKNOWN/MODERATE and RARG comes back NOT_ASSESSED, neither of which is a
# regression and both of which look exactly like one. Run in isolation, the same two cases
# return ACTIONABLE/HIGH and TESTED_REPORTED.
#
# Two mitigations, and they are not the same thing. `--space` puts a gap between cases.
# `outcome_tools._TRIAL_CACHE` stops the same settled record being fetched twice in a run,
# which is what makes the batch pass nearly free. Neither helps if the PREVIOUS run finished
# seconds ago. Leave a minute between full runs.


async def one(case: dict, cfg, ot, ct, adjudicator=None, outcome_provider=None,
              provider_name: str = "file", skipped: list | None = None) -> dict:
    skipped = skipped if skipped is not None else []
    assessment = None
    async for ev in run_assessment(
        case["gene"], case["condition"], config=cfg, ot=ot, ct=ct, adjudicator=adjudicator,
        outcome_provider=outcome_provider
    ):
        if ev.event == "assessment":
            assessment = ev.data
    assert assessment is not None

    v = assessment["final_verdict"]
    got_tiers = set(v["cited_tiers"])
    want_tiers = set(case.get("expect_tiers", []))

    checks = {
        "position": (v["position"], case["expect_position"]),
        "targetability": (v["targetability"], case["expect_targetability"]),
        "mode": (assessment["mode"], case["expect_mode"]),
        # Confidence is asserted exactly, and it is not a tolerance.
        #
        # Confidence is asserted because a borrow cap change can move it silently, from HIGH
        # to MODERATE, HIGH to LOW and HIGH to MODERATE, and this harness stayed green
        # through all three because it never looked. A cap that silently stops applying is
        # exactly the class of change an eval exists to catch.
        #
        # Exact here because everything in these eleven cases comes off the deterministic
        # spine. When the outcome axis becomes agentic its confidence is band-asserted
        # instead.
        "confidence": (v["confidence"], case.get("expect_confidence")),
    }
    # Asserted only against a provider that can produce it. The file provider structurally
    # cannot say TESTED_UNREPORTED: a curated file has no way to know a drug exists whose
    # trial posted nothing. Skipping is the honest behaviour and the skip is reported, so
    # the assertion is not quietly absent.
    if case.get("expect_outcome_state"):
        if provider_name == "file":
            skipped.append(f"{case['gene']} x {case['condition']}: outcome_state not "
                           f"assertable against the file provider")
        else:
            checks["outcome_state"] = (v.get("outcome_state"), case["expect_outcome_state"])
    failures = [f"{k}: got {g!r} want {w!r}" for k, (g, w) in checks.items() if g != w]

    # A DEGRADED RUN IS A FAILED RUN, not a quieter pass.
    #
    # `could_not_check` means a source the verdict would have rested on could not be read.
    # When that happens the confidence cap and the outcome state are computed over less
    # evidence than the case was written against, and the answer changes without saying so.
    #
    # This is not hypothetical. A concurrent sweep starved the ClinicalTrials.gov client,
    # NCT01231607 came back `exists: False`, the finasteride fact silently stayed tier 1b
    # asserted, and SRD5A2 reported MODERATE instead of HIGH. Every assertion above passed.
    # Isolated, the same fetch succeeded four times out of four.
    #
    # So a case that names an expectation about confidence or an outcome state must have
    # been able to read the evidence those depend on.
    degraded = [
        c for c in assessment["tier_profile"]["could_not_check"]
        # `outcome_graph(...)` is in this list because the outcome layer failing wholesale is
        # the most degraded a run can be, and it was the one shape the filter missed. A
        # missing API key produced exactly it, and the case reported a clean verdict diff
        # instead of a degraded run.
        if ("clinicaltrials" in c.lower() or "open_targets" in c.lower()
            or "outcome_graph" in c.lower())
    ]
    asserts_derived = (
        case.get("expect_confidence") is not None
        or case.get("expect_outcome_state") is not None
    )
    if degraded and asserts_derived:
        failures.append(
            "DEGRADED: a source this case depends on could not be read, so the confidence "
            "band and outcome state were derived over partial evidence. "
            + "; ".join(d[:110] for d in degraded[:2])
        )
    if want_tiers and not want_tiers.issubset(got_tiers):
        failures.append(f"tiers: missing {sorted(want_tiers - got_tiers)} (cited {sorted(got_tiers)})")

    # The provider the verdict actually came from, read off the verdict rather than from the
    # flag that was requested. If a provider ever falls back, the result says so.
    got_provider = v.get("outcome_provider")
    if got_provider and got_provider != provider_name:
        failures.append(f"provider: verdict says {got_provider!r}, run requested "
                        f"{provider_name!r}")

    return {
        "case": f"{case['gene']} x {case['condition']}",
        "provider": got_provider,
        "position": v["position"],
        "targetability": v["targetability"],
        "confidence": v["confidence"],
        "mode": assessment["mode"],
        "tiers": sorted(got_tiers),
        "rule": v["rule_fired"],
        "passed": not failures,
        "failures": failures,
        "assessment": assessment,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    # THE SHIPPING DEFAULT SINCE STAGE 7. The benchmark asserts what ships; `--provider file`
    # runs the retired curated file, which is kept as the demo fallback and deviates from
    # these expectations in the documented ways (TNF loses its tier 1b fact, and the outcome
    # states the file cannot produce are skipped).
    ap.add_argument("--provider", default="graph",
                    help="outcome provider: graph (default, shipping), file, or graph-rules")
    # RUNNING FIFTEEN CASES BACK TO BACK IS ITSELF A SWEEP AGAINST CLINICALTRIALS.GOV.
    #
    # The graph provider gathers every reachable trial per case, so a full run is several
    # hundred fetches in a few minutes. Unspaced, the source starts refusing partway through
    # and the failures arrive as verdicts: SRD5A2 came back UNKNOWN/MODERATE and RARG came
    # back NOT_ASSESSED, neither of which is a regression and both of which look exactly like
    # one. The degraded-run guard catches the cases that assert a band; spacing is what stops
    # it happening. Set to 0 for the file provider, which makes no trial calls of its own.
    ap.add_argument("--space", type=float, default=None,
                    help="seconds between cases. Defaults to 6 on graph, 0 on file")
    ap.add_argument("--model", action="store_true", help="run adjudication and ablation")
    args = ap.parse_args()

    # WITHOUT THIS THE OUTCOME AGENT HAS NO KEY, AND THE FAILURE DOES NOT LOOK LIKE ONE.
    #
    # The graph node raises, the provider turns that into NOT_ASSESSED, the case loses its
    # directional tier 1 and comes back UNKNOWN/MODERATE. Cases with nothing ambiguous to
    # read pass anyway, because they never call the model. That is exactly the pattern that
    # made SRD5A2 and RARG fail here and pass everywhere else: every other entry point calls
    # `_load_env`, and this one did not.
    _load_env()

    cases = json.loads(CASES_PATH.read_text())
    cfg = load_config(strict=True)
    # Haiku for the ablation. The question is only whether the call changed when the
    # curated layer was stripped, and both arms use the same model either way.
    adjudicator = build_adjudicator(ABLATION_MODEL) if args.model else None
    is_stub = bool(adjudicator and getattr(adjudicator, "is_stub", False))

    async with OpenTargetsClient() as ot, ClinicalTrialsClient() as ct:
        # `--provider retrieved` runs the cases against the retrieval layer instead of the
        # curated file. The default stays `file`, so the shipping behaviour is what is
        # asserted unless someone asks otherwise.
        # ALWAYS BUILT EXPLICITLY. This used to pass None for `file` and rely on the
        # process default being `file`. The default is `graph`, so a run
        # asking for the curated file silently got the subgraph, and every expectation
        # calibrated against the file failed. The provider assertion in `one()` is what
        # caught it, which is the assertion doing its job on its first outing.
        import httpx
        from app.services.outcome import build_outcome_provider
        pname = args.provider
        http = httpx.AsyncClient(timeout=90)
        prov = build_outcome_provider(pname, ot=ot, http=http)
        skipped: list[str] = []
        space = args.space if args.space is not None else (
            0.0 if pname == "file" else 6.0)
        results = []
        for i, c in enumerate(cases):
            if i and space:
                await asyncio.sleep(space)
            results.append(await one(c, cfg, ot, ct, adjudicator, prov, pname, skipped))
        if http is not None:
            await http.aclose()
        if skipped:
            print(f"\n{len(skipped)} assertion(s) skipped for this provider:")
            for sk in skipped[:6]:
                print(f"  {sk}")

        # Named at the top and on every row. Three providers exist and only `graph` applies
        # the attribution cap and the disagreement rule, so an eval table without a provider
        # column is a table that can be quoted as the wrong thing.
        print(f"\n{'=' * 118}")
        print(f"OUTCOME PROVIDER: {pname}")
        if pname == "file":
            print("  Curated `config/clinical_facts.yaml`. No attribution cap, no "
                  "disagreement rule. This is the shipping default.")
        elif pname == "retrieved":
            print("  Retrieval only. NO attribution cap and NO disagreement rule. "
                  "Do not quote this as the rebuild's answer.")
        elif pname == "graph":
            print("  The outcome subgraph. Attribution cap and disagreement rule both "
                  "applied. Costs one model call chain per drugged gene.")
        print(f"{'=' * 118}")
        print(f"\n{'CASE':34}{'POSITION':17}{'TARGET':16}{'CONF':10}{'MODE':11}"
              f"{'TIERS':14}{'PROV':10}OK")
        print("-" * 128)
        for r in results:
            print(f"{r['case']:34}{r['position']:17}{r['targetability']:16}"
                  f"{str(r['confidence']):10}{r['mode']:11}{','.join(r['tiers']):14}"
                  f"{str(r.get('provider')):10}{'PASS' if r['passed'] else 'FAIL'}")
            for f in r["failures"]:
                print(f"      -> {f}")

        passed = sum(r["passed"] for r in results)
        print(f"\nRegression check: {passed}/{len(results)} cases match expected behaviour, "
              f"against the {pname!r} outcome provider.")
        print("NOT an accuracy figure. At n=15 a single miss moves it ~7 points.")

        # ---- Batch equivalence ----------------------------------------------------
        # Batch fetches differently and must decide identically. The claim that it shares
        # the verdict code is worth nothing unasserted: the two paths reach the same
        # association route from opposite ends, and a divergence would mean the triage
        # table and the drill-down disagree about the same gene on the same screen.
        print("\n" + "=" * 60)
        by_condition: dict[str, list[str]] = {}
        for c in cases:
            by_condition.setdefault(c["condition"], []).append(c["gene"])

        batch_verdicts: dict[tuple[str, str], dict] = {}
        for cond, genes in by_condition.items():
            # THE SAME PROVIDER AS THE SINGLE-GENE RUN. The claim being asserted is that
            # the two paths decide identically, and letting batch pick its own provider
            # tests something else entirely: single ran on the requested
            # provider and batch on the process default, and the "disagreement" was the
            # curated file against the subgraph.
            async for ev in run_batch(genes, cond, config=cfg, ot=ot, ct=ct,
                                      outcome_provider=prov):
                if ev.event == "batch_result":
                    for row in ev.data["rows"]:
                        batch_verdicts[(row["gene"], cond)] = row["verdict"]

        def verdict_fields(v: dict | None):
            if v is None:
                return None
            return (v["position"], v["targetability"], v["confidence"])

        # The one difference the two paths are ALLOWED to have, and it is not a defect.
        # Single-gene calls the row-level evidence route and cross-checks it against the
        # association route; batch does not call it at all. When that route flakes -- step
        # zero measured the same query returning 165 rows and then 0, and it still does,
        # reproducing on roughly 1 run in 5 for FASN -- the single-gene path fires
        # abstention trigger 6 and batch fires trigger 5. Both abstain, both land on
        # INSUFFICIENT / UNKNOWN, so no triage row moves. Only the stated reason differs.
        #
        # Tolerated in exactly that shape and nowhere else: single must be the trigger 6
        # side, and the verdict fields must already match.
        mismatches: list[str] = []
        tolerated: list[str] = []
        for c, r in zip(cases, results):
            sv = r["assessment"]["final_verdict"]
            bv = batch_verdicts.get((c["gene"], c["condition"]))
            label = f"{c['gene']} x {c['condition']}"

            if verdict_fields(sv) != verdict_fields(bv):
                mismatches.append(f"{label}: verdict differs, "
                                  f"single {verdict_fields(sv)} vs batch {verdict_fields(bv)}")
                continue
            s_rule = (sv or {}).get("rule_fired")
            b_rule = (bv or {}).get("rule_fired")
            if s_rule == b_rule:
                continue
            if s_rule == "ABSTAIN_6_source_degraded" and str(b_rule).startswith("ABSTAIN_"):
                tolerated.append(f"{label}: single {s_rule}, batch {b_rule} "
                                 f"(row-level route degraded; batch does not call it)")
            else:
                mismatches.append(f"{label}: rule differs, single {s_rule} vs batch {b_rule}")

        print(f"Batch/single equivalence: {len(cases) - len(mismatches)}/{len(cases)} agree "
              f"on position, modulation outcome and confidence.")
        for t in tolerated:
            print(f"  ~  {t}")
        for m in mismatches:
            print(f"  -> {m}")
        if tolerated:
            print("  ~ rows are the known one-way difference, not failures. See the comment "
                  "above this check.")
        equivalent = not mismatches

        # ---- Ablation -------------------------------------------------------------
        if args.model:
            print("\n" + "=" * 60)
            if is_stub:
                print("ABLATION SKIPPED: no ANTHROPIC_API_KEY, so the stub adjudicator is in")
                print("use. The stub is deterministic and not a model opinion, so agreement")
                print("and ablation numbers from it would be meaningless. Reported as such")
                print("rather than quietly printed.")
            else:
                print(f"Adjudicator model: {getattr(adjudicator, '_model', 'unknown')}")
                changed = disagreed = 0
                events: list[dict] = []
                for r in results:
                    a = r["assessment"]
                    if a.get("model_verdict") is None:
                        continue
                    if a.get("agreement") is False:
                        disagreed += 1
                        mv = a["model_verdict"]
                        events.append({"kind": "disagreement", "case": r["case"],
                                       "rule": [r["position"], r["targetability"]],
                                       "model": [mv["position"], mv["targetability"]]})
                        print(f"  rule and model disagree on {r['case']}: "
                              f"rule {r['position']}/{r['targetability']} vs "
                              f"model {mv['position']}/{mv['targetability']}")
                    packet = {
                        "gene": a["gene"], "condition": a["condition_as_typed"],
                        "mode": a["mode"], "proxy": a["proxy"],
                        "tiers": a["tier_profile"]["tiers"],
                        "checked_and_empty": a["tier_profile"]["checked_and_empty"],
                        "could_not_check": a["tier_profile"]["could_not_check"],
                    }
                    ab = await adjudicator.adjudicate(ablate(packet))
                    full = a["model_verdict"]
                    if ab and (ab.position != full["position"]
                               or ab.targetability != full["targetability"]):
                        changed += 1
                        events.append({"kind": "curation_changed", "case": r["case"],
                                       "ablated": [ab.position, ab.targetability],
                                       "full": [full["position"], full["targetability"]]})
                        print(f"  curation changed the model on {r['case']}: "
                              f"{ab.position}/{ab.targetability} -> "
                              f"{full['position']}/{full['targetability']}")
                n = len(results)
                # Persist per-case detail. Reading flip counts back off scrollback loses which
                # cases flipped, which is the only part of an ablation worth anything.
                out = Path(__file__).parent / "ablation_results.jsonl"
                with out.open("a") as fh:
                    fh.write(json.dumps({
                        "model": getattr(adjudicator, "_model", None),
                        "cases": n, "disagreed": disagreed, "changed": changed,
                        "events": events,
                    }) + "\n")
                print(f"Per-case detail appended to {out}")
                print(f"\nRule vs model disagreement: {disagreed}/{n}")
                print(f"Curation changed the model's call: {changed}/{n}")
                if disagreed == 0:
                    print("Zero disagreement. Present the dual-verdict mechanism as a")
                    print("safeguard rather than a feature, and say the rate was zero.")

    return 0 if (passed == len(results) and equivalent) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Write a preset from the targets Open Targets already associates with a disease.

Not a differential expression list. This is the other kind of starting point: the genes a
reference database already links to the disease, best association first. It exists because
a DE list and an association list are different questions, and the tool answers them
differently. The DE lists stay, as the harder case.

Regenerate:
    python3 tools/fetch_association_list.py
"""
from __future__ import annotations
import asyncio, csv, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.clients.open_targets import OpenTargetsClient  # noqa: E402

OUT_DIR = ROOT / "data" / "de_lists"
SETS = [("androgenetic alopecia", "opentargets_aga_top50_by_association.csv", 50)]

Q = ("query A($d:String!,$s:Int!){disease(efoId:$d){id name associatedTargets"
     "(page:{index:0,size:$s}){count rows{target{approvedSymbol id} score "
     "datatypeScores{id score}}}}}")


async def main() -> int:
    async with OpenTargetsClient() as ot:
        version = await ot.data_version()
        for term, fname, n in SETS:
            d = await ot.resolve_disease(term)
            if d is None:
                raise SystemExit(f"{term!r} did not resolve")
            r = await ot._gql(Q, {"d": d.id, "s": n})
            at = r["disease"]["associatedTargets"]
            rows = at["rows"]
            if len(rows) != n:
                raise SystemExit(f"asked for {n}, got {len(rows)}; do not write a short list")
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            with (OUT_DIR / fname).open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["gene", "ensembl_id", "association_score",
                            "genetic_association", "clinical", "literature"])
                for x in rows:
                    ds = {y["id"]: y["score"] for y in x["datatypeScores"]}
                    w.writerow([x["target"]["approvedSymbol"], x["target"]["id"],
                                round(x["score"], 4),
                                round(ds.get("genetic_association", 0), 4),
                                round(ds.get("clinical", 0), 4),
                                round(ds.get("literature", 0), 4)])
            print(f"wrote {fname}  {len(rows)} genes  disease={d.name} ({d.id})  {version}")
            print(f"  score range {rows[0]['score']:.3f} to {rows[-1]['score']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

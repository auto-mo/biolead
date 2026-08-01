"""Open Targets GraphQL client.

Three findings from step zero are structural here, not incidental. Each one silently
produces a wrong answer rather than an error, so each is handled in the shape of the code:

  1. `enableIndirect` is IGNORED when passed as a GraphQL variable. Sent as
     `$ind:Boolean!` with `"ind": true`, the server behaves as if it were false and
     returns zero evidence rows. Written inline it returns 165 for the same query. A
     correctly written, injection-safe, parameterised client therefore gets zero evidence
     for every query and would lead you to conclude the data does not exist. We build the
     literal into the query string from a WHITELIST, never by interpolating a caller value.

  2. Gene symbol search is fuzzy and RANKED. search("AR") returns AR first, then FDXR,
     AREG, AKR1B1, ARX, ARG1 and dozens more. We require an exact case-insensitive symbol
     match and return None otherwise. Never `hits[0]`.

  3. Identical queries returned different evidence counts within one session, HTTP 200
     both times, health check green throughout: 165 rows reproduced 4x, then 0 rows
     reproduced 10x ten minutes later. So a zero from the evidence route is NOT a finding
     until corroborated against the association route. `checked_and_empty` and
     `could_not_check` are different states and the API will not distinguish them for you.

THREE MORE, on the batch routes. Same character as the first three:
each returns HTTP 200 and a plausible answer that is wrong.

  4. `mapIds` resolves many symbols in one call, and its ranking is WORSE than search().
     `mapIds(["AR"])` returns AREG, FDXR, AKR1B1, then AR -- the correct gene fourth -- and
     every hit carries `score: 1`, so the score cannot break the tie either. Taking hits[0]
     resolves AR to AREG. The exact-symbol rule from finding 2 is what makes this route
     usable at all.

  5. Symbols are not unique. Five of the 506 genes in the demo list resolve to TWO Ensembl
     IDs sharing one approved symbol: AZGP1P1, WFDC21P, KRT16P3, FMO6P (pseudogene pairs)
     and CRLF2, which is protein-coding on both the X and Y copies of the pseudoautosomal
     region. Picking either is a coin flip presented as a resolution, so an ambiguous
     symbol abstains the same way an unresolvable one does.

  6. `associatedTargets` SILENTLY TRUNCATES to the page size. Asking for 474 targets with
     `page:{size:25}` returns `count: 40` and 25 rows. The 15 missing genes are
     indistinguishable from genes with no association, which would understate the headline
     coverage figure by exactly the amount the page size clipped. `count` is the honest
     number, so every batch fetch asserts `len(rows) == count`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.cache import CACHE
from app.core.limits import REGISTRY

ENDPOINT = "https://api.platform.opentargets.org/api/v4/graphql"

# `Bs` accepted 474 ids in one call during step zero. Chunked below that anyway: the limit
# is undocumented, so the safe assumption is that one exists and we have not found it.
BULK_CHUNK = 400

# Only these two strings are ever inlined into a query. Finding 1 forces inlining;
# this whitelist is what stops that from becoming an injection surface.
_INDIRECT_LITERAL = {True: "true", False: "false"}

# IDs coming back from the API. Anything inlined is validated against this first.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_:.\-]+$")


class OpenTargetsError(RuntimeError):
    pass


@dataclass
class DiseaseHit:
    id: str
    name: str


@dataclass
class TargetHit:
    ensembl_id: str
    symbol: str
    approved_name: str = ""


@dataclass
class BulkResolution:
    """Outcome of resolving many symbols at once.

    Three outcomes, not two. `ambiguous` exists because five symbols in the demo list map
    to two Ensembl IDs each and choosing one would be a guess wearing a resolution's
    clothes (finding 5).
    """

    resolved: dict[str, TargetHit] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class BulkAssociation:
    """Datatype scores keyed by Ensembl id, for targets that HAVE an association.

    A target in `checked` and absent from `scores` was checked and has none. That is the
    distinction the headline coverage figure is built on, so `truncated` being non-empty
    invalidates it: see finding 6.
    """

    scores: dict[str, dict[str, float]] = field(default_factory=dict)
    checked: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)

    @property
    def trustworthy(self) -> bool:
        return not self.truncated


@dataclass
class EvidenceLookup:
    """Result of one gene x disease evidence lookup, with the corroboration verdict.

    `status` is the field the pipeline branches on:
      FOUND             - evidence rows returned
      CHECKED_AND_EMPTY - both routes agree there is nothing
      COULD_NOT_CHECK   - routes disagree, or a call failed. NOT a finding.
    """

    status: str
    datatype_scores: dict[str, float] = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)
    note: str = ""


class OpenTargetsClient:
    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 60.0):
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._data_version: str | None = None

    async def __aenter__(self) -> "OpenTargetsClient":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _post(self, query: str, variables: dict | None = None) -> dict:
        assert self._client is not None, "use as an async context manager"
        resp = await self._client.post(
            ENDPOINT,
            json={"query": query, "variables": variables or {}},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        payload = resp.json()
        if "errors" in payload:
            raise OpenTargetsError(payload["errors"][0].get("message", "unknown GraphQL error"))
        return payload["data"]

    async def _gql(self, query: str, variables: dict | None = None) -> dict:
        """Every call goes through the limiter. There is no unlimited route out of here.

        The limiter is applied at the transport rather than at the batch runner so the
        single-gene path is bounded by the same budget. Two people running assessments
        while a 500-gene batch is in flight share one allowance per source.
        """
        return await REGISTRY.get("open_targets").run(self._post, query, variables)

    # -----------------------------------------------------------------------------------
    # Provenance. Determinism is only claimed against a pinned snapshot, so the snapshot
    # has to be recorded on every assessment and folded into the cache key.
    # -----------------------------------------------------------------------------------

    async def data_version(self) -> str:
        if self._data_version is None:
            d = await self._gql("{ meta { dataVersion { year month } } }")
            dv = d["meta"]["dataVersion"]
            self._data_version = f"opentargets-{dv['year']}.{dv['month']}"
        return self._data_version

    # -----------------------------------------------------------------------------------
    # Resolution
    # -----------------------------------------------------------------------------------

    _TARGET_SEARCH = (
        "query R($q:String!,$n:Int!){search(queryString:$q,entityNames:[\"target\"],"
        "page:{index:0,size:$n}){hits{id name description}}}"
    )

    async def resolve_target(self, symbol: str) -> TargetHit | None:
        """Exact case-insensitive symbol match, or None. Finding 2.

        Returning None here is not a failure, it routes to abstention trigger 4. That is
        strictly better than confidently answering about a different gene.
        """
        d = await self._gql(self._TARGET_SEARCH, {"q": symbol, "n": 25})
        for hit in d["search"]["hits"]:
            if hit["name"].upper() == symbol.upper():
                return TargetHit(
                    ensembl_id=hit["id"],
                    symbol=hit["name"],
                    approved_name=hit.get("description") or "",
                )
        return None

    async def search_targets(self, query: str, limit: int = 8) -> list[TargetHit]:
        """The ranked list, returned as a list rather than collapsed to a top hit.

        Finding 2 says the ranking is fuzzy: `AR` returns AR, FDXR, AREG, AKR1B1, ARX.
        `resolve_target` answers that by refusing anything but an exact match. This method
        answers it the other way, by handing the whole ranked list to the person asking so
        they can pick. Same finding, two mitigations: the machine will not guess, and the
        human is shown what the machine would have had to guess between.
        """
        d = await self._gql(self._TARGET_SEARCH, {"q": query, "n": max(1, min(limit, 25))})
        return [
            TargetHit(
                ensembl_id=h["id"],
                symbol=h["name"],
                approved_name=h.get("description") or "",
            )
            for h in d["search"]["hits"]
        ]

    async def resolve_disease(self, term: str) -> DiseaseHit | None:
        """Top disease hit, which may well be a DIFFERENT disease than the one asked for.

        The caller must compare the returned name against what it expected. The API
        substituted the term in 6 of 12 lookups during step zero (melasma -> freckles,
        xerosis -> Dry skin) with nothing in the response signalling it.

        Cached: a batch run asks this once per run by construction, but the endpoint menu
        asks it once per borrow row and the answer cannot change within a data version.
        """

        async def fetch() -> DiseaseHit | None:
            d = await self._gql(
                'query R($q:String!){search(queryString:$q,entityNames:["disease"]){hits{id name}}}',
                {"q": term},
            )
            hits = d["search"]["hits"]
            return DiseaseHit(id=hits[0]["id"], name=hits[0]["name"]) if hits else None

        return await CACHE.get_or_set(
            ("ot.disease", await self.data_version(), term.strip().lower()), fetch
        )

    # -----------------------------------------------------------------------------------
    # Bulk resolution. Findings 4 and 5.
    # -----------------------------------------------------------------------------------

    _MAP_IDS = (
        "query M($q:[String!]!){mapIds(queryTerms:$q,entityNames:[\"target\"])"
        "{mappings{term hits{id name}}}}"
    )

    async def _map_ids(self, chunk: list[str]) -> dict[str, list[dict]]:
        d = await self._gql(self._MAP_IDS, {"q": chunk})
        return {m["term"]: m["hits"] for m in d["mapIds"]["mappings"]}

    async def resolve_targets_bulk(self, symbols: list[str]) -> "BulkResolution":
        """Resolve many symbols in one call, applying the exact-match rule to each.

        Finding 4 is why the exact-match filter is not optional here: mapIds ranks the
        correct gene fourth for "AR" and scores every hit 1, so there is nothing in the
        response that would let a caller pick correctly. Finding 5 is why an ambiguous
        symbol is its own outcome rather than being resolved to the first of two.
        """
        resolved: dict[str, TargetHit] = {}
        unresolved: list[str] = []
        ambiguous: dict[str, list[str]] = {}

        wanted = [s for s in symbols if s.strip()]
        version = await self.data_version()
        for i in range(0, len(wanted), BULK_CHUNK):
            chunk = wanted[i : i + BULK_CHUNK]
            # Cached on the chunk contents, not on the whole request, so re-running a
            # preset is free and a pasted list that overlaps one reuses what it can.
            got = await CACHE.get_or_set(
                ("ot.mapids", version, tuple(sorted(s.upper() for s in chunk))),
                lambda c=chunk: self._map_ids(c),
            )
            for symbol in chunk:
                hits = got.get(symbol, [])
                exact = [h for h in hits if h["name"].upper() == symbol.upper()]
                if len(exact) == 1:
                    resolved[symbol] = TargetHit(
                        ensembl_id=exact[0]["id"], symbol=exact[0]["name"]
                    )
                elif len(exact) > 1:
                    ambiguous[symbol] = [h["id"] for h in exact]
                else:
                    unresolved.append(symbol)

        return BulkResolution(resolved=resolved, unresolved=unresolved, ambiguous=ambiguous)

    # -----------------------------------------------------------------------------------
    # Bulk association. Finding 6.
    # -----------------------------------------------------------------------------------

    _ASSOC_TARGETS = (
        "query A($d:String!,$b:[String!],$s:Int!){disease(efoId:$d){id name "
        "associatedTargets(Bs:$b,page:{index:0,size:$s})"
        "{count rows{target{id approvedSymbol} datatypeScores{id score}}}}}"
    )

    async def _assoc_chunk(
        self, disease_id: str, chunk: list[str]
    ) -> tuple[dict[str, dict[str, float]], list[str]]:
        # There cannot be more rows than targets asked for, so the page is sized to the
        # request. Asking for fewer is what produces the silent truncation of finding 6.
        d = await self._gql(
            self._ASSOC_TARGETS, {"d": disease_id, "b": chunk, "s": len(chunk)}
        )
        disease = d.get("disease")
        if not disease:
            raise OpenTargetsError(f"disease {disease_id} did not resolve")
        at = disease["associatedTargets"]
        rows = at["rows"]
        truncated: list[str] = []
        if len(rows) != at["count"]:
            # Never silently accept a short page: the missing targets would be read as
            # having no association, which turns a paging bug into a scientific claim.
            truncated.append(
                f"requested {len(chunk)} targets, count={at['count']}, rows={len(rows)}"
            )
        scores = {
            row["target"]["id"]: {x["id"]: x["score"] for x in row["datatypeScores"]}
            for row in rows
        }
        return scores, truncated

    async def association_scores_bulk(
        self, disease_id: str, ensembl_ids: list[str]
    ) -> "BulkAssociation":
        """Datatype scores for many targets against one disease.

        This is the SAME association route the single-gene path uses, addressed from the
        disease side instead of the target side, so the scores a batch row is tiered from
        are the scores the single-gene assessment would have produced. `eval/run_eval.py`
        asserts that equivalence rather than assuming it.

        A target absent from `rows` has no association,  the headline
        coverage number rests on. Finding 6 is why that absence is only trusted after
        `count` and `len(rows)` agree: a truncated page makes present genes look absent.
        """
        if not _SAFE_ID.match(disease_id):
            raise OpenTargetsError(f"unsafe identifier: {disease_id!r}")

        scores: dict[str, dict[str, float]] = {}
        truncated: list[str] = []
        version = await self.data_version()

        for i in range(0, len(ensembl_ids), BULK_CHUNK):
            chunk = ensembl_ids[i : i + BULK_CHUNK]
            chunk_scores, chunk_trunc = await CACHE.get_or_set(
                ("ot.assoc_bulk", version, disease_id, tuple(sorted(chunk))),
                lambda c=chunk: self._assoc_chunk(disease_id, c),
                # A truncated fetch is a transport-shaped failure. Caching it would freeze
                # a paging accident into every subsequent run of the same list.
                cacheable=lambda r: not r[1],
            )
            scores.update(chunk_scores)
            truncated.extend(chunk_trunc)

        return BulkAssociation(
            scores=scores,
            checked=[e for e in ensembl_ids],
            truncated=truncated,
        )

    # -----------------------------------------------------------------------------------
    # Tractability.
    # -----------------------------------------------------------------------------------
    #
    # `Target.tractability` is a FLAT list of {label, modality, value}, not nested by
    # modality, and there is no `id` field. Modality is a two-letter code, SM / AB / PR /
    # OC, not a spelled-out name.
    #
    # Two shapes that mislead:
    #   - A protein-coding target returns the full 28-entry grid whether or not anything is
    #     tractable, most of it `value: false`. A non-empty list does NOT mean tractable;
    #     only `value == true` does. An lncRNA returns `[]` (verified on MALAT1), and the
    #     field is `[Tractability!]!` so it is never null.
    #   - `targets(ensemblIds:)` SILENTLY DROPS ids it does not know. Six requested with one
    #     bad id returns five elements. Zipping request to response by index misaligns every
    #     row after the bad one, so results are matched on the returned `id`.

    _TRACTABILITY = (
        "query T($ids:[String!]!){targets(ensemblIds:$ids)"
        "{id approvedSymbol tractability{label modality value}}}"
    )

    async def tractability(self, ensembl_ids: list[str]) -> dict[str, dict[str, list[str]]]:
        """{ensembl_id: {modality: [labels that are true]}}, batched.

        A target present in the result with an empty mapping was assessed and nothing is
        tractable. A target ABSENT from the result was not assessed at all, and the caller
        has to keep those apart.
        """
        out: dict[str, dict[str, list[str]]] = {}
        ids = [i for i in ensembl_ids if _SAFE_ID.match(i)]
        version = await self.data_version()

        for i in range(0, len(ids), BULK_CHUNK):
            chunk = ids[i : i + BULK_CHUNK]
            got = await CACHE.get_or_set(
                ("ot.tractability", version, tuple(sorted(chunk))),
                lambda c=chunk: self._tractability_chunk(c),
            )
            out.update(got)
        return out

    async def _tractability_chunk(
        self, chunk: list[str]
    ) -> dict[str, dict[str, list[str]]]:
        d = await self._gql(self._TRACTABILITY, {"ids": chunk})
        out: dict[str, dict[str, list[str]]] = {}
        for target in d.get("targets") or []:
            by_modality: dict[str, list[str]] = {}
            for row in target.get("tractability") or []:
                if row.get("value"):
                    by_modality.setdefault(row["modality"], []).append(row["label"])
            # Keyed on the RETURNED id. Unknown ids are dropped by the API, so index
            # alignment would silently attribute one target's buckets to another.
            out[target["id"]] = by_modality
        return out

    async def associated_disease_ids(
        self, ensembl_id: str, disease_ids: list[str]
    ) -> dict[str, dict[str, float]]:
        """Which of `disease_ids` this target has any association with, and how.

        Used to rank the endpoint menu once a gene is chosen. One call for the whole borrow
        table rather than one per row.
        """
        if not _SAFE_ID.match(ensembl_id):
            raise OpenTargetsError(f"unsafe identifier: {ensembl_id!r}")
        if not disease_ids:
            return {}
        d = await self._gql(
            "query D($e:String!,$b:[String!],$s:Int!){target(ensemblId:$e){"
            "associatedDiseases(Bs:$b,page:{index:0,size:$s})"
            "{count rows{disease{id name} datatypeScores{id score}}}}}",
            {"e": ensembl_id, "b": disease_ids, "s": len(disease_ids)},
        )
        target = d.get("target") or {}
        ad = target.get("associatedDiseases") or {"rows": []}
        return {
            r["disease"]["id"]: {x["id"]: x["score"] for x in r["datatypeScores"]}
            for r in ad.get("rows", [])
        }

    @staticmethod
    def substitution_occurred(requested: str, resolved: str, expected: str | None) -> bool:
        """True when the source answered about something other than what was declared.

        If the config declares `expected_resolved_name`, the substitution is documented and
        is simply how that borrow works, so this is False. Only an UNDECLARED substitution
        fires abstention trigger 7.
        """
        target = (expected or requested).strip().lower()
        return resolved.strip().lower() != target

    # -----------------------------------------------------------------------------------
    # Evidence, with cross-route corroboration. Finding 3.
    # -----------------------------------------------------------------------------------

    async def association_scores(self, ensembl_id: str, disease_id: str) -> dict[str, float]:
        """The STABLE route. Returns {datatype_id: score}, empty dict if no association."""
        for ident in (ensembl_id, disease_id):
            if not _SAFE_ID.match(ident):
                raise OpenTargetsError(f"unsafe identifier: {ident!r}")
        d = await self._gql(
            "query A($e:String!,$b:[String!]){target(ensemblId:$e){"
            "associatedDiseases(Bs:$b){rows{score datatypeScores{id score}}}}}",
            {"e": ensembl_id, "b": [disease_id]},
        )
        target = d.get("target") or {}
        rows = (target.get("associatedDiseases") or {}).get("rows") or []
        if not rows:
            return {}
        return {x["id"]: x["score"] for x in rows[0]["datatypeScores"]}

    async def evidence_rows(
        self, ensembl_id: str, disease_id: str, indirect: bool = True, size: int = 200
    ) -> list[dict]:
        """The row-level route. Finding 1: `enableIndirect` MUST be inline.

        Passing it as a GraphQL variable is silently ignored and yields zero rows.
        """
        for ident in (ensembl_id, disease_id):
            if not _SAFE_ID.match(ident):
                raise OpenTargetsError(f"unsafe identifier: {ident!r}")
        lit = _INDIRECT_LITERAL[bool(indirect)]  # whitelist, never caller-interpolated
        query = (
            f'{{disease(efoId:"{disease_id}"){{name evidences('
            f'ensemblIds:["{ensembl_id}"],enableIndirect:{lit},size:{int(size)})'
            f"{{count rows{{datasourceId datatypeId score}}}}}}}}"
        )
        d = await self._gql(query)
        disease = d.get("disease")
        if not disease:
            return []
        return disease["evidences"]["rows"]

    async def lookup_evidence(self, ensembl_id: str, disease_id: str) -> EvidenceLookup:
        """Query both routes and reconcile. This is where finding 3 is handled.

        A zero from the evidence route only becomes CHECKED_AND_EMPTY if the association
        route also reports nothing. If the association route shows a datatype score above
        zero while the evidence route returns no rows for it, that is COULD_NOT_CHECK,
        which drives abstention trigger 6 rather than being read as absence.
        """
        try:
            scores = await self.association_scores(ensembl_id, disease_id)
        except (OpenTargetsError, httpx.HTTPError) as exc:
            return EvidenceLookup("COULD_NOT_CHECK", note=f"association route failed: {exc}")

        try:
            rows = await self.evidence_rows(ensembl_id, disease_id, indirect=True)
        except (OpenTargetsError, httpx.HTTPError) as exc:
            if scores:
                return EvidenceLookup(
                    "COULD_NOT_CHECK",
                    datatype_scores=scores,
                    note=(
                        f"evidence route failed ({exc}) while the association route reports "
                        f"{len(scores)} datatype score(s). Evidence exists but could not be read."
                    ),
                )
            return EvidenceLookup("COULD_NOT_CHECK", note=f"both routes unavailable: {exc}")

        if rows:
            return EvidenceLookup("FOUND", datatype_scores=scores, rows=rows)

        if scores:
            positive = {k: v for k, v in scores.items() if v > 0}
            if positive:
                return EvidenceLookup(
                    "COULD_NOT_CHECK",
                    datatype_scores=scores,
                    note=(
                        "Routes disagree: association reports "
                        + ", ".join(f"{k}={v:.2f}" for k, v in sorted(positive.items()))
                        + " but the evidence route returned no rows. During step zero the same "
                        "query returned 165 rows and then 0, HTTP 200 both times. Treating this "
                        "as absence would be wrong."
                    ),
                )

        return EvidenceLookup(
            "CHECKED_AND_EMPTY",
            datatype_scores=scores,
            note="Both routes agree there is no association.",
        )

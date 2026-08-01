"""ClinicalTrials.gov v2 client.

Narrow on purpose. It does ONE thing: given an NCT id from a curated fact, fetch the study
and confirm it completed with results posted. That turns a curated claim about a trial into
RETRIEVED tier 1a evidence rather than asserted tier 1b, which is the difference between a
passenger call capped at Moderate and one that can reach High.

Not built: a general "did this trial fail" classifier over search results.
Step zero found that across 86 terminated or withdrawn skin trials with a stated reason,
only ~6% stopped for efficacy or futility against ~60% operational. Measured again
over the 114 stopped trials reachable from drug-linked indication rows in seven conditions:
operational 56%, efficacy or futility 18%. Different populations, both stand; do not quote the
6% over the drug-linked set. Reading trial status as
an efficacy signal would manufacture false passenger calls at scale, so the human names the
trial and the client only verifies it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from app.core import trial_snapshot
from app.core.limits import REGISTRY

BASE = "https://clinicaltrials.gov/api/v2/studies"
_NCT = re.compile(r"^NCT\d{8}$")


@dataclass
class TrialRecord:
    nct_id: str
    exists: bool
    status: str | None = None
    has_results: bool = False
    title: str | None = None
    enrollment: int | None = None
    why_stopped: str | None = None
    error: str | None = None
    # LIVE or SNAPSHOT. Never dropped: "this is what the registry says" and "this is what
    # the registry said on 31 July" are different claims and only one was checked today.
    provenance: str = "LIVE"
    snapshot_date: str | None = None

    @property
    def usable_as_tier_1a(self) -> bool:
        """Retrieved tier 1a requires a completed trial that actually posted results.

        A registered-but-unreported trial is not a result. Gevokizumab in acne is exactly
        that shape: completed 2013, no results ever posted, and the only account of its
        outcome is a secondary assertion in a review. It is excluded for that reason.
        """
        return self.exists and self.has_results and self.status == "COMPLETED"


class ClinicalTrialsClient:
    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 45.0):
        self._client = client
        self._owns = client is None
        self._timeout = timeout
        self._cache: dict[str, TrialRecord] = {}

    async def __aenter__(self) -> "ClinicalTrialsClient":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns and self._client is not None:
            await self._client.aclose()

    async def fetch(self, nct_id: str) -> TrialRecord:
        if not _NCT.match(nct_id):
            return TrialRecord(nct_id, exists=False, error="malformed NCT id")
        if nct_id in self._cache:
            return self._cache[nct_id]

        assert self._client is not None, "use as an async context manager"

        async def get() -> dict | None:
            resp = await self._client.get(f"{BASE}/{nct_id}", params={"format": "json"})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

        try:
            # Through the limiter like everything else. A batch run only reaches here for
            # genes carrying a curated NCT id and the ids are deduped by the cache above,
            # so this is single digits per run rather than one per gene.
            data = await REGISTRY.get("clinicaltrials").run(get)
            if data is None:
                rec = TrialRecord(nct_id, exists=False, error="not found")
                self._cache[nct_id] = rec
                return rec
        except httpx.HTTPError as exc:
            # A completed trial's record is settled, so a pinned copy is the same fact from
            # a different place. Serving it beats losing the case, provided the reader is
            # told which they are looking at.
            snap = trial_snapshot.get(nct_id)
            if snap is not None:
                rec = TrialRecord(**{**snap, "provenance": "SNAPSHOT",
                                     "snapshot_date": trial_snapshot.fetched_on()})
                self._cache[nct_id] = rec
                return rec
            # Do NOT cache a transport failure. "Could not check" must stay distinguishable
            # from "checked and absent", and a cached failure would erase that distinction.
            return TrialRecord(nct_id, exists=False, error=f"fetch failed: {exc}")

        proto = data.get("protocolSection", {})
        status_mod = proto.get("statusModule", {})
        design = proto.get("designModule", {})

        rec = TrialRecord(
            nct_id=nct_id,
            exists=True,
            status=status_mod.get("overallStatus"),
            has_results=bool(data.get("resultsSection")),
            title=proto.get("identificationModule", {}).get("briefTitle"),
            enrollment=(design.get("enrollmentInfo") or {}).get("count"),
            why_stopped=status_mod.get("whyStopped"),
        )
        self._cache[nct_id] = rec
        return rec

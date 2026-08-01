"""A pinned, checked-in copy of the trial records the demo depends on.

WHY THIS IS A CACHE AND NOT A STUB. A completed trial's registry record does not change.
Enrolment, status, arms and posted results are settled facts about something that already
happened. So serving them from a file is serving the same fact from a different place, not
inventing one. An ongoing trial would be a different matter and is not what this holds.

WHY IT EXISTS. The in-memory cache is not a fallback. Failed fetches are deliberately not
cached, and successful ones only exist if a fetch already succeeded in that process, so a
process that starts during an outage has nothing warm. ClinicalTrials.gov returned sustained
429s under sustained load, and under a full outage the demo loses its two best cases:
SRD5A2 dropped HIGH to MODERATE, which collapses the borrow pair to MODERATE against
MODERATE, and AR went from TESTED_UNREPORTED with three trials to NOT_ASSESSED with none.

The deployment diagram already names a pre-computed fallback mode. This makes that true
rather than aspirational.

PROVENANCE IS NEVER LOST. A record served from here is marked `SNAPSHOT` and carries the date
it was fetched. The trace and the output both say which one the reader is looking at, because
"this is what the registry says" and "this is what the registry said on 31 July" are different
claims and only one of them was checked today.
"""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parents[3] / "data" / "trial_snapshot.json"

_records: dict[str, dict] = {}
_searches: dict[str, list[str]] = {}
_meta: dict = {}


def load(path: Path | None = None) -> int:
    """Read the snapshot. Missing or unreadable is not fatal: it degrades to live-only."""
    global _records, _searches, _meta
    p = path or SNAPSHOT_PATH
    if not p.exists():
        _records, _searches, _meta = {}, {}, {"loaded": False,
                                             "reason": f"{p.name} not present"}
        return 0
    try:
        blob = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _records, _searches, _meta = {}, {}, {"loaded": False,
                                             "reason": f"unreadable: {exc}"}
        return 0
    _records = blob.get("trials", {})
    _searches = blob.get("searches", {})
    _meta = {k: v for k, v in blob.items() if k not in ("trials", "searches")}
    _meta["loaded"] = True
    return len(_records)


def get(nct_id: str) -> dict | None:
    return _records.get(nct_id.upper())


def search_key(drug: str, condition: str) -> str:
    return f"{(drug or '').strip().lower()}|{(condition or '').strip().lower()}"


def get_search(drug: str, condition: str) -> list[str] | None:
    """Which trials a drug-and-condition search returned when it was pinned.

    Pinning the records without pinning the searches loses trials silently. AR reaches
    three clascoterone trials live and only two from records alone, because the third is
    found by search rather than named by an indication row. Two trials is 857 participants
    against 1,560, on a case whose whole point is the size of the silence.
    """
    return _searches.get(search_key(drug, condition))


def fetched_on() -> str | None:
    return _meta.get("fetched_on")


def info() -> dict:
    return {**_meta, "count": len(_records), "searches": len(_searches)}


load()

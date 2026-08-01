"""Shared-secret gate, per-IP rate limiting, and a daily model-spend ceiling.

Deployment concern, not a product feature. Nothing here appears in the README or the
product documentation. Operational notes live outside the repository.

This is a gate, not authentication. There are no accounts, no passwords belonging to a
person, and no authorisation decisions. It stops a public URL from being open to the
internet and it records who came through.

WHY A COOKIE AND NOT A HEADER. `EventSource` cannot set request headers, and both the
single-gene and batch endpoints are SSE. A bearer token in a header would work for `fetch`
and silently fail for the two endpoints that matter, so the token travels in an HttpOnly
cookie signed with HMAC-SHA256.

CONFIGURED ENTIRELY BY ENVIRONMENT:

    BIOLEAD_ACCESS_CODES   "label:code,label:code"   no default; unset means the gate is
                                                     off and the app is open
    BIOLEAD_GATE_SECRET    random string             signs the cookie; generated per
                                                     process if unset, which logs everyone
                                                     out on restart
    BIOLEAD_ACCESS_LOG     path                      JSONL, one line per entry
    BIOLEAD_GATE_TTL_HOURS  12
    BIOLEAD_IP_RATE         requests per minute per IP behind the gate, default 30
    BIOLEAD_DAILY_USD       hard ceiling on model spend per UTC day, default 5.00
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

COOKIE = "biolead_gate"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _parse_codes(raw: str) -> dict[str, str]:
    """`label:code` pairs into {code: label}. Keyed by code so lookup is by what is typed."""
    out: dict[str, str] = {}
    for chunk in raw.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        label, code = chunk.split(":", 1)
        label, code = label.strip(), code.strip()
        if label and code:
            out[code] = label
    return out


@dataclass
class Gate:
    codes: dict[str, str] = field(default_factory=dict)
    secret: str = ""
    ttl_seconds: int = 12 * 3600
    log_path: Path | None = None

    @classmethod
    def from_env(cls) -> "Gate":
        codes = _parse_codes(_env("BIOLEAD_ACCESS_CODES"))
        secret = _env("BIOLEAD_GATE_SECRET") or secrets.token_urlsafe(32)
        ttl = int(_env("BIOLEAD_GATE_TTL_HOURS", "12") or 12) * 3600
        log = _env("BIOLEAD_ACCESS_LOG")
        return cls(codes=codes, secret=secret, ttl_seconds=ttl,
                   log_path=Path(log) if log else None)

    @property
    def enabled(self) -> bool:
        return bool(self.codes)

    # -- token ------------------------------------------------------------------------
    def issue(self, label: str) -> str:
        expires = int(time.time()) + self.ttl_seconds
        payload = f"{label}|{expires}"
        sig = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}|{sig}"

    def verify(self, token: str | None) -> str | None:
        """Return the label the token was issued to, or None."""
        if not token or token.count("|") != 2:
            return None
        label, expires, sig = token.split("|")
        expect = hmac.new(
            self.secret.encode(), f"{label}|{expires}".encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        try:
            if int(expires) < time.time():
                return None
        except ValueError:
            return None
        return label

    def check_code(self, submitted: str) -> str | None:
        """Constant-time comparison against every configured code."""
        found: str | None = None
        for code, label in self.codes.items():
            if hmac.compare_digest(submitted.strip(), code):
                found = label
        return found

    # -- access log -------------------------------------------------------------------
    def record(self, label: str, ip: str, user_agent: str = "") -> None:
        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "label": label,
            "ip": ip,
            "user_agent": user_agent[:200],
        }
        # stdout always, so it reaches CloudWatch without a file being mounted.
        print(f"[gate] entry {json.dumps(entry)}", flush=True)
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError as exc:  # a full disk must not take the app down
            print(f"[gate] access log write failed: {exc}", flush=True)

    def recent(self, limit: int = 200) -> list[dict]:
        if self.log_path is None or not self.log_path.exists():
            return []
        lines = self.log_path.read_text().splitlines()[-limit:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out


# --------------------------------------------------------------------------------------
# Per-IP rate limiting, behind the gate.
# --------------------------------------------------------------------------------------


class IPRateLimiter:
    """Token bucket per IP. Bounds INBOUND requests, which `core/limits.py` does not."""

    def __init__(self, per_minute: int = 30, burst: int | None = None) -> None:
        self.rate = per_minute / 60.0
        self.burst = burst if burst is not None else max(5, per_minute // 3)
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, ip: str) -> tuple[bool, float]:
        """(allowed, retry_after_seconds)."""
        now = time.monotonic()
        tokens, last = self._buckets.get(ip, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - last) * self.rate)
        if tokens >= 1.0:
            self._buckets[ip] = (tokens - 1.0, now)
            return True, 0.0
        self._buckets[ip] = (tokens, now)
        return False, round((1.0 - tokens) / self.rate, 1)

    def prune(self, max_entries: int = 10_000) -> None:
        if len(self._buckets) > max_entries:
            self._buckets.clear()


# --------------------------------------------------------------------------------------
# Daily model spend ceiling.
# --------------------------------------------------------------------------------------


class SpendCap:
    """A hard stop on model spend per UTC day.

    Counted from real token usage rather than from an assumed per-call price, so a change
    in packet size shows up.

    TWO THINGS CHARGE HERE SINCE STAGE 7, and the second is the larger one. The adjudicator
    is one call per assessment at about $0.018. The outcome subgraph is $0.026 to $0.268
    depending on how many trials publish no readable statistic, and it runs on every drugged
    gene. A ceiling that metered only the adjudicator would have been watching the smaller
    number.

    ON REACHING THE CEILING NOTHING ERRORS. The adjudicator is skipped and the rule verdict
    stands alone, and the outcome subgraph runs with its agent switched off: same gathering,
    same arithmetic, same attribution cap, same disagreement rule. Trials that publish no
    readable comparison come back undetermined rather than read. The app degrades to
    deterministic output and says so.

    Batch above the gate never charges here, because it already runs with the agent off.
    """

    # USD per million tokens, input/output.
    PRICES = {
        "claude-sonnet-5": (2.0, 10.0),
        "claude-haiku-4-5": (1.0, 5.0),
    }

    def __init__(self, daily_usd: float = 5.0) -> None:
        self.daily_usd = daily_usd
        self._day = ""
        self._spent = 0.0
        self._calls = 0

    def _roll(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._spent, self._calls = today, 0.0, 0

    def allowed(self) -> bool:
        self._roll()
        return self._spent < self.daily_usd

    def charge(self, model: str, input_tokens: int, output_tokens: int) -> float:
        self._roll()
        pin, pout = self.PRICES.get(model, (3.0, 15.0))
        cost = input_tokens / 1e6 * pin + output_tokens / 1e6 * pout
        self._spent += cost
        self._calls += 1
        if not self.allowed():
            print(f"[spend] daily ceiling ${self.daily_usd:.2f} reached after "
                  f"{self._calls} calls. Adjudication and the outcome agent are off until "
                  f"UTC midnight; the deterministic path continues.", flush=True)
        return cost

    def snapshot(self) -> dict:
        self._roll()
        return {
            "day": self._day,
            "spent_usd": round(self._spent, 4),
            "ceiling_usd": self.daily_usd,
            "calls": self._calls,
            "adjudication_enabled": self.allowed(),
            "outcome_agent_enabled": self.allowed(),
        }


def client_ip(request) -> str:
    """Behind CloudFront and an ALB the socket peer is the load balancer.

    X-Forwarded-For is appended to by each hop, so the client is the FIRST entry. Trusting
    the last entry would let a caller spoof their own address into the rate limiter.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return getattr(request.client, "host", "unknown") or "unknown"


# One ceiling per process, shared by every call site. Created here rather than in the API
# module so the adjudicator can consult it without importing the routes.
SPEND_CAP = SpendCap(daily_usd=float(os.environ.get("BIOLEAD_DAILY_USD", "5") or 5))

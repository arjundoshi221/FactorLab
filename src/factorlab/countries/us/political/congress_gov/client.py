"""Congress.gov API client.

Wraps the shared `PoliticalHTTPClient` with Congress.gov-specific concerns:
  - Cooperative throttle to stay under the verified 20,000 req/hr cap
  - Offset+limit pagination for list endpoints (250 per page)
  - Lowercase bill-type in detail URLs (`/bill/119/hr/1`, NOT `/bill/119/HR/1`)

Reference: docs/data-sources/political/senator-trades.md §1C.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Iterator

from dotenv import find_dotenv, load_dotenv

from factorlab.countries.us.political._client import PoliticalHTTPClient

load_dotenv(find_dotenv(usecwd=True))

log = logging.getLogger(__name__)

BASE = "https://api.congress.gov/v3"
MIN_INTERVAL_SEC = 0.3   # 20K/hr ≈ 333/min ≈ 0.18s; pad to 0.3s for safety
DEFAULT_LIMIT = 250

# When the entire key pool is exhausted (all keys returned 429), wait this
# long in-process before retrying the whole pool one more time. Congress.gov
# per-key quota is hourly (5K/hr), so after 30 min at least the first key
# should usually have quota back. If the second pool-pass also fails, we
# defer to hourly mode.
POOL_EXHAUSTED_WAIT_SEC = 1800.0  # 30 min
POOL_RETRY_CYCLES = 2  # initial pass + 1 retry after wait

# Active congresses — responses for these are mutable (new bills filed,
# latestAction updates, cosponsors added). Cache TTL applies; closed
# congresses cache forever. Override via env var when a new congress
# begins (e.g. `POLITICAL_ACTIVE_CONGRESSES=119,120` during a transition).
# Congress N runs Jan 3, year (2N + 1785) → Jan 3, year (2N + 1787).
ACTIVE_CONGRESSES: frozenset[int] = frozenset(
    int(x.strip()) for x in os.getenv("POLITICAL_ACTIVE_CONGRESSES", "119").split(",")
    if x.strip()
)
ACTIVE_CACHE_TTL_SEC: float = float(
    os.getenv("POLITICAL_CACHE_TTL_ACTIVE_SEC", "3600")
)  # 1 hour default — fresh enough for daily ingest, low enough quota burn


def get_api_keys() -> list[str]:
    """Return all available API keys usable for Congress.gov, in priority order.

    api.data.gov keys are cross-federated — a single key works for Congress.gov,
    FEC, USAspending, NREL, USDA, etc. Each key has its OWN per-key quota, so
    pooling N keys gives N× the effective throughput.

    Priority order (Congress-flavoured names first so logs are readable):
      CONGRESS_API_KEY               (primary; required)
      BACKUP_CONGRESS_API_KEY        (legacy alias if set)
      BACKUP_CONGRESS_FEC_API_KEY    (shared backup, also valid here)
      FEC_API_KEY                    (also valid here — cross-federated)

    Congress.gov enforces 5,000 req/hr per key. With 3 distinct keys the
    effective ceiling is 15,000/hr, which makes the bill detail/deep enrichment
    (~45K calls remaining) finish in ~3 hours instead of ~9.

    Rotation: on RateLimitDeferred, advance to the next key. When all defer,
    the orchestrator's run-state gate pauses; `--mode hourly` picks it back up.

    DEMO_KEY values dropped. Duplicate keys de-duplicated (preserves first-seen
    priority).
    """
    raw = [
        (os.getenv("CONGRESS_API_KEY") or "").strip(),
        (os.getenv("BACKUP_CONGRESS_API_KEY") or "").strip(),
        (os.getenv("BACKUP_CONGRESS_FEC_API_KEY") or "").strip(),
        (os.getenv("FEC_API_KEY") or "").strip(),
    ]
    seen: set[str] = set()
    keys: list[str] = []
    for k in raw:
        if k and k != "DEMO_KEY" and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys or ["DEMO_KEY"]


# Back-compat shim — some callers may still import the old name.
def get_api_key() -> str:
    return get_api_keys()[0]


class CongressGovClient:
    """Congress.gov API thin wrapper.

    All network calls go through `PoliticalHTTPClient.get_json`, which logs to
    `audit.raw_archive` and caches bytes under
    `data/political/raw/congress_gov/...`.

    Supports key rotation: see `get_api_keys()` docstring. The first key in
    the pool is used until it defers (429 with Retry-After exceeding the
    inline-sleep cap), then the client rotates to the next available key.
    """

    def __init__(self, http: PoliticalHTTPClient) -> None:
        self.http = http
        self.api_keys = get_api_keys()
        self.is_demo = self.api_keys == ["DEMO_KEY"]
        self._key_idx = 0
        # Throttle is enforced inside PoliticalHTTPClient on actual network
        # calls only (cache hits skip).
        if self.http.min_interval_sec < MIN_INTERVAL_SEC:
            self.http.min_interval_sec = MIN_INTERVAL_SEC
        log.info("[congress_gov] key pool: %d key(s)%s",
                 len(self.api_keys),
                 " (DEMO only — limited quota)" if self.is_demo else "")

    # ---- key rotation --------------------------------------------------

    def _current_key(self) -> str:
        return self.api_keys[self._key_idx]

    def _rotate_key(self) -> bool:
        """Advance to the next key. Returns False if no further key available
        (caller propagates the underlying RateLimitDeferred)."""
        if self._key_idx >= len(self.api_keys) - 1:
            return False
        self._key_idx += 1
        log.warning("[congress_gov] rotating to key #%d (primary deferred)",
                    self._key_idx)
        return True

    # ---- internals -----------------------------------------------------

    @staticmethod
    def _max_age_for_congress(congress: int | None) -> float | None:
        """Return cache TTL for a request scoped to a particular congress.

        Active congresses (currently 119) get ``ACTIVE_CACHE_TTL_SEC``;
        closed congresses cache forever (``None``). ``congress=None``
        (rare — used by congress-agnostic endpoints) keeps the legacy
        forever-cache behaviour.
        """
        if congress is None or congress not in ACTIVE_CONGRESSES:
            return None
        return ACTIVE_CACHE_TTL_SEC

    def _get(self, path: str, save_as: Path | str, *,
             congress: int | None = None, **params) -> dict:
        from factorlab.countries.us.political._client import RateLimitDeferred

        url = f"{BASE}{path}"
        max_age_sec = self._max_age_for_congress(congress)
        last_exc: RateLimitDeferred | None = None
        for cycle in range(POOL_RETRY_CYCLES):
            # Sticky on cycle 0: keep using whichever key the previous call
            # left us on (usually the last working one). Reset to 0 only on
            # retry cycles (after the 30-min cooldown) so we re-probe the
            # full pool from scratch.
            if cycle > 0:
                self._key_idx = 0
            for _attempt in range(len(self.api_keys)):
                full = {k: v for k, v in {**params,
                                           "api_key": self._current_key(),
                                           "format": "json"}.items() if v is not None}
                try:
                    return self.http.get_json(url, params=full, save_as=save_as,
                                              max_age_sec=max_age_sec)
                except RateLimitDeferred as e:
                    last_exc = e
                    if not self._rotate_key():
                        break
                    # else: loop, retry with the next key
            # All keys exhausted. If another cycle is available, sleep 30 min
            # and retry the whole pool — by then per-key quotas should be
            # replenishing.
            if cycle + 1 < POOL_RETRY_CYCLES:
                log.warning(
                    "[congress_gov] all %d keys exhausted (cycle %d/%d). "
                    "Sleeping %.0fs before retrying the pool.",
                    len(self.api_keys), cycle + 1, POOL_RETRY_CYCLES,
                    POOL_EXHAUSTED_WAIT_SEC,
                )
                time.sleep(POOL_EXHAUSTED_WAIT_SEC)
        assert last_exc is not None
        raise last_exc

    # ---- /bill list & detail -------------------------------------------

    def iter_bills_list(
        self,
        congress: int,
        *,
        limit: int = DEFAULT_LIMIT,
        from_datetime: str | None = None,
        sort: str | None = None,
    ) -> Iterator[dict]:
        """Paginated list of bills in a congress.

        List-mode payload is sparse — `{congress, type, number, title,
        originChamber, latestAction, updateDate, url}`. NO policyArea, NO
        sponsors. Use `get_bill_detail()` to enrich.
        """
        offset = 0
        while True:
            save_as = Path(f"bill/{congress}") / f"list_off{offset}_l{limit}.json"
            data = self._get(
                f"/bill/{congress}", save_as=save_as, congress=congress,
                limit=limit, offset=offset,
                fromDateTime=from_datetime, sort=sort,
            )
            bills = data.get("bills") or []
            if not bills:
                break
            yield from bills
            pag = data.get("pagination") or {}
            if not pag.get("next"):
                break
            offset += limit

    def get_bill_detail(self, congress: int, bill_type: str, number: int) -> dict:
        """Detail for one bill — adds policyArea, introducedDate, primary
        sponsors, sub-resource counts. Lowercase bill_type required."""
        bt = bill_type.lower()
        save_as = Path(f"bill/{congress}/{bt}") / f"{number}_detail.json"
        data = self._get(f"/bill/{congress}/{bt}/{number}", save_as=save_as,
                         congress=congress)
        return data.get("bill") or {}

    def get_bill_subresource(
        self, congress: int, bill_type: str, number: int, sub: str,
        *, limit: int = DEFAULT_LIMIT, offset: int = 0,
    ) -> dict:
        """Sub-resource fetch: `cosponsors | committees | actions | subjects | summaries`."""
        bt = bill_type.lower()
        save_as = Path(f"bill/{congress}/{bt}") / f"{number}_{sub}_off{offset}.json"
        return self._get(
            f"/bill/{congress}/{bt}/{number}/{sub}", save_as=save_as,
            congress=congress, limit=limit, offset=offset,
        )

    def iter_bill_subresource(
        self, congress: int, bill_type: str, number: int, sub: str, key: str,
        *, limit: int = DEFAULT_LIMIT,
    ) -> Iterator[dict]:
        """Yield all rows from a bill sub-resource, paginating."""
        offset = 0
        while True:
            data = self.get_bill_subresource(
                congress, bill_type, number, sub, limit=limit, offset=offset,
            )
            rows = data.get(key) or []
            if isinstance(rows, dict):
                # /actions returns {actions: [...]}, /committees returns
                # {committees: [...]}. Some endpoints wrap differently.
                rows = list(rows.values())[0] if rows else []
            if not rows:
                break
            yield from rows
            pag = data.get("pagination") or {}
            if not pag.get("next"):
                break
            offset += limit

    # ---- /hearing list & detail ----------------------------------------

    def iter_hearings_list(
        self, congress: int, *, limit: int = DEFAULT_LIMIT,
    ) -> Iterator[dict]:
        offset = 0
        while True:
            save_as = Path(f"hearing/{congress}") / f"list_off{offset}_l{limit}.json"
            data = self._get(
                f"/hearing/{congress}", save_as=save_as, congress=congress,
                limit=limit, offset=offset,
            )
            hh = data.get("hearings") or []
            if not hh:
                break
            yield from hh
            pag = data.get("pagination") or {}
            if not pag.get("next"):
                break
            offset += limit

    def get_hearing_detail(self, congress: int, chamber: str, jacket_number: int) -> dict:
        chmb = chamber.lower()
        save_as = Path(f"hearing/{congress}/{chmb}") / f"{jacket_number}.json"
        data = self._get(f"/hearing/{congress}/{chmb}/{jacket_number}",
                         save_as=save_as, congress=congress)
        return data.get("hearing") or {}

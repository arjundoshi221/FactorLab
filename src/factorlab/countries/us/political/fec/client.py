"""FEC OpenAPI client.

Wraps the shared `PoliticalHTTPClient` (raw_archive logging, retry/backoff,
on-disk cache) with FEC-specific concerns:
  - Cooperative throttle to stay under the 60-req/min cap (verified 2026-05-01)
  - Cursor-based pagination via `pagination.last_indexes` for >10K result sets
  - Page+offset pagination for committee searches (smaller result sets)

Reference: docs/data-sources/political/senator-trades.md §1F.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from dotenv import find_dotenv, load_dotenv

from factorlab.countries.us.political._client import PoliticalHTTPClient

load_dotenv(find_dotenv(usecwd=True))

log = logging.getLogger(__name__)

BASE = "https://api.open.fec.gov/v1"
MIN_INTERVAL_SEC = 1.05  # 60/min hard cap → 1.0s min, 5% headroom
DEFAULT_PER_PAGE = 100   # max 100 for /committees/, 100 for /schedule_a/

# When the entire key pool is exhausted (all keys returned 429), wait this
# long in-process before retrying the whole pool one more time. FEC's
# per-key quota is hourly, so after 30 min at least the first key should
# usually have quota back. If the second pool-pass also fails, we defer to
# hourly mode and let `--mode hourly` pick up the run.
POOL_EXHAUSTED_WAIT_SEC = 1800.0  # 30 min
POOL_RETRY_CYCLES = 2  # initial pass + 1 retry after wait

# Active cycle = the 2-year transaction period covering the current year.
# Cycles end in even years; e.g. 2025 and 2026 both fall in the 2026 cycle.
# Closed cycles are immutable; current-cycle pages get a TTL so amendments
# and newly-reported transactions are picked up on the next run.
# Override via `POLITICAL_CACHE_TTL_ACTIVE_SEC` env var.
ACTIVE_CYCLE_CACHE_TTL_SEC: float = float(
    os.getenv("POLITICAL_CACHE_TTL_ACTIVE_SEC", "3600")
)


def active_cycle(today: datetime | None = None) -> int:
    """Return the FEC two-year cycle covering ``today`` (default: now UTC)."""
    y = (today or datetime.now(timezone.utc)).year
    return y if y % 2 == 0 else y + 1


def get_api_keys() -> list[str]:
    """Return all available API keys usable for FEC, in priority order.

    api.data.gov keys are cross-federated — a single key works for FEC,
    Congress.gov, USAspending, NREL, USDA, etc. Each key has its OWN per-key
    quota, so pooling N keys gives N× the effective throughput.

    Priority order (FEC-flavoured names first so logs are readable):
      FEC_API_KEY                    (primary; required)
      BACKUP_CONGRESS_FEC_API_KEY    (shared backup)
      CONGRESS_API_KEY               (also valid here — cross-federated)

    FEC enforces 1,000 req/hr per key. With 3 keys the effective ceiling is
    3,000/hr, which lets Mode A (4,179 calls) finish in ~1.5 hr and Mode B
    (11,040 calls) in ~4 hr.

    Rotation: on RateLimitDeferred (429 with Retry-After > inline-sleep cap),
    the client advances to the next key in the pool. When ALL keys defer,
    the orchestrator's run-state gate pauses FEC until the earliest cooldown
    elapses; `--mode hourly` picks it back up.

    DEMO_KEY values are dropped — they share a global tiny quota.
    Duplicate keys (same value across env vars) are de-duplicated, preserving
    first-seen priority order.
    """
    raw = [
        (os.getenv("FEC_API_KEY") or "").strip(),
        (os.getenv("BACKUP_CONGRESS_FEC_API_KEY") or "").strip(),
        (os.getenv("CONGRESS_API_KEY") or "").strip(),
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


class FECClient:
    """FEC OpenAPI thin wrapper.

    Methods return either a single dict (for one-shot lookups) or an iterator
    of result rows (for paginated endpoints). All network calls go through
    `PoliticalHTTPClient.get_json`, which logs to `audit.raw_archive`
    and caches bytes under `data/political/raw/fec/...`.

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
        # calls only (cache hits skip). Set the cap here if not already set.
        if self.http.min_interval_sec < MIN_INTERVAL_SEC:
            self.http.min_interval_sec = MIN_INTERVAL_SEC
        log.info("[fec] key pool: %d key(s)%s",
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
        log.warning("[fec] rotating to key #%d (primary deferred)", self._key_idx)
        return True

    # ---- internals -----------------------------------------------------

    @staticmethod
    def _max_age_for_cycle(cycle: int | None) -> float | None:
        """TTL for a request scoped to a 2-year cycle. Active cycle gets a
        short TTL; closed cycles cache forever. ``None`` for cycle-agnostic
        endpoints (e.g. committee searches)."""
        if cycle is None or cycle != active_cycle():
            return None
        return ACTIVE_CYCLE_CACHE_TTL_SEC

    def _get(self, path: str, save_as: Path | str, *,
             cycle: int | None = None, **params) -> dict:
        from factorlab.countries.us.political._client import RateLimitDeferred

        url = f"{BASE}{path}"
        max_age_sec = self._max_age_for_cycle(cycle)
        last_exc: RateLimitDeferred | None = None
        for retry_cycle in range(POOL_RETRY_CYCLES):
            # Sticky on cycle 0: keep using whichever key the previous call
            # left us on (usually the last working one). Reset to 0 only on
            # retry cycles (after the 30-min cooldown) so we re-probe the
            # full pool from scratch.
            if retry_cycle > 0:
                self._key_idx = 0
            for _attempt in range(len(self.api_keys)):
                full = {k: v for k, v in {**params,
                                           "api_key": self._current_key()}.items()
                        if v is not None}
                try:
                    return self.http.get_json(url, params=full, save_as=save_as,
                                              max_age_sec=max_age_sec)
                except RateLimitDeferred as e:
                    last_exc = e
                    if not self._rotate_key():
                        break
                    # else: loop, retry with the next key
            # All keys in the pool returned 429. If we have another retry
            # cycle available, sleep 30 min and try the whole pool again —
            # by then the per-key hourly quotas should be replenishing.
            if retry_cycle + 1 < POOL_RETRY_CYCLES:
                log.warning(
                    "[fec] all %d keys exhausted (retry cycle %d/%d). Sleeping "
                    "%.0fs before retrying the pool.",
                    len(self.api_keys), retry_cycle + 1, POOL_RETRY_CYCLES,
                    POOL_EXHAUSTED_WAIT_SEC,
                )
                time.sleep(POOL_EXHAUSTED_WAIT_SEC)
        assert last_exc is not None
        raise last_exc

    # ---- /committees/ --------------------------------------------------

    def search_committees(
        self,
        *,
        q: str | None = None,
        committee_type: str | None = None,
        organization_type: str | None = None,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> Iterator[dict]:
        """Paginated committee search. Use either `q` (free text) or
        `committee_type` + `organization_type` (the corp-PAC sweep).
        """
        page = 1
        while True:
            tag = (q or f"{committee_type or ''}_{organization_type or ''}").replace(" ", "_")[:60]
            save_as = Path("committees") / f"search_{tag}_p{page}.json"
            data = self._get(
                "/committees/", save_as=save_as,
                q=q, committee_type=committee_type, organization_type=organization_type,
                per_page=per_page, page=page,
            )
            results = data.get("results") or []
            if not results:
                break
            yield from results
            pages = (data.get("pagination") or {}).get("pages") or page
            if page >= pages:
                break
            page += 1

    def iter_corporate_pacs(self) -> Iterator[dict]:
        """Sweep all corporate-sponsored qualified PACs (Q + C)."""
        yield from self.search_committees(committee_type="Q", organization_type="C")

    # ---- /candidate/{id}/committees/ -----------------------------------

    def get_candidate_committees(self, candidate_id: str) -> list[dict]:
        """All committees ever associated with a candidate. Filter
        `designation == 'P'` for the Principal Campaign Committee."""
        save_as = Path("candidate_committees") / f"{candidate_id}.json"
        data = self._get(f"/candidate/{candidate_id}/committees/", save_as=save_as,
                         per_page=20)
        return data.get("results") or []

    # ---- /schedules/schedule_a/ ----------------------------------------

    def iter_schedule_a(
        self,
        *,
        contributor_id: str | None = None,
        committee_id: str | None = None,
        two_year_transaction_period: int,
        min_amount: int | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        max_rows: int | None = None,
    ) -> Iterator[dict]:
        """Cursor-paginated Schedule A iteration.

        Pass exactly one of `contributor_id` (Mode A — donations FROM a PAC)
        or `committee_id` (Mode B — donations TO a candidate's PCC).

        Yields one dict per donation row.
        """
        if (contributor_id is None) == (committee_id is None):
            raise ValueError("pass exactly one of contributor_id or committee_id")

        cycle = two_year_transaction_period
        if contributor_id:
            mode_tag, anchor = "A", contributor_id
        else:
            mode_tag, anchor = "B", committee_id

        last_index_state: dict[str, str] = {}
        page = 0
        emitted = 0
        while True:
            page += 1
            save_as = Path("schedule_a") / f"{mode_tag}_{anchor}_{cycle}_p{page}.json"
            data = self._get(
                "/schedules/schedule_a/", save_as=save_as,
                cycle=cycle,
                contributor_id=contributor_id,
                committee_id=committee_id,
                two_year_transaction_period=cycle,
                min_amount=min_amount,
                per_page=per_page,
                sort="-contribution_receipt_date",
                **last_index_state,
            )
            results = data.get("results") or []
            if not results:
                break
            for row in results:
                yield row
                emitted += 1
                if max_rows is not None and emitted >= max_rows:
                    return

            li = (data.get("pagination") or {}).get("last_indexes") or {}
            # FEC returns last_indexes with keys like 'last_contribution_receipt_date',
            # 'last_index'. Pass them straight back.
            if not li or all(v is None for v in li.values()):
                break
            last_index_state = {k: v for k, v in li.items() if v is not None}

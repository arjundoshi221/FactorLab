"""HTTPClient — generic retry + disk cache + audit.raw_archive writer.

Generalised from ``political/_client.PoliticalHTTPClient``. Any FactorLab
source that fetches over HTTP should use this; the political subclass
(``PoliticalHTTPClient``) keeps the same name as a thin alias for back-compat.

Responsibilities:
  - GET / POST with a configurable User-Agent (``FACTORLAB_USER_AGENT`` env)
  - Exponential backoff on 429 / 502 / 503 / 504
  - Per-query disk cache (sha1 hash of URL + params) under ``<data_root>/<source>/_cache/``
  - Optional audit-row write to ``audit.raw_archive`` (URL + path + status, NEVER bytes)
  - Cooperative ``min_interval_sec`` throttle (applied only on actual network calls)
  - Per-policy 429 handling (rotating-client raises ``RateLimitDeferred``;
    single-key inline-sleeps the Retry-After)

The ``data_root`` argument decides where the per-source cache lives. For
political sources (LDA, FEC, etc.), pass ``raw_dir('political')`` so all
caches nest under ``data/political/raw/<source>/``. For top-level sources
(EODHD, Schwab), pass ``raw_dir('<source>')`` — the cache then lives directly
under that source's root.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine

from factorlab.shared.ingest.security import (
    assert_under_root,
    redact_error,
    redact_url,
)
from factorlab.shared.paths import REPO_ROOT

log = logging.getLogger(__name__)

UA = os.getenv("FACTORLAB_USER_AGENT", "FactorLab/1.0")


class RateLimitDeferred(Exception):
    """Server returned 429 — caller should rotate keys or defer.

    Rotating clients (FEC, Congress.gov) catch this in ``_get``, advance to
    the next key, retry. When all keys are exhausted, the exception
    propagates to the orchestrator, which records the deferral in run-state
    so the next hourly mode retries after cooldown — instead of holding the
    daily run hostage on a long Retry-After.
    """

    def __init__(self, source: str, retry_after_sec: float, url_redacted: str) -> None:
        self.source = source
        self.retry_after_sec = retry_after_sec
        self.url_redacted = url_redacted
        super().__init__(
            f"[{source}] rate-limit Retry-After={retry_after_sec:.0f}s; "
            f"deferring (rotate or hourly). URL={url_redacted}"
        )


def _hash_key(url: str, params: dict | None) -> str:
    sig = url + json.dumps(sorted((params or {}).items()))
    return hashlib.sha1(sig.encode()).hexdigest()[:16]


class HTTPClient:
    """Reusable HTTP base with retry + cache + audit writer.

    Parameters
    ----------
    source : str
        Source code — used as cache dir, audit ``endpoint_id`` lookup key,
        and exception attribute.
    data_root : Path
        Parent on-disk cache root (e.g. ``raw_dir('political')``). The actual
        cache subtree is ``data_root / source / _cache / ...``.
    engine : Engine | None
        SQLAlchemy engine for audit writes. If None, audit writes are skipped.
    country_code : str
        Tags audit rows in the ``metadata_json`` blob.
    write_archive : bool
        Master switch for audit-row writing. False short-circuits everything.
    retries, backoff_base, timeout : int / float / int
        Retry policy. Defaults preserve political-pipeline behaviour.
    headers : dict | None
        Extra headers merged with the default ``User-Agent`` + ``Accept``.
    min_interval_sec : float
        Cooperative throttle between network calls (cache hits skip the sleep).
    max_retry_after_sec : float
        Cap on inline 429 sleeps (single-key clients only).
    single_key_inline_sleep : bool
        When True, 429 with Retry-After <= cap is inline-slept and retried
        on the same key (LDA, USASpending). When False, 429 raises
        ``RateLimitDeferred`` immediately so a rotating-client pool can
        advance to the next key (FEC, Congress.gov).
    """

    def __init__(
        self,
        source: str,
        *,
        data_root: Path,
        engine: Engine | None = None,
        country_code: str = "US",
        write_archive: bool = True,
        retries: int = 3,
        backoff_base: float = 2.0,
        timeout: int = 180,
        headers: dict[str, str] | None = None,
        min_interval_sec: float = 0.0,
        max_retry_after_sec: float = 600.0,
        single_key_inline_sleep: bool = False,
    ) -> None:
        self.source = source
        self.data_root = data_root
        self.engine = engine
        self.country_code = country_code
        self.write_archive = write_archive and (engine is not None)
        self.retries = retries
        self.backoff_base = backoff_base
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json,text/html,*/*",
            **(headers or {}),
        })
        self._endpoint_id: int | None = None
        self._endpoint_id_resolved: bool = False
        self.min_interval_sec = min_interval_sec
        self._last_network_call_ts: float = 0.0
        self.max_retry_after_sec = max_retry_after_sec
        self.single_key_inline_sleep = single_key_inline_sleep

    # ---- core fetch -----------------------------------------------------

    def get(
        self,
        url: str,
        params: dict | None = None,
        *,
        save_as: Path | str | None = None,
        skip_archive: bool = False,
        ext: str = "json",
        max_age_sec: float | None = None,
    ) -> tuple[bytes, Path | None]:
        """GET ``url``. Returns ``(body_bytes, on_disk_path)``.

        ``max_age_sec``: if set, a cached file older than this many seconds is
        treated as a miss and re-fetched. ``None`` (default) preserves the
        legacy forever-cache behaviour. Use ``0`` to force-refetch unconditionally
        (still writes to disk on success).
        """
        return self._fetch("GET", url, params=params, save_as=save_as,
                           skip_archive=skip_archive, ext=ext,
                           max_age_sec=max_age_sec)

    def post(
        self,
        url: str,
        json_body: dict | None = None,
        *,
        save_as: Path | str | None = None,
        skip_archive: bool = False,
        ext: str = "json",
        max_age_sec: float | None = None,
    ) -> tuple[bytes, Path | None]:
        return self._fetch("POST", url, json_body=json_body, save_as=save_as,
                           skip_archive=skip_archive, ext=ext,
                           max_age_sec=max_age_sec)

    def _fetch(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        save_as: Path | str | None = None,
        skip_archive: bool = False,
        ext: str = "json",
        max_age_sec: float | None = None,
    ) -> tuple[bytes, Path | None]:
        # Resolve on-disk path
        if save_as is not None:
            disk_path = Path(save_as)
            if not disk_path.is_absolute():
                disk_path = self.data_root / self.source / disk_path
        else:
            key = _hash_key(url, params or json_body)
            disk_path = self.data_root / self.source / "_cache" / f"{key}.{ext}"

        # SECURITY: ensure the resolved path stays under data_root.
        disk_path = assert_under_root(disk_path, self.data_root)
        disk_path.parent.mkdir(parents=True, exist_ok=True)

        # Cache hit: re-use bytes from disk, still log a "cache" audit row.
        # If ``max_age_sec`` is set, the file mtime must be within that window;
        # otherwise treat the cached copy as stale and fall through to network.
        if disk_path.exists():
            cache_fresh = True
            if max_age_sec is not None:
                cache_age = time.time() - disk_path.stat().st_mtime
                cache_fresh = cache_age <= max_age_sec
            if cache_fresh:
                body = disk_path.read_bytes()
                self._log_archive(url, disk_path, status_code=200, from_cache=True,
                                  skip=skip_archive, body_len=len(body))
                return body, disk_path

        # Cooperative throttle — only on actual network calls
        if self.min_interval_sec > 0:
            elapsed = time.time() - self._last_network_call_ts
            if elapsed < self.min_interval_sec:
                time.sleep(self.min_interval_sec - elapsed)

        # Network fetch with retry + backoff
        last_status: int | None = None
        for attempt in range(self.retries):
            try:
                self._last_network_call_ts = time.time()
                if method == "GET":
                    r = self.session.get(url, params=params, timeout=self.timeout)
                else:
                    r = self.session.post(url, json=json_body, timeout=self.timeout)
                last_status = r.status_code
                if r.status_code == 429:
                    sleep_for = float(r.headers.get("Retry-After",
                                                    self.backoff_base ** (attempt + 1)))
                    if self.single_key_inline_sleep and sleep_for <= self.max_retry_after_sec:
                        log.warning(
                            "[%s] %s %s status=429 Retry-After=%.0fs -- sleeping "
                            "(no key pool; single-key inline retry)",
                            self.source, method, redact_url(url), sleep_for,
                        )
                        self._log_archive(url, None, status_code=429,
                                          from_cache=False, skip=skip_archive,
                                          body_len=0,
                                          error=f"inline_sleep: Retry-After={sleep_for:.0f}s")
                        time.sleep(sleep_for)
                        continue
                    log.warning(
                        "[%s] %s %s status=429 Retry-After=%.0fs -- deferring "
                        "(rotate to next key or fall through to hourly mode)",
                        self.source, method, redact_url(url), sleep_for,
                    )
                    self._log_archive(url, None, status_code=429,
                                      from_cache=False, skip=skip_archive,
                                      body_len=0,
                                      error=f"deferred: Retry-After={sleep_for:.0f}s")
                    raise RateLimitDeferred(
                        self.source, sleep_for, redact_url(url),
                    )
                if r.status_code in (502, 503, 504):
                    sleep_for = float(r.headers.get("Retry-After",
                                                    self.backoff_base ** (attempt + 1)))
                    log.warning("[%s] %s %s status=%d, sleeping %.1fs (attempt %d/%d)",
                                self.source, method, redact_url(url), r.status_code,
                                sleep_for, attempt + 1, self.retries)
                    time.sleep(sleep_for)
                    continue
                r.raise_for_status()
                body = r.content
                disk_path.write_bytes(body)
                self._log_archive(url, disk_path, status_code=r.status_code,
                                  from_cache=False, skip=skip_archive,
                                  body_len=len(body))
                return body, disk_path
            except requests.exceptions.RequestException as e:
                log.warning("[%s] %s %s failed (attempt %d/%d): %s",
                            self.source, method, redact_url(url), attempt + 1,
                            self.retries, redact_error(str(e)))
                if attempt + 1 == self.retries:
                    self._log_archive(url, None, status_code=last_status or 0,
                                      from_cache=False, skip=skip_archive,
                                      body_len=0, error=str(e))
                    raise
                time.sleep(self.backoff_base ** (attempt + 1))
        raise RuntimeError(f"unreachable -- {method} {url} retries exhausted")

    # ---- audit.raw_archive write ---------------------------------------

    def _resolve_endpoint_id(self) -> int | None:
        """Resolve ``self.source`` to a ``ref.data_endpoints`` row id."""
        if self._endpoint_id_resolved:
            return self._endpoint_id
        if self.engine is None:
            return None
        try:
            with self.engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT id FROM ref.data_endpoints WHERE code = :code LIMIT 1"
                ), {"code": self.source}).fetchone()
            self._endpoint_id = row[0] if row else None
        except Exception as e:
            log.warning("[%s] endpoint_id lookup failed: %s", self.source, e)
            self._endpoint_id = None
        self._endpoint_id_resolved = True
        if self._endpoint_id is None:
            log.warning(
                "[%s] no ref.data_endpoints row matches source=%r -- audit writes "
                "will be skipped. Add an endpoint code via a migration.",
                self.source, self.source,
            )
        return self._endpoint_id

    def _log_archive(
        self,
        url: str,
        disk_path: Path | None,
        *,
        status_code: int,
        from_cache: bool,
        skip: bool,
        body_len: int,
        error: str | None = None,
    ) -> UUID | None:
        if skip or not self.write_archive:
            return None
        endpoint_id = self._resolve_endpoint_id()
        if endpoint_id is None:
            return None
        rel_path = (
            str(disk_path.relative_to(REPO_ROOT)).replace("\\", "/")
            if disk_path and self._is_under_repo(disk_path)
            else (str(disk_path).replace("\\", "/") if disk_path else None)
        )
        meta = {
            "country_code": self.country_code,
            "from_cache": from_cache,
            "body_len": body_len,
        }
        if disk_path is not None:
            meta["local_path"] = rel_path
        if error is not None:
            meta["error"] = redact_error(error)
        raw_id = uuid4()
        scrubbed_url = redact_url(url)
        try:
            with self.engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO audit.raw_archive
                        (raw_id, endpoint_id, fetched_at, source_url,
                         status_code, body_bytes, metadata_json)
                    VALUES (:raw_id, :endpoint_id, :fetched_at, :url,
                            :status, :body_bytes, CAST(:meta AS jsonb))
                """), {
                    "raw_id": raw_id,
                    "endpoint_id": endpoint_id,
                    "fetched_at": datetime.now(timezone.utc),
                    "url": (scrubbed_url or "")[:800],
                    "status": status_code or None,
                    "body_bytes": body_len or None,
                    "meta": json.dumps(meta),
                })
        except Exception as e:
            log.warning("[%s] audit.raw_archive write failed (non-fatal): %s",
                        self.source, redact_error(str(e)))
            return None
        return raw_id

    @staticmethod
    def _is_under_repo(p: Path) -> bool:
        try:
            p.resolve().relative_to(REPO_ROOT.resolve())
            return True
        except ValueError:
            return False

    # ---- helpers --------------------------------------------------------

    def get_json(self, url: str, params: dict | None = None, **kw) -> dict | list:
        body, _ = self.get(url, params=params, **kw)
        return json.loads(body)

    def post_json(self, url: str, json_body: dict | None = None, **kw) -> dict | list:
        body, _ = self.post(url, json_body=json_body, **kw)
        return json.loads(body)


__all__ = ["HTTPClient", "RateLimitDeferred", "UA"]

"""Company-name → SEC ticker resolver.

5-tier match cascade:
  1. Manual alias table (configs/reference/contractor_aliases.yaml)
  2. Exact normalized match against SEC company_tickers.json
  3. 2-token prefix match
  4. Token-set Jaccard ≥ 0.6
  5. Give up → return None + log to learn-queue for manual review

Lifted from `playground/explore/usaspending/resolver.py` (11/11 self-test
pass). Differences: reads aliases from configs/reference/, persists learned
aliases to data/political/_state/_aliases_learned.yaml.
"""

from __future__ import annotations

import csv
import logging
import re
import time
from pathlib import Path
from typing import NamedTuple

import requests
import yaml

log = logging.getLogger(__name__)

from factorlab.shared.paths import REPO_ROOT  # noqa: E402

ALIASES_FILE = REPO_ROOT / "configs" / "reference" / "contractor_aliases.yaml"
LEARNED_FILE = REPO_ROOT / "data" / "political" / "_state" / "_aliases_learned.yaml"
LEARN_QUEUE = REPO_ROOT / "data" / "political" / "_state" / "_learn_queue.csv"
SEC_TICKERS_CACHE = REPO_ROOT / "data" / "political" / "raw" / "sec" / "company_tickers.json"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# SEC EDGAR fair-access policy expects a UA that identifies the requester.
# Default is project-scoped + anonymous; set FACTORLAB_USER_AGENT in .env to
# override (NEVER hardcode a personal email).
import os as _os
SEC_HEADERS = {"User-Agent": _os.getenv("FACTORLAB_USER_AGENT", "FactorLab/1.0")}
TTL_SEC = 7 * 24 * 3600


# ---- name normalization ----------------------------------------------------

_SUFFIX_PATTERNS = [
    r"\b(?:common stock|class [a-z]|adr|ads|sponsored adr)\b",
    r"\b(?:incorporated|corporation|corp|company|holdings?|limited|ltd|llc|llp|lp|plc|inc)\.?",
    r"\b(?:the|a|an|of|and|&)\b",
    r"[\.,;:'\"\(\)/\\\-]",
]
_SUFFIX_RE = re.compile("|".join(_SUFFIX_PATTERNS), re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


# PAC-specific noise tokens — strip BEFORE standard normalization for FEC names.
# Keep this conservative: better to leave a token in (forcing a no-match) than
# to overstrip and create false positives.
_PAC_NOISE_PATTERNS = [
    # Parentheticals are usually acronyms ("(LMPAC)", "(AAPAC)") — strip whole
    r"\([^)]*\)",
    # Tokens ending in 'pac' — FEDPAC, MSVPAC, AAPAC, ARTPAC, GOPAC, NAPAC,
    # plus the bare token 'pac'. Deliberately NOT matching tokens that merely
    # contain 'pac' (e.g. 'pacific', 'impact') so 'Union Pacific' survives.
    r"\b\w*pac\b",
    # Core PAC vocabulary
    r"\b(?:political|action|committee|fund|funds|foundation)\b",
    # Sponsorship descriptors
    r"\b(?:employees?|stakeholders|members?|voluntary|sponsored|associates?|partners?)\b",
    # Ideological / governance descriptors
    r"\b(?:good|government|federal|nonpartisan|bipartisan|partisan)\b",
    # Vehicle types
    r"\b(?:trust|trustees|connect|forward|activity|activities|leadership)\b",
    # Aliasing markers ("FKA X", "AKA Y")
    r"\b(?:fka|aka|nka|formerly|known\s+as)\b",
    # Common short conjunctions/prepositions when used in PAC titles
    r"\b(?:for|to|with|by|its|their|us|usa)\b",
]
_PAC_NOISE_RE = re.compile("|".join(_PAC_NOISE_PATTERNS), re.IGNORECASE)


def normalize_name(s: str) -> str:
    """'LOCKHEED MARTIN CORPORATION' → 'lockheed martin'"""
    if not s:
        return ""
    s = s.lower()
    s = _SUFFIX_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def normalize_pac_name(s: str) -> str:
    """FEC PAC name → company-identifier tokens.

    Strips PAC-vocabulary noise BEFORE running standard normalization, so
    'MICROSOFT CORPORATION STAKEHOLDERS VOLUNTARY PAC - MSVPAC' reduces to
    'microsoft' rather than 'microsoft stakeholders voluntary pac msvpac'.
    Empty result → caller should treat as no-match (refuse to guess).
    """
    if not s:
        return ""
    s = s.lower()
    s = _PAC_NOISE_RE.sub(" ", s)
    return normalize_name(s)


class TickerRecord(NamedTuple):
    ticker: str
    cik: int
    name: str
    name_norm: str


# ---- SEC master ------------------------------------------------------------

def fetch_sec_master(force: bool = False) -> dict:
    SEC_TICKERS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if SEC_TICKERS_CACHE.exists() and not force:
        age = time.time() - SEC_TICKERS_CACHE.stat().st_mtime
        if age < TTL_SEC:
            import json
            return json.loads(SEC_TICKERS_CACHE.read_text(encoding="utf-8"))
    log.info("fetching SEC company_tickers.json")
    r = requests.get(SEC_TICKERS_URL, headers=SEC_HEADERS, timeout=30)
    r.raise_for_status()
    SEC_TICKERS_CACHE.write_bytes(r.content)
    import json
    return json.loads(r.content)


def load_sec_records() -> list[TickerRecord]:
    raw = fetch_sec_master()
    out: list[TickerRecord] = []
    for v in raw.values():
        ticker = (v.get("ticker") or "").upper()
        cik = int(v.get("cik_str") or 0)
        name = v.get("title") or ""
        if ticker and name:
            out.append(TickerRecord(ticker, cik, name, normalize_name(name)))
    return out


# ---- aliases ---------------------------------------------------------------

def _load_aliases() -> dict[str, str]:
    out: dict[str, str] = {}
    if LEARNED_FILE.exists():
        try:
            out.update(yaml.safe_load(LEARNED_FILE.read_text()) or {})
        except Exception:
            pass
    if ALIASES_FILE.exists():
        out.update(yaml.safe_load(ALIASES_FILE.read_text()) or {})
    return {normalize_name(k): v.upper() for k, v in out.items() if v}


# ---- resolver --------------------------------------------------------------

class Resolver:
    """Resolves any company-name string to an SEC ticker (or None)."""

    def __init__(self) -> None:
        self.records = load_sec_records()
        self.by_norm: dict[str, list[TickerRecord]] = {}
        for r in self.records:
            self.by_norm.setdefault(r.name_norm, []).append(r)
        self.aliases = _load_aliases()
        self.token_index: dict[frozenset[str], list[TickerRecord]] = {}
        self.prefix_index: dict[str, list[TickerRecord]] = {}
        for r in self.records:
            toks = frozenset(r.name_norm.split())
            if toks:
                self.token_index.setdefault(toks, []).append(r)
            tlist = r.name_norm.split()
            if len(tlist) >= 2:
                self.prefix_index.setdefault(" ".join(tlist[:2]), []).append(r)
            elif tlist:
                self.prefix_index.setdefault(tlist[0], []).append(r)

    @staticmethod
    def _pick_primary(recs: list[TickerRecord]) -> TickerRecord:
        # Prefer ticker without '-'/'.' suffix, then shortest, then alphabetical
        return sorted(recs, key=lambda r: ("-" in r.ticker or "." in r.ticker, len(r.ticker), r.ticker))[0]

    def resolve(self, name: str) -> tuple[str | None, str, float]:
        if not name:
            return None, "none", 0.0
        norm = normalize_name(name)
        if not norm:
            return None, "none", 0.0
        # 1. manual alias
        if norm in self.aliases:
            return self.aliases[norm], "alias", 1.0
        # 2. exact normalized
        if norm in self.by_norm:
            return self._pick_primary(self.by_norm[norm]).ticker, "exact", 0.95
        # 3. 2-token prefix
        toks = norm.split()
        if len(toks) >= 2:
            key = " ".join(toks[:2])
            if key in self.prefix_index:
                cands = sorted(self.prefix_index[key], key=lambda r: len(r.name_norm))
                return self._pick_primary(cands[:3]).ticker, "prefix", 0.75
        # 4. fuzzy token-set Jaccard
        in_toks = frozenset(toks)
        best_score = 0.0
        best_rec: TickerRecord | None = None
        candidate_recs: list[TickerRecord] = []
        for tok in toks:
            for k, recs in self.token_index.items():
                if tok in k:
                    candidate_recs.extend(recs)
        for r in set(candidate_recs):
            sec_toks = frozenset(r.name_norm.split())
            if not sec_toks or not in_toks:
                continue
            jac = len(in_toks & sec_toks) / len(in_toks | sec_toks)
            if jac > best_score:
                best_score = jac
                best_rec = r
        if best_rec and best_score >= 0.6:
            return best_rec.ticker, "fuzzy", best_score
        # 5. miss
        self._log_miss(name, norm)
        return None, "none", 0.0

    def resolve_pac_name(self, pac_name: str) -> tuple[str | None, str, float]:
        """Conservative resolver tuned for FEC PAC names.

        Cascade (stops at first hit):
          1. PAC-stripped exact alias        confidence 1.00
          2. PAC-stripped exact SEC name     confidence 0.95
          3. Token-coverage prefix match     confidence 0.85
             (require ALL tokens of the SEC company's normalized name to be
              present in the PAC-stripped name — prevents 'first interstate
              texas' falsely matching FIBK)

        DROPS the loose 2-token prefix path and the fuzzy Jaccard path to
        eliminate the false-positive class observed on FEC PAC names. Misses
        are logged for manual / LLM review.
        """
        if not pac_name:
            return None, "none", 0.0
        pac_norm = normalize_pac_name(pac_name)
        if not pac_norm:
            self._log_miss(pac_name, pac_norm)
            return None, "none", 0.0

        # 1. Manual alias (post PAC-strip, full normalized form)
        if pac_norm in self.aliases:
            return self.aliases[pac_norm], "alias", 1.0

        # 2. Exact SEC normalized name
        if pac_norm in self.by_norm:
            return self._pick_primary(self.by_norm[pac_norm]).ticker, "exact", 0.95

        # 3. Longest-prefix alias hit — try first 3, then 2, then 1 tokens.
        # Catches "BLACKROCK FUNDS SERVICES GROUP" → "blackrock" alias → BLK
        # without polluting the alias file with every possible PAC suffix.
        # Single-token aliases gated by a min-length safety to avoid common
        # short words ("ge", "f") accidentally matching unrelated PACs.
        toks = pac_norm.split()
        for plen in (3, 2, 1):
            if len(toks) < plen:
                continue
            key = " ".join(toks[:plen])
            if key in self.aliases:
                # Single-token alias must be ≥4 chars to be safe (e.g. "ford"
                # ok; "ge"/"f" rejected — those need the multi-token form)
                if plen == 1 and len(key) < 4:
                    continue
                return self.aliases[key], "alias_prefix", 0.90

        # 4. Verified prefix match (all SEC-name tokens must appear in pac_norm)
        pac_tok_set = frozenset(toks)
        if len(toks) >= 2:
            key = " ".join(toks[:2])
            cands = self.prefix_index.get(key, [])
            verified: list[TickerRecord] = []
            for r in cands:
                sec_toks = frozenset(r.name_norm.split())
                # All SEC tokens must be in the PAC-stripped name.
                # Also require ≥ 2-token overlap to avoid single-word matches.
                if sec_toks and sec_toks.issubset(pac_tok_set) and len(sec_toks) >= 2:
                    verified.append(r)
            if verified:
                return self._pick_primary(verified).ticker, "verified_prefix", 0.85

        # No match — log and refuse to guess
        self._log_miss(pac_name, pac_norm)
        return None, "none", 0.0

    def _log_miss(self, original: str, norm: str) -> None:
        LEARN_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        new_file = not LEARN_QUEUE.exists()
        with LEARN_QUEUE.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["unresolved_name", "normalized", "suggested_ticker"])
            w.writerow([original, norm, ""])

    def learn(self, name: str, ticker: str) -> None:
        norm = normalize_name(name)
        existing: dict = {}
        if LEARNED_FILE.exists():
            existing = yaml.safe_load(LEARNED_FILE.read_text()) or {}
        existing[norm] = ticker.upper()
        LEARNED_FILE.parent.mkdir(parents=True, exist_ok=True)
        LEARNED_FILE.write_text(yaml.safe_dump(existing, sort_keys=True))
        self.aliases[norm] = ticker.upper()

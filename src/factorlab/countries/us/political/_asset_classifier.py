"""Asset classifier — `asset_name_raw` → (ticker, asset_type_code, confidence, reason).

Used to backfill the ~25% of `legislator_trades` rows where the ingest parser
did not extract a ticker. Three phases:

  Phase A — explicit ticker extraction (regex, no API calls)
    A1. "RTN-Raytheon Company"            → ticker=RTN  code=ST   reason=prefix_dash_ticker
    A2. "Kinder Morgan (KMI)"              → ticker=KMI  code=ST   reason=paren_ticker
    A3. "WMT" / "aapl" / "GE"              → ticker=WMT  code=ST   reason=bare_ticker
  Phase B — keyword-based asset class tagging
    "Rate/Coupon" / "Matures"              → code=CS / GS  (bond/note)
    "City of X" / "State of Y" / "Auth"    → code=GS  (muni)
    "Call Option" / "Put Option"           → code=OP  (and try to extract underlying)
    "iShares" / "SPDR" / "Vanguard"        → code=EF / MF
    "Fund" / "ETF" / "Trust"               → code=MF / EF
    "REIT"                                 → code=RE
    "Cryptocurrency" / "Bitcoin"           → code=CT
    "401(k)" / "401k"                      → code=4K
    "529"                                  → code=5C / 5F / 5P
    "RSU" / "Restricted Stock"             → code=RS
    "Common Stock" / "CMN"                 → code=ST  (and try Resolver)
    "scanned PDF"                          → code=OT  (paper-PTR placeholder)
  Phase C — fallback to 5-tier company-name Resolver

Ticker outputs are validated against the SEC company_tickers master so we
don't store fake tickers like "FAKE" or "ABCDE".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from factorlab.countries.us.political._resolver import Resolver, load_sec_records

from factorlab.shared.paths import REPO_ROOT  # noqa: E402

log = logging.getLogger(__name__)

ETF_ALIASES_FILE = REPO_ROOT / "configs" / "reference" / "etf_aliases.yaml"


@dataclass(frozen=True)
class ClassifyResult:
    ticker: str | None
    asset_type_code: str | None
    confidence: float
    reason: str


# ---- HTML / formatting cleanup ---------------------------------------------

_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_ENTITY = re.compile(r"&[a-z]+;|&#\d+;", re.IGNORECASE)
_WS = re.compile(r"\s+")


def _clean(s: str) -> str:
    """Strip HTML tags + entities, collapse whitespace."""
    if not s:
        return ""
    s = _HTML_TAG.sub(" ", s)
    s = _HTML_ENTITY.sub(" ", s)
    return _WS.sub(" ", s).strip()


# ---- Phase B keyword patterns (order matters — most-specific first) -------

# Each entry: (compiled regex, asset_type_code, reason).
# Patterns match against lowercased *cleaned* text.
CLASS_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # Most specific — paper-PTR placeholder (Senate scanned PDFs)
    (re.compile(r"this filing was disclosed via scanned pdf"), "OT", "paper_ptr_placeholder"),
    # Paper-PTR phrasing variants
    (re.compile(r"scanned pdf|view the pdf"), "OT", "paper_ptr_placeholder"),
    # Cryptocurrency
    (re.compile(r"\b(bitcoin|ethereum|crypto(currency)?|btc|eth)\b"), "CT", "crypto_keyword"),
    # 529 plans
    (re.compile(r"\b529\b.*\bprepaid\b"), "5P", "529_prepaid"),
    (re.compile(r"\b529\b.*\bportfolio\b"), "5F", "529_portfolio"),
    (re.compile(r"\b529\b"), "5C", "529_plan"),
    # 401(k) / pension
    (re.compile(r"\b401\s*\(?k\)?\b"), "4K", "401k"),
    (re.compile(r"\bdefined benefit\b|\bpension\b"), "PE", "pension"),
    # Options (stronger pattern — explicit option language)
    (re.compile(r"\b(call|put)\s+option\b"), "OP", "option"),
    (re.compile(r"\boption\s+(contract|premium)\b"), "OP", "option"),
    # RSU / restricted stock
    (re.compile(r"\brestricted stock unit"), "RS", "rsu"),
    (re.compile(r"\brsu\b"), "RS", "rsu"),
    (re.compile(r"\bstock appreciation right"), "SA", "sar"),
    # REIT
    (re.compile(r"\breit\b|real estate investment trust"), "RE", "reit"),
    # ETN
    (re.compile(r"\betn\b|exchange[\- ]traded note"), "ET", "etn"),
    # ETF (must come before MF — many funds say "fund" but are ETFs)
    (re.compile(r"\betf\b|exchange[\- ]traded fund"), "EF", "etf_keyword"),
    # Mutual funds / fund families
    (re.compile(r"\bmutual fund\b"), "MF", "mutual_fund"),
    (re.compile(r"\b(vanguard|fidelity|t\.? rowe price|aqr|blackrock|pimco|dodge\s*&\s*cox)\b.*\bfunds?\b"), "MF", "fund_family"),
    (re.compile(r"\b(spdr|ishares|invesco|powershares|proshares|wisdomtree)\b"), "EF", "etf_brand"),
    (re.compile(r"\bindex fund\b"), "MF", "index_fund"),
    (re.compile(r"\bfunds?\b"), "MF", "fund_keyword"),
    # Bonds / Treasuries — STRONG signal: HTML "Rate/Coupon" or "Matures"
    (re.compile(r"rate/coupon|matures"), "CS", "bond_html_marker"),
    # Percent rate + "Due" date is a near-certain bond/muni marker
    # ("Maryland ST Dept TR 5% Comb Tax Due Nov 1, 2027")
    (re.compile(r"\d+(\.\d+)?\s*%[^\n]*\bdue\b"), "GS", "rate_and_due"),
    (re.compile(r"\btreasury\b.*\b(bill|note|bond)\b"), "GS", "treasury"),
    (re.compile(r"\bt[\- ]bill\b|\btips\b"), "GS", "treasury"),
    (re.compile(r"\bus(a)? treasury\b"), "GS", "treasury"),
    # Muni bonds — usually "City of X", "State of Y", "X Authority", "School District"
    (re.compile(r"\bmunicipal\b|\bmuni\b"), "GS", "muni"),
    (re.compile(r"\b(city|state|county|borough|township) of\b.*\b(bond|rev|go)\b"), "GS", "muni_govt"),
    (re.compile(r"\bschool district\b"), "GS", "muni_school"),
    (re.compile(r"\b(housing|hsg)\b.*\b(authority|auth|dev|fin)\b"), "GS", "muni_housing"),
    (re.compile(r"\butility (board|district)\b|util.*\brev\b"), "GS", "muni_utility"),
    (re.compile(r"\b(auth|authority)\b.*\b(rev|bond|fac)\b"), "GS", "muni_authority"),
    (re.compile(r"\bcnty\b|\bcounty\b"), "GS", "muni_county"),
    (re.compile(r"\b(revenue|general obligation|go) bond\b"), "GS", "muni_bond"),
    # Generic bonds / notes
    (re.compile(r"\bbonds?\b"), "CS", "bond_keyword"),
    (re.compile(r"\bnts? b/e\b|\bsr nt\b"), "CS", "corp_note"),
    (re.compile(r"\bnotes?\b"), "CS", "note_keyword"),
    # Asset-backed
    (re.compile(r"asset[\- ]backed"), "AB", "abs"),
    # Foreign exchange
    (re.compile(r"\bforex\b|\bcurrency\b"), "FE", "fx"),
    # Futures
    (re.compile(r"\bfutures\b|\bcommodity\b"), "FU", "futures"),
    # Bank accounts / CDs
    (re.compile(r"\bbank account\b|\bmoney market\b|certificate of deposit|\bcd\b"), "BA", "bank_account"),
    # Brokerage
    (re.compile(r"\bbrokerage account\b"), "BK", "brokerage"),
    # Trust — narrow to non-publicly-traded trust language only.
    # Bare "Trust" is a noisy match because lots of public REITs/CEFs/ETFs use the word
    # ("SPDR Gold Trust", "Medical Properties Trust", "Sprott Physical Gold Trust").
    (re.compile(r"\bdelaware statutory trust\b|\bstatutory trust\b"), "DS", "delaware_trust"),
    (re.compile(r"\b(blind|family|revocable|irrevocable|living)\s+trust\b"), "TR", "trust"),
    # Annuities / insurance
    (re.compile(r"\bvariable annuity\b"), "VA", "variable_annuity"),
    (re.compile(r"\bfixed annuity\b"), "FN", "fixed_annuity"),
    (re.compile(r"\bvariable insurance\b"), "VI", "variable_insurance"),
    (re.compile(r"\bwhole life|universal (life )?insurance\b"), "WU", "whole_universal_ins"),
    # Real property / farms / collectibles
    (re.compile(r"\b(farm|farmland)\b"), "FA", "farm"),
    (re.compile(r"\bcollectibles?\b"), "CO", "collectible"),
    # Mineral RIGHTS only — not "Compass Minerals" (real ticker)
    (re.compile(r"(mineral|oil|solar(?:\s+energy)?)\s+(rights|royalt)"), "MO", "mineral_rights"),
    (re.compile(r"\b(real property|residence)\b"), "RP", "real_property"),
    # IRA
    (re.compile(r"\bira\s*\(.*cash"), "IH", "ira_cash"),
    (re.compile(r"\b(traditional|roth)?\s*ira\b"), "IR", "ira"),
    # Private/non-public stock — LLC/Inc with no ticker resolution should fall here
    # (we only set this in Phase C if Resolver fails)
]


# ---- Phase A explicit ticker extraction -----------------------------------

# Strip these company suffixes to improve resolver hit rate.
# IMPORTANT: closing \b is required so "co" doesn't strip "Co" out of "Coca-Cola".
# Standalone "co" intentionally NOT in the list — "company" handles the legit cases.
_STRIP_SUFFIXES = re.compile(
    r"\b(cmn|class\s+[a-z]|cl\s+[a-z]|common stock|ordinary shares|adr|ads|"
    r"sponsored adr|holdings?|incorporated|inc|corp(oration)?|company|"
    r"plc|llc|llp|lp|limited|ltd|the)\b\.?",
    re.IGNORECASE,
)

# "RTN-Raytheon Company" / "KMI - Kinder Morgan Inc" / "INTC-Intel Corporation"
_PREFIX_DASH_TICKER = re.compile(r"^([A-Z]{1,5})\s*[-–—]\s*([A-Za-z])")
# "Sunoco Logistics Partners (SXL)"
_PAREN_TICKER = re.compile(r"\(([A-Z]{1,5})\)\s*$")
# "WMT" / "aapl" / "GE" alone
_BARE_TICKER = re.compile(r"^[A-Za-z]{1,5}$")


def _is_real_ticker(t: str, sec_set: set[str]) -> bool:
    """SEC company_tickers.json contains every public US stock + ETF."""
    return t.upper() in sec_set


# ---- Main classifier -----------------------------------------------------

class AssetClassifier:
    def __init__(self, resolver: Resolver | None = None) -> None:
        self.resolver = resolver or Resolver()
        # Build SEC ticker set for verification
        self.sec_set = {r.ticker for r in load_sec_records()}
        # Load ETF alias map
        self.etf_aliases: dict[str, str] = {}
        if ETF_ALIASES_FILE.exists():
            raw = yaml.safe_load(ETF_ALIASES_FILE.read_text(encoding="utf-8")) or {}
            self.etf_aliases = {k.lower(): v.upper() for k, v in raw.items()}
        log.info("[asset_classifier] sec=%d etf_aliases=%d",
                 len(self.sec_set), len(self.etf_aliases))

    def classify(self, name: str) -> ClassifyResult:
        if not name:
            return ClassifyResult(None, None, 0.0, "empty")
        cleaned = _clean(name)
        s_lower = cleaned.lower()

        # Phase A1 — paper-PTR placeholder takes precedence
        if "this filing was disclosed via scanned pdf" in s_lower:
            return ClassifyResult(None, "OT", 1.0, "paper_ptr_placeholder")

        # Phase A2 — prefix-dash ticker ("RTN-Raytheon Company")
        m = _PREFIX_DASH_TICKER.match(cleaned)
        if m:
            cand = m.group(1).upper()
            if _is_real_ticker(cand, self.sec_set):
                return ClassifyResult(cand, "ST", 0.95, "prefix_dash_ticker")

        # Phase A3 — paren-suffix ticker
        m = _PAREN_TICKER.search(cleaned)
        if m:
            cand = m.group(1).upper()
            # Skip exchange tags like (NYSE), (NASDAQ)
            if cand not in {"NYSE", "NASDAQ", "AMEX", "OTC", "BATS", "ARCA"} \
                    and _is_real_ticker(cand, self.sec_set):
                return ClassifyResult(cand, "ST", 0.95, "paren_ticker")

        # Phase A4 — bare ticker
        if _BARE_TICKER.match(cleaned):
            cand = cleaned.upper()
            if _is_real_ticker(cand, self.sec_set):
                return ClassifyResult(cand, "ST", 0.9, "bare_ticker")

        # Phase A5 — ETF brand alias (substring lookup)
        for pattern, ticker in self.etf_aliases.items():
            if pattern in s_lower:
                code = "EF" if not ticker.startswith("MF") else "MF"
                return ClassifyResult(ticker, code, 0.9, "etf_alias")

        # Phase B — keyword classification
        b_code: str | None = None
        b_reason: str | None = None
        for pattern, code, reason in CLASS_PATTERNS:
            if pattern.search(s_lower):
                b_code, b_reason = code, reason
                break

        # Codes for which we DON'T try to find a ticker (no-equity instruments)
        NO_TICKER_CODES = {"OT", "CT", "FE", "FU", "BA", "BK", "FA", "CO", "MO", "RP",
                            "IR", "IH", "PE", "DB", "VA", "FN", "VI", "WU", "DS", "TR",
                            "4K", "5C", "5F", "5P", "GS", "CS", "AB", "DO", "IP",
                            "EQ", "IC", "PS", "RS", "SA"}

        # If Phase B classified as a non-equity, return without trying ticker
        if b_code and b_code in NO_TICKER_CODES:
            return ClassifyResult(None, b_code, 0.7, b_reason)

        # For options, try the underlying ticker
        if b_code == "OP":
            ticker = self._try_extract_underlying(cleaned)
            return ClassifyResult(ticker, "OP", 0.8 if ticker else 0.7, b_reason)

        # Phase C — strip suffixes and run 5-tier Resolver
        # For Phase-B-fund-like classes (EF/MF/ET), restrict to high-confidence
        # match kinds only — prefix/fuzzy matches the ETF *issuer* (Goldman Sachs,
        # First Trust, BlackRock) instead of the fund itself, producing wrong tickers
        # like "Goldman Sachs Treasury ETF" -> GS.
        FUND_LIKE_CLASSES = {"EF", "MF", "ET"}
        if b_code in FUND_LIKE_CLASSES:
            allowed_kinds = {"alias", "exact"}
        else:
            allowed_kinds = {"alias", "exact", "prefix", "fuzzy"}

        stripped = _STRIP_SUFFIXES.sub(" ", cleaned).strip()
        if len(stripped) >= 3 and any(c.isalpha() for c in stripped):
            ticker, kind, conf = self.resolver.resolve(stripped)
            if ticker and conf >= 0.7 and kind in allowed_kinds:
                final_code = b_code if b_code else "ST"
                final_reason = f"{b_reason}+resolver_{kind}" if b_reason else f"resolver_{kind}"
                return ClassifyResult(ticker, final_code, conf, final_reason)

        # Phase C miss — return Phase B class if any, else fully unresolved
        if b_code:
            return ClassifyResult(None, b_code, 0.6, b_reason)
        return ClassifyResult(None, None, 0.0, "unresolved")

    def _try_extract_underlying(self, cleaned: str) -> str | None:
        """For options, try to find a ticker in the description."""
        # Look for any ALL-CAPS 1-5 letter token
        for tok in re.findall(r"\b[A-Z]{1,5}\b", cleaned):
            if _is_real_ticker(tok, self.sec_set):
                return tok
        # Look for company name → resolver
        stripped = _STRIP_SUFFIXES.sub(" ", cleaned).strip()
        if stripped:
            ticker, _, conf = self.resolver.resolve(stripped)
            if ticker and conf >= 0.7:
                return ticker
        return None

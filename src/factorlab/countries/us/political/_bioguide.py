"""Strict legislator-name → bioguide_id resolver.

Replaces the per-source inline matchers (which used naive lastname-substring
matching against legislators-historical, producing ~30% false positives on
SSW historical data — old 1800s legislators with common last names like
James / Thomas / White caught modern-name searches).

Strict policy: resolve only when *exactly one* legislator matches:
  1. correct chamber ('sen' | 'rep')
  2. had a term active during the trade window (term_start - 60d <= D <= term_end + 60d)
  3. lastname matches (case-insensitive, exact whole-word)
  4. if input has a first name, first-name prefix matches (≥3 chars)

Otherwise return None — better a NULL than a wrong attribution.

Shared by all three trade-source ingestions:
  senate_stock_watcher/ingest.py
  house_clerk/ingest.py
  senate_efd/ingest.py
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# Slack on either side of the term window — captures trades reported just after
# a member's term ended (45-day STOCK Act lag) and just before it started.
TERM_SLACK = timedelta(days=60)

# Honorifics + middle-name noise we strip from filer name strings before matching.
# Catches eFD result-table quirks like "Marjorie Taylor Mrs Greene" and the
# "King, Angus (Senator)" office-field format.
HONORIFIC_TOKENS = {
    "mr", "mrs", "ms", "dr", "hon", "jr", "sr", "ii", "iii", "iv",
    "rep", "sen", "senator", "representative", "congressman",
    "congresswoman", "the",
    # Period-stripped middle initials handled separately
}

# Surname particles — if they precede the final token, they belong with the lastname.
# "Chris Van Hollen" → last="van hollen", not "hollen". Same for "De La Cruz" etc.
SURNAME_PARTICLES = {
    "van", "von", "de", "del", "della", "di", "da", "la", "le", "el",
    "mc", "mac", "o", "st", "saint", "san", "ten", "ter", "vander",
}

# Common first-name nicknames ↔ full names. Bidirectional check at match time.
# Only includes mappings that occur among US legislators historically.
NICKNAME_PAIRS = (
    ("mike", "michael"), ("bob", "robert"), ("rob", "robert"), ("bobby", "robert"),
    ("bill", "william"), ("billy", "william"), ("will", "william"),
    ("tom", "thomas"), ("tommy", "thomas"),
    ("jim", "james"), ("jimmy", "james"), ("jamie", "james"),
    ("dick", "richard"), ("rick", "richard"), ("rich", "richard"),
    ("dan", "daniel"), ("danny", "daniel"),
    ("chuck", "charles"), ("charlie", "charles"),
    ("steve", "stephen"), ("steven", "stephen"),
    ("dave", "david"), ("davey", "david"),
    ("ben", "benjamin"), ("benny", "benjamin"),
    ("tony", "anthony"), ("nick", "nicholas"),
    ("joe", "joseph"), ("joey", "joseph"),
    ("ed", "edward"), ("eddie", "edward"), ("ted", "edward"),
    ("frank", "francis"), ("franky", "francis"),
    ("kate", "katherine"), ("kathy", "katherine"), ("katie", "katherine"),
    ("liz", "elizabeth"), ("beth", "elizabeth"), ("betty", "elizabeth"),
    ("debbie", "deborah"), ("deb", "deborah"),
    ("matt", "matthew"), ("matty", "matthew"),
    ("greg", "gregory"),
    ("alex", "alexander"), ("alec", "alexander"),
    ("cindy", "cynthia"), ("cindi", "cynthia"),
    ("susie", "susan"), ("sue", "susan"),
    ("peggy", "margaret"), ("maggie", "margaret"), ("meg", "margaret"),
    ("patty", "patricia"), ("pat", "patricia"), ("trish", "patricia"),
    ("jenny", "jennifer"), ("jen", "jennifer"),
    ("becky", "rebecca"), ("becca", "rebecca"),
    ("nick", "nicholas"), ("nicky", "nicholas"),
    ("rick", "frederick"), ("fred", "frederick"),
    ("tony", "antonio"),
    ("jeff", "jeffrey"), ("jeffery", "jeffrey"),
    ("larry", "lawrence"), ("lawrence", "laurence"),
    ("hank", "henry"), ("harry", "henry"),
    ("nate", "nathaniel"), ("nat", "nathaniel"),
    ("phil", "philip"), ("phillip", "philip"),
    ("ken", "kenneth"), ("kenny", "kenneth"),
    ("ron", "ronald"), ("ronny", "ronald"),
    ("don", "donald"), ("donny", "donald"),
    ("sam", "samuel"), ("sammy", "samuel"),
    ("andy", "andrew"), ("drew", "andrew"),
    ("mitch", "mitchell"),
)
NICKNAME_MAP: dict[str, set[str]] = {}
for short, long in NICKNAME_PAIRS:
    NICKNAME_MAP.setdefault(short, set()).add(long)
    NICKNAME_MAP.setdefault(long, set()).add(short)


@dataclass(frozen=True)
class _LegRow:
    bioguide_id: str
    first_lower: str          # primary first name (may be just an initial)
    last_lower: str
    term_start: date
    term_end: date
    chamber: str              # 'sen' | 'rep'
    first_aliases: frozenset = frozenset()  # all known first-name aliases (lowercase, ascii-folded)


def _ascii_fold(s: str) -> str:
    """Strip diacritics and normalize Unicode quotes to ASCII equivalents.

    "Barragán" → "Barragan"
    "O'Rourke"  (U+2019) → "O'Rourke" (U+0027)
    """
    if not s:
        return s
    # Unicode quotes/apostrophes → ASCII apostrophe
    quotes_map = {
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"',
    }
    for k, v in quotes_map.items():
        s = s.replace(k, v)
    # NFKD decomposition + drop combining marks
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm_token(s: str) -> str:
    """Lowercase, strip punctuation, single token."""
    return re.sub(r"[^a-z]", "", _ascii_fold(s).lower())


def _norm_lastname(s: str) -> str:
    """Lowercase + ascii-fold + collapse whitespace, but PRESERVE multi-word
    structure (used as the lookup key for compound surnames)."""
    s = _ascii_fold(s or "")
    s = re.sub(r"[^A-Za-z\s'\-]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _split_name(full_name: str) -> tuple[str, str]:
    """Return (first, last) from a free-form name string.

    Handles two formats:
      "Last, First [Middle] [Suffix] [(Title)]" — eFD office field
        e.g. 'King, Angus (Senator)' or 'McConnell, A. Mitchell Jr. (Senator)'
      "First [Middle] Last [Suffix]" — freeform
        e.g. 'Sheldon Whitehouse', 'David A Perdue , Jr', 'Hon. Lloyd Doggett'

    Detection: if a comma exists and the substring before the FIRST comma is a
    single alphabetic word (with optional hyphens/apostrophes — surnames like
    "O'Brien" or "Van-Hollen"), treat as "Last, First" format. Else freeform.
    """
    raw = (full_name or "").strip()

    # "Last, First" detection
    if "," in raw:
        before_comma, after_comma = raw.split(",", 1)
        before_clean = before_comma.strip()
        # Single alphabetic word before comma → "Last, First" format
        if (before_clean and " " not in before_clean and
                re.fullmatch(r"[A-Za-z][A-Za-z'\-]*", before_clean)):
            last = _norm_token(before_clean)
            after_parts = [p for p in re.split(r"[\s,]+", after_comma) if p]
            after_parts = [p.rstrip(".") for p in after_parts]
            for p in after_parts:
                n = _norm_token(p)
                if not n or n in HONORIFIC_TOKENS or len(n) == 1:
                    continue
                return (n, last)  # first non-honorific surviving token
            return ("", last)

    # Freeform "First Middle Last [Suffix]"
    parts = [p for p in re.split(r"[\s,]+", raw) if p]
    parts = [p.rstrip(".") for p in parts]
    keep: list[str] = []
    for p in parts:
        n = _norm_token(p)
        if not n:
            continue
        if n in HONORIFIC_TOKENS:
            continue
        if len(n) == 1:  # middle initial
            continue
        keep.append(n)
    if not keep:
        return ("", "")
    if len(keep) == 1:
        return ("", keep[0])
    # If the token before the final one is a surname particle, glue them.
    if len(keep) >= 2 and keep[-2] in SURNAME_PARTICLES:
        last = f"{keep[-2]} {keep[-1]}"
        first = keep[0] if len(keep) >= 3 else ""
        return (first, last)
    return (keep[0], keep[-1])


class StrictBioguideMatcher:
    """Resolves filer name → bioguide_id with strict disambiguation rules.

    Indexes the legislators table by multiple lookup keys so that compound
    surnames and cross-source name splits both resolve:
      - "Wasserman Schultz" (House Clerk index) ↔ last="wasserman schultz" (bioguide)
      - "April McClain"+"Delaney" (House Clerk) ↔ "April"+"McClain Delaney" (bioguide)
      - "Nicholas Van Taylor" (House Clerk) ↔ "Van"+"Taylor" (bioguide)

    All keys are ASCII-folded so diacritics and Unicode apostrophes match.
    """

    def __init__(self, engine: Engine):
        # PRIMARY index: (chamber, exact normalized last_name) → list of _LegRow
        # The lookup that's safe to use without first-name match.
        self._primary: dict[tuple[str, str], list[_LegRow]] = {}
        # SECONDARY index: (chamber, derived-key) → list of _LegRow. Entries here
        # came from compound-surname collapse / cross-split mapping; must ALWAYS
        # be confirmed by first-name overlap before returning a match.
        self._secondary: dict[tuple[str, str], list[_LegRow]] = {}

        n_rows = 0
        with engine.connect() as c:
            for bio, first, middle, last, nickname, official, ts, te, chamber in c.execute(text("""
                SELECT l.bioguide_id, l.first_name, l.middle_name, l.last_name,
                       l.nickname, l.official_full,
                       t.term_start, t.term_end, t.chamber
                FROM alt_political_us.legislators l
                JOIN alt_political_us.legislator_terms t USING (bioguide_id)
            """)):
                if not (bio and last and ts and te and chamber):
                    continue
                first_norm = _norm_lastname(first or "")
                last_norm = _norm_lastname(last)

                # Build a comprehensive alias set covering preferred-name patterns:
                # - first_name (e.g. "C.")
                # - middle_name (e.g. "Scott" — common when person goes by middle)
                # - nickname
                # - first word of official_full (typically the preferred name)
                # All ascii-folded + lowercased + nickname-expanded.
                first_aliases: set[str] = set()
                for src in (first or "", middle or "", nickname or ""):
                    for tok in _norm_lastname(src).split():
                        if len(tok) > 1:  # drop single-letter initials like "C", "V"
                            first_aliases.add(tok)
                            first_aliases |= NICKNAME_MAP.get(tok, set())
                # Official_full first word (e.g. "Scott Franklin" → "scott")
                if official:
                    parts = _norm_lastname(official).split()
                    if parts and len(parts[0]) > 1:
                        first_aliases.add(parts[0])
                        first_aliases |= NICKNAME_MAP.get(parts[0], set())

                row = _LegRow(
                    bioguide_id=bio,
                    first_lower=first_norm,
                    last_lower=last_norm,
                    term_start=ts, term_end=te,
                    chamber=chamber,
                    first_aliases=frozenset(first_aliases),
                )
                n_rows += 1

                # PRIMARY: exact normalized last name (compound preserved).
                self._add_primary(chamber, last_norm, row)

                # SECONDARY keys (require first-name overlap at lookup):
                if " " in last_norm:
                    # Collapse to last token: "Wasserman Schultz" → "schultz"
                    self._add_secondary(chamber, last_norm.split()[-1], row)
                    # Collapse to first token: "Hinson Arenholz" → "hinson"
                    self._add_secondary(chamber, last_norm.split()[0], row)
                if first_norm and " " not in first_norm:
                    # Index that puts particle in lastname:
                    # bioguide first="Van" + last="Taylor" → also "van taylor"
                    self._add_secondary(chamber, f"{first_norm} {last_norm}", row)

        # Stage 0 alias overrides — manually-curated bioguide mappings that
        # the strict matcher (Stages 1-3) cannot bridge. Loaded from
        # alt_political_us.legislator_aliases (mig 024).
        # Format: alias_normalized → bioguide_id
        self._aliases: dict[str, str] = {}
        try:
            with engine.connect() as c:
                for alias, bg in c.execute(text("""
                    SELECT alias_normalized, bioguide_id
                    FROM alt_political_us.legislator_aliases
                    WHERE country_code = 'US'
                """)):
                    if alias and bg:
                        self._aliases[alias] = bg
        except Exception as e:
            # Pre-mig-024 fallback: table doesn't exist yet — Stage 0 simply
            # returns no matches, the existing 3-stage cascade still runs.
            log.warning("[bioguide] legislator_aliases load skipped: %s", e)

        log.info(
            "[bioguide] indexed %d legislator-term rows: %d primary keys, "
            "%d secondary keys, %d Stage-0 aliases",
            n_rows, len(self._primary), len(self._secondary), len(self._aliases),
        )

    def _add_primary(self, chamber: str, key: str, row: "_LegRow") -> None:
        if not key:
            return
        bucket = self._primary.setdefault((chamber, key), [])
        if row not in bucket:
            bucket.append(row)

    def _add_secondary(self, chamber: str, key: str, row: "_LegRow") -> None:
        if not key:
            return
        bucket = self._secondary.setdefault((chamber, key), [])
        if row not in bucket:
            bucket.append(row)

    # Compatibility shim — exposes both for read-only callers.
    @property
    def _by_chamber_last(self) -> dict[tuple[str, str], list[_LegRow]]:
        merged = dict(self._primary)
        for k, v in self._secondary.items():
            if k in merged:
                merged[k] = merged[k] + [r for r in v if r not in merged[k]]
            else:
                merged[k] = list(v)
        return merged

    @property
    def _idx(self) -> dict[tuple[str, str], list[_LegRow]]:
        return self._by_chamber_last

    # ------------------------------------------------------------------

    def resolve(self, *, full_name: str | None = None,
                first: str | None = None, last: str | None = None,
                chamber: str, trade_date: date | None) -> str | None:
        """Resolve a filer to a bioguide_id, or None if ambiguous / not found.

        Provide either (full_name) OR (first, last). Chamber is required.

        Stage 0: alt_political_us.legislator_aliases override (highest
                 priority — beats all fuzzy matching). Used for cases the
                 strict matcher cannot bridge (e.g. Van Taylor files as
                 'Nicholas V. Taylor'). Each entry is hand-verified.
        Stage 1: primary index lookup (exact normalized last_name + first
                 overlap). Always-required first-name overlap, even on
                 single-candidate primary match — guards against
                 false-positive "Random Taylor" → Van Taylor.
        Stage 2: secondary index (collapsed compound surnames + cross-mapped
                 first+last splits). First overlap also required.
        Stage 3: comprehensive split-search across all token combinations.
        """
        if chamber not in ("sen", "rep"):
            return None

        # ---- Stage 0: legislator_aliases override -----------------------
        # Build the lookup key the same way the migration seeded the column:
        # lowercase, whitespace-collapsed, punctuation preserved.
        if self._aliases:
            candidates = []
            if full_name:
                candidates.append(full_name)
            if first or last:
                candidates.append(f"{(first or '').strip()} {(last or '').strip()}".strip())
            for raw in candidates:
                key = re.sub(r"\s+", " ", (raw or "").strip()).lower()
                if key and key in self._aliases:
                    log.debug("[bioguide] Stage 0 hit: %r -> %s",
                              key, self._aliases[key])
                    return self._aliases[key]

        # Get a "raw" first/last preserving compound structure where possible.
        if full_name is not None and (first is None and last is None):
            first, last = _split_name(full_name)
        # When caller passes first/last explicitly, KEEP the original last as-is
        # (don't pass through _split_name which collapses compounds). Only strip
        # honorifics / suffixes from the suffix end of last.
        raw_last = (last or "").strip()
        # Strip trailing "Jr.", "Sr.", "II", "III", "IV", "MD", "PhD", etc.
        raw_last_parts = [p for p in re.split(r"[\s,]+", raw_last) if p]
        cleaned_last_parts: list[str] = []
        for p in raw_last_parts:
            n = _norm_token(p)
            if n in HONORIFIC_TOKENS:
                continue
            cleaned_last_parts.append(p)
        last = " ".join(cleaned_last_parts)
        last_norm = _norm_lastname(last)

        # First-name normalization (handles "Hon. Lloyd" → "Lloyd",
        # multi-word "April McClain" → keeps both for cross-key lookup).
        raw_first = (first or "").strip()
        raw_first_parts = [p for p in re.split(r"[\s,]+", raw_first) if p]
        clean_first_parts = [
            p for p in raw_first_parts
            if _norm_token(p) and _norm_token(p) not in HONORIFIC_TOKENS
            and len(_norm_token(p)) > 1  # drop middle initials
        ]
        first_norm = _norm_lastname(" ".join(clean_first_parts))
        first_first_token = first_norm.split()[0] if first_norm else ""

        if not last_norm:
            return None

        # Build the input first-name alias set (for first-name disambig at all stages).
        input_aliases: set[str] = set()
        for tok in (first_norm or "").split():
            if tok:
                input_aliases.add(tok)
                input_aliases |= NICKNAME_MAP.get(tok, set())

        def _term_filter(rows: list[_LegRow]) -> list[_LegRow]:
            if trade_date is None:
                return list(rows)
            return [
                r for r in rows
                if (r.term_start - TERM_SLACK) <= trade_date <= (r.term_end + TERM_SLACK)
            ]

        def _first_name_overlap(c: _LegRow) -> bool:
            """Strict first-name match using all known aliases (first/middle/
            nickname/official) + nickname mapping + 3-char prefix."""
            cand_aliases = set(c.first_aliases)
            if c.first_lower:
                cand_tok = _norm_token(c.first_lower).split()[0] if c.first_lower else ""
                if cand_tok and len(cand_tok) > 1:
                    cand_aliases.add(cand_tok)
                    cand_aliases |= NICKNAME_MAP.get(cand_tok, set())
            if not cand_aliases:
                return False
            if input_aliases & cand_aliases:
                return True
            # 3-char prefix overlap on any pair
            for it in input_aliases:
                if len(it) < 3:
                    continue
                for ct in cand_aliases:
                    if len(ct) >= 3 and it[:3] == ct[:3]:
                        return True
            return False

        # === STAGE 1: PRIMARY KEY ============================================
        # Exact lastname match. ALWAYS require first-name overlap when input has
        # a first name — even if there's only one candidate. Otherwise a single
        # Van Taylor's term-covering window would wrongly absorb a "Random Taylor"
        # filer.
        primary_rows = _term_filter(self._primary.get((chamber, last_norm), []))
        primary_bios = {r.bioguide_id for r in primary_rows}

        if input_aliases:
            with_first_primary = {
                r.bioguide_id for r in primary_rows if _first_name_overlap(r)
            }
            if len(with_first_primary) == 1:
                return next(iter(with_first_primary))
            # If multiple first-name matches → ambiguous, fall through.
        else:
            # No first name from caller — fall back to single-candidate primary.
            if len(primary_bios) == 1:
                return next(iter(primary_bios))

        # === STAGE 2: SECONDARY KEYS =========================================
        # Compound-surname collapses (last → last-word and last → first-word).
        # Always require first-name overlap to avoid mis-attribution.
        sec_keys: list[str] = []
        if " " in last_norm:
            sec_keys.append(last_norm.split()[-1])
            sec_keys.append(last_norm.split()[0])

        if not input_aliases:
            # Without input first name we can't safely use secondary or
            # cross-mapping — strict NULL.
            return None

        for k in sec_keys:
            # Look in BOTH primary (e.g., Ashley Hinson with bioguide last="hinson"
            # when input has last="Hinson Arenholz") AND secondary (compound-collapse).
            # First-name overlap required either way to avoid mis-attribution.
            combined_rows = list(self._secondary.get((chamber, k), [])) + list(
                self._primary.get((chamber, k), [])
            )
            sec_rows = _term_filter(combined_rows)
            with_first = {r.bioguide_id for r in sec_rows if _first_name_overlap(r)}
            if len(with_first) == 1:
                return next(iter(with_first))

        # === STAGE 3: COMPREHENSIVE SPLIT SEARCH =============================
        # Caller's first/last split may not match bioguide's. Tokenize the
        # combined name and try every reasonable (first, last) split point,
        # looking up each candidate lastname in PRIMARY with first-name overlap.
        # Examples this catches:
        #   caller "April McClain" + "Delaney"  → bioguide "April" + "McClain Delaney"
        #   caller "Ashley Hinson" + "Arenholz" → bioguide "Ashley" + "Hinson"
        #   caller "Nicholas" + "Van Taylor"    → bioguide "Van" + "Taylor"
        combined_parts = (first_norm.split() if first_norm else []) + last_norm.split()

        def _alias_overlap_with(c: _LegRow, aliases: set[str]) -> bool:
            cand_aliases = set(c.first_aliases)
            if c.first_lower:
                cand_tok = _norm_token(c.first_lower).split()[0] if c.first_lower else ""
                if cand_tok and len(cand_tok) > 1:
                    cand_aliases.add(cand_tok)
                    cand_aliases |= NICKNAME_MAP.get(cand_tok, set())
            if not cand_aliases:
                return False
            if aliases & cand_aliases:
                return True
            for it in aliases:
                if len(it) < 3:
                    continue
                for ct in cand_aliases:
                    if len(ct) >= 3 and it[:3] == ct[:3]:
                        return True
            return False

        seen_lasts: set[str] = {last_norm}

        # Pass 1: contiguous-suffix splits (most natural)
        for i in range(1, len(combined_parts)):
            first_tokens = combined_parts[:i]
            last_tokens = combined_parts[i:]

            cand_last = " ".join(last_tokens)
            if cand_last in seen_lasts:
                continue
            seen_lasts.add(cand_last)

            cand_aliases: set[str] = set()
            for t in first_tokens:
                cand_aliases.add(t)
                cand_aliases |= NICKNAME_MAP.get(t, set())

            rows = _term_filter(self._primary.get((chamber, cand_last), []))
            with_first = {r.bioguide_id for r in rows if _alias_overlap_with(r, cand_aliases)}
            if len(with_first) == 1:
                return next(iter(with_first))

        # Pass 2: each individual token as candidate lastname.
        # Catches cases where bioguide stores a SHORTER last than the input (e.g.,
        # input="Ashley Hinson Arenholz", bioguide last="Hinson"). All non-last
        # tokens go into first-name aliases.
        for j, tok in enumerate(combined_parts):
            cand_last = tok
            if cand_last in seen_lasts:
                continue
            seen_lasts.add(cand_last)

            other_tokens = [t for k, t in enumerate(combined_parts) if k != j]
            cand_aliases = set()
            for t in other_tokens:
                cand_aliases.add(t)
                cand_aliases |= NICKNAME_MAP.get(t, set())

            rows = _term_filter(self._primary.get((chamber, cand_last), []))
            with_first = {r.bioguide_id for r in rows if _alias_overlap_with(r, cand_aliases)}
            if len(with_first) == 1:
                return next(iter(with_first))

        # All stages failed → strict NULL
        return None

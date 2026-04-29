"""Seed alt_political reference data.

  asset_type_codes          48 (House Clerk)
  lda_issue_codes           79 (LDA constants)
  lda_government_entities   257 (LDA constants)
  committee_sector_map      21 (manual — encodes alpha thesis from
                                 docs/data-sources/04 Part 5)

All rows seeded with country_code='US'.

The asset/lobby reference data is loaded from committed YAMLs in
configs/reference/. The committee_sector_map is inline below since the
manual encoding lives in the migration history.

Revision ID: 008
Revises: 007
Create Date: 2026-04-30
"""

from pathlib import Path
from typing import Sequence, Union

import yaml
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "alt_political"
REPO_ROOT = Path(__file__).resolve().parents[2]
REF_DIR = REPO_ROOT / "configs" / "reference"

# ---------------------------------------------------------------------------
# committee_sector_map — manual encoding from docs/data-sources/04 Part 5
# (country_code, committee_id, gics_code, signal_strength, rationale)
# ---------------------------------------------------------------------------
SECTOR_MAP = [
    # Senate — extreme signal (broad jurisdiction)
    ("US", "SSFI", "*",      "extreme",     "Finance — Medicare pricing, tax rates, REITs"),
    ("US", "SSAP", "*",      "extreme",     "Appropriations — controls 12 sub-agencies"),
    # Senate — very_high
    ("US", "SSAS", "201010", "very_high",   "Armed Services — Aerospace & Defense"),
    ("US", "SSBK", "401010", "very_high",   "Banking — Diversified Banks"),
    ("US", "SSBK", "402010", "very_high",   "Banking — Capital Markets"),
    ("US", "SSBK", "601010", "very_high",   "Banking — REITs"),
    # Senate — high
    ("US", "SSEG", "10",     "high",        "Energy & Natural Resources — Energy sector"),
    ("US", "SSEG", "551010", "high",        "Energy & Natural Resources — Utilities"),
    ("US", "SSHR", "352020", "high",        "Health (HELP) — Pharma"),
    ("US", "SSHR", "351010", "high",        "Health (HELP) — Health Care Equipment"),
    ("US", "SSCM", "501010", "high",        "Commerce — Telecom"),
    ("US", "SSCM", "201020", "high",        "Commerce — Airlines / Transport"),
    ("US", "SSEV", "151020", "high",        "Environment & Public Works — Construction Materials"),
    ("US", "SSAF", "302020", "medium_high", "Agriculture — Food/Ag commodities"),
    ("US", "SSJU", "5020",   "medium",      "Judiciary — Big Tech antitrust"),
    ("US", "SLIN", "201010", "medium",      "Intelligence — Defense/intel contractors"),
    # House parallels
    ("US", "HSWM", "*",      "extreme",     "Ways and Means — Tax + Medicare"),
    ("US", "HSAP", "*",      "extreme",     "Appropriations"),
    ("US", "HSAS", "201010", "very_high",   "Armed Services — Defense"),
    ("US", "HSBA", "40",     "very_high",   "Financial Services"),
    ("US", "HSIF", "*",      "very_high",   "Energy and Commerce — broadest jurisdiction"),
]


def _load_yaml(name: str) -> list[dict]:
    path = REF_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Reference YAML missing: {path}. "
            f"Re-run `python -c \"...\"` snapshot from the API or copy from "
            f"a prior commit."
        )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def upgrade() -> None:
    # ---- asset_type_codes (48) ----
    asset_codes = _load_yaml("house_asset_type_codes.yaml")
    rows = [
        {
            "country_code": "US",
            "code": code,
            "name": meta["name"],
            "equity_ticker_likely": bool(meta.get("equity_ticker_likely", False)),
        }
        for code, meta in asset_codes["codes"].items()
    ]
    op.bulk_insert(
        op.inline_literal,  # placeholder — pattern replaced below
        rows,
    ) if False else None  # noqa: keep import-valid; use raw SQL below

    # The op.bulk_insert API needs a Table object; easier here to use raw SQL
    # for compactness given we already have the row dicts.
    if rows:
        values = ",".join(
            f"('{r['country_code']}', '{r['code']}', "
            f"$${r['name']}$$, {str(r['equity_ticker_likely']).lower()})"
            for r in rows
        )
        op.execute(
            f"INSERT INTO {SCHEMA}.asset_type_codes "
            f"(country_code, code, name, equity_ticker_likely) "
            f"VALUES {values} "
            f"ON CONFLICT (country_code, code) DO NOTHING"
        )

    # ---- lda_issue_codes (79) ----
    issue_codes = _load_yaml("lda_issue_codes.yaml")
    if issue_codes:
        values = ",".join(
            f"('US', '{r['code']}', $${r['name']}$$)" for r in issue_codes
        )
        op.execute(
            f"INSERT INTO {SCHEMA}.lda_issue_codes (country_code, code, name) "
            f"VALUES {values} "
            f"ON CONFLICT (country_code, code) DO NOTHING"
        )

    # ---- lda_government_entities (257) ----
    entities = _load_yaml("lda_government_entities.yaml")
    if entities:
        values = ",".join(
            f"('US', {r['entity_id']}, $${r['name']}$$)" for r in entities
        )
        op.execute(
            f"INSERT INTO {SCHEMA}.lda_government_entities (country_code, entity_id, name) "
            f"VALUES {values} "
            f"ON CONFLICT (country_code, entity_id) DO NOTHING"
        )

    # ---- committee_sector_map (21) ----
    # NOTE: depends on committees rows existing for the FK to validate. Since
    # this seed runs before any committee ingestion, we create placeholder
    # committee rows for the keys we reference. Production ingestion will
    # update the placeholder rows with real chamber/name when YAML loads.
    committee_ids_referenced = sorted({row[1] for row in SECTOR_MAP})
    placeholders = ",".join(
        f"('US', '{cid}', "
        f"'{'senate' if cid.startswith('S') else 'house'}', "
        f"'PLACEHOLDER {cid}', true)"
        for cid in committee_ids_referenced
    )
    op.execute(
        f"INSERT INTO {SCHEMA}.committees "
        f"(country_code, committee_id, chamber, name, is_current) "
        f"VALUES {placeholders} "
        f"ON CONFLICT (country_code, committee_id) DO NOTHING"
    )

    sector_values = ",".join(
        f"('{cc}', '{cid}', '{gics}', '{strength}', $${rationale}$$)"
        for cc, cid, gics, strength, rationale in SECTOR_MAP
    )
    op.execute(
        f"INSERT INTO {SCHEMA}.committee_sector_map "
        f"(country_code, committee_id, gics_code, signal_strength, rationale) "
        f"VALUES {sector_values} "
        f"ON CONFLICT (country_code, committee_id, gics_code) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.committee_sector_map WHERE country_code='US'")
    op.execute(f"DELETE FROM {SCHEMA}.lda_government_entities WHERE country_code='US'")
    op.execute(f"DELETE FROM {SCHEMA}.lda_issue_codes WHERE country_code='US'")
    op.execute(f"DELETE FROM {SCHEMA}.asset_type_codes WHERE country_code='US'")
    # leave committees placeholder rows; they're harmless and ingestion overwrites

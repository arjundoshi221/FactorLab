"""Common ingest-result types so `us_political_daily.py` can read uniform
attributes off every source's return value.

Each source's existing `IngestResult` dataclass should subclass `BaseIngestResult`
and add source-specific fields. The orchestrator only reads the base fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BaseIngestResult:
    """Uniform shape across all political sources.

    Required (always populated):
      source           — one of `_constants.Source.*`
      attempted        — total upstream items considered (rows in JSON, PTRs in index, etc.)
      inserted         — total rows written to legislator_trades / gov_contracts / etc.
      bioguide_resolved / unresolved — for trade sources only; 0 for non-trade

    Optional:
      errors           — count of upstream items that failed parse/fetch
      notes            — list of human-readable breadcrumbs (e.g. "paper-scan 2024-01-12 Donalds")
    """

    source: str = ""
    attempted: int = 0
    inserted: int = 0
    bioguide_resolved: int = 0
    bioguide_unresolved: int = 0
    errors: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One-line summary for orchestrator logs."""
        return (
            f"{self.source}: attempted={self.attempted} inserted={self.inserted} "
            f"bg_ok={self.bioguide_resolved} bg_null={self.bioguide_unresolved} "
            f"errors={self.errors}"
        )

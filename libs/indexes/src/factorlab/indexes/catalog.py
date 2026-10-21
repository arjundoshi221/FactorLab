"""The US index catalogue (``catalog_us.yaml``, package data): tracked series and definitions.

Shipped inside the package so every image that computes or serves indexes reads the
same file; ``deploy/us-universe.yaml`` must list its benchmark symbols (tested).
"""

from __future__ import annotations

from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from factorlab.indexes.engine import IndexDefinition


class TrackedSeries(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    name: str
    group: Literal["index", "market_etf"]


class SectorEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sector_id: str = Field(pattern=r"^[0-9]{2}$")
    name: str
    cw_etf: str
    ew_etf: str


class IndexCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    tracked: tuple[TrackedSeries, ...]
    base_date: date
    base_value: float = Field(gt=0)
    universe: str
    classification: str = "gics"
    coverage_gate: float = Field(gt=0, le=1)
    sectors: tuple[SectorEntry, ...]

    def definitions(self) -> list[IndexDefinition]:
        """Two composites over the whole universe, then ew/cw for every sector."""
        out = [
            IndexDefinition(
                f"{self.universe}_{w}",
                f"Russell 3000 proxy ({'equal' if w == 'ew' else 'cap'} weight)",
                w,
                None,
                self.base_value,
                self.coverage_gate,
            )
            for w in ("ew", "cw")
        ]
        for sector in self.sectors:
            for w in ("ew", "cw"):
                out.append(
                    IndexDefinition(
                        sector_code(sector.sector_id, w),
                        f"{sector.name} ({'equal' if w == 'ew' else 'cap'} weight)",
                        w,
                        sector.sector_id,
                        self.base_value,
                        self.coverage_gate,
                    )
                )
        return out

    def benchmark_symbols(self) -> list[str]:
        symbols = [t.symbol for t in self.tracked]
        symbols += [s.cw_etf for s in self.sectors] + [s.ew_etf for s in self.sectors]
        return list(dict.fromkeys(symbols))


def sector_code(sector_id: str, weighting: str) -> str:
    return f"us_gics_{sector_id}_{weighting}"


def load_catalog(path: str | Path | None = None) -> IndexCatalog:
    text = (
        Path(path).read_text(encoding="utf-8")
        if path
        else files("factorlab.indexes").joinpath("catalog_us.yaml").read_text(encoding="utf-8")
    )
    payload = yaml.safe_load(text)
    return IndexCatalog.model_validate(payload)


__all__ = ["IndexCatalog", "SectorEntry", "TrackedSeries", "load_catalog", "sector_code"]

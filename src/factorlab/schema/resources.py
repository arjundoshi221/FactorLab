"""Location of the schema package's SQL (shipped as package data)."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def v2_sql_dir() -> Path:
    """Directory of the checksum-tracked ClickHouse v2 wave files."""
    return Path(str(files("factorlab.schema").joinpath("sql", "clickhouse", "v2")))

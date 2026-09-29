"""Query-result helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def rows(result: Any) -> list[dict[str, Any]]:
    """Query result -> row dicts, with naive ClickHouse datetimes marked as UTC."""
    return [{key: value.replace(tzinfo=timezone.utc)
             if isinstance(value, datetime) and value.tzinfo is None else value
             for key, value in zip(result.column_names, row, strict=True)}
            for row in result.result_rows]


__all__ = ["rows"]

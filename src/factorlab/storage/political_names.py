"""Congressional name keys and committee-role normalization shared by the v2 political writer."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


def build_name_resolver(
    legislators: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], str]:
    resolver = {}
    for legislator in legislators:
        name = legislator["name"]
        resolver[_name_key(name.get("first", ""), name.get("last", ""))] = (
            legislator["id"]["bioguide"]
        )
    return resolver


def resolve_filing_bioguide(
    filing: Mapping[str, Any],
    resolver: Mapping[tuple[str, str], str],
) -> str | None:
    return resolver.get(
        _name_key(
            filing["filer_first_name"],
            filing["filer_last_name"],
        )
    )


def _name_key(first_name: str, last_name: str) -> tuple[str, str]:
    first = re.sub(r"[^a-z]", "", first_name.lower().split()[0])
    last = re.sub(r"[^a-z]", "", last_name.lower())
    return first, last


def _normalize_role(title: str) -> str:
    normalized = title.lower().replace("-", " ").strip()
    if "ranking" in normalized:
        return "ranking_member"
    if "vice" in normalized and "chair" in normalized:
        return "vice_chair"
    if "chair" in normalized:
        return "chair"
    return "member"


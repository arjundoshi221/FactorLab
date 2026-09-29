"""congress-legislators (unitedstates.github.io) -> ``ref.legislators`` (docs/architecture/07 §6).

Three public JSON files, fetched in dependency order as three units of one run:
legislators, then committees, then memberships (which reference both).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, ClassVar

import requests
from pydantic import Field

from factorlab.ingest.bindings import ProviderSettings
from factorlab.ingest.datasets import Capabilities, EntityRef, FetchUnit, ReferenceRequest
from factorlab.ingest.datasets.political import (
    CommitteeRecord,
    LegislatorRecord,
    LegislatorRow,
    LegislatorTerm,
    MembershipRecord,
    congress_number,
)
from factorlab.ingest.errors import NormalizationError, TransientError
from factorlab.ingest.provider import RawCapture
from factorlab.ingest.transport import raise_for_status

FILES = ("legislators", "committees", "memberships")
_CHAMBER = {"rep": "house", "house": "house", "sen": "senate", "senate": "senate"}


class CongressLegislatorsSettings(ProviderSettings):
    base_url: str = "https://unitedstates.github.io/congress-legislators"
    files: dict[str, str] = Field(
        default_factory=lambda: {
            "legislators": "legislators-current.json",
            "committees": "committees-current.json",
            "memberships": "committee-membership-current.json",
        }
    )
    timeout: float = 60.0


def normalize_role(title: Any) -> str:
    """Committee title -> canonical role (same rule as ``political_names._normalize_role``)."""
    text = str(title or "").lower().replace("-", " ").strip()
    if "ranking" in text:
        return "ranking_member"
    if "vice" in text and "chair" in text:
        return "vice_chair"
    if "chair" in text:
        return "chair"
    return "member"


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _legislators(items: Any) -> list[LegislatorRecord]:
    if not isinstance(items, list):
        raise NormalizationError("legislators file is not a JSON array")
    records = []
    for item in items:
        bioguide = str(((item or {}).get("id") or {}).get("bioguide") or "")
        name = item.get("name") or {}
        terms = tuple(
            LegislatorTerm(
                chamber=_CHAMBER[str(term.get("type", "")).lower()],  # type: ignore[arg-type]
                state=str(term.get("state") or ""),
                start=date.fromisoformat(term["start"]),
                end=date.fromisoformat(term["end"]),
                party=str(term.get("party") or ""),
                district=_int(term.get("district")),
                seat_class=_int(term.get("class")),
            )
            for term in item.get("terms") or []
            if str(term.get("type", "")).lower() in _CHAMBER
        )
        if not bioguide or not terms:
            continue
        official = name.get("official_full") or " ".join(
            str(v)
            for v in (name.get("first"), name.get("middle"), name.get("last"), name.get("suffix"))
            if v
        )
        records.append(
            LegislatorRecord(
                entity=EntityRef("bioguide", bioguide, "person_legislator", official),
                first_name=str(name.get("first") or ""),
                last_name=str(name.get("last") or ""),
                terms=terms,
            )
        )
    return records


def _committees(items: Any, congress: int) -> list[CommitteeRecord]:
    if not isinstance(items, list):
        raise NormalizationError("committees file is not a JSON array")
    records = []
    for committee in items:
        parent = str(committee["thomas_id"])
        chamber = str(committee.get("type") or "").lower()
        records.append(
            CommitteeRecord(
                parent,
                str(committee["name"]),
                chamber,
                congress,
                jurisdiction=str(committee.get("jurisdiction") or ""),
                url=str(committee.get("url") or ""),
            )
        )
        for sub in committee.get("subcommittees") or []:
            records.append(
                CommitteeRecord(
                    parent + str(sub["thomas_id"]),
                    str(sub["name"]),
                    chamber,
                    congress,
                    parent_code=parent,
                    jurisdiction=str(sub.get("jurisdiction") or ""),
                    url=str(sub.get("url") or ""),
                )
            )
    return records


def _memberships(items: Any, congress: int) -> list[MembershipRecord]:
    if not isinstance(items, Mapping):
        raise NormalizationError("committee membership file is not a JSON object")
    records = []
    for code, members in items.items():
        for member in members or []:
            bioguide = member.get("bioguide")
            if not bioguide:
                continue
            records.append(
                MembershipRecord(
                    str(code),
                    EntityRef("bioguide", str(bioguide), "person_legislator"),
                    normalize_role(member.get("title")),
                    congress,  # type: ignore[arg-type]
                    party_side=str(member.get("party") or ""),
                    rank=_int(member.get("rank")) or 0,
                )
            )
    return records


class CongressLegislators:
    provider: ClassVar[str] = "congress_legislators"
    dataset: ClassVar[str] = "ref.legislators"
    capabilities: ClassVar[Capabilities] = Capabilities(markets=frozenset({"USA"}))
    settings_model: ClassVar[type[CongressLegislatorsSettings]] = CongressLegislatorsSettings

    def __init__(
        self,
        settings: CongressLegislatorsSettings,
        *,
        instance: str = "congress_legislators",
        session: requests.Session | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.instance = instance
        self.session = session or requests.Session()
        self._clock = clock

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        wanted = set(request.params.get("files") or FILES)
        return [
            FetchUnit(f"file:{name}", f"{self.instance}:{name}", params={"file": name})
            for name in FILES
            if name in wanted
        ]  # dependency order is fixed

    def fetch(self, unit: FetchUnit) -> RawCapture:
        name = str(unit.params["file"])
        url = f"{self.settings.base_url.rstrip('/')}/{self.settings.files[name]}"
        try:
            response = self.session.get(url, timeout=self.settings.timeout)
        except requests.RequestException as exc:
            raise TransientError(
                f"congress-legislators request failed: {type(exc).__name__}"
            ) from exc
        raise_for_status("congress-legislators", response.status_code, response.headers, name)
        return RawCapture(
            body=response.content,
            request_key=unit.name,
            transport="http",
            fetched_at=self._clock(),
            source_url=url,
            content_type=response.headers.get("Content-Type", "application/json"),
            status_code=response.status_code,
            headers=dict(response.headers),
            metadata={"file": name},
        )

    def normalize(self, capture: RawCapture) -> Sequence[LegislatorRow]:
        try:
            payload = json.loads(capture.body)
        except ValueError as exc:
            raise NormalizationError(f"{capture.request_key}: body is not JSON") from exc
        name = capture.metadata.get("file")
        congress = congress_number(capture.fetched_at.date())
        if name == "legislators":
            return _legislators(payload)
        if name == "committees":
            return _committees(payload, congress)
        if name == "memberships":
            return _memberships(payload, congress)
        raise NormalizationError(f"{capture.request_key}: unknown file {name!r}")


__all__ = ["FILES", "CongressLegislators", "CongressLegislatorsSettings", "normalize_role"]

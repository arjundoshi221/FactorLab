"""Fixture helpers for source conformance (docs/architecture/07 §13.1).

Layout per registered ``(provider, dataset)``::

    tests/fixtures/providers/<provider>/<dataset>/<case>.capture.json   serialized RawCapture
    tests/fixtures/providers/<provider>/<dataset>/<case>.expected.json  records normalize() returns
    tests/fixtures/providers/<provider>/<dataset>/requests.json         requests for plan() checks

Regenerate expectations after an intentional normalization change, then review the diff::

    python -m tests.contracts.kit regen <provider>
"""

from __future__ import annotations

import base64
import dataclasses
import json
import sys
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from factorlab.shared.ingest.bindings import Binding
from factorlab.shared.ingest.datasets import (
    DATASETS,
    BarRequest,
    InstrumentRef,
    ReferenceRequest,
    SeriesWindow,
    SnapshotRequest,
)
from factorlab.shared.ingest.datasets.fundamentals import CompanyRef, CompanyRequest
from factorlab.shared.ingest.datasets.political import (
    FilingRef,
    FilingsRequest,
)
from factorlab.shared.ingest.provider import RawCapture

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"


def dump_capture(capture: RawCapture) -> dict[str, Any]:
    return {
        "request_key": capture.request_key, "transport": capture.transport,
        "fetched_at": capture.fetched_at.isoformat(), "source_url": capture.source_url,
        "content_type": capture.content_type, "status_code": capture.status_code,
        "headers": dict(capture.headers), "metadata": dict(capture.metadata),
        "body_b64": base64.b64encode(capture.body).decode("ascii"),
    }


def load_capture(data: Mapping[str, Any]) -> RawCapture:
    return RawCapture(
        body=base64.b64decode(data["body_b64"]), request_key=data["request_key"],
        transport=data["transport"], fetched_at=datetime.fromisoformat(data["fetched_at"]),
        source_url=data.get("source_url", ""),
        content_type=data.get("content_type", "application/json"),
        status_code=data.get("status_code"), headers=data.get("headers", {}),
        metadata=data.get("metadata", {}),
    )


def jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"_type": type(value).__name__,
                **{f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | frozenset | set):
        return [jsonable(v) for v in value]
    return value


def _ref(data: Mapping[str, Any]) -> InstrumentRef:
    return InstrumentRef(**{k: v for k, v in data.items() if k != "_type"})


def load_request(dataset_id: str, data: Mapping[str, Any]) -> Any:
    spec = DATASETS[dataset_id]
    if spec.request_type is ReferenceRequest:
        return ReferenceRequest(market=data["market"], params=data.get("params", {}))
    if spec.request_type is CompanyRequest:
        return CompanyRequest(market=data["market"], params=data.get("params", {}),
                              companies=tuple(CompanyRef(**c) for c in data["companies"]))
    if spec.request_type is FilingsRequest:
        return FilingsRequest(market=data["market"], params=data.get("params", {}),
                              filings=tuple(FilingRef(**f) for f in data["filings"]))
    if spec.request_type is SnapshotRequest:
        return SnapshotRequest(market=data["market"], params=data.get("params", {}))
    if spec.request_type is BarRequest:
        return BarRequest(
            market=data["market"], resolution=data["resolution"],
            series=tuple(SeriesWindow(_ref(w["instrument"]), datetime.fromisoformat(w["start"]),
                                      datetime.fromisoformat(w["end"]))
                         for w in data["series"]),
            params=data.get("params", {}),
        )
    raise NotImplementedError(f"no request loader for {dataset_id}")


def cases(provider: str, dataset_id: str) -> list[tuple[str, RawCapture, Any]]:
    folder = FIXTURES / provider / dataset_id
    found = []
    for path in sorted(folder.glob("*.capture.json")):
        name = path.name.removesuffix(".capture.json")
        expected_path = folder / f"{name}.expected.json"
        expected = json.loads(expected_path.read_text(encoding="utf-8")) \
            if expected_path.exists() else None
        found.append((name, load_capture(json.loads(path.read_text(encoding="utf-8"))), expected))
    return found


def requests(provider: str, dataset_id: str) -> list[Any]:
    path = FIXTURES / provider / dataset_id / "requests.json"
    if not path.exists():
        return []
    return [load_request(dataset_id, item) for item in json.loads(path.read_text("utf-8"))]


def binding_for(provider: str, dataset_id: str, cls: type) -> Binding:
    market = min(cls.capabilities.markets)
    resolution = min(cls.capabilities.resolutions) \
        if DATASETS[dataset_id].has_resolution else None
    return Binding(dataset=dataset_id, market=market, provider=provider, resolution=resolution)


def regen(provider: str) -> None:
    from factorlab.shared.ingest.registry import load_providers, registered, source_for

    load_providers()
    for (name, dataset_id), cls in sorted(registered().items()):
        if name != provider:
            continue
        source = source_for(binding_for(name, dataset_id, cls))
        for case, capture, _ in cases(name, dataset_id):
            out = FIXTURES / name / dataset_id / f"{case}.expected.json"
            out.write_text(json.dumps(jsonable(list(source.normalize(capture))), indent=1)
                           + "\n", encoding="utf-8")
            print(f"wrote {out.relative_to(FIXTURES)}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "regen":
        sys.exit("usage: python -m tests.contracts.kit regen <provider>")
    regen(sys.argv[2])

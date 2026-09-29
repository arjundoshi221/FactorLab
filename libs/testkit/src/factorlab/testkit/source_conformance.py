"""Source conformance for one provider (docs/architecture/07 §13.1).

A provider cannot ship a source without recorded fixtures that prove:
``normalize`` is deterministic and offline, its records are valid for the
dataset, and ``plan`` honours the declared capabilities.

Each provider's ``tests/test_conformance.py`` pulls the suite in with::

    from factorlab.testkit.source_conformance import conformance_tests

    globals().update(conformance_tests("upstox"))
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import requests as http

from factorlab.ingest.datasets import DATASETS
from factorlab.ingest.registry import load_providers, registered, source_for
from factorlab.testkit import conformance as kit


def _source(provider: str, dataset_id: str, cls: type) -> Any:
    return source_for(kit.binding_for(provider, dataset_id, cls))


def _primary_refs(record: Any) -> Iterator[Any]:
    for name in ("ref", "instrument", "contract"):
        if hasattr(record, name):
            yield getattr(record, name)


def conformance_tests(provider: str) -> dict[str, Callable[..., Any]]:
    """The conformance tests (and their ``offline`` fixture) for *provider*'s sources."""
    load_providers([provider])
    sources = sorted((key, cls) for key, cls in registered().items() if key[0] == provider)
    assert sources, f"{provider} registers no dataset sources"
    ids = [dataset for (_, dataset), _ in sources]

    @pytest.fixture
    def offline(monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("normalize() must not touch the network")

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(http.Session, "request", refuse)

    @pytest.mark.parametrize(("key", "cls"), sources, ids=ids)
    def test_every_source_has_fixtures(key: tuple[str, str], cls: type) -> None:
        _, dataset_id = key
        assert kit.cases(provider, dataset_id), (
            f"add captures under {kit.fixtures_dir(provider) / dataset_id}")
        if DATASETS[dataset_id].request_type is not None:
            assert kit.requests(provider, dataset_id), f"add {dataset_id}/requests.json"

    @pytest.mark.parametrize(("key", "cls"), sources, ids=ids)
    def test_normalize_matches_fixtures_offline_and_deterministically(
            key: tuple[str, str], cls: type, offline: None) -> None:
        _, dataset_id = key
        source = _source(provider, dataset_id, cls)
        spec = DATASETS[dataset_id]
        for name, capture, expected in kit.cases(provider, dataset_id):
            first = list(source.normalize(capture))
            assert kit.jsonable(first) == kit.jsonable(list(source.normalize(capture))), name
            assert expected is not None, (
                f"{name}: run `python -m factorlab.testkit.conformance regen {provider}`")
            assert kit.jsonable(first) == expected, f"{name}: normalize output changed"
            for record in first:
                assert isinstance(record, spec.record_types), name
                for ref in _primary_refs(record):
                    if spec.requires_alias:
                        assert ref.has_alias, f"{name}: records must carry the provider alias"
                    if ref.has_alias and cls.capabilities.alias_kind:
                        assert ref.alias_kind == cls.capabilities.alias_kind, name
                if spec.has_resolution:
                    assert record.resolution in cls.capabilities.resolutions, name

    @pytest.mark.parametrize(("key", "cls"), sources, ids=ids)
    def test_plan_honours_capabilities(key: tuple[str, str], cls: type) -> None:
        _, dataset_id = key
        source = _source(provider, dataset_id, cls)
        caps = cls.capabilities
        for request in kit.requests(provider, dataset_id):
            units = list(source.plan(request))
            assert units, f"{dataset_id}: a non-empty request planned no units"
            assert len({unit.name for unit in units}) == len(units), "unit names must be unique"
            for unit in units:
                assert unit.source_channel.startswith(f"{source.instance}:"), unit
                assert len(unit.instruments) <= caps.max_batch, unit.name
                if caps.max_lookback and unit.start and unit.end:
                    assert unit.end - unit.start <= caps.max_lookback, unit.name

    return {
        "offline": offline,
        "test_every_source_has_fixtures": test_every_source_has_fixtures,
        "test_normalize_matches_fixtures_offline_and_deterministically":
            test_normalize_matches_fixtures_offline_and_deterministically,
        "test_plan_honours_capabilities": test_plan_honours_capabilities,
    }

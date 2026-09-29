"""Source conformance, parametrised over every registered source (docs/architecture/07 §13.1).

A provider cannot ship a source without recorded fixtures that prove:
``normalize`` is deterministic and offline, its records are valid for the
dataset, and ``plan`` honours the declared capabilities.
"""

from __future__ import annotations

import socket

import pytest
import requests as http

from factorlab.shared.ingest.datasets import DATASETS
from factorlab.shared.ingest.registry import load_providers, registered, source_for
from tests.contracts import kit

load_providers()
SOURCES = sorted(registered().items())
IDS = [f"{provider}/{dataset}" for (provider, dataset), _ in SOURCES]


@pytest.fixture
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("normalize() must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.Session, "request", refuse)


def _source(provider, dataset_id, cls):
    return source_for(kit.binding_for(provider, dataset_id, cls))


def _primary_refs(record):
    for name in ("ref", "instrument", "contract"):
        if hasattr(record, name):
            yield getattr(record, name)


@pytest.mark.parametrize(("key", "cls"), SOURCES, ids=IDS)
def test_every_source_has_fixtures(key, cls):
    provider, dataset_id = key
    assert kit.cases(provider, dataset_id), (
        f"add captures under tests/fixtures/providers/{provider}/{dataset_id}/")
    if DATASETS[dataset_id].request_type is not None:
        assert kit.requests(provider, dataset_id), f"add {provider}/{dataset_id}/requests.json"


@pytest.mark.parametrize(("key", "cls"), SOURCES, ids=IDS)
def test_normalize_matches_fixtures_offline_and_deterministically(key, cls, offline):
    provider, dataset_id = key
    source = _source(provider, dataset_id, cls)
    spec = DATASETS[dataset_id]
    for name, capture, expected in kit.cases(provider, dataset_id):
        first = list(source.normalize(capture))
        assert kit.jsonable(first) == kit.jsonable(list(source.normalize(capture))), name
        assert expected is not None, f"{name}: run `python -m tests.contracts.kit regen {provider}`"
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


@pytest.mark.parametrize(("key", "cls"), SOURCES, ids=IDS)
def test_plan_honours_capabilities(key, cls):
    provider, dataset_id = key
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

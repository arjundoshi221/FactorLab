"""Bindings, provider settings and the source registry (docs/architecture/07 §9)."""

from __future__ import annotations

from datetime import timedelta

import pytest
import yaml
from pydantic import Field

from factorlab.ingest.bindings import (
    Binding,
    BindingError,
    BindingsFile,
    ProviderSettings,
    load_bindings,
    load_provider_settings,
    validate_bindings,
)
from factorlab.ingest.datasets import Capabilities
from factorlab.ingest.registry import Registry, RegistryError


class FakeSettings(ProviderSettings):
    base_url: str = "https://default.invalid"
    rate_limits: dict[str, int] = Field(default_factory=dict)
    token_env: str = "FAKE_TOKEN"


class FakeBars:
    provider = "fake"
    dataset = "market.bars"
    capabilities = Capabilities(markets=frozenset({"IND"}), resolutions=frozenset({"1min"}),
                                alias_kind="fake_key", max_lookback=timedelta(days=30))
    settings_model = FakeSettings

    def __init__(self, settings, *, instance):
        self.settings = settings
        self.instance = instance

    def plan(self, request):
        return ()

    def fetch(self, unit):
        raise NotImplementedError

    def normalize(self, capture):
        return ()


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _bindings(*items):
    return {"version": 1, "bindings": list(items)}


BARS = {"dataset": "market.bars", "market": "IND", "resolution": "1min", "provider": "fake"}


def test_bindings_file_rejects_inconsistent_config(tmp_path):
    ok = load_bindings(_write(tmp_path, "b.yaml", _bindings(BARS)))
    assert ok.bindings[0].instance_name == "fake"
    assert ok.bindings[0].pipeline == "market.bars.1min:fake"
    bad_cases = [
        _bindings({**BARS, "resolution": None}),                      # bars need a resolution
        _bindings({**BARS, "dataset": "ref.listings"}),                # listings take none
        _bindings({**BARS, "dataset": "market.nope"}),
        _bindings(BARS, {**BARS}),                                     # duplicate instance
        _bindings(BARS, {**BARS, "instance": "fake_b"}),               # two primaries
        _bindings({**BARS, "role": "leader"}),
        _bindings({**BARS, "surprise": 1}),
    ]
    for index, case in enumerate(bad_cases):
        with pytest.raises(BindingError):
            load_bindings(_write(tmp_path, f"bad{index}.yaml", case))


def test_enabled_orders_primary_first_and_skips_disabled():
    file = BindingsFile.model_validate(_bindings(
        {**BARS, "instance": "b", "role": "secondary", "priority": 5},
        {**BARS, "instance": "c", "role": "disabled"},
        {**BARS, "priority": 50},
        {**BARS, "instance": "d", "role": "shadow", "priority": 1},
    ))
    assert [b.instance_name for b in file.enabled(dataset="market.bars")] == ["fake", "d", "b"]
    shadow = Binding(**{**BARS, "role": "shadow"})
    assert (shadow.reference_mode, shadow.source_name) == ("resolve_only", "fake:shadow")
    assert Binding(**{**BARS, "role": "secondary"}).reference_mode == "alias_only"
    assert (Binding(**BARS).reference_mode, Binding(**BARS).source_name) ==         ("authoritative", "fake")


def test_registry_validates_and_rejects_duplicates():
    registry = Registry()
    registry.register(FakeBars)
    registry.register(FakeBars)  # idempotent for the same class
    assert registry.providers() == {"fake"}

    class Clash(FakeBars):
        pass

    with pytest.raises(RegistryError):
        registry.register(Clash)

    class NoRes(FakeBars):
        provider = "nores"
        capabilities = Capabilities(markets=frozenset({"IND"}))

    with pytest.raises(RegistryError):
        registry.register(NoRes)

    class Unknown(FakeBars):
        provider = "unknown"
        dataset = "market.nope"

    with pytest.raises(RegistryError):
        registry.register(Unknown)
    with pytest.raises(RegistryError):
        registry.source_class("fake", "ref.listings")


def test_validate_bindings_against_capabilities(tmp_path):
    registry = Registry()
    registry.register(FakeBars)
    validate_bindings(BindingsFile.model_validate(_bindings(BARS)), registry)
    for case in ({**BARS, "market": "USA"}, {**BARS, "resolution": "daily"},
                 {**BARS, "provider": "ghost"}):
        with pytest.raises(BindingError):
            validate_bindings(BindingsFile.model_validate(_bindings(case)), registry)
    # disabled bindings are not validated against the registry
    validate_bindings(BindingsFile.model_validate(_bindings(
        {**BARS, "provider": "ghost", "role": "disabled"})), registry)


def test_provider_settings_merge_instance_overrides(tmp_path):
    _write(tmp_path, "sources/fake.yaml", {
        "name": "fake", "base_url": "https://api.invalid", "token_env": "FAKE_TOKEN",
        "rate_limits": {"per_second": 50, "per_minute": 500},
        "instances": {"fake_backup": {"token_env": "FAKE_BACKUP_TOKEN",
                                      "rate_limits": {"per_second": 5}}},
    })
    main = load_provider_settings("fake", FakeSettings, configs_dir=tmp_path)
    backup = load_provider_settings("fake", FakeSettings, instance="fake_backup",
                                    configs_dir=tmp_path)
    assert (main.instance, main.token_env, main.rate_limits["per_second"]) == \
        ("fake", "FAKE_TOKEN", 50)
    assert (backup.instance, backup.token_env) == ("fake_backup", "FAKE_BACKUP_TOKEN")
    assert backup.rate_limits == {"per_second": 5, "per_minute": 500}
    with pytest.raises(BindingError):
        load_provider_settings("fake", FakeSettings, instance="nope", configs_dir=tmp_path)
    # a provider without a settings file gets model defaults
    assert load_provider_settings("ghost", FakeSettings, configs_dir=tmp_path).base_url == \
        "https://default.invalid"


def test_registry_builds_source_with_instance_settings(tmp_path):
    _write(tmp_path, "sources/fake.yaml", {"instances": {"fake_b": {"token_env": "B"}}})
    registry = Registry()
    registry.register(FakeBars)
    source = registry.build(Binding(**{**BARS, "instance": "fake_b"}), configs_dir=tmp_path)
    assert (source.instance, source.settings.token_env) == ("fake_b", "B")

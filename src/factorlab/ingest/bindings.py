"""Bindings and provider settings (docs/architecture/07 §9.2-9.3).

``configs/ingestion/bindings.yaml`` decides which provider instance feeds which
dataset, in which role and at which priority. ``configs/sources/<provider>.yaml``
holds each provider's settings, with an ``instances`` block for duplicated
instances. Neither file holds secret values, only secret *names*.
"""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from factorlab.core.paths import config_dir
from factorlab.ingest.datasets import DATASETS
from factorlab.ingest.datasets.reference import ReferenceMode

if TYPE_CHECKING:
    from factorlab.ingest.registry import Registry

CONFIGS_DIR = config_dir()
BINDINGS_PATH = CONFIGS_DIR / "ingestion" / "bindings.yaml"

Role = Literal["primary", "secondary", "shadow", "disabled"]
WRITING_ROLES: frozenset[str] = frozenset({"primary", "secondary", "shadow"})


class BindingError(ValueError):
    """Bindings or provider settings are inconsistent with the catalogue or registry."""


class Binding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: str
    market: str
    provider: str
    instance: str | None = None
    resolution: str | None = None
    role: Role = "primary"
    priority: int = Field(default=100, ge=0)
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def instance_name(self) -> str:
        return self.instance or self.provider

    @property
    def key(self) -> tuple[str, str, str | None]:
        return (self.dataset, self.market, self.resolution)

    @property
    def writes(self) -> bool:
        return self.role in WRITING_ROLES

    @property
    def source_name(self) -> str:
        """Value written to ``source`` on archive, run and fact rows.

        A shadow binding writes under ``<provider>:shadow`` so its rows sit beside
        the incumbent's instead of replacing them: ``source`` is part of every fact
        table's sort key, so the same ``source`` would overwrite (07 §9.2).
        """
        return f"{self.provider}:shadow" if self.role == "shadow" else self.provider

    @property
    def reference_mode(self) -> ReferenceMode:
        """How a reference dataset may write for this role (07 §8.1).

        primary   -> ``authoritative``: mint identity and write attributes + aliases
        secondary -> ``alias_only``: attach aliases to identities that already exist
        shadow    -> ``resolve_only``: resolve and report, write nothing to ``ref.*``
        """
        return {"primary": "authoritative", "secondary": "alias_only"}.get(
            self.role, "resolve_only")  # type: ignore[return-value]

    @property
    def pipeline(self) -> str:
        suffix = f".{self.resolution}" if self.resolution else ""
        return f"{self.dataset}{suffix}:{self.instance_name}"


class BindingsFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    bindings: tuple[Binding, ...]

    @model_validator(mode="after")
    def _consistent(self) -> BindingsFile:
        errors: list[str] = []
        for binding in self.bindings:
            spec = DATASETS.get(binding.dataset)
            if spec is None:
                errors.append(f"unknown dataset {binding.dataset!r}")
                continue
            if spec.has_resolution and not binding.resolution:
                errors.append(f"{binding.pipeline}: {binding.dataset} needs a resolution")
            if not spec.has_resolution and binding.resolution:
                errors.append(f"{binding.pipeline}: {binding.dataset} takes no resolution")
        seen = Counter((b.key, b.instance_name) for b in self.bindings)
        errors += [f"duplicate instance {inst!r} for {key}" for (key, inst), n in seen.items()
                   if n > 1]
        primaries = Counter(b.key for b in self.bindings if b.role == "primary")
        errors += [f"more than one primary for {key}" for key, n in primaries.items() if n > 1]
        readable = Counter(
            (b.key, b.provider, b.priority) for b in self.bindings
            if b.role in ("primary", "secondary") and DATASETS.get(b.dataset) is not None
            and DATASETS[b.dataset].source_keyed)
        ranks = Counter((key, priority) for key, _, priority in readable)
        errors += [f"two providers share priority {priority} for {key}; *_best needs an order"
                   for (key, priority), n in ranks.items() if n > 1]
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def enabled(self, *, dataset: str | None = None, market: str | None = None,
                resolution: str | None = None) -> tuple[Binding, ...]:
        """Writing bindings, optionally filtered; primaries first, then by priority."""
        chosen = [
            b for b in self.bindings
            if b.writes and (dataset is None or b.dataset == dataset)
            and (market is None or b.market == market)
            and (resolution is None or b.resolution == resolution)
        ]
        return tuple(sorted(chosen, key=lambda b: (b.role != "primary", b.priority)))

    def for_providers(self, providers: Iterable[str]) -> BindingsFile:
        """The bindings a component can run: those whose provider it ships.

        The whole-file checks (one primary per key, distinct priorities) already
        ran when this file was loaded, so they still hold for the subset.
        """
        names = frozenset(providers)
        return self.model_copy(update={"bindings": tuple(
            b for b in self.bindings if b.provider in names)})


def load_bindings(path: Path | None = None) -> BindingsFile:
    target = path or BINDINGS_PATH
    with open(target, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    try:
        return BindingsFile.model_validate(raw)
    except ValueError as exc:
        raise BindingError(f"{target}: {exc}") from exc


def validate_bindings(bindings: BindingsFile, registry: Registry) -> None:
    """Fail fast when a binding names an unregistered source or unsupported scope."""
    errors: list[str] = []
    for binding in bindings.bindings:
        if binding.role == "disabled":
            continue
        try:
            cls = registry.source_class(binding.provider, binding.dataset)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        caps = cls.capabilities
        if binding.market not in caps.markets:
            errors.append(f"{binding.pipeline}: {binding.provider} does not serve {binding.market}")
        if binding.resolution and binding.resolution not in caps.resolutions:
            errors.append(
                f"{binding.pipeline}: {binding.provider} does not serve {binding.resolution}"
            )
    if errors:
        raise BindingError("; ".join(errors))


class ProviderSettings(BaseModel):
    """Base settings model; providers subclass it with the fields they read."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = ""
    instance: str = ""


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def provider_settings_data(provider: str, *, instance: str | None = None,
                           configs_dir: Path | None = None) -> dict[str, Any]:
    """Raw settings for one instance: the provider file merged with its instance override."""
    path = (configs_dir or CONFIGS_DIR) / "sources" / f"{provider}.yaml"
    data: dict[str, Any] = {}
    if path.exists():
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    instances = data.pop("instances", None) or {}
    name = instance or provider
    if name != provider:
        if name not in instances:
            raise BindingError(f"{path}: no instances.{name} block for instance {name!r}")
        data = _deep_merge(data, instances[name])
    data["name"] = provider
    data["instance"] = name
    return data


def load_provider_settings(provider: str, model: type[ProviderSettings] = ProviderSettings, *,
                           instance: str | None = None,
                           configs_dir: Path | None = None) -> ProviderSettings:
    return model.model_validate(
        provider_settings_data(provider, instance=instance, configs_dir=configs_dir)
    )


__all__ = [
    "BINDINGS_PATH",
    "CONFIGS_DIR",
    "Binding",
    "BindingError",
    "BindingsFile",
    "ProviderSettings",
    "Role",
    "load_bindings",
    "load_provider_settings",
    "provider_settings_data",
    "validate_bindings",
]

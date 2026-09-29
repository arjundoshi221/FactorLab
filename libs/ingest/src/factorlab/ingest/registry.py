"""Source-adapter registry (docs/architecture/07 §9.1).

Provider packages call :func:`register_source` from their ``__init__``. The
engine and orchestration obtain sources only through :func:`source_for`, so
they never import a concrete provider module (rule R4). Discovery is explicit:
each deployable component lists the providers it ships in its ``providers.py``
and passes them to :func:`load_providers`; nothing is found by scanning or
entry points, so a provider that is not listed cannot start writing.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factorlab.ingest.bindings import ProviderSettings, load_provider_settings
from factorlab.ingest.datasets import DATASETS, Capabilities

if TYPE_CHECKING:
    from factorlab.ingest.bindings import Binding
    from factorlab.ingest.datasets.common import DatasetSource


class RegistryError(ValueError):
    """A source class is malformed, duplicated, or not registered."""


_REQUIRED_METHODS = ("plan", "fetch", "normalize")


class Registry:
    def __init__(self) -> None:
        self._sources: dict[tuple[str, str], type] = {}

    def register(self, cls: type) -> type:
        """Register ``cls`` for its ``(provider, dataset)``; usable as a decorator."""
        provider = getattr(cls, "provider", None)
        dataset_id = getattr(cls, "dataset", None)
        if not isinstance(provider, str) or not provider or provider != provider.lower():
            raise RegistryError(f"{cls.__name__}.provider must be a non-empty lowercase str")
        if dataset_id not in DATASETS:
            raise RegistryError(f"{cls.__name__}.dataset {dataset_id!r} is not in the catalogue")
        if not isinstance(getattr(cls, "capabilities", None), Capabilities):
            raise RegistryError(f"{cls.__name__}.capabilities must be a Capabilities")
        missing = [name for name in _REQUIRED_METHODS if not callable(getattr(cls, name, None))]
        if missing:
            raise RegistryError(f"{cls.__name__} is missing {missing}")
        settings_model = getattr(cls, "settings_model", ProviderSettings)
        if not (isinstance(settings_model, type) and issubclass(settings_model, ProviderSettings)):
            raise RegistryError(f"{cls.__name__}.settings_model must subclass ProviderSettings")
        spec = DATASETS[dataset_id]
        if spec.has_resolution and not cls.capabilities.resolutions:
            raise RegistryError(f"{cls.__name__} serves {dataset_id} but declares no resolutions")
        key = (provider, dataset_id)
        if key in self._sources and self._sources[key] is not cls:
            raise RegistryError(f"{key} is already registered by {self._sources[key].__name__}")
        self._sources[key] = cls
        return cls

    def registered(self) -> Mapping[tuple[str, str], type]:
        return dict(self._sources)

    def providers(self) -> frozenset[str]:
        return frozenset(provider for provider, _ in self._sources)

    def source_class(self, provider: str, dataset_id: str) -> type:
        try:
            return self._sources[(provider, dataset_id)]
        except KeyError:
            raise RegistryError(f"no source registered for {provider!r} / {dataset_id!r}") from None

    def build(
        self,
        binding: Binding,
        *,
        settings: ProviderSettings | None = None,
        configs_dir: Path | None = None,
    ) -> DatasetSource[Any, Any]:
        """Instantiate the source for ``binding`` with its instance's settings."""
        cls = self.source_class(binding.provider, binding.dataset)
        if settings is None:
            settings = load_provider_settings(
                binding.provider,
                getattr(cls, "settings_model", ProviderSettings),
                instance=binding.instance_name,
                configs_dir=configs_dir,
            )
        return cls(settings, instance=binding.instance_name)


REGISTRY = Registry()


def register_source(cls: type) -> type:
    return REGISTRY.register(cls)


def registered() -> Mapping[tuple[str, str], type]:
    return REGISTRY.registered()


def source_for(binding: Binding, **kwargs: Any) -> DatasetSource[Any, Any]:
    return REGISTRY.build(binding, **kwargs)


def load_providers(names: Iterable[str]) -> tuple[str, ...]:
    """Import the listed provider packages so they register themselves."""
    loaded = tuple(names)
    for name in loaded:
        importlib.import_module(f"factorlab.sources.{name}")
    return loaded


__all__ = [
    "REGISTRY",
    "Registry",
    "RegistryError",
    "load_providers",
    "register_source",
    "registered",
    "source_for",
]

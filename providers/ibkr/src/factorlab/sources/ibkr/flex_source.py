"""IBKR ``broker.flex_activity`` dataset source: statement history via the Flex Web Service.

The Gateway's ``reqExecutions`` reaches back about a day; a saved Activity Flex
Query reaches back up to 365 days and also carries executed orders and cash
transactions. ``plan`` makes one unit (one Flex statement per run); ``fetch``
archives the statement XML as received; ``normalize`` reads only those bytes
(:mod:`factorlab.sources.ibkr.flex_normalize`).

Source channel is ``<instance>:flex_statement``. The broker writer keeps any
``exec_id`` the Gateway already wrote (it knows the order and client ids Flex
does not report), so backfilling over Gateway history is safe.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from pydantic import Field

from factorlab.ingest.bindings import ProviderSettings
from factorlab.ingest.datasets import Capabilities, FetchUnit, SnapshotRequest
from factorlab.ingest.provider import RawCapture
from factorlab.sources.ibkr.flex import FlexClient, FlexSettings
from factorlab.sources.ibkr.flex_normalize import normalize_flex_statement
from factorlab.sources.ibkr.normalize import DEFAULT_COUNTRY_CODE

FLEX_CHANNEL = "flex_statement"


class IbkrFlexSettings(ProviderSettings):
    timeout: float = Field(default=30.0, gt=0)
    poll_interval: float = Field(default=5.0, ge=0)
    poll_attempts: int = Field(default=24, ge=1)
    country_code: str = DEFAULT_COUNTRY_CODE


def _default_client(settings: IbkrFlexSettings) -> FlexClient:
    return FlexClient(
        FlexSettings(
            timeout=settings.timeout,
            poll_interval=settings.poll_interval,
            poll_attempts=settings.poll_attempts,
        )
    )


class IbkrFlexActivity:
    provider: ClassVar[str] = "ibkr"
    dataset: ClassVar[str] = "broker.flex_activity"
    capabilities: ClassVar[Capabilities] = Capabilities(markets=frozenset({"USA"}))
    settings_model: ClassVar[type[IbkrFlexSettings]] = IbkrFlexSettings

    def __init__(
        self,
        settings: IbkrFlexSettings,
        *,
        instance: str = "ibkr",
        client: Callable[[IbkrFlexSettings], FlexClient] = _default_client,
    ) -> None:
        self.settings = settings
        self.instance = instance
        self._client = client

    def plan(self, request: SnapshotRequest) -> Sequence[FetchUnit]:
        return [FetchUnit("flex:statement", f"{self.instance}:{FLEX_CHANNEL}")]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        return self._client(self.settings).fetch_statement()

    def normalize(self, capture: RawCapture) -> Sequence[Any]:
        return normalize_flex_statement(capture.body, country_code=self.settings.country_code)


__all__ = ["FLEX_CHANNEL", "IbkrFlexActivity", "IbkrFlexSettings"]

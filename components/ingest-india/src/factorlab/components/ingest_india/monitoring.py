"""Interruptible monitoring for the production collector's idle waits."""

from collections.abc import Iterator, Mapping, Sequence
from typing import Any
from uuid import UUID

from factorlab.runtime.monitor import monitor_client, monitoring_sleep
from factorlab.storage.v2_india import V2IndiaStorage


class _ProgressItems(Sequence[Mapping[str, Any]]):
    def __init__(self, items: Sequence[Mapping[str, Any]]) -> None:
        self.items = items

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: Any) -> Any:
        return self.items[index]

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        client = monitor_client()
        for item in self.items:
            yield item
            # Called after the caller completed this instrument, so stuck DB I/O
            # still stops heartbeat delivery.
            client.tick()


class MonitoredIndiaStorage(V2IndiaStorage):
    def sync_instruments(
        self,
        instruments: Sequence[Mapping[str, Any]],
        *,
        raw_id: UUID | None = None,
    ) -> dict[str, UUID]:
        return super().sync_instruments(_ProgressItems(instruments), raw_id=raw_id)


def sleep(seconds: float) -> None:
    from factorlab.components.ingest_india.legacy import daemon

    monitoring_sleep(seconds, should_stop=lambda: daemon.shutdown.triggered)

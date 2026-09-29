"""Contract qualification with a small in-memory cache.

``ib.qualifyContracts`` counts against pacing (probe 03 confirmed). Cache
by ``(symbol, sec_type, exchange, currency, primary_exchange)`` so the
same session doesn't re-qualify the same instrument. Persistent caching
into ``ref.security_aliases`` is Wave 1 / storage layer's job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ib_async import IB, Contract

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContractKey:
    symbol: str
    sec_type: str
    exchange: str
    currency: str
    primary_exchange: str = ""

    @classmethod
    def from_contract(cls, c: Contract) -> ContractKey:
        return cls(
            symbol=c.symbol,
            sec_type=c.secType,
            exchange=c.exchange or "SMART",
            currency=c.currency,
            primary_exchange=c.primaryExchange or "",
        )


class ContractCache:
    """Session-lifetime cache for qualified contracts."""

    def __init__(self) -> None:
        self._by_key: dict[ContractKey, Contract] = {}
        self._by_conid: dict[int, Contract] = {}

    def get(self, key: ContractKey) -> Contract | None:
        return self._by_key.get(key)

    def get_by_conid(self, conid: int) -> Contract | None:
        return self._by_conid.get(conid)

    def put(self, key: ContractKey, contract: Contract) -> None:
        self._by_key[key] = contract
        if contract.conId:
            self._by_conid[contract.conId] = contract

    def __len__(self) -> int:
        return len(self._by_key)


def qualify_contracts(
    ib: IB,
    contracts: list[Contract],
    *,
    cache: ContractCache | None = None,
) -> list[Contract]:
    """Return contracts with ``conId`` and ``primaryExchange`` filled in.

    Members present in ``cache`` are served locally; the rest go through
    ``ib.qualifyContracts`` in one batched call.
    """
    cache = cache if cache is not None else ContractCache()

    resolved: list[Contract] = []
    to_qualify: list[Contract] = []
    for c in contracts:
        key = ContractKey.from_contract(c)
        hit = cache.get(key)
        if hit is not None:
            resolved.append(hit)
        else:
            to_qualify.append(c)

    if to_qualify:
        log.debug("Qualifying %d contracts (%d cached)", len(to_qualify), len(resolved))
        qualified = ib.qualifyContracts(*to_qualify)
        for c in qualified:
            cache.put(ContractKey.from_contract(c), c)
            resolved.append(c)

    return resolved

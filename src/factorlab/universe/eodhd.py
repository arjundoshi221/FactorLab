"""EODHD Components universe adapter."""

from __future__ import annotations

from factorlab.universe.base import UniverseResolver


class EodhdUniverseResolver(UniverseResolver):
    provider = "eodhd"

    def __init__(self, *, indexes, validator, client):
        super().__init__(validator=validator)
        self.indexes = indexes
        self.client = client

    def _retrieve_index(self, name: str):
        symbol = self.indexes[name].symbol
        payload = self.client.get_index_components(symbol)
        if isinstance(payload, dict) and "Components" in payload:
            payload = payload["Components"]
        records = list(payload.values()) if isinstance(payload, dict) else payload
        if not isinstance(records, list) or not records:
            raise ValueError(f"{name} returned a malformed or empty Components response")
        symbols = []
        for record in records:
            if not isinstance(record, dict) or not record.get("Code"):
                raise ValueError(f"{name} returned a malformed constituent")
            symbols.append(record["Code"])
        self._provenance.setdefault("indexes", {})[name] = {
            "symbol": symbol,
            "raw_id": str(getattr(self.client, "last_raw_id", None) or "") or None,
        }
        return symbols

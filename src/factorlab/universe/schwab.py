"""Required Schwab equity validation for resolved universes."""

from __future__ import annotations

from factorlab.universe.models import ResolvedConstituent, normalize_symbol

SUPPORTED_EXCHANGES = {
    "A": "XASE", "AMEX": "XASE", "NYSE AMERICAN": "XASE",
    "N": "XNYS", "NYSE": "XNYS",
    "P": "ARCX", "NYSE ARCA": "ARCX",
    "Q": "XNAS", "NASDAQ": "XNAS", "NASDAQ GLOBAL MARKET": "XNAS",
    "NASDAQ GLOBAL SELECT": "XNAS", "NASDAQ CAPITAL MARKET": "XNAS",
    # Cboe's equities feed identifies Z as BZX; the equities MIC is BATS.
    "Z": "BATS", "CBOE": "BATS", "CBOE BZX": "BATS",
}


class SchwabEquityValidator:
    def __init__(self, client, *, batch_size: int = 50):
        self.client = client
        self.batch_size = batch_size

    def validate(self, symbols):
        requested = list(symbols)
        resolved = {}
        for offset in range(0, len(requested), self.batch_size):
            batch = requested[offset:offset + self.batch_size]
            provider_symbols = [value.replace("-", "/") for value in batch]
            payload, _raw_id = self.client.get(
                "/quotes", {"symbols": ",".join(provider_symbols)}
            )
            if not isinstance(payload, dict):
                raise TypeError("Schwab quotes response is malformed")
            invalid = payload.get("errors", {}).get("invalidSymbols", [])
            invalid = {normalize_symbol(value) for value in invalid}
            for provider_symbol, record in payload.items():
                if provider_symbol == "errors" or not isinstance(record, dict):
                    continue
                symbol = normalize_symbol(record.get("symbol") or provider_symbol)
                if symbol not in batch or symbol in invalid:
                    continue
                if record.get("assetMainType") != "EQUITY":
                    continue
                reference = record.get("reference") or {}
                exchange_value = str(
                    reference.get("exchangeName") or reference.get("exchange")
                    or record.get("exchangeName") or record.get("exchange") or ""
                ).strip().upper()
                exchange = SUPPORTED_EXCHANGES.get(exchange_value)
                if not exchange:
                    continue
                currency = str(
                    reference.get("currency") or record.get("currency") or "USD"
                ).upper()
                if currency != "USD":
                    continue
                description = str(
                    record.get("description") or reference.get("description") or symbol
                ).strip()
                resolved[symbol] = ResolvedConstituent(
                    symbol=symbol, name=description, exchange=exchange,
                    currency=currency, instrument_type="EQUITY",
                )
        return [resolved[symbol] for symbol in sorted(resolved)]

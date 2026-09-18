"""Symbol normalization between FactorLab canonical form and Schwab's API.

Schwab uses ``/`` as the class-share separator (``BRK/B``); the rest of our
sources and most market-data providers use ``.`` (``BRK.B``). Translate at
the boundary.

Verified during playground exploration (2026-05-01): sending ``BRK.B`` to
``get_quotes`` returns it under ``errors.invalidSymbols`` rather than failing
loudly. Always normalize before calling Schwab.
"""

# Affected canonical symbols (not exhaustive, but covers the common ones).
# When we encounter new class-share tickers, no code change needed -- the
# transformation is purely a `.`/`/` swap.
_KNOWN_CLASS_SHARES = frozenset({
    "BRK.A", "BRK.B",
    "BF.A", "BF.B",
    "GEF.B",
    "MOG.A", "MOG.B",
    "RDS.A", "RDS.B",
    "STZ.B",
    "WSO.B",
})


def to_schwab(symbol: str) -> str:
    """Convert canonical FactorLab symbol -> Schwab API form.

    >>> to_schwab("AAPL")
    'AAPL'
    >>> to_schwab("BRK.B")
    'BRK/B'
    """
    return symbol.replace(".", "/", 1) if "." in symbol else symbol


def from_schwab(symbol: str) -> str:
    """Convert Schwab API symbol -> canonical FactorLab form.

    >>> from_schwab("AAPL")
    'AAPL'
    >>> from_schwab("BRK/B")
    'BRK.B'
    """
    return symbol.replace("/", ".", 1) if "/" in symbol else symbol


def to_schwab_batch(symbols: list[str]) -> list[str]:
    """Vectorized form of ``to_schwab`` preserving order."""
    return [to_schwab(s) for s in symbols]


def from_schwab_batch(symbols: list[str]) -> list[str]:
    """Vectorized form of ``from_schwab`` preserving order."""
    return [from_schwab(s) for s in symbols]

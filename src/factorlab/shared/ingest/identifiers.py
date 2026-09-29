"""Vendor-neutral security identifier helpers."""

from __future__ import annotations

import re

_CUSIP = re.compile(r"^[0-9A-Z*@#]{9}$")


def _luhn_digits(text: str) -> str:
    out = []
    for char in text:
        if char.isdigit():
            out.append(char)
        elif char.isalpha():
            out.append(str(ord(char) - ord("A") + 10))
        else:
            out.append({"*": "36", "@": "37", "#": "38"}[char])
    return "".join(out)


def isin_check_digit(body: str) -> str:
    """ISO 6166 check digit for the first 11 characters of an ISIN."""
    digits = _luhn_digits(body.upper())
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char) * (2 if index % 2 == 0 else 1)
        total += value // 10 + value % 10
    return str((10 - total % 10) % 10)


def isin_from_cusip(cusip: str | None, country: str = "US") -> str | None:
    """``US`` + CUSIP + check digit, or ``None`` for anything that is not a CUSIP."""
    text = str(cusip or "").strip().upper()
    if not _CUSIP.fullmatch(text):
        return None
    body = f"{country.upper()}{text}"
    return body + isin_check_digit(body)


def legislator_name_key(first_name: str, last_name: str) -> str | None:
    """``first|last`` match key for a legislator (07 §5.5).

    Same rule the legacy House name resolver used (``political_names._name_key``):
    first word of the first name and the whole last name, lower-case letters only.
    """
    first_words = str(first_name or "").lower().split()
    first = re.sub(r"[^a-z]", "", first_words[0]) if first_words else ""
    last = re.sub(r"[^a-z]", "", str(last_name or "").lower())
    return f"{first}|{last}" if first and last else None


__all__ = ["isin_check_digit", "isin_from_cusip", "legislator_name_key"]

"""Resolve an operator-supplied authoritative constituent CSV, without dropping symbols."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, datetime

from factorlab.components.ingest_us.settings import ConstituentCsvIndex
from factorlab.components.ingest_us.universe.base import UniverseResolver
from factorlab.ingest.provider import RawCapture


class ConstituentCsvUniverseResolver(UniverseResolver):
    provider = "constituent_csv"

    def __init__(self, *, indexes: dict[str, ConstituentCsvIndex], validator, storage):
        super().__init__(validator=validator)
        self.indexes = indexes
        self.storage = storage

    def _retrieve_index(self, name):
        from factorlab.components.ingest_us.universe.models import normalize_symbol

        mapping = self.indexes[name]
        now = datetime.now(UTC)
        if mapping.as_of.tzinfo is None:
            raise ValueError("Constituent as_of must include a timezone")
        age = (now - mapping.as_of).total_seconds() / 86400
        if not 0 <= age <= mapping.max_age_days:
            raise ValueError(f"Constituent roster for {name} is stale or future-dated")
        body = mapping.path.read_bytes()
        capture = RawCapture(
            body=body,
            request_key=f"constituents:{name}",
            transport="file",
            fetched_at=now,
            source_url=mapping.source_url,
            content_type="text/csv",
            metadata={
                "index": name,
                "as_of": mapping.as_of.isoformat(),
                "sha256": hashlib.sha256(body).hexdigest(),
            },
        )
        raw_id = self.storage.archive_raw(
            capture, source=self.provider, source_channel="constituent_csv:file"
        )
        reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
        if not reader.fieldnames or mapping.symbol_column not in reader.fieldnames:
            raise ValueError(f"Constituent CSV for {name} has no symbol column")
        symbols = [normalize_symbol(row.get(mapping.symbol_column)) for row in reader]
        if len(set(symbols)) != len(symbols):
            raise ValueError(f"Constituent CSV for {name} contains duplicate securities")
        if len(symbols) != mapping.expected_constituents:
            raise ValueError(
                f"Constituent CSV for {name} has {len(symbols)} securities; "
                f"expected exactly {mapping.expected_constituents}"
            )
        indexes = self._provenance.get("indexes")
        if not isinstance(indexes, dict):
            indexes = {}
            self._provenance["indexes"] = indexes
        indexes[name] = {
            "source_url": mapping.source_url,
            "as_of": mapping.as_of.isoformat(),
            "raw_id": str(raw_id),
            "membership_basis": "operator_supplied_exact_roster",
        }
        return symbols

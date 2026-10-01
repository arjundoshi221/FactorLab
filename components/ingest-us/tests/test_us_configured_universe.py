from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from factorlab.storage.v2_us import V2USStorage


def test_expected_series_activates_additions_and_deactivates_removals():
    removed, retained, added = uuid4(), uuid4(), uuid4()
    client = Mock()
    client.query.return_value = SimpleNamespace(
        column_names=["listing_id", "symbol", "provider_symbol", "universe"],
        result_rows=[
            (removed, "OLD", "OLD.US", "previous"),
            (retained, "AAPL", "AAPL.US", "previous"),
        ],
    )
    storage = V2USStorage(client)
    storage.sync_expected_series(
        [
            {"instrument_id": retained, "symbol": "AAPL", "provider_symbol": "AAPL.US"},
            {"instrument_id": added, "symbol": "MSFT", "provider_symbol": "MSFT.US"},
        ],
        source="eodhd",
        universe="us_configured",
        resolution="daily",
    )
    inserted = client.insert.call_args.args[1]
    columns = client.insert.call_args.kwargs["column_names"]
    rows = [dict(zip(columns, values, strict=True)) for values in inserted]
    assert {(row["symbol"], row["active"]) for row in rows} == {
        ("OLD", False),
        ("AAPL", True),
        ("MSFT", True),
    }

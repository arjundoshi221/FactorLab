"""Reference seeding is additive and preserves existing venue names."""

from types import SimpleNamespace
from unittest.mock import Mock

from factorlab.storage.v2_us import V2USStorage


def test_seed_adds_verified_us_auxiliary_venues_only():
    client = Mock()
    client.query.side_effect = [
        SimpleNamespace(result_rows=[(0,)]),
        SimpleNamespace(result_rows=[(1,)]),
    ]
    V2USStorage(client).seed_us_reference_data()
    call = client.insert.call_args
    row = dict(zip(call.kwargs["column_names"], call.args[1][0], strict=True))
    assert call.args[0] == "ref.exchanges"
    assert (row["exchange_code"], row["mic"], row["country_code"]) == (
        "NYSE American",
        "XASE",
        "US",
    )
    assert client.insert.call_count == 1

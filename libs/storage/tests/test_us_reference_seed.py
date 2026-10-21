"""Reference seeding is additive and preserves existing venue names."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from factorlab.storage.v2_us import V2USStorage


def test_seed_adds_verified_us_auxiliary_and_futures_venues_only():
    client = Mock()
    # Three equity venues (Cboe BZX already exists), then six futures venues, all new.
    exists = [0, 1, 0, 0, 0, 0, 0, 0, 0]
    client.query.side_effect = [SimpleNamespace(result_rows=[(n,)]) for n in exists]
    V2USStorage(client).seed_us_reference_data()
    rows = []
    for call in client.insert.call_args_list:
        assert call.args[0] == "ref.exchanges"
        rows.append(dict(zip(call.kwargs["column_names"], call.args[1][0], strict=True)))
    assert [(r["exchange_code"], r["mic"], r["country_code"]) for r in rows] == [
        ("NYSE American", "XASE", "US"),
        ("US Index", "INDX", "US"),
        ("XCME", "XCME", "US"),
        ("XCBT", "XCBT", "US"),
        ("XNYM", "XNYM", "US"),
        ("XCEC", "XCEC", "US"),
        ("XCBF", "XCBF", "US"),
        ("IFUS", "IFUS", "US"),
    ]
    sessions = {r["exchange_code"]: json.loads(r["sessions"])["regular"] for r in rows}
    assert sessions["NYSE American"] == {"open": "09:30", "close": "16:00"}
    assert sessions["XCME"] == {"open": "18:00", "close": "17:00"}  # the Globex day

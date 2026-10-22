"""Index storage groups closes by day, year by year, and records its computation."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from factorlab.storage.indexes import IndexStorage

A, B = UUID(int=1), UUID(int=2)


def test_closes_are_grouped_by_trade_date_and_read_per_year():
    client = Mock()
    client.query.side_effect = [
        SimpleNamespace(
            result_rows=[
                (date(2025, 12, 30), A, 10.0),
                (date(2025, 12, 30), B, 5.0),
                (date(2025, 12, 31), A, 11.0),
            ]
        ),
        SimpleNamespace(result_rows=[(date(2026, 1, 2), B, 6.0)]),
    ]
    days = list(
        IndexStorage(client).closes([A, B], date(2025, 12, 1), date(2026, 1, 5), source="x")
    )
    assert days == [
        (date(2025, 12, 30), {A: 10.0, B: 5.0}),
        (date(2025, 12, 31), {A: 11.0}),
        (date(2026, 1, 2), {B: 6.0}),
    ]
    first, second = (call.kwargs["parameters"] for call in client.query.call_args_list)
    assert (first["lo"], first["hi"], first["source"]) == (
        date(2025, 12, 1),
        date(2025, 12, 31),
        "x",
    )
    assert (second["lo"], second["hi"]) == (date(2026, 1, 1), date(2026, 1, 5))
    # bar_time bounds prune partitions (FINAL otherwise merges every daily partition)
    assert (first["t0"].date(), first["t1"].date()) == (date(2025, 11, 30), date(2026, 1, 2))
    assert "do_not_merge_across_partitions_select_final" in client.query.call_args_list[0].args[0]


def test_computation_rows_bracket_a_run():
    client = Mock()
    store = IndexStorage(client)
    computation = store.start_computation(
        dataset="derived.index_levels", code_version="t", parameters={"a": 1}
    )
    store.finish_computation(input_rows=3, rows=7, status="success")
    rows = [
        dict(zip(call.kwargs["column_names"], call.args[1][0], strict=True))
        for call in client.insert.call_args_list
    ]
    assert [r["status"] for r in rows] == ["running", "success"]
    assert {r["computation_id"] for r in rows} == {computation}
    assert rows[1]["row_count"] == 7 and rows[1]["completed_at"] is not None

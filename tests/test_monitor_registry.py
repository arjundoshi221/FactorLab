import pytest
from pydantic import ValidationError
from tools.components import Monitoring, load_all, render_monitor_services


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"interval_s": 0},
        {"interval_s": float("nan")},
        {"max_silence_s": -1},
        {
            "interval_s": 30,
            "active_window": {"timezone": "UTC", "days": [8], "start": "09:00", "end": "17:00"},
        },
        {"interval_s": 30, "active_window": {"timezone": "UTC", "start": "25:00", "end": "17:00"}},
    ],
)
def test_invalid_registry_configuration(config):
    with pytest.raises(ValidationError):
        Monitoring.model_validate(config)


def test_registry_has_daemons_jobs_and_optional_profiles():
    import yaml

    data = yaml.safe_load(render_monitor_services(load_all()))
    assert data["ingest-us"]["interval_s"] == 30
    assert data["ingest-political"]["max_silence_s"] == 93600
    assert data["ibkr-snapshot"]["profile"] == "ibkr"
    assert "web" not in data

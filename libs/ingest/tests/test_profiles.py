from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from factorlab.ingest.profiles import ProfileRegistry

USER = "d677331a-a04f-454a-8a1e-02425be2c101"
OTHER = "d677331a-a04f-454a-8a1e-02425be2c102"
CONFIG = {
    "users": [{"user_id": USER, "display_name": "Alice"}],
    "accounts": [
        {
            "user_id": USER,
            "broker_code": "ibkr",
            "account_id": "SIM_A",
            "account_mode": "paper",
            "base_currency": "USD",
            "booking_country": "US",
            "opened_date": "2026-10-01",
            "simulated": True,
        }
    ],
}


def test_stable_identity_and_several_accounts():
    config = deepcopy(CONFIG)
    config["accounts"].append({**config["accounts"][0], "account_id": "SIM_B"})
    registry = ProfileRegistry.model_validate(config)
    assert registry.users[0].user_id == UUID(USER)
    assert len(registry.accounts) == 2


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_user",
        "duplicate_account",
        "unknown_owner",
        "real_sim_id",
        "live_sim",
        "extra_secret",
    ],
)
def test_invalid_registry_is_rejected(change):
    config = deepcopy(CONFIG)
    if change == "duplicate_user":
        config["users"].append(config["users"][0])
    elif change == "duplicate_account":
        config["accounts"].append(config["accounts"][0])
    elif change == "unknown_owner":
        config["accounts"][0]["user_id"] = OTHER
    elif change == "real_sim_id":
        config["accounts"][0]["simulated"] = False
    elif change == "live_sim":
        config["accounts"][0]["account_mode"] = "live"
    else:
        config["accounts"][0]["password"] = "must-not-be-in-config"
    with pytest.raises(ValidationError):
        ProfileRegistry.model_validate(config)

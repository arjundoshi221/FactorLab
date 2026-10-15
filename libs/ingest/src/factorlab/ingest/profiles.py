"""Operator-managed application profiles and broker account ownership contracts."""

from __future__ import annotations

from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AppProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)

    user_id: UUID
    display_name: str = Field(min_length=1, max_length=120)
    active: bool = True


class ProfileAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)

    user_id: UUID
    broker_code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    account_id: str = Field(min_length=1, max_length=128)
    account_mode: Literal["paper", "live"]
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    booking_country: str = Field(pattern=r"^[A-Z]{2}$")
    account_type: str = "individual"
    margin_type: str = "cash"
    opened_date: date
    purpose: str = "forward_test"
    notes: str = ""
    active: bool = True
    simulated: bool = False

    @model_validator(mode="after")
    def check_simulation(self) -> Self:
        if self.simulated:
            if not self.account_id.startswith("SIM_") or self.account_mode != "paper":
                raise ValueError("simulated accounts require a SIM_ ID and paper mode")
            if self.broker_code != "ibkr":
                raise ValueError("the current simulator supports IBKR only")
        elif self.account_id.startswith("SIM_"):
            raise ValueError("SIM_ account IDs are reserved for simulation")
        return self


class ProfileRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    users: list[AppProfile] = Field(min_length=1)
    accounts: list[ProfileAccount] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_links(self) -> Self:
        users = {user.user_id for user in self.users}
        if len(users) != len(self.users):
            raise ValueError("duplicate user_id")
        accounts = {(account.broker_code, account.account_id) for account in self.accounts}
        if len(accounts) != len(self.accounts):
            raise ValueError("duplicate broker/account mapping")
        if any(account.user_id not in users for account in self.accounts):
            raise ValueError("account refers to an unknown user")
        return self

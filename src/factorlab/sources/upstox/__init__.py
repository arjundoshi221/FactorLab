"""Upstox data source — India equities, F&O via Upstox API."""

from factorlab.sources.upstox.auth import (
    ensure_token,
    exchange_code,
    fetch_remote_token,
    get_auth_url,
    login_interactive,
    open_auth_in_browser,
    read_auth_code_file,
    read_token_file,
    save_token,
    validate_token,
    write_token_file,
)
from factorlab.sources.upstox.client import get_session
from factorlab.sources.upstox.instruments import (
    download_instruments,
    find_equities,
    find_nearest_future,
    load_or_download,
    refresh_all,
)
from factorlab.sources.upstox.universes import (
    build_fo_eligible,
    build_universes,
    load_universe,
    seed_index_universe,
)

__all__ = [
    "ensure_token",
    "exchange_code",
    "fetch_remote_token",
    "get_auth_url",
    "login_interactive",
    "open_auth_in_browser",
    "read_auth_code_file",
    "read_token_file",
    "save_token",
    "validate_token",
    "write_token_file",
    "get_session",
    "download_instruments",
    "find_equities",
    "find_nearest_future",
    "load_or_download",
    "refresh_all",
    "build_fo_eligible",
    "build_universes",
    "load_universe",
    "seed_index_universe",
]

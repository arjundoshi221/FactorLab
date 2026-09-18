"""Schwab API authentication via OAuth2 (delegated to schwab-py).

Schwab OAuth2 has two token tiers:
  - Access token: 30 min, auto-refreshed lazily by ``schwab-py`` via authlib (5-min leeway)
  - Refresh token: 7 days, requires browser + MFA to renew

``schwab-py`` owns the token-file lifecycle. We never read/write its bytes
directly — only ask it to load or refresh.

Token file format (verified 2026-05-01 against schwab-py 1.5.1):
  ``{"creation_timestamp": <epoch_seconds>, "token": {access_token, refresh_token, expires_in, expires_at, scope, token_type}}``

Read-only by policy:
  See ``docs/data-sources/us/schwab.md`` ("Hard rule: no trading").
  Callers must restrict themselves to ``/marketdata/v1/*`` endpoints.

Run ``ensure_client()`` before any API work to get a ready-to-use client.
"""

import logging
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from factorlab.shared.paths import token_path

log = logging.getLogger(__name__)

_REQUIRED_KEYS = {
    "SCHWAB_APP_KEY": "OAuth client_id from developer.schwab.com",
    "SCHWAB_APP_SECRET": "OAuth client_secret from developer.schwab.com",
    "SCHWAB_CALLBACK_URL": "OAuth callback URL (must match dev portal exactly, e.g. https://127.0.0.1:8182)",
}

# Default resolved through factorlab.shared.paths. SCHWAB_TOKEN_PATH env var
# still wins when set — that legacy override stays for back-compat.
_DEFAULT_TOKEN_PATH = str(token_path("schwab"))


# ── Credential helpers ──────────────────────────────────────────────────────


def _load_credentials() -> dict[str, str]:
    """Load and validate Schwab credentials from .env."""
    load_dotenv(find_dotenv(usecwd=True))
    creds: dict[str, str] = {}
    missing: list[str] = []
    for key, desc in _REQUIRED_KEYS.items():
        val = os.environ.get(key, "").strip()
        if not val:
            missing.append(f"  {key} -- {desc}")
        creds[key] = val
    if missing:
        raise EnvironmentError(
            "Missing Schwab credentials in .env:\n" + "\n".join(missing)
        )
    creds["SCHWAB_TOKEN_PATH"] = (
        os.environ.get("SCHWAB_TOKEN_PATH", "").strip() or _DEFAULT_TOKEN_PATH
    )
    return creds


# ── Token-file helpers ──────────────────────────────────────────────────────


def token_file_path() -> Path:
    """Return the configured token-file path."""
    return Path(_load_credentials()["SCHWAB_TOKEN_PATH"])


def token_file_exists() -> bool:
    """True iff a token file exists on disk."""
    return token_file_path().exists()


def delete_token() -> None:
    """Delete the local token file (forces next auth call to run browser flow)."""
    p = token_file_path()
    if p.exists():
        p.unlink()
        log.info("Deleted token at %s", p)


# ── Validation ──────────────────────────────────────────────────────────────


def validate_client(client) -> dict:
    """Validate *client* by pulling an AAPL quote.

    Returns the parsed JSON payload on HTTP 200; raises ``RuntimeError`` otherwise.
    """
    resp = client.get_quote("AAPL")
    if resp.status_code == 401:
        raise RuntimeError("Token rejected (HTTP 401) -- refresh token may be expired")
    if resp.status_code != 200:
        raise RuntimeError(
            f"Quote failed: HTTP {resp.status_code} -- {resp.text[:300]}"
        )
    return resp.json()


# ── Core auth ───────────────────────────────────────────────────────────────


def login_interactive():
    """Run the browser OAuth flow and return a fresh client.

    Opens a browser -> Schwab login + MFA -> redirects to local HTTPS listener
    -> ``schwab-py`` captures the code, exchanges it, writes the token file.

    Note: OAuth login uses regular Schwab brokerage credentials, NOT a
    separate developer-portal account.
    """
    from schwab.auth import client_from_login_flow

    creds = _load_credentials()
    Path(creds["SCHWAB_TOKEN_PATH"]).parent.mkdir(parents=True, exist_ok=True)

    log.info("Opening browser for Schwab OAuth login")
    log.info("After login + MFA, browser redirects to %s", creds["SCHWAB_CALLBACK_URL"])
    log.info("Browser will warn about a self-signed cert -- click Advanced -> Proceed.")
    log.info("Windows: first run may prompt firewall to allow python.exe to listen on 127.0.0.1.")

    client = client_from_login_flow(
        api_key=creds["SCHWAB_APP_KEY"],
        app_secret=creds["SCHWAB_APP_SECRET"],
        callback_url=creds["SCHWAB_CALLBACK_URL"],
        token_path=creds["SCHWAB_TOKEN_PATH"],
    )
    log.info("Token saved to %s", creds["SCHWAB_TOKEN_PATH"])
    return client


def ensure_client(interactive: bool = False):
    """Return a ready-to-use Schwab client.

    Resolution order:
      1. Load existing token file via ``schwab.auth.client_from_token_file``
         (auto-refreshes the 30-min access token within the 7-day window)
      2. Run interactive browser OAuth (only if *interactive* is True)

    Args:
        interactive: If True and no valid token exists, opens a browser for
                     manual login. If False (cron-safe default), raises
                     ``RuntimeError`` instead.

    Raises:
        RuntimeError: When no valid token is available and *interactive* is False.
    """
    from schwab.auth import client_from_token_file

    creds = _load_credentials()
    token_path = creds["SCHWAB_TOKEN_PATH"]

    if Path(token_path).exists():
        try:
            client = client_from_token_file(
                token_path=token_path,
                api_key=creds["SCHWAB_APP_KEY"],
                app_secret=creds["SCHWAB_APP_SECRET"],
            )
            log.info("Loaded Schwab client from %s", token_path)
            return client
        except Exception as exc:
            log.warning("Failed to load token file (%s)", exc)
            if not interactive:
                raise RuntimeError(
                    f"Schwab token at {token_path} is invalid: {exc}. "
                    "Run scripts/us/equities/schwab/us_equities_schwab_auth.py to re-authenticate."
                ) from exc

    if interactive:
        return login_interactive()

    raise RuntimeError(
        f"No Schwab token at {token_path}. "
        "Run scripts/us/equities/schwab/us_equities_schwab_auth.py to authenticate (browser + MFA required)."
    )

"""Continuously render Cloudflare-managed secrets into Docker tmpfs volumes."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from factorlab.core.logging import configure_logging

log = logging.getLogger("factorlab.cloudflare_secrets_agent")

CLICKHOUSE_DIR = Path("/run/secrets/clickhouse")
INDIA_DIR = Path("/run/secrets/india")
US_DIR = Path("/run/secrets/us")
POLITICAL_DIR = Path("/run/secrets/political")
MONITOR_DIR = Path("/run/secrets/monitor")
TOKEN_FILE = INDIA_DIR / "UPSTOX_ACCESS_TOKEN"
TOKEN_EXPIRY_FILE = INDIA_DIR / "UPSTOX_ACCESS_TOKEN.expires_at"
SCHWAB_TOKEN_FILE = US_DIR / "SCHWAB_ACCESS_TOKEN"
SCHWAB_TOKEN_EXPIRY_FILE = US_DIR / "SCHWAB_ACCESS_TOKEN.expires_at"
# Pooled Schwab accounts beyond `primary` render as SCHWAB_ACCESS_TOKEN_<ID>; the id
# becomes a file name, so it is validated (same rule as the provider settings).
SCHWAB_ACCOUNT_ID = re.compile(r"[a-z0-9_]{1,32}")
# IBKR volumes are optional: rendered only when mounted into this container.
IBKR_PAPER_DIR = Path("/run/secrets/ibkr-paper")
IBKR_LIVE_DIR = Path("/run/secrets/ibkr-live")
IBKR_CLIENT_DIR = Path("/run/secrets/ibkr-client")
# Consumers run as non-root users (UID 10001 for FactorLab images, the IB Gateway
# image's own user) and this agent drops CAP_CHOWN, so it cannot hand files to them.
# Each tmpfs volume is mounted only into its own consumer, so world-readable files
# there grant nothing to any other service.
CONSUMER_SECRET_MODE = 0o444
GATEWAY_SECRET_MODE = CONSUMER_SECRET_MODE


def _atomic_write(path: Path, value: str, mode: int = CONSUMER_SECRET_MODE) -> None:
    """Atomically replace a secret without exposing a partially written value."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)


def _clickhouse_users_xml(password_hash: str) -> str:
    normalized = password_hash.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(
            "CLICKHOUSE_PASSWORD_SHA256 must contain exactly 64 hexadecimal characters"
        )
    return f"""<clickhouse>
  <profiles>
    <default>
      <query_profiler_cpu_time_period_ns>0</query_profiler_cpu_time_period_ns>
      <query_profiler_real_time_period_ns>0</query_profiler_real_time_period_ns>
    </default>
  </profiles>
  <users>
    <factorlab>
      <password_sha256_hex>{normalized}</password_sha256_hex>
      <networks><ip>::/0</ip></networks>
      <profile>default</profile>
      <quota>default</quota>
      <access_management>1</access_management>
    </factorlab>
    <monitor>
      <password_sha256_hex>{normalized}</password_sha256_hex>
      <networks><ip>::/0</ip></networks>
      <profile>default</profile>
      <quota>default</quota>
      <grants>
        <query>GRANT SELECT, INSERT ON meta.service_events</query>
        <query>GRANT SELECT, INSERT ON meta.service_status</query>
      </grants>
    </monitor>
  </users>
</clickhouse>
"""


def _require_string(mapping: dict[str, Any], name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Cloudflare response is missing {name}")
    return value.strip()


def _render_bundle(payload: dict[str, Any]) -> str:
    if payload.get("version") != 1 or not isinstance(payload.get("secrets"), dict):
        raise ValueError("Unsupported Cloudflare runtime-secret response")

    secrets = payload["secrets"]
    clickhouse_password = _require_string(secrets, "CLICKHOUSE_PASSWORD")
    clickhouse_hash = _require_string(secrets, "CLICKHOUSE_PASSWORD_SHA256")
    factorlab_api_key = _require_string(secrets, "FACTORLAB_API_KEY")
    calculated_hash = hashlib.sha256(clickhouse_password.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(calculated_hash, clickhouse_hash.lower()):
        raise ValueError("CLICKHOUSE_PASSWORD_SHA256 does not match CLICKHOUSE_PASSWORD")

    _atomic_write(
        CLICKHOUSE_DIR / "runtime-users.xml", _clickhouse_users_xml(clickhouse_hash), 0o444
    )
    if MONITOR_DIR.is_dir():
        _atomic_write(MONITOR_DIR / "CLICKHOUSE_PASSWORD", clickhouse_password)
    for directory in (INDIA_DIR, US_DIR, POLITICAL_DIR):
        _atomic_write(directory / "CLICKHOUSE_PASSWORD", clickhouse_password)
    eodhd_api_key = secrets.get("EODHD_API_KEY")
    if isinstance(eodhd_api_key, str) and eodhd_api_key.strip():
        _atomic_write(US_DIR / "EODHD_API_KEY", eodhd_api_key.strip())
    else:
        _remove(US_DIR / "EODHD_API_KEY")
    _atomic_write(POLITICAL_DIR / "FACTORLAB_API_KEY", factorlab_api_key)
    ibkr_status = _render_ibkr(secrets, clickhouse_password)

    upstox = payload.get("upstox") if isinstance(payload.get("upstox"), dict) else {}
    access_token = secrets.get("UPSTOX_ACCESS_TOKEN")
    expires_at = upstox.get("expires_at")
    if upstox.get("status") == "valid" and isinstance(access_token, str) and access_token.strip():
        if not isinstance(expires_at, str) or not expires_at:
            raise ValueError("Valid Upstox token is missing expires_at")
        _atomic_write(TOKEN_FILE, access_token.strip())
        _atomic_write(TOKEN_EXPIRY_FILE, expires_at)
    else:
        _remove(TOKEN_FILE)
        _remove(TOKEN_EXPIRY_FILE)

    schwab: dict[str, Any] = payload["schwab"] if isinstance(payload.get("schwab"), dict) else {}
    schwab_access_token = secrets.get("SCHWAB_ACCESS_TOKEN")
    schwab_expires_at = schwab.get("access_expires_at")
    if (
        schwab.get("status") == "valid"
        and isinstance(schwab_access_token, str)
        and schwab_access_token.strip()
    ):
        if not isinstance(schwab_expires_at, str) or not schwab_expires_at:
            raise ValueError("Valid Schwab token is missing access_expires_at")
        _atomic_write(SCHWAB_TOKEN_FILE, schwab_access_token.strip())
        _atomic_write(SCHWAB_TOKEN_EXPIRY_FILE, schwab_expires_at)
    else:
        _remove(SCHWAB_TOKEN_FILE)
        _remove(SCHWAB_TOKEN_EXPIRY_FILE)
    schwab_status = str(schwab.get("status", "missing"))
    extra_accounts = _render_schwab_accounts(payload, secrets)
    if extra_accounts:
        schwab_status = ",".join([f"primary:{schwab_status}", *extra_accounts])

    generated_at = payload.get("generated_at", "unknown")
    _atomic_write(CLICKHOUSE_DIR / ".agent-ready", str(generated_at), 0o444)
    status = f"upstox={upstox.get('status', 'missing')}, schwab={schwab_status}"
    return f"{status}, ibkr={ibkr_status}" if ibkr_status is not None else status


def _schwab_account_files(account: str) -> tuple[Path, Path]:
    token = SCHWAB_TOKEN_FILE.with_name(f"{SCHWAB_TOKEN_FILE.name}_{account.upper()}")
    return token, token.with_name(f"{token.name}.expires_at")


def _render_schwab_accounts(payload: dict[str, Any], secrets: dict[str, Any]) -> list[str]:
    """Render pooled Schwab accounts other than ``primary``; return ``id:status`` items.

    Files of accounts the Worker no longer lists are removed.
    """
    accounts = payload.get("schwab_accounts")
    accounts = accounts if isinstance(accounts, dict) else {}
    rendered: set[Path] = set()
    statuses = []
    for account, state in sorted(accounts.items()):
        if account == "primary":
            continue
        if not isinstance(account, str) or not SCHWAB_ACCOUNT_ID.fullmatch(account):
            log.warning("Skipped Schwab account with an invalid id")
            continue
        state = state if isinstance(state, dict) else {}
        token_file, expiry_file = _schwab_account_files(account)
        token = _optional_secret(secrets, token_file.name)
        expires_at = state.get("access_expires_at")
        if state.get("status") == "valid" and token is not None:
            if not isinstance(expires_at, str) or not expires_at:
                raise ValueError(f"Valid Schwab {account} token is missing access_expires_at")
            _atomic_write(token_file, token)
            _atomic_write(expiry_file, expires_at)
            rendered.update((token_file, expiry_file))
        statuses.append(f"{account}:{state.get('status', 'missing')}")
    if US_DIR.is_dir():
        for path in US_DIR.glob(f"{SCHWAB_TOKEN_FILE.name}_*"):
            if path not in rendered:
                _remove(path)
    return statuses


def _optional_secret(secrets: dict[str, Any], name: str) -> str | None:
    value = secrets.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _render_ibkr(secrets: dict[str, Any], clickhouse_password: str) -> str | None:
    """Render IB Gateway logins and the snapshot client's ClickHouse password.

    Returns ``None`` when no IBKR volume is mounted, else the modes whose
    Gateway password is present (``paper+live``, ``paper``, ``none``...).
    """
    if IBKR_CLIENT_DIR.is_dir():
        _atomic_write(IBKR_CLIENT_DIR / "CLICKHOUSE_PASSWORD", clickhouse_password)
    vnc_password = _optional_secret(secrets, "IBKR_VNC_PASSWORD")
    mounted = False
    ready = []
    for mode, directory in (("paper", IBKR_PAPER_DIR), ("live", IBKR_LIVE_DIR)):
        if not directory.is_dir():
            continue
        mounted = True
        for file_name, value in (
            ("TWS_PASSWORD", _optional_secret(secrets, f"IBKR_{mode.upper()}_PASSWORD")),
            ("VNC_SERVER_PASSWORD", vnc_password),
        ):
            if value is None:
                _remove(directory / file_name)
            else:
                _atomic_write(directory / file_name, value, GATEWAY_SECRET_MODE)
        if (directory / "TWS_PASSWORD").exists():
            ready.append(mode)
    if not mounted and not IBKR_CLIENT_DIR.is_dir():
        return None
    return "+".join(ready) or "none"


def _expire_local_token() -> None:
    tokens = [
        ("Upstox", TOKEN_FILE, TOKEN_EXPIRY_FILE),
        ("Schwab", SCHWAB_TOKEN_FILE, SCHWAB_TOKEN_EXPIRY_FILE),
    ]
    if US_DIR.is_dir():
        for expiry_path in sorted(US_DIR.glob(f"{SCHWAB_TOKEN_FILE.name}_*.expires_at")):
            token_path = expiry_path.with_name(expiry_path.name.removesuffix(".expires_at"))
            tokens.append(("Schwab", token_path, expiry_path))
    for provider, token_path, expiry_path in tokens:
        try:
            expires_at = expiry_path.read_text(encoding="utf-8").strip()
            expiry = datetime.fromisoformat(expires_at)
        except (FileNotFoundError, OSError, ValueError):
            continue
        if expiry <= datetime.now(UTC):
            _remove(token_path)
            _remove(expiry_path)
            log.warning("Removed expired %s access token from tmpfs", provider)


def _read_identity_file(path: str, name: str) -> str:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OSError(f"Unable to read {name} identity file: {exc}") from exc
    if not value:
        raise OSError(f"{name} identity file is empty")
    return value


def sync_once(url: str, client_id: str, client_secret: str) -> str:
    response = requests.post(
        url,
        headers={
            "Accept": "application/json",
            "CF-Access-Client-Id": client_id,
            "CF-Access-Client-Secret": client_secret,
        },
        timeout=(5, 20),
    )
    response.raise_for_status()
    if len(response.content) > 64 * 1024:
        raise ValueError("Cloudflare runtime-secret response exceeded 64 KiB")
    payload = json.loads(response.content)
    if not isinstance(payload, dict):
        raise TypeError("Cloudflare response must be a JSON object")
    return _render_bundle(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    configure_logging(component="secrets-agent", service="cloudflare-secrets-agent")

    url = os.environ.get("CLOUDFLARE_SECRETS_URL", "").strip()
    client_id_path = os.environ.get(
        "CLOUDFLARE_ACCESS_CLIENT_ID_FILE",
        "/run/identity/cloudflare-access-client-id",
    )
    client_secret_path = os.environ.get(
        "CLOUDFLARE_ACCESS_CLIENT_SECRET_FILE",
        "/run/identity/cloudflare-access-client-secret",
    )
    interval = max(int(os.environ.get("CLOUDFLARE_SYNC_INTERVAL_SECONDS", "60")), 15)
    if not url.startswith("https://"):
        raise OSError("CLOUDFLARE_SECRETS_URL must be an https:// URL")

    while True:
        _expire_local_token()
        try:
            client_id = _read_identity_file(client_id_path, "Cloudflare Access client ID")
            client_secret = _read_identity_file(
                client_secret_path, "Cloudflare Access client secret"
            )
            status = sync_once(url, client_id, client_secret)
            log.info("Runtime secrets synchronized; token status: %s", status)
        except (
            OSError,
            TypeError,
            ValueError,
            requests.RequestException,
            json.JSONDecodeError,
        ) as exc:
            log.error("Runtime secret synchronization failed: %s", exc)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

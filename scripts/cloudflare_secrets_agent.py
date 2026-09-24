"""Continuously render Cloudflare-managed secrets into Docker tmpfs volumes."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("factorlab.cloudflare_secrets_agent")

CLICKHOUSE_DIR = Path("/run/secrets/clickhouse")
INDIA_DIR = Path("/run/secrets/india")
US_DIR = Path("/run/secrets/us")
POLITICAL_DIR = Path("/run/secrets/political")
TOKEN_FILE = INDIA_DIR / "UPSTOX_ACCESS_TOKEN"
TOKEN_EXPIRY_FILE = INDIA_DIR / "UPSTOX_ACCESS_TOKEN.expires_at"
SCHWAB_TOKEN_FILE = US_DIR / "SCHWAB_ACCESS_TOKEN"
SCHWAB_TOKEN_EXPIRY_FILE = US_DIR / "SCHWAB_ACCESS_TOKEN.expires_at"


def _atomic_write(path: Path, value: str, mode: int = 0o400) -> None:
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
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("CLICKHOUSE_PASSWORD_SHA256 must contain exactly 64 hexadecimal characters")
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

    _atomic_write(CLICKHOUSE_DIR / "runtime-users.xml", _clickhouse_users_xml(clickhouse_hash), 0o444)
    for directory in (INDIA_DIR, US_DIR, POLITICAL_DIR):
        _atomic_write(directory / "CLICKHOUSE_PASSWORD", clickhouse_password)
    eodhd_api_key = secrets.get("EODHD_API_KEY")
    if isinstance(eodhd_api_key, str) and eodhd_api_key.strip():
        _atomic_write(US_DIR / "EODHD_API_KEY", eodhd_api_key.strip())
    else:
        _remove(US_DIR / "EODHD_API_KEY")
    _atomic_write(POLITICAL_DIR / "FACTORLAB_API_KEY", factorlab_api_key)

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

    schwab = payload.get("schwab") if isinstance(payload.get("schwab"), dict) else {}
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

    generated_at = payload.get("generated_at", "unknown")
    _atomic_write(CLICKHOUSE_DIR / ".agent-ready", str(generated_at), 0o444)
    return f"upstox={upstox.get('status', 'missing')}, schwab={schwab.get('status', 'missing')}"


def _expire_local_token() -> None:
    for provider, token_path, expiry_path in (
        ("Upstox", TOKEN_FILE, TOKEN_EXPIRY_FILE),
        ("Schwab", SCHWAB_TOKEN_FILE, SCHWAB_TOKEN_EXPIRY_FILE),
    ):
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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
            client_secret = _read_identity_file(client_secret_path, "Cloudflare Access client secret")
            status = sync_once(url, client_id, client_secret)
            log.info("Runtime secrets synchronized; token status: %s", status)
        except (OSError, TypeError, ValueError, requests.RequestException, json.JSONDecodeError) as exc:
            log.error("Runtime secret synchronization failed: %s", exc)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

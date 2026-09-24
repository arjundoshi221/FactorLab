import hashlib
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _load_agent_module():
    path = Path(__file__).resolve().parent.parent / "scripts" / "cloudflare_secrets_agent.py"
    spec = importlib.util.spec_from_file_location("cloudflare_secrets_agent", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configure_paths(agent, tmp_path, monkeypatch):
    clickhouse = tmp_path / "clickhouse"
    india = tmp_path / "india"
    us = tmp_path / "us"
    political = tmp_path / "political"
    monkeypatch.setattr(agent, "CLICKHOUSE_DIR", clickhouse)
    monkeypatch.setattr(agent, "INDIA_DIR", india)
    monkeypatch.setattr(agent, "US_DIR", us)
    monkeypatch.setattr(agent, "POLITICAL_DIR", political)
    monkeypatch.setattr(agent, "TOKEN_FILE", india / "UPSTOX_ACCESS_TOKEN")
    monkeypatch.setattr(agent, "TOKEN_EXPIRY_FILE", india / "UPSTOX_ACCESS_TOKEN.expires_at")
    monkeypatch.setattr(agent, "SCHWAB_TOKEN_FILE", us / "SCHWAB_ACCESS_TOKEN")
    monkeypatch.setattr(agent, "SCHWAB_TOKEN_EXPIRY_FILE", us / "SCHWAB_ACCESS_TOKEN.expires_at")
    return clickhouse, india, us, political


def test_render_bundle_writes_static_and_rotating_secrets(tmp_path, monkeypatch):
    agent = _load_agent_module()
    clickhouse, india, us, political = _configure_paths(agent, tmp_path, monkeypatch)
    expiry = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
    payload = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "secrets": {
            "CLICKHOUSE_PASSWORD": "database-password",
            "CLICKHOUSE_PASSWORD_SHA256": hashlib.sha256(b"database-password").hexdigest(),
            "UPSTOX_ACCESS_TOKEN": "daily-token",
            "SCHWAB_ACCESS_TOKEN": "short-lived-token",
            "EODHD_API_KEY": "eodhd-key",
            "FACTORLAB_API_KEY": "factorlab-api-key",
        },
        "upstox": {"status": "valid", "expires_at": expiry},
        "schwab": {"status": "valid", "access_expires_at": expiry},
    }

    assert agent._render_bundle(payload) == "upstox=valid, schwab=valid"
    assert (india / "UPSTOX_ACCESS_TOKEN").read_text() == "daily-token"
    assert (us / "SCHWAB_ACCESS_TOKEN").read_text() == "short-lived-token"
    assert (india / "CLICKHOUSE_PASSWORD").read_text() == "database-password"
    assert (us / "EODHD_API_KEY").read_text() == "eodhd-key"
    assert (political / "CLICKHOUSE_PASSWORD").read_text() == "database-password"
    assert (political / "FACTORLAB_API_KEY").read_text() == "factorlab-api-key"
    assert "<password_sha256_hex>" + hashlib.sha256(b"database-password").hexdigest() in (
        clickhouse / "runtime-users.xml"
    ).read_text()
    assert "<query_profiler_cpu_time_period_ns>0</query_profiler_cpu_time_period_ns>" in (
        clickhouse / "runtime-users.xml"
    ).read_text()
    assert "<query_profiler_real_time_period_ns>0</query_profiler_real_time_period_ns>" in (
        clickhouse / "runtime-users.xml"
    ).read_text()


def test_render_bundle_removes_an_expired_upstox_token(tmp_path, monkeypatch):
    agent = _load_agent_module()
    _, india, _, _ = _configure_paths(agent, tmp_path, monkeypatch)
    india.mkdir()
    (india / "UPSTOX_ACCESS_TOKEN").write_text("stale-token")
    (india / "UPSTOX_ACCESS_TOKEN.expires_at").write_text("2026-01-01T00:00:00+00:00")
    payload = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "secrets": {
            "CLICKHOUSE_PASSWORD": "database-password",
            "CLICKHOUSE_PASSWORD_SHA256": hashlib.sha256(b"database-password").hexdigest(),
            "UPSTOX_ACCESS_TOKEN": None,
            "SCHWAB_ACCESS_TOKEN": None,
            "EODHD_API_KEY": "eodhd-key",
            "FACTORLAB_API_KEY": "factorlab-api-key",
        },
        "upstox": {"status": "expired"},
        "schwab": {"status": "reauth_required"},
    }

    assert agent._render_bundle(payload) == "upstox=expired, schwab=reauth_required"
    assert not (india / "UPSTOX_ACCESS_TOKEN").exists()
    assert not (india / "UPSTOX_ACCESS_TOKEN.expires_at").exists()


def test_render_bundle_removes_schwab_token_when_refresh_fails(tmp_path, monkeypatch):
    agent = _load_agent_module()
    _, _, us, _ = _configure_paths(agent, tmp_path, monkeypatch)
    us.mkdir()
    (us / "SCHWAB_ACCESS_TOKEN").write_text("stale-token")
    (us / "SCHWAB_ACCESS_TOKEN.expires_at").write_text("2026-01-01T00:00:00+00:00")
    payload = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "secrets": {
            "CLICKHOUSE_PASSWORD": "database-password",
            "CLICKHOUSE_PASSWORD_SHA256": hashlib.sha256(b"database-password").hexdigest(),
            "UPSTOX_ACCESS_TOKEN": None,
            "SCHWAB_ACCESS_TOKEN": None,
            "EODHD_API_KEY": "eodhd-key",
            "FACTORLAB_API_KEY": "factorlab-api-key",
        },
        "upstox": {"status": "missing"},
        "schwab": {"status": "refresh_failed"},
    }

    assert agent._render_bundle(payload) == "upstox=missing, schwab=refresh_failed"
    assert not (us / "SCHWAB_ACCESS_TOKEN").exists()
    assert not (us / "SCHWAB_ACCESS_TOKEN.expires_at").exists()


def test_render_bundle_does_not_require_eodhd_for_github_universe(tmp_path, monkeypatch):
    agent = _load_agent_module()
    _, _, us, _ = _configure_paths(agent, tmp_path, monkeypatch)
    payload = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "secrets": {
            "CLICKHOUSE_PASSWORD": "database-password",
            "CLICKHOUSE_PASSWORD_SHA256": hashlib.sha256(b"database-password").hexdigest(),
            "UPSTOX_ACCESS_TOKEN": None,
            "SCHWAB_ACCESS_TOKEN": None,
            "FACTORLAB_API_KEY": "factorlab-api-key",
        },
        "upstox": {"status": "missing"},
        "schwab": {"status": "reauth_required"},
    }

    assert agent._render_bundle(payload) == "upstox=missing, schwab=reauth_required"
    assert not (us / "EODHD_API_KEY").exists()

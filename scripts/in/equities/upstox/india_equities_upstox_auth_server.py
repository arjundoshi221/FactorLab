"""india_equities_upstox_auth_server — OAuth2 callback server for hands-free token refresh.

Pattern:    country_domain_vendor_action
Country:    India
Domain:     equities (vendor-wide; one auth server per vendor)
Vendor:     Upstox V3
Action:     auth_server (always-on Flask service on Railway)

Run model:  Always-on Flask app. Deployed to Railway via Procfile / railway.json:
              gunicorn india_equities_upstox_auth_server:app --chdir scripts --bind 0.0.0.0:$PORT
            Locally for debugging:  python scripts/in/equities/upstox/india_equities_upstox_auth_server.py

Flow:
  1. Operator visits /login (PIN-protected via X-Auth-Pin header or ?pin= param)
  2. Server redirects to Upstox authorization URL
  3. Operator logs in in browser; Upstox redirects back with ?code=xxx
  4. JS captures the code and POSTs to /callback (Railway proxy strips query params on /)
  5. Server exchanges code for access token and saves to data/upstox/.token
  6. Local scripts pull the token via /token (PIN-protected) using X-Auth-Pin header

Env vars required:
  UPSTOX_API_KEY       — OAuth client_id
  UPSTOX_API_SECRET    — OAuth client_secret
  UPSTOX_REDIRECT_URL  — Must point at this server (e.g. https://factorlab.up.railway.app/)
  AUTH_SERVER_PIN      — Shared PIN protecting /login, /token, /status
  FLASK_SECRET_KEY     — Pin to env on Railway (no fallback)
  PORT                 — Provided by Railway

Endpoints:
  GET  /              — landing page (renders token status + JS code-catcher)
  GET  /login         — redirect to Upstox auth URL (PIN-protected, rate-limited 5/min)
  GET  /callback      — exchange ?code= for token (called by browser JS)
  GET  /token         — return raw token JSON (PIN-protected, used by local scripts)
  GET  /status        — token status (PIN-protected)
  GET  /health        — Railway health check (unauthenticated)

Security:
  * 127.0.0.1-only is NOT enforced — this IS the public OAuth callback by design
  * PIN check uses hmac.compare_digest (timing-safe)
  * HSTS + X-Frame-Options DENY + nosniff on every response
  * Rate-limit 5/min on PIN-protected endpoints

Docs:
  docs/data-sources/india/upstox.md          — vendor integration
  docs/security/audit-2026-05-08.md          — last security review
"""

import hmac
import logging
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, redirect, request
from markupsafe import escape
from werkzeug.middleware.proxy_fix import ProxyFix

# ── Project root ─────────────────────────────────────────────────────────────
# Path layout: scripts/in/equities/upstox/<file>.py
#   parents[0]=upstox, [1]=equities, [2]=in, [3]=scripts, [4]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[4]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.chdir(PROJECT_ROOT)  # so token file paths resolve correctly

from dotenv import find_dotenv, load_dotenv  # noqa: E402

from factorlab.countries.in_.equities.upstox.auth import (  # noqa: E402
    exchange_code,
    get_auth_url,
    read_auth_code_file,
    read_token_file,
    save_token,
    validate_token,
)

# ── Config ───────────────────────────────────────────────────────────────────
load_dotenv(find_dotenv(usecwd=True))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("india_equities_upstox_auth_server")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

# Trust Railway's reverse proxy headers (X-Forwarded-Proto, X-Forwarded-Host)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

PIN = os.environ.get("AUTH_SERVER_PIN", "").strip()
if not PIN:
    log.warning("AUTH_SERVER_PIN not set — /login and /status are UNPROTECTED")

# ── Rate limiting ────────────────────────────────────────────────────────────
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[],
        storage_uri="memory://",
    )
    _pin_limit = limiter.limit("5 per minute")
    log.info("Rate limiting enabled (5 req/min on protected endpoints)")
except ImportError:
    log.warning("flask-limiter not installed — rate limiting DISABLED")
    # No-op decorator fallback
    def _pin_limit(f):
        return f


# ── Security headers ────────────────────────────────────────────────────────


@app.after_request
def _security_headers(response):
    """Add security and cache-control headers to every response."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# ── Helpers ──────────────────────────────────────────────────────────────────


def _check_pin() -> str | None:
    """Return an error message if PIN check fails, None if OK.

    Accepts PIN from X-Auth-Pin header (preferred) or ?pin= query param (browser fallback).
    Uses timing-safe comparison to prevent timing oracle attacks.
    """
    if not PIN:
        return None
    # Prefer header, fall back to query param (for browser /login redirects)
    provided = request.headers.get("X-Auth-Pin", "") or request.args.get("pin", "")
    if not hmac.compare_digest(provided, PIN):
        return "Unauthorized."
    return None


def _token_status() -> dict:
    """Check current token status."""
    token = read_token_file()
    if not token:
        return {"status": "no_token", "message": "No token found. Visit /login to authenticate."}
    try:
        profile = validate_token(token)
        return {
            "status": "valid",
            "user": profile.get("user_name", "?"),
            "email": profile.get("email", "?"),
        }
    except RuntimeError as exc:
        return {"status": "expired", "message": str(exc)}


# ── HTML page template ────────────────────────────────────────────────────────
# Every page includes JS that checks the URL for ?code= or #code= and sends
# it to /callback via fetch(). This is the PRIMARY code capture mechanism
# because Railway's proxy strips query params from the root route before they
# reach Flask.

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html><head><title>FactorLab Auth</title></head>
<body>
<div id="content">{body}</div>
<script>
(function() {{
    var search = window.location.search;
    var hash = window.location.hash;
    var code = null;

    if (search) {{
        var qp = new URLSearchParams(search);
        code = qp.get('code');
    }}
    if (!code && hash) {{
        var hp = new URLSearchParams(hash.substring(1));
        code = hp.get('code');
    }}

    if (code) {{
        document.getElementById('content').innerText = 'Processing login... Exchanging authorization code...';

        fetch('/callback?code=' + encodeURIComponent(code))
            .then(function(r) {{ return r.text().then(function(t) {{ return {{status: r.status, body: t}}; }}); }})
            .then(function(res) {{
                if (res.status >= 200 && res.status < 300) {{
                    document.getElementById('content').innerHTML = res.body;
                }} else {{
                    document.getElementById('content').innerText = 'Login failed. Status: ' + res.status;
                }}
            }})
            .catch(function(err) {{
                document.getElementById('content').innerText = 'Error: ' + err;
            }});
    }}
}})();
</script>
</body></html>
"""


def _render(body: str, status_code: int = 200):
    """Render HTML page with code-catcher JS included."""
    return _PAGE_TEMPLATE.format(body=body), status_code


# ── Routes ───────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    """Landing page — JS captures ?code= from URL if present."""
    # Server-side: try to catch the code directly (works locally, not on Railway)
    code = request.args.get("code", "").strip()
    if code:
        log.info("Root route received code param (len=%d)", len(code))
        return _handle_callback(code)

    # Show status page — JS will handle code capture if present in URL
    status = _token_status()

    if status["status"] == "valid":
        body = (
            f"<h2>FactorLab Auth Server</h2>"
            f"<p>Token: <strong>valid</strong></p>"
            f"<p>User: {escape(status['user'])} ({escape(status['email'])})</p>"
            f"<p><small>Last checked: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</small></p>"
        )
    else:
        body = (
            f"<h2>FactorLab Auth Server</h2>"
            f"<p>Token: <strong>{escape(status['status'])}</strong></p>"
            f"<p>{escape(status.get('message', ''))}</p>"
            f"<p><a href='/login?pin=PIN'>Login to Upstox</a> (replace PIN with your auth PIN)</p>"
        )
    return _render(body)


@app.route("/login")
@_pin_limit
def login():
    """Redirect to Upstox authorization page. Protected by PIN."""
    err = _check_pin()
    if err:
        return err, 403

    url = get_auth_url()
    log.info("Redirecting to Upstox login")
    return redirect(url)


def _handle_callback(code: str):
    """Exchange authorization code for access token, save it, return result."""
    log.info("Exchanging code (len=%d) for token...", len(code))
    try:
        token = exchange_code(code)
        save_token(token, auth_code=code)
        profile = validate_token(token)
        user = profile.get("user_name", "?")
        log.info("Login successful — user=%s", user)
        return (
            f"<h2>Login Successful</h2>"
            f"<p>Welcome, <strong>{escape(user)}</strong></p>"
            f"<p>Token saved. All scripts will use this token until ~3:30 AM IST tomorrow.</p>"
            f"<p>You can close this tab.</p>"
        ), 200
    except Exception as exc:
        log.error("Token exchange failed: %s", exc)
        return (
            f"<h2>Login Failed</h2>"
            f"<p><strong>Error:</strong> {escape(str(exc))}</p>"
        ), 500


@app.route("/callback")
def callback():
    """OAuth2 callback — exchange ?code=xxx for access token."""
    code = request.args.get("code", "").strip()
    if not code:
        return "Missing 'code' parameter in callback.", 400
    return _handle_callback(code)


@app.route("/token")
@_pin_limit
def token_endpoint():
    """Return the raw access token + auth code. Protected by PIN.

    Accepts PIN via X-Auth-Pin header (preferred) or ?pin= query param.
    Used by local scripts to fetch the token from Railway.
    """
    err = _check_pin()
    if err:
        return err, 403

    token = read_token_file()
    if not token:
        return {"error": "no_token", "message": "No token on server. Login first."}, 404

    auth_code = read_auth_code_file() or os.environ.get("UPSTOX_AUTH_CODE", "")

    try:
        profile = validate_token(token)
        result = {
            "access_token": token,
            "user": profile.get("user_name", "?"),
            "email": profile.get("email", "?"),
            "status": "valid",
        }
        if auth_code:
            result["auth_code"] = auth_code
        return result, 200
    except RuntimeError as exc:
        return {"error": "expired", "message": str(exc)}, 401


@app.route("/status")
@_pin_limit
def status():
    """Show token status. Protected by PIN."""
    err = _check_pin()
    if err:
        return err, 403

    s = _token_status()
    return s, 200


@app.route("/health")
def health():
    """Railway health check (no auth)."""
    return {"status": "ok"}, 200


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8888))
    log.info("Starting auth server on port %d", port)
    log.info("Login URL: http://localhost:%d/login", port)
    app.run(host="0.0.0.0", port=port, debug=False)

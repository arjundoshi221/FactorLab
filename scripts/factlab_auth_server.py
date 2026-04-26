"""factlab_auth_server — OAuth2 callback server for hands-free token refresh.

Deploy on Railway (or run locally). Handles the Upstox OAuth2 redirect so you
never have to copy-paste authorization codes.

Flow:
  1. You visit /login (protected by PIN)
  2. Server redirects to Upstox login page
  3. You log in normally in your browser
  4. Upstox redirects back with ?code=xxx
  5. Server exchanges the code for an access token and saves it
  6. All other scripts (hourly, 5min) read the token from data/upstox/.token

Env vars required:
  UPSTOX_API_KEY       — OAuth client_id
  UPSTOX_API_SECRET    — OAuth client_secret
  UPSTOX_REDIRECT_URL  — Must point to this server (e.g. https://your-app.up.railway.app/)
  AUTH_SERVER_PIN       — Simple PIN to protect /login and /status (e.g. "7291")

Usage:
  # Local
  python scripts/factlab_auth_server.py

  # Railway (via Procfile)
  web: python scripts/factlab_auth_server.py
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, redirect, request

# ── Project root ─────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.chdir(PROJECT_ROOT)  # so token file paths resolve correctly

from dotenv import find_dotenv, load_dotenv  # noqa: E402

from factorlab.sources.upstox.auth import (  # noqa: E402
    exchange_code,
    get_auth_url,
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
log = logging.getLogger("factlab_auth_server")

app = Flask(__name__)

PIN = os.environ.get("AUTH_SERVER_PIN", "").strip()
if not PIN:
    log.warning("AUTH_SERVER_PIN not set — /login and /status are UNPROTECTED")


# ── No-cache headers ─────────────────────────────────────────────────────────


@app.after_request
def _no_cache(response):
    """Prevent browser from caching any page (especially the callback redirect)."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


# ── Helpers ──────────────────────────────────────────────────────────────────


def _check_pin() -> str | None:
    """Return an error message if PIN check fails, None if OK."""
    if not PIN:
        return None  # no PIN configured = no protection
    provided = request.args.get("pin", "")
    if provided != PIN:
        return "Unauthorized. Append ?pin=YOUR_PIN to the URL."
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


def _handle_callback(code: str):
    """Exchange authorization code for access token, save it, return result page."""
    log.info("Exchanging code (len=%d) for token...", len(code))
    try:
        token = exchange_code(code)
        save_token(token, auth_code=code)
        profile = validate_token(token)
        user = profile.get("user_name", "?")
        log.info("Login successful — user=%s", user)
        return (
            f"<h2>Login Successful</h2>"
            f"<p>Welcome, <strong>{user}</strong></p>"
            f"<p>Token saved. All scripts will use this token until ~3:30 AM IST tomorrow.</p>"
            f"<p>You can close this tab.</p>"
        ), 200
    except Exception as exc:
        log.error("Token exchange failed: %s", exc, exc_info=True)
        return (
            f"<h2>Login Failed</h2>"
            f"<p><strong>Error:</strong> {exc}</p>"
        ), 500


# ── JS that catches the code client-side ─────────────────────────────────────
# If the server-side check misses the ?code= param (e.g. proxy/cache issue),
# this JS picks it up from the browser URL and redirects to /callback explicitly.

_CODE_CATCHER_JS = """
<script>
(function() {
    var search = window.location.search;
    var hash = window.location.hash;
    var code = null;

    // Check query string (?code=xxx)
    if (search) {
        var qp = new URLSearchParams(search);
        code = qp.get('code');
    }
    // Check fragment (#code=xxx) — some OAuth providers use implicit flow
    if (!code && hash) {
        var hp = new URLSearchParams(hash.substring(1));
        code = hp.get('code');
    }

    if (code) {
        // Redirect to /callback so the server can exchange the code
        window.location.replace('/callback?code=' + encodeURIComponent(code));
    }
})();
</script>
"""


# ── Routes ───────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    """Landing page — also handles OAuth callback if ?code= is present."""
    # Server-side: try to catch the code directly
    code = request.args.get("code", "").strip()
    if code:
        log.info("Root route received code param (len=%d)", len(code))
        return _handle_callback(code)

    # No code — show status page with JS fallback to catch code client-side
    status = _token_status()

    if status["status"] == "valid":
        return (
            f"<h2>FactorLab Auth Server</h2>"
            f"<p>Token: <strong>valid</strong></p>"
            f"<p>User: {status['user']} ({status['email']})</p>"
            f"<p><small>Last checked: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</small></p>"
        ), 200
    else:
        return (
            f"<h2>FactorLab Auth Server</h2>"
            f"<p>Token: <strong>{status['status']}</strong></p>"
            f"<p>{status.get('message', '')}</p>"
            f"<p><a href='/login?pin=PIN'>Login to Upstox</a> (replace PIN with your auth PIN)</p>"
            f"{_CODE_CATCHER_JS}"
        ), 200


@app.route("/login")
def login():
    """Redirect to Upstox authorization page. Protected by PIN."""
    err = _check_pin()
    if err:
        return err, 403

    url = get_auth_url()
    log.info("Redirecting to Upstox login")
    return redirect(url)


@app.route("/callback")
def callback():
    """OAuth2 callback — Upstox redirects here with ?code=xxx."""
    code = request.args.get("code", "").strip()
    if not code:
        return "Missing 'code' parameter in callback.", 400
    return _handle_callback(code)


@app.route("/token")
def token_endpoint():
    """Return the raw access token. Protected by PIN.

    Used by local scripts to fetch the token from Railway:
      curl -s 'https://your-app.up.railway.app/token?pin=1234'
    """
    err = _check_pin()
    if err:
        return err, 403

    token = read_token_file()
    if not token:
        return {"error": "no_token", "message": "No token on server. Login first."}, 404

    auth_code = os.environ.get("UPSTOX_AUTH_CODE", "")

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
    log.info("Login URL: http://localhost:%d/login%s", port, f"?pin={PIN}" if PIN else "")
    app.run(host="0.0.0.0", port=port, debug=False)

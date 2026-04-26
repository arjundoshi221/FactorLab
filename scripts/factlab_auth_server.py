"""factlab_auth_server — OAuth2 callback server for hands-free token refresh.

Deploy on Railway (or run locally). Handles the Upstox OAuth2 redirect so you
never have to copy-paste authorization codes.

Flow:
  1. You visit /login (protected by PIN)
  2. Server redirects to Upstox login page
  3. You log in normally in your browser
  4. Upstox redirects to /callback?code=xxx on this server
  5. Server exchanges the code for an access token and saves it
  6. All other scripts (hourly, 5min) read the token from data/upstox/.token

Env vars required:
  UPSTOX_API_KEY       — OAuth client_id
  UPSTOX_API_SECRET    — OAuth client_secret
  UPSTOX_REDIRECT_URL  — Must point to this server's /callback endpoint
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
import traceback
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

# Track last callback for debugging
_last_callback: dict = {}


# ── Request logging ───────────────────────────────────────────────────────────


@app.before_request
def _log_request():
    log.info(">> %s %s  args=%s", request.method, request.url, dict(request.args))


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


# ── Routes ───────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    """Landing page — also handles OAuth callback if ?code= is present.

    This lets the redirect_uri be the root (http://localhost:8888/) without
    needing to change the Upstox developer portal.
    """
    # If Upstox redirected here with a code, handle it as a callback
    code = request.args.get("code", "").strip()
    if code:
        log.info("Root route received code param (len=%d), delegating to callback", len(code))
        return _handle_callback(code)

    status = _token_status()
    last_err = _last_callback.get("error", "")
    debug_html = ""
    if last_err:
        debug_html = f"<p style='color:red'><strong>Last callback error:</strong> {last_err}</p>"

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
            f"{debug_html}"
            f"<p><a href='/login?pin=PIN'>Login to Upstox</a> (replace PIN with your auth PIN)</p>"
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


def _handle_callback(code: str):
    """Shared callback logic — exchange code for token."""
    global _last_callback
    _last_callback = {"time": datetime.now().isoformat(), "code_len": len(code)}
    log.info("Exchanging code (len=%d) for token...", len(code))

    try:
        token = exchange_code(code)
        save_token(token)
        profile = validate_token(token)
        user = profile.get("user_name", "?")
        log.info("Login successful — user=%s", user)
        _last_callback["status"] = "success"
        _last_callback["user"] = user
        return (
            f"<h2>Login Successful</h2>"
            f"<p>Welcome, <strong>{user}</strong></p>"
            f"<p>Token saved. All scripts will use this token until ~3:30 AM IST tomorrow.</p>"
            f"<p>You can close this tab.</p>"
        ), 200
    except Exception as exc:
        tb = traceback.format_exc()
        log.error("Token exchange failed: %s\n%s", exc, tb)
        _last_callback["status"] = "error"
        _last_callback["error"] = str(exc)
        _last_callback["traceback"] = tb
        return (
            f"<h2>Login Failed</h2>"
            f"<p><strong>Error:</strong> {exc}</p>"
            f"<pre style='font-size:12px;background:#f5f5f5;padding:10px'>{tb}</pre>"
        ), 500


@app.route("/callback")
def callback():
    """OAuth2 callback — Upstox redirects here with ?code=xxx."""
    code = request.args.get("code", "").strip()
    if not code:
        return "Missing 'code' parameter in callback.", 400
    return _handle_callback(code)


@app.route("/status")
def status():
    """Show token status. Protected by PIN."""
    err = _check_pin()
    if err:
        return err, 403

    s = _token_status()
    return s, 200


@app.route("/debug")
def debug():
    """Debug info — shows env config and last callback. Protected by PIN."""
    err = _check_pin()
    if err:
        return err, 403

    redirect_url = os.environ.get("UPSTOX_REDIRECT_URL", "(NOT SET)")
    api_key = os.environ.get("UPSTOX_API_KEY", "(NOT SET)")
    api_secret = os.environ.get("UPSTOX_API_SECRET", "(NOT SET)")
    # Mask secrets
    masked_key = api_key[:6] + "..." if len(api_key) > 6 else api_key
    masked_secret = api_secret[:4] + "..." if len(api_secret) > 4 else api_secret

    return {
        "redirect_url": redirect_url,
        "api_key": masked_key,
        "api_secret_set": bool(api_secret and api_secret != "(NOT SET)"),
        "api_secret_preview": masked_secret,
        "pin_set": bool(PIN),
        "token_file_exists": Path("data/upstox/.token").exists(),
        "cwd": os.getcwd(),
        "last_callback": _last_callback,
    }, 200


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

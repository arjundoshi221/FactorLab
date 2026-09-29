"""Upstox OAuth2 code-grant authentication.

Standard OAuth2 flow:
  1. Open authorization URL in browser
  2. User logs in (mobile → OTP → MPIN)
  3. Upstox redirects to redirect_uri with ``?code=xxx``
  4. Exchange code for access token via POST

Token expires daily ~3:30-4:30 AM IST.  No refresh tokens.
Run ``ensure_token()`` before any API work to guarantee a live token.

Token sources (checked in order):
  1. Token file at ``data/upstox/.token``, when ``FACTORLAB_PERSIST_SECRETS`` allows it
  2. ``UPSTOX_ACCESS_TOKEN`` via ``get_secret`` (production: the runtime secret volume
     the secrets agent fills from the Cloudflare auth Worker's daily login)
  3. Interactive login prompt (local only)
"""

import logging
import os
import stat
import webbrowser
from urllib.parse import urlencode

import requests

from factorlab.core.paths import token_path
from factorlab.core.secrets import get_secret

log = logging.getLogger(__name__)

_PROFILE_URL = "https://api.upstox.com/v2/user/profile"
_AUTH_DIALOG_URL = "https://api.upstox.com/v2/login/authorization/dialog"
_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"

# Token files for local development (production disables persistence).
# Resolved via factorlab.core.paths so the location is one knob away
# (FACTORLAB_TOKEN_ROOT). Default preserves legacy data/upstox/.token shape.
_TOKEN_FILE = token_path("upstox")
_AUTH_CODE_FILE = token_path("upstox_auth_code")
_TOKEN_DIR = _TOKEN_FILE.parent

# ── Credential helpers ──────────────────────────────────────────────────────

_REQUIRED_KEYS = {
    "UPSTOX_API_KEY": "OAuth client_id from developer portal",
    "UPSTOX_API_SECRET": "OAuth client_secret from developer portal",
    "UPSTOX_REDIRECT_URL": "OAuth redirect URI (e.g. http://localhost:8888/)",
}


def _persistence_enabled() -> bool:
    return os.getenv("FACTORLAB_PERSIST_SECRETS", "true").lower() in {
        "1", "true", "yes", "on",
    }


def _load_credentials() -> dict[str, str]:
    """Load and validate all required Upstox credentials (secret volume or environment)."""
    creds: dict[str, str] = {}
    missing: list[str] = []
    for key, desc in _REQUIRED_KEYS.items():
        val = (get_secret(key, "") or "").strip()
        if not val:
            missing.append(f"  {key} — {desc}")
        creds[key] = val
    if missing:
        raise OSError(
            "Missing Upstox credentials:\n" + "\n".join(missing)
        )
    return creds


# ── Token file helpers ──────────────────────────────────────────────────────


def read_token_file() -> str | None:
    """Read token from the shared token file. Returns None if not found."""
    if _TOKEN_FILE.exists():
        token = _TOKEN_FILE.read_text().strip()
        if token:
            return token
    return None


def write_token_file(token: str) -> None:
    """Write token to the shared token file (owner-only permissions)."""
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(token)
    try:
        os.chmod(_TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError:
        pass  # Windows doesn't support Unix permissions
    log.info("Token written to %s", _TOKEN_FILE)


def read_auth_code_file() -> str | None:
    """Read auth code from file. Returns None if not found."""
    if _AUTH_CODE_FILE.exists():
        code = _AUTH_CODE_FILE.read_text().strip()
        if code:
            return code
    return None


def write_auth_code_file(code: str) -> None:
    """Write auth code to file (owner-only permissions)."""
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    _AUTH_CODE_FILE.write_text(code)
    try:
        os.chmod(_AUTH_CODE_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError:
        pass  # Windows doesn't support Unix permissions
    log.info("Auth code written to %s", _AUTH_CODE_FILE)


# ── Core auth functions ─────────────────────────────────────────────────────


def get_auth_url() -> str:
    """Build the Upstox authorization URL.

    Open this in a browser. After login, Upstox redirects to your
    ``redirect_uri`` with ``?code=xxx`` in the query string.
    """
    creds = _load_credentials()
    params = urlencode({
        "response_type": "code",
        "client_id": creds["UPSTOX_API_KEY"],
        "redirect_uri": creds["UPSTOX_REDIRECT_URL"],
    })
    return f"{_AUTH_DIALOG_URL}?{params}"


def open_auth_in_browser() -> str:
    """Open the authorization URL in the default browser and return it."""
    url = get_auth_url()
    log.info("Opening authorization URL in browser")
    webbrowser.open(url)
    return url


def exchange_code(code: str) -> str:
    """Exchange an authorization code for an access token.

    Args:
        code: The ``?code=xxx`` value from the redirect URL after login.

    Returns:
        The access token string.

    Raises:
        RuntimeError: If the token exchange fails.
    """
    creds = _load_credentials()
    resp = requests.post(
        _TOKEN_URL,
        data={
            "code": code,
            "client_id": creds["UPSTOX_API_KEY"],
            "client_secret": creds["UPSTOX_API_SECRET"],
            "redirect_uri": creds["UPSTOX_REDIRECT_URL"],
            "grant_type": "authorization_code",
        },
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Token exchange failed: HTTP {resp.status_code} — {resp.text[:300]}"
        )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {data}")

    log.info("Token obtained — email=%s", data.get("email", "?"))
    return token


def validate_token(token: str) -> dict:
    """Validate *token* via ``GET /v2/user/profile``.

    Returns the profile dict on success, raises ``RuntimeError`` otherwise.
    """
    resp = requests.get(
        _PROFILE_URL,
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code == 401:
        raise RuntimeError("Token expired or invalid (HTTP 401)")
    if resp.status_code != 200:
        raise RuntimeError(
            f"Token validation failed: HTTP {resp.status_code} — {resp.text[:200]}"
        )

    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Unexpected profile response: {data}")

    profile = data["data"]
    log.info("Token valid — user=%s", profile.get("user_name", "?"))
    return profile


def save_token(token: str, *, auth_code: str | None = None) -> None:
    """Persist token (and optionally auth code) to the token files, then this process."""
    if _persistence_enabled():
        # Local development opts into persistence; production disables it
        # (FACTORLAB_PERSIST_SECRETS=false) and reads the runtime secret volume.
        write_token_file(token)
        if auth_code:
            write_auth_code_file(auth_code)

    # In-process env
    os.environ["UPSTOX_ACCESS_TOKEN"] = token
    if auth_code:
        os.environ["UPSTOX_AUTH_CODE"] = auth_code


def _load_existing_token() -> str | None:
    """Try the token file (when persistence is on), then the runtime secret."""
    if _persistence_enabled():
        token = read_token_file()
        if token:
            return token
    token = (get_secret("UPSTOX_ACCESS_TOKEN", "") or "").strip()
    return token or None


def login_interactive() -> str:
    """Run the full interactive login flow: open browser → prompt for code → exchange → save.

    Returns the new access token.
    """
    url = open_auth_in_browser()
    print(f"\nAuthorization URL (also opened in browser):\n  {url}\n")
    print("After logging in, Upstox will redirect to your redirect_uri.")
    print("Copy the 'code' parameter from the URL bar.\n")
    code = input("Paste the auth code here: ").strip()
    if not code:
        raise RuntimeError("No authorization code provided")

    token = exchange_code(code)
    save_token(token, auth_code=code)
    return token


def ensure_token(interactive: bool = True) -> str:
    """Return a valid access token.

    Resolution order:
      1. Token file (when persistence is on) / runtime secret -> validate
      2. Interactive browser login (if *interactive* is True)

    Args:
        interactive: If True, prompt for manual login when all else fails.
                     If False, raise RuntimeError (daemons and scheduled jobs).
    """
    existing = _load_existing_token()
    if existing:
        try:
            validate_token(existing)
            log.info("Existing token is valid")
            return existing
        except RuntimeError as exc:
            log.info("Existing token invalid (%s)", exc)

    if interactive:
        return login_interactive()

    raise RuntimeError(
        "No valid Upstox token. Complete the daily login in the Upstox auth Worker so the "
        "secrets agent delivers UPSTOX_ACCESS_TOKEN "
        "(docs/architecture/05-secrets-and-upstox-auth.md)."
    )

"""Upstox OAuth2 code-grant authentication.

Standard OAuth2 flow:
  1. Open authorization URL in browser
  2. User logs in (mobile → OTP → MPIN)
  3. Upstox redirects to redirect_uri with ``?code=xxx``
  4. Exchange code for access token via POST

Token expires daily ~3:30-4:30 AM IST.  No refresh tokens.
Run ``ensure_token()`` before any API work to guarantee a live token.

Token storage (checked in order):
  1. Token file at ``data/upstox/.token`` (used by Railway auth server)
  2. ``UPSTOX_ACCESS_TOKEN`` env var / ``.env`` file
  3. Interactive login prompt (local only)
"""

import logging
import os
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import find_dotenv, load_dotenv, set_key

log = logging.getLogger(__name__)

_PROFILE_URL = "https://api.upstox.com/v2/user/profile"
_AUTH_DIALOG_URL = "https://api.upstox.com/v2/login/authorization/dialog"
_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"

# Token file — shared between auth server and scripts on Railway
_TOKEN_DIR = Path("data/upstox")
_TOKEN_FILE = _TOKEN_DIR / ".token"
_AUTH_CODE_FILE = _TOKEN_DIR / ".auth_code"

# ── Credential helpers ──────────────────────────────────────────────────────

_REQUIRED_KEYS = {
    "UPSTOX_API_KEY": "OAuth client_id from developer portal",
    "UPSTOX_API_SECRET": "OAuth client_secret from developer portal",
    "UPSTOX_REDIRECT_URL": "OAuth redirect URI (e.g. http://localhost:8888/)",
}


def _load_credentials() -> dict[str, str]:
    """Load and validate all required Upstox credentials from environment."""
    load_dotenv(find_dotenv(usecwd=True))
    creds: dict[str, str] = {}
    missing: list[str] = []
    for key, desc in _REQUIRED_KEYS.items():
        val = os.environ.get(key, "").strip()
        if not val:
            missing.append(f"  {key} — {desc}")
        creds[key] = val
    if missing:
        raise EnvironmentError(
            "Missing Upstox credentials in .env:\n" + "\n".join(missing)
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
    """Write token to the shared token file."""
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(token)
    log.info("Token written to %s", _TOKEN_FILE)


def read_auth_code_file() -> str | None:
    """Read auth code from file. Returns None if not found."""
    if _AUTH_CODE_FILE.exists():
        code = _AUTH_CODE_FILE.read_text().strip()
        if code:
            return code
    return None


def write_auth_code_file(code: str) -> None:
    """Write auth code to file."""
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    _AUTH_CODE_FILE.write_text(code)
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
    """Persist token (and optionally auth code) to files and .env."""
    # Token file (for Railway / cross-process sharing)
    write_token_file(token)

    # Auth code file
    if auth_code:
        write_auth_code_file(auth_code)

    # .env (for local development)
    env_path = find_dotenv(usecwd=True)
    if env_path:
        set_key(env_path, "UPSTOX_ACCESS_TOKEN", token)
        if auth_code:
            set_key(env_path, "UPSTOX_AUTH_CODE", auth_code)
        log.info("Updated UPSTOX_ACCESS_TOKEN in %s", env_path)

    # In-process env
    os.environ["UPSTOX_ACCESS_TOKEN"] = token
    if auth_code:
        os.environ["UPSTOX_AUTH_CODE"] = auth_code


def fetch_remote_token() -> str | None:
    """Fetch a valid token from the Railway auth server.

    Requires ``AUTH_SERVER_URL`` and ``AUTH_SERVER_PIN`` in .env.
    Returns the token string if the server has one, None otherwise.
    """
    load_dotenv(find_dotenv(usecwd=True))
    server_url = os.environ.get("AUTH_SERVER_URL", "").strip().rstrip("/")
    pin = os.environ.get("AUTH_SERVER_PIN", "").strip()

    if not server_url:
        return None

    url = f"{server_url}/token"
    if pin:
        url += f"?pin={pin}"

    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            log.info("Remote token fetch: HTTP %d — %s", resp.status_code, resp.text[:100])
            return None
        data = resp.json()
        token = data.get("access_token")
        if token:
            auth_code = data.get("auth_code", "")
            log.info("Fetched valid token from %s (user=%s)", server_url, data.get("user", "?"))
            # Save auth code to .env if present
            if auth_code:
                env_path = find_dotenv(usecwd=True)
                if env_path:
                    set_key(env_path, "UPSTOX_AUTH_CODE", auth_code)
                os.environ["UPSTOX_AUTH_CODE"] = auth_code
            return token
    except Exception as exc:
        log.info("Remote token fetch failed: %s", exc)
    return None


def _load_existing_token() -> str | None:
    """Try to load a token from token file, env var, or remote server."""
    # 1. Token file (written by Railway auth server)
    token = read_token_file()
    if token:
        return token

    # 2. Env var / .env
    load_dotenv(find_dotenv(usecwd=True))
    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    if token:
        return token

    # 3. Remote auth server (Railway)
    token = fetch_remote_token()
    if token:
        save_token(token)  # cache locally
        return token

    return None


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

    Checks token file and .env first. If expired and *interactive* is True,
    prompts for browser login. If *interactive* is False (headless/Railway),
    raises RuntimeError instead of prompting.

    Args:
        interactive: If True, prompt for manual login when token is expired.
                     If False, raise RuntimeError (for cron jobs / Railway workers).
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
        "No valid token found. Login via the auth server first: "
        "visit /login on your Railway app."
    )

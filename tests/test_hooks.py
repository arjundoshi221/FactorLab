"""tools/hooks: the pre-commit checks catch real secrets and ignore ordinary code."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_no_plaintext_secrets", REPO / "tools" / "hooks" / "check_no_plaintext_secrets.py"
)
assert spec is not None and spec.loader is not None
secrets_hook = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = secrets_hook
spec.loader.exec_module(secrets_hook)


@pytest.mark.parametrize(
    "line",
    [
        'url = "https://eodhd.com/api/eod/AAPL.US?api_token=0123456789abcdef0123&fmt=json"',
        "GET /v2/quotes?symbol=AAPL&token=AbCdEf0123456789xyz HTTP/1.1",
        'API_KEY = "sk_live_0123456789abcdefghij"',
        "{'access_token': 'eyJhbGciOiJIUzI1NiJ9abcdef'}",
        'token: "ghp_0123456789abcdefghijklmn"',
    ],
)
def test_real_secrets_are_blocked(tmp_path, line):
    path = tmp_path / "leak.py"
    path.write_text(line + "\n", encoding="utf-8")
    assert secrets_hook.scan(path)


@pytest.mark.parametrize(
    "line",
    [
        "token = read_token_file()",
        'factorlab_api_key = _require_string(secrets, "FACTORLAB_API_KEY")',
        "FACTORLAB_API_KEY: factorlabApiKey,",
        'API_KEY = "REDACTED"',
        "url = f'{base}?api_token=<your_key_here>'",
        "token = _provider_token(node.value)",
    ],
)
def test_ordinary_code_passes(tmp_path, line):
    path = tmp_path / "ok.py"
    path.write_text(line + "\n", encoding="utf-8")
    assert secrets_hook.scan(path) == []

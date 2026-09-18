from factorlab.core.secrets import get_secret, require_secret


def test_secret_file_takes_precedence_over_environment(tmp_path, monkeypatch):
    secret_file = tmp_path / "API_KEY"
    secret_file.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("FACTORLAB_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("API_KEY", "from-environment")

    assert get_secret("API_KEY") == "from-file"


def test_environment_is_local_development_fallback(monkeypatch):
    monkeypatch.delenv("FACTORLAB_SECRETS_DIR", raising=False)
    monkeypatch.setenv("API_KEY", "from-environment")

    assert get_secret("API_KEY") == "from-environment"


def test_required_secret_rejects_missing_value(monkeypatch):
    monkeypatch.delenv("FACTORLAB_SECRETS_DIR", raising=False)
    monkeypatch.delenv("MISSING_KEY", raising=False)

    try:
        require_secret("MISSING_KEY")
    except OSError as exc:
        assert "MISSING_KEY" in str(exc)
    else:
        raise AssertionError("require_secret accepted a missing secret")

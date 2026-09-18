import requests

from factorlab.sources.upstox.client import UpstoxSession


def _response(status_code: int) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = b""
    response._content_consumed = True
    return response


def test_session_adopts_token_from_runtime_secret(monkeypatch):
    session = UpstoxSession("old-token")
    monkeypatch.setattr("factorlab.sources.upstox.client.get_secret", lambda *args: "new-token")
    observed = []

    def fake_request(instance, method, url, *args, **kwargs):
        observed.append(instance.headers["Authorization"])
        return _response(200)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    session.get("https://example.test")

    assert observed == ["Bearer new-token"]


def test_session_retries_401_once_when_token_rotates(monkeypatch):
    session = UpstoxSession("old-token")
    available_tokens = iter(["old-token", "new-token"])
    monkeypatch.setattr(
        "factorlab.sources.upstox.client.get_secret",
        lambda *args: next(available_tokens),
    )
    observed = []

    def fake_request(instance, method, url, *args, **kwargs):
        observed.append(instance.headers["Authorization"])
        return _response(401 if len(observed) == 1 else 200)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    response = session.get("https://example.test")

    assert response.status_code == 200
    assert observed == ["Bearer old-token", "Bearer new-token"]


def test_session_does_not_automatically_retry_post(monkeypatch):
    session = UpstoxSession("old-token")
    available_tokens = iter(["old-token", "new-token"])
    monkeypatch.setattr(
        "factorlab.sources.upstox.client.get_secret",
        lambda *args: next(available_tokens),
    )
    calls = []

    def fake_request(instance, method, url, *args, **kwargs):
        calls.append((method, instance.headers["Authorization"]))
        return _response(401)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    response = session.post("https://example.test/orders", json={"quantity": 1})

    assert response.status_code == 401
    assert calls == [("POST", "Bearer old-token")]

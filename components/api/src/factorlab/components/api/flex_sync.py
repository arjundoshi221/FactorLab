"""Hub proxy to the ingest-broker Flex runner: start an IBKR Flex history sync, read its state.

The api never calls IBKR. ``ibkr-flex-runner`` (backend network, VPS IP, Flex token)
runs the sync; these routes forward to it and pass its status codes through
(202 started, 409 already running, 429 cooldown). Cloudflare Access is the only
authentication, as for every ``/hub/api/v1`` route; the POST also has the portfolio
cross-site guard.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic_settings import SettingsConfigDict

from factorlab.components.api.portfolio_tagging import same_origin
from factorlab.core.settings import FactorLabSettings

DEFAULT_RUNNER_URL = "http://ibkr-flex-runner:8091"
TIMEOUT_SECONDS = 5.0

router = APIRouter()


class FlexRunnerSettings(FactorLabSettings):
    """``IBKR_FLEX_RUNNER_URL``: the ingest-broker ``ibkr-flex-runner`` service."""

    model_config = SettingsConfigDict(env_prefix="IBKR_FLEX_RUNNER_")

    url: str = DEFAULT_RUNNER_URL


def runner_url() -> str:
    return (FlexRunnerSettings().url or DEFAULT_RUNNER_URL).rstrip("/")


def forward(method: str, path: str) -> JSONResponse:
    request = urllib.request.Request(f"{runner_url()}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    except (OSError, ValueError) as exc:
        raise HTTPException(
            503, f"Flex runner unavailable (ibkr profile off?): {type(exc).__name__}"
        ) from exc
    try:
        payload: Any = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(502, "Flex runner returned invalid JSON") from exc
    return JSONResponse(payload, status_code=status)


@router.post("/flex/runs")
def start_flex_sync(request: Request) -> JSONResponse:
    same_origin(request)
    return forward("POST", "/runs")


@router.get("/flex/runs/latest")
def latest_flex_sync() -> JSONResponse:
    return forward("GET", "/runs/latest")

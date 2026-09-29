"""``python -m factorlab.components.api``: serve the API with FactorLab's JSON logging.

uvicorn's own loggers propagate into the handlers :func:`configure_logging` installs,
and the app's middleware writes one access line per request, so uvicorn's access
log is off.
"""

from __future__ import annotations

import argparse

import uvicorn
from pydantic_settings import SettingsConfigDict

from factorlab.core.logging import configure_logging
from factorlab.core.settings import FactorLabSettings


class ApiServerSettings(FactorLabSettings):
    """``FACTORLAB_API_HOST`` / ``FACTORLAB_API_PORT`` (defaults match compose)."""

    model_config = SettingsConfigDict(env_prefix="FACTORLAB_API_")

    host: str = "0.0.0.0"  # noqa: S104 - inside the container; compose publishes loopback only
    port: int = 8000


def main(argv: list[str] | None = None) -> int:
    settings = ApiServerSettings()
    parser = argparse.ArgumentParser(prog="factorlab-api", description="Serve the FactorLab API.")
    parser.add_argument("--host", default=settings.host, help="bind address (FACTORLAB_API_HOST)")
    parser.add_argument("--port", type=int, default=settings.port,
                        help="bind port (FACTORLAB_API_PORT)")
    args = parser.parse_args(argv)
    configure_logging(component="api", service="api")
    uvicorn.run("factorlab.components.api.app:app", host=args.host, port=args.port,
                log_config=None, access_log=False, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

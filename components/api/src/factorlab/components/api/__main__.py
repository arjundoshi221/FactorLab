"""``python -m factorlab.components.api``: serve the API with FactorLab's JSON logging.

uvicorn's own loggers propagate into the handlers :func:`configure_logging` installs,
and the app's middleware writes one access line per request, so uvicorn's access
log is off.
"""

from __future__ import annotations

import uvicorn
from pydantic_settings import SettingsConfigDict

from factorlab.core.logging import configure_logging
from factorlab.core.settings import FactorLabSettings


class ApiServerSettings(FactorLabSettings):
    """``FACTORLAB_API_HOST`` / ``FACTORLAB_API_PORT`` (defaults match compose)."""

    model_config = SettingsConfigDict(env_prefix="FACTORLAB_API_")

    host: str = "0.0.0.0"  # noqa: S104 - inside the container; compose publishes loopback only
    port: int = 8000


def main() -> int:
    configure_logging(component="api", service="api")
    settings = ApiServerSettings()
    uvicorn.run("factorlab.components.api.app:app", host=settings.host, port=settings.port,
                log_config=None, access_log=False, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

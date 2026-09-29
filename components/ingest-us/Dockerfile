# syntax=docker/dockerfile:1.7
# ghcr.io/arjundoshi221/factorlab-ingest-us: US universe resolution and daily/1-min bars into ClickHouse v2.
# Build from the repo root (components/ingest-us/Dockerfile.dockerignore trims the context):
#   docker build -f components/ingest-us/Dockerfile -t factorlab-ingest-us .
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ghcr.io/astral-sh/uv:0.12.20 AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY . .
# Only this component's dependency closure, installed non-editable from uv.lock.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package factorlab-component-ingest-us

FROM ${PYTHON_IMAGE}
RUN groupadd --system --gid 10001 factorlab \
 && useradd --system --uid 10001 --gid factorlab --no-create-home \
      --home-dir /nonexistent --shell /usr/sbin/nologin factorlab \
 && mkdir -p /app/data && chown 10001:10001 /app/data
COPY --from=build /opt/venv /opt/venv
COPY configs /app/configs
WORKDIR /app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FACTORLAB_HOME=/app \
    FACTORLAB_COMPONENT=ingest-us
USER 10001:10001
HEALTHCHECK NONE
ENTRYPOINT ["factorlab-ingest-us"]

# Release metadata last, so it never invalidates the layers above.
ARG VERSION=0.0.0-dev
ARG COMMIT=unknown
ARG CREATED=""
ENV FACTORLAB_VERSION=$VERSION \
    FACTORLAB_COMMIT=$COMMIT
LABEL org.opencontainers.image.title="factorlab-ingest-us" \
      org.opencontainers.image.description="US universe resolution and daily/1-min bars into ClickHouse v2." \
      org.opencontainers.image.source="https://github.com/arjundoshi221/FactorLab" \
      org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$COMMIT \
      org.opencontainers.image.created=$CREATED \
      io.factorlab.component="ingest-us"

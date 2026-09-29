# syntax=docker/dockerfile:1.7
# ghcr.io/arjundoshi221/factorlab-ingest-political: Congressional references, House filing index and PTR trades into ClickHouse v2.
# Build from the repo root (components/ingest-political/Dockerfile.dockerignore trims the context):
#   docker build -f components/ingest-political/Dockerfile -t factorlab-ingest-political .
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
    uv sync --frozen --no-dev --no-editable --package factorlab-component-ingest-political

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
    FACTORLAB_COMPONENT=ingest-political
USER 10001:10001
HEALTHCHECK NONE
ENTRYPOINT ["factorlab-ingest-political"]

# Release metadata last, so it never invalidates the layers above.
ARG VERSION=0.0.0-dev
ARG COMMIT=unknown
ARG CREATED=""
ENV FACTORLAB_VERSION=$VERSION \
    FACTORLAB_COMMIT=$COMMIT
LABEL org.opencontainers.image.title="factorlab-ingest-political" \
      org.opencontainers.image.description="Congressional references, House filing index and PTR trades into ClickHouse v2." \
      org.opencontainers.image.source="https://github.com/arjundoshi221/FactorLab" \
      org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$COMMIT \
      org.opencontainers.image.created=$CREATED \
      io.factorlab.component="ingest-political"

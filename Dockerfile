# syntax=docker/dockerfile:1.7
# Interim monolith image: every workspace member in one image, still deployed by the
# legacy release.yml + deploy-release.sh path until each component ships its own
# image (components/<name>/Dockerfile). Compose supplies each service's command.

FROM node:22-bookworm-slim AS web-builder
WORKDIR /web
COPY components/web/package.json components/web/package-lock.json ./
RUN npm ci
COPY components/web/ ./
RUN npm run build


FROM ghcr.io/astral-sh/uv:0.12.20 AS uv


FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY libs ./libs
COPY providers ./providers
COPY components ./components
# Non-editable: the venv holds real copies, nothing points back at /src. Installing the
# seven Python components pulls in every library and provider they use, and nothing
# dev-only (factorlab-testkit, pytest).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
      --package factorlab-component-api \
      --package factorlab-component-secrets-agent \
      --package factorlab-component-ingest-india \
      --package factorlab-component-ingest-us \
      --package factorlab-component-ingest-political \
      --package factorlab-component-ingest-broker \
      --package factorlab-component-schema-migrator


FROM python:3.12-slim
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY scripts ./scripts
COPY configs ./configs
COPY --from=web-builder /web/dist /app/web-dist

# FACTORLAB_HOME anchors data/, logs/ and configs/ at /app; the packages run from the
# venv (the ClickHouse SQL ships inside factorlab-schema as package data).
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FACTORLAB_HOME=/app \
    FACTORLAB_WEB_DIST=/app/web-dist

# Release metadata last, so it never invalidates the dependency layers above.
ARG FACTORLAB_RELEASE_ID=""
ARG FACTORLAB_COMMIT=""
ENV FACTORLAB_RELEASE_ID=$FACTORLAB_RELEASE_ID \
    FACTORLAB_COMMIT=$FACTORLAB_COMMIT
LABEL org.opencontainers.image.title="FactorLab" \
      org.opencontainers.image.source="https://github.com/arjundoshi221/FactorLab" \
      org.opencontainers.image.version=$FACTORLAB_RELEASE_ID \
      org.opencontainers.image.revision=$FACTORLAB_COMMIT

# Every Compose service supplies its own command; the default serves the API.
CMD ["python", "-m", "factorlab.components.api"]

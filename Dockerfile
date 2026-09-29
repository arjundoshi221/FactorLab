FROM node:22-bookworm-slim AS web-builder

WORKDIR /web

COPY src/factorlab/webui/package.json src/factorlab/webui/package-lock.json ./
RUN npm ci

COPY src/factorlab/webui/ ./
RUN npm run build


FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts
COPY configs ./configs
COPY --from=web-builder /web/dist /app/web-dist

RUN pip install --no-cache-dir .

# FACTORLAB_HOME anchors data/, logs/ and configs/ at /app; the package itself runs
# from site-packages (the ClickHouse SQL ships inside it as package data).
ENV FACTORLAB_HOME=/app \
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
CMD ["uvicorn", "factorlab.components.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

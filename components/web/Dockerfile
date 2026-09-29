# syntax=docker/dockerfile:1.7
# ghcr.io/arjundoshi221/factorlab-web: the hub UI as static files behind non-root nginx.
# Build from the repo root:
#   docker build -f components/web/Dockerfile -t factorlab-web .
FROM node:22-bookworm-slim AS build
WORKDIR /web
COPY components/web/package.json components/web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY components/web/ ./
ARG VERSION=0.0.0-dev
ARG COMMIT=unknown
ENV VITE_FACTORLAB_VERSION=$VERSION \
    VITE_FACTORLAB_COMMIT=$COMMIT
RUN npm run build \
 && printf '{"component":"web","version":"%s","commit":"%s"}\n' "$VERSION" "$COMMIT" \
      > dist/version.json

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY components/web/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -q --spider http://127.0.0.1:8080/healthz || exit 1

ARG VERSION=0.0.0-dev
ARG COMMIT=unknown
ARG CREATED=""
LABEL org.opencontainers.image.title="factorlab-web" \
      org.opencontainers.image.description="FactorLab hub UI (static)" \
      org.opencontainers.image.source="https://github.com/arjundoshi221/FactorLab" \
      org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$COMMIT \
      org.opencontainers.image.created=$CREATED \
      io.factorlab.component="web"

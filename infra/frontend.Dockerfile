# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS web
WORKDIR /src/frontend
COPY frontend/ ./
COPY contracts/ /src/contracts/
RUN --mount=type=cache,target=/root/.npm \
    if [ ! -f dist/index.html ]; then npm ci --no-audit --no-fund && npm run build; fi

FROM nginx:stable-alpine
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=web /src/frontend/dist/ /srv/frontend/

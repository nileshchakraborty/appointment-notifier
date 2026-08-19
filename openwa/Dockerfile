# syntax=docker/dockerfile:1.7

FROM node:22-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    DO_NOT_TRACK=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    TURBO_TELEMETRY_DISABLED=1 \
    LIGHTPANDA_EXECUTABLE_PATH=/bin/false \
    PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ git make python3 \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global pnpm@11.9.0

WORKDIR /src
COPY openwa-fork/ ./

RUN pnpm install --frozen-lockfile \
    && pnpm exec turbo build --filter='@open-wa/wa-automate...'

RUN LIGHTPANDA_EXECUTABLE_PATH=/bin/false \
    pnpm --config.inject-workspace-packages=true \
    --filter @open-wa/wa-automate deploy --prod /prod/openwa

FROM node:22-bookworm-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_ENV=production \
    IS_DOCKER=true \
    DO_NOT_TRACK=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    TURBO_TELEMETRY_DISABLED=1 \
    WA_DISABLE_SPINS=true \
    WA_EXECUTABLE_PATH=/usr/bin/chromium \
    WA_USE_CHROME=true \
    PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium curl fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app

COPY --from=builder --chown=node:node /prod/openwa ./

RUN mkdir -p /config /sessions /home/node/Downloads \
    && chown -R node:node /config /sessions /home/node

USER node

EXPOSE 8081

CMD ["node", "dist/cli.cjs", "--config", "/config/wa.config.json"]

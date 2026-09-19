# Dashboard frontend — build context is this directory (dashboard/frontend).
#
# Four stages, two of them useful as targets:
#
#   --target dev      Vite dev server with HMR. What docker-compose runs by
#                     default, with this directory bind-mounted over /app.
#   (default)         nginx serving the production bundle: gzip, immutable
#                     asset caching, and /api load-balanced across one or more
#                     backend replicas. See docker/nginx.conf.
#
# The backend image lives in the repository root Dockerfile; nothing here
# needs Python, so this context stays limited to the frontend.

# ---------- deps ----------
# Split from the source copy so a source-only change does not reinstall.
FROM node:22-alpine AS deps

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci


# ---------- dev ----------
FROM deps AS dev

ENV CHOKIDAR_USEPOLLING=true

COPY . /app

EXPOSE 5173

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD wget -q --spider http://localhost:5173/ || exit 1

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]


# ---------- build ----------
FROM deps AS build

# Baked into the bundle at build time, so it cannot be changed by the runtime
# environment. Leave it empty for the normal setup, where nginx proxies /api on
# the same origin; set it only to point a build at a backend on another host.
ARG VITE_API_ORIGIN=""
ENV VITE_API_ORIGIN=$VITE_API_ORIGIN

COPY . /app

# `prebuild` regenerates src/theme.css first (scripts/gen-theme.mjs).
RUN npm run build


# ---------- runtime ----------
FROM nginx:1.27-alpine AS nginx

# Where the SPA is served from; nginx.conf points its root here.
COPY --from=build /app/dist /usr/share/nginx/html

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
# Runs before nginx starts (stock entrypoint sources /docker-entrypoint.d/*.sh)
# and writes the generated upstream + cache includes from the environment.
COPY docker/20-agents-hub-config.sh /docker-entrypoint.d/20-agents-hub-config.sh
RUN chmod +x /docker-entrypoint.d/20-agents-hub-config.sh \
    && mkdir -p /var/cache/nginx/agents_hub_api \
    && chown -R nginx:nginx /var/cache/nginx

# Space-separated `host:port` list the /api traffic is balanced over. One entry
# is the normal case; add more to put several backend replicas behind nginx.
ENV BACKEND_SERVERS="backend:8000"
# Seconds nginx may serve a cached view asset (GET /api/views/*/assets/*, the
# only API path cached by default). 0 disables that cache.
ENV API_ASSET_CACHE_SECONDS=30

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -q --spider http://localhost/nginx-health || exit 1

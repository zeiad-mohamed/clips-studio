# Production image of the renderer: `vite build` the web bundle, then serve
# it with nginx. nginx also reverse-proxies every non-static request (the
# REST API and the /ws WebSocket) to the engine service, so from the
# browser's point of view the whole app is same-origin — no CORS, no
# hard-coded engine host, works behind any reverse proxy (Coolify, Traefik).

FROM node:20-slim AS build

WORKDIR /ui

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# '' (default) = same-origin: API calls become relative and nginx proxies
# them. Set to a full origin (https://engine.example.com) only if you serve
# the UI and the engine on different domains.
ARG VITE_API_BASE=""
ENV VITE_API_BASE=${VITE_API_BASE}

# Plain `vite build` reads vite.config.mts — the web-only config. The
# `build` npm script is electron-vite, which builds the desktop app.
RUN npx vite build


FROM nginx:alpine

# Where nginx forwards API/WebSocket traffic. Matches the compose service
# name; override if your engine lives elsewhere.
ARG ENGINE_ORIGIN=http://engine:8765
ENV ENGINE_ORIGIN=${ENGINE_ORIGIN}

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /ui/src/renderer/dist /usr/share/nginx/html

EXPOSE 80

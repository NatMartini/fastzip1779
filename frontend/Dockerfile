FROM node:22-alpine AS build
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /web/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile
ENV SITE_ADDRESS=:80
EXPOSE 80 443
HEALTHCHECK --interval=20s --timeout=5s CMD wget -q -O /dev/null http://localhost:8081/health || exit 1

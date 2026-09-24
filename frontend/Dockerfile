# Debian (glibc) build stage, not alpine: musl's resolver fails the whole lookup
# when a router answers AAAA queries with NXDOMAIN, so npm can't reach the
# registry ("Exit handler never called!" / ENOTFOUND) on such networks.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
# Explicit vite check: npm can exit 0 with node_modules half-installed.
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund && test -x node_modules/.bin/vite
COPY . .
# Absolute base for the web build (vite.config's './' is for Electron file://).
RUN npx vite build --base=/ \
 && npx vite build --config vite.portal.config.js \
 && node scripts/portal-dist.mjs

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY --from=build /app/dist-portal /usr/share/nginx/portal
ENV NGINX_PORT=80 \
    PORTAL_PORT=81 \
    BACKEND_URL=http://backend:8000
COPY nginx.conf /etc/nginx/templates/default.conf.template
EXPOSE 80 81
CMD ["nginx", "-g", "daemon off;"]

# ---- build ----
FROM node:20-alpine AS build
WORKDIR /app

# lockfile first so a source change does not re-resolve the dependency tree
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .

# Vite reads VITE_* from the environment at build time and inlines them, so
# these have to be ARGs and they have to be exported before `npm run build` -
# an ARG alone is not in the shell environment that npm sees.
#
# AFTER the npm ci layer on purpose. A new version number is a new value for
# these on every single deploy, and putting them any earlier would invalidate
# the dependency cache each time and turn a 40-second rebuild into four minutes.
ARG VITE_APP_VERSION=unknown
ARG VITE_APP_ENV=unknown
RUN VITE_APP_VERSION="$VITE_APP_VERSION" VITE_APP_ENV="$VITE_APP_ENV" npm run build

# ---- serve ----
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

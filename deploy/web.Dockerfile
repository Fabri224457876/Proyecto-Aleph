# Las dos consolas, compiladas y servidas por nginx. Contexto de build: la raíz del repositorio.
FROM node:24-alpine AS build
WORKDIR /src
COPY frontend-simple/package.json frontend-simple/package-lock.json frontend-simple/
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend-simple && npm ci && cd ../frontend && npm ci
COPY frontend-simple frontend-simple
COPY frontend frontend
RUN cd frontend-simple && npm run build && cd ../frontend && npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/aleph-common.inc /etc/nginx/conf.d/aleph-common.inc
COPY --from=build /src/frontend-simple/dist /usr/share/nginx/simple
COPY --from=build /src/frontend/dist /usr/share/nginx/completa
EXPOSE 8101 8102

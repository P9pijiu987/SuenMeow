FROM registry-1.docker.io/library/node:22-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build
FROM registry-1.docker.io/nginxinc/nginx-unprivileged:1.28-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY --chmod=644 deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080

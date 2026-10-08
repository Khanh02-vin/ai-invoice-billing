# Frontend production image: build React (Vite) rồi phục vụ bằng Caddy.
#
# Caddy tự lấy + gia hạn cert Let's Encrypt khi SITE_ADDRESS là domain thật
# (đặt trong docker-compose / k8s env), nên không cần certbot riêng.
# Dev không dùng image này — README hướng dẫn `npm run dev`.
FROM node:20-alpine AS build

WORKDIR /app

# Copy manifest trước để tận dụng layer cache khi chỉ sửa source
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# --- Runtime ---
FROM caddy:2-alpine

COPY --from=build /app/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
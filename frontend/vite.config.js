import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxy các route API sang backend FastAPI (cùng path như production).
const API_TARGET = "http://localhost:8005";

const DEV_PROXY = {
  "/auth": API_TARGET,
  "/places": API_TARGET,
  "/spends": API_TARGET,
  "/discover": API_TARGET,
  "/health": API_TARGET,
};

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    port: 5173,
    // Nghe mọi interface: cần cho điện thoại vào qua LAN / Tailscale.
    host: true,
    // Vite chặn host lạ mặc định — mở cho tên miền tunnel (Tailscale / Cloudflare).
    allowedHosts: [".ts.net", ".trycloudflare.com"],
    proxy: DEV_PROXY,
  },
  // Bản build (vite preview) — IS_DEV=false nên không lộ nút reset-password dev.
  // Cùng proxy API như dev để chạy thử qua tunnel mà không cần Caddy.
  preview: {
    port: 5175,
    host: true,
    allowedHosts: [".ts.net", ".trycloudflare.com"],
    proxy: DEV_PROXY,
  },
});

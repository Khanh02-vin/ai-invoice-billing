import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxy các route API sang backend FastAPI (cùng path như production).
// Port 8004 trùng với cấu hình dev bạn đang dùng; đổi thành 8000 nếu chạy backend local.
const API_TARGET = "http://localhost:8004";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    port: 5173,
    // Nghe mọi interface: cần cho điện thoại vào qua LAN / Tailscale.
    host: true,
    // Vite chặn host lạ mặc định — mở cho tên miền tunnel (Tailscale / Cloudflare).
    allowedHosts: [".ts.net", ".trycloudflare.com"],
    proxy: {
      "/auth": API_TARGET,
      "/invoices": API_TARGET,
      "/payments": API_TARGET,
      "/push": API_TARGET,
      "/reports": API_TARGET,
      "/orgs": API_TARGET,
      "/search": API_TARGET,
      "/health": API_TARGET,
      "/eval": API_TARGET,
    },
  },
});

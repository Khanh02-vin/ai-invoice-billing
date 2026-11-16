// Service worker tối giản cho PWA Quanh Ta.
// Chiến lược: chỉ cache shell tĩnh (index.html, JS/CSS build có hash, icons, manifest).
// API (/auth, /spends, /places, /discover, /health, /ready) KHÔNG BAO GIỜ cache —
// dữ liệu tài chính + JWT phải luôn lấy từ server thật.
const CACHE = "quanh-ta-shell-v1";
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // QR/VietQR/SMTP — để trình duyệt xử lý

  // API: network-only (không cache dữ liệu nhạy cảm)
  const isApi = /^\/(auth|spends|places|discover|health|ready)(\/|$)/.test(url.pathname);
  if (isApi) return;

  // Điều hướng (mở/trang): network-first, mất mạng thì fallback index.html (SPA offline)
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put("/index.html", copy));
          return res;
        })
        .catch(() => caches.match("/index.html").then((c) => c || caches.match("/"))),
    );
    return;
  }

  // Shell tĩnh: cache-first, fallback network rồi cache lại
  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req).then((res) => {
        if (res.ok && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      });
    }),
  );
});

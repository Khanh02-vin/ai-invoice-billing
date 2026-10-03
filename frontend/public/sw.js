/* Service worker: PWA + Web Push.
   Không cache asset — chỉ nhận push và mở app khi bấm vào thông báo. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

// Không cache gì — fetch handler rỗng để browser dùng mạng mặc định.
// Chrome yêu cầu service worker có fetch handler mới coi PWA là "cài được".
self.addEventListener("fetch", () => {});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Invoice & Billing", {
      body: data.body || "",
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      const existing = list[0];
      if (existing) return existing.focus();
      return self.clients.openWindow(target);
    })
  );
});

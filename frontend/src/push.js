// Web Push helper — đăng ký nhận thông báo native (PWA).
// Lưu ý: service worker + push chỉ chạy trên HTTPS (hoặc localhost), và trên
// iPhone phải "Thêm vào màn hình chính" rồi mở từ icon (iOS 16.4+).
import { api } from "./api";

export function pushSupported() {
  return (
    typeof navigator !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

async function activeRegistration() {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration();
  return reg || null;
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

/** "on" | "off" | "blocked" | "unsupported" */
export async function pushStatus() {
  if (!pushSupported()) return "unsupported";
  const reg = await activeRegistration();
  if (!reg) return "off";
  const sub = await reg.pushManager.getSubscription();
  if (sub && Notification.permission === "granted") return "on";
  if (Notification.permission === "denied") return "blocked";
  return "off";
}

/** Bật thông báo đẩy: xin quyền -> subscribe -> lưu subscription lên server. */
export async function enablePush() {
  const reg = await activeRegistration();
  if (!reg) {
    throw new Error(
      "Không đăng ký được service worker — hãy mở app qua HTTPS (hoặc localhost)."
    );
  }
  const { enabled, key } = await api("/push/vapid-public-key");
  if (!enabled) {
    throw new Error("Server chưa cấu hình VAPID key — xem README mục Thông báo đẩy.");
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Bạn đã từ chối quyền thông báo.");
  }
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(key),
  });
  await api("/push/subscribe", {
    method: "POST",
    body: JSON.stringify(sub.toJSON()),
  });
  return "on";
}

/** Tắt thông báo đẩy trên thiết bị này (xoá subscription khỏi server + browser). */
export async function disablePush() {
  const reg = await activeRegistration();
  if (!reg) return "off";
  const sub = await reg.pushManager.getSubscription();
  if (sub) {
    try {
      await api("/push/unsubscribe", {
        method: "POST",
        body: JSON.stringify({ endpoint: sub.endpoint }),
      });
    } catch (e) {
      // Server lỗi vẫn gỡ phía browser để user không bị kẹt
    }
    await sub.unsubscribe();
  }
  return "off";
}

export async function sendTestPush() {
  return api("/push/test", { method: "POST" });
}

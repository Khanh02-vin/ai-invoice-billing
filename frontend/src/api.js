// Fetch wrapper: tự gắn Authorization header từ token lưu localStorage.
const TOKEN_KEY = "invoice_token";

// Con số duy nhất được phép hiển thị cạnh tiền: đây là tổng bill chia số người,
// KHÔNG phải giá món hay giá niêm yết.
export const SPEND_LABEL = "mức chi ghi nhận";

const VND = new Intl.NumberFormat("vi-VN", {
  style: "currency",
  currency: "VND",
  maximumFractionDigits: 0,
});

export function fmtVnd(value) {
  return value == null ? "—" : VND.format(value);
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

// Địa chỉ backend. Dev (vite proxy) để trống → gọi tương đối.
// Bản Capacitor/PWA triển khai: bake VITE_API_BASE=https://domain-cua-ban vào lúc build.
export const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/+$/, "");

export async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData) && !(options.body instanceof Blob)) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("auth-expired"));
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error?.message || data.detail || "Lỗi yêu cầu");
  return data;
}

// Nén ảnh biên lai về ≤1280px JPEG (tối ưu dung lượng trước khi upload).
export async function compressImage(file, maxSide = 1280, quality = 0.8) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", quality);
}

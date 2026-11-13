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

export async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData) && !(options.body instanceof Blob)) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("auth-expired"));
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error?.message || data.detail || "Lỗi yêu cầu");
  return data;
}

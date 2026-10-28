// Fetch wrapper: tự gắn Authorization header từ token lưu localStorage.
const TOKEN_KEY = "invoice_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  // Đổi danh tính = bỏ ngữ cảnh org (không cho user kế thừa header của user trước)
  localStorage.removeItem("active_org_id");
}

export async function api(path, options = {}) {
  // Không gán Content-Type khi body là FormData — fetch tự thêm boundary multipart.
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData) && !(options.body instanceof Blob)) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  // Ngữ cảnh tổ chức (multi-tenant): request mang X-Organization-Id khi đã chọn org
  const orgId = localStorage.getItem("active_org_id");
  if (orgId) headers["X-Organization-Id"] = orgId;

  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("auth-expired"));
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error?.message || data.detail || "Lỗi yêu cầu");
  return data;
}

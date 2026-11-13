// Nhãn hiển thị dùng chung giữa các view.
export const CATEGORY_LABELS = {
  cafe: "Cà phê",
  restaurant: "Quán ăn",
  drink: "Trà sữa / nước uống",
  bakery: "Tiệm bánh",
  bar: "Bar",
  other: "Khác",
};

export function categoryLabel(value) {
  return CATEGORY_LABELS[value] || "Khác";
}

export function distanceText(km) {
  if (km == null) return "";
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
}

// Chỉ mở trang tìm kiếm TikTok trong tab mới — app không đọc hay lấy dữ liệu từ đó.
export function tiktokSearchUrl(name) {
  return `https://www.tiktok.com/search?q=${encodeURIComponent(name || "")}`;
}

import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")).render(<App />);

// PWA: đăng ký service worker (chỉ cache shell tĩnh — API/tài chính luôn network-only).
// SW cần HTTPS hoặc localhost — dev LAN HTTP thì trình duyệt sẽ tự bỏ qua đăng ký.
if ("serviceWorker" in navigator && (window.isSecureContext || location.hostname === "localhost")) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Không làm gián đoạn app nếu trình duyệt từ chối SW (vd: HTTP LAN)
    });
  });
}

import { useState, useEffect, useRef } from "react";
import { api, setToken } from "./api";
import Aurora from "./components/react-bits/Aurora.jsx";
import SpotlightCard from "./components/react-bits/SpotlightCard.jsx";

export default function Login({ onLogin }) {
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);

  const errorRef = useRef(null);
  const usernameRef = useRef(null);

  // Focus error message when it appears
  useEffect(() => {
    if (error && errorRef.current) {
      errorRef.current.focus();
    }
  }, [error]);

  const isLogin = mode === "login";

  async function submit(e) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setNotice("");
    try {
      const payload = { username, password };
      if (!isLogin && email) payload.email = email;
      const res = await api("/auth/" + mode, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (isLogin) {
        setToken(res.access_token);
        onLogin();
        return;
      }
      // Đăng ký: tài khoản phải xác minh email trước khi gọi được API,
      // nên không vào app ngay — báo user kiểm tra hộp thư.
      setMode("login");
      setPassword("");
      setNotice(
        res.verification_sent
          ? "Tài khoản đã tạo. Kiểm tra email, bấm link xác nhận rồi quay lại đăng nhập."
          : "Tài khoản đã tạo nhưng chưa gửi được email xác nhận (máy chủ chưa cấu hình SMTP). Liên hệ quản trị viên để kích hoạt."
      );
    } catch (err) {
      setError(err.message);
      // Focus username field on error for better keyboard navigation
      if (usernameRef.current) {
        usernameRef.current.focus();
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-wrap">
      <Aurora />
      <SpotlightCard className="auth-card">
        <form onSubmit={submit} noValidate>
          <h1>🧾 Invoice &amp; Billing</h1>
          <h2>{isLogin ? "Đăng nhập" : "Đăng ký"}</h2>

          <div className="form-group">
            <label htmlFor="login-username" id="login-username-label">
              Tên đăng nhập <span className="required" aria-hidden="true">*</span>
              <span className="sr-only">(bắt buộc)</span>
            </label>
            <input
              ref={usernameRef}
              id="login-username"
              type="text"
              placeholder="Nhập tên đăng nhập"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              aria-required="true"
              aria-invalid={!!error}
              aria-describedby={error ? "login-error" : "login-username-label"}
              autoComplete="username"
            />
          </div>

          <div className="form-group">
            <label htmlFor="login-password" id="login-password-label">
              Mật khẩu <span className="required" aria-hidden="true">*</span>
              <span className="sr-only">(bắt buộc, tối thiểu 6 ký tự)</span>
            </label>
            <input
              id="login-password"
              type="password"
              placeholder="Nhập mật khẩu"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
              aria-required="true"
              aria-invalid={!!error}
              aria-describedby={error ? "login-error" : "login-password-label"}
              autoComplete={isLogin ? "current-password" : "new-password"}
            />
          </div>

          {!isLogin && (
            <div className="form-group">
              <label htmlFor="login-email" id="login-email-label">
                Email <span className="required" aria-hidden="true">*</span>
                <span className="sr-only">(bắt buộc, để nhận link xác minh)</span>
              </label>
              <input
                id="login-email"
                type="email"
                placeholder="ban@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                aria-required="true"
                aria-describedby="login-email-label"
                autoComplete="email"
              />
            </div>
          )}

          {error && (
            <div
              id="login-error"
              className="err"
              role="alert"
              aria-live="assertive"
              ref={errorRef}
              tabIndex={-1}
            >
              ⚠ {error}
            </div>
          )}

          {notice && (
            <div className="msg ok" role="status" aria-live="polite">
              {notice}
            </div>
          )}

          <button type="submit" disabled={loading} aria-busy={loading}>
            {loading ? "Đang xử lý..." : isLogin ? "Đăng nhập" : "Tạo tài khoản"}
          </button>

          <p className="toggle">
            {isLogin ? "Chưa có tài khoản? " : "Đã có tài khoản? "}
            <button
              type="button"
              onClick={() => { setMode(isLogin ? "register" : "login"); setError(""); setNotice(""); }}
              className="link-button"
              aria-label={isLogin ? "Chuyển sang chế độ đăng ký" : "Chuyển sang chế độ đăng nhập"}
            >
              {isLogin ? "Đăng ký" : "Đăng nhập"}
            </button>
          </p>
        </form>
      </SpotlightCard>
    </div>
  );
}

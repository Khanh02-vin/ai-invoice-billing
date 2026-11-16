import { useState, useEffect, useRef } from "react";
import { api, setToken, API_BASE } from "./api";

const GOOGLE_AUTH_URL = import.meta.env.VITE_GOOGLE_AUTH_URL || "/auth/google/start";
// Chỉ bản dev (npm run dev) mới có đặt lại mật khẩu trực tiếp không cần email.
// Bản production build vẫn dùng luồng email — nếu không, ai vào trang cũng
// đổi được mật khẩu mọi tài khoản.
const IS_DEV = import.meta.env.DEV;

const MODE_TITLE = {
  login: "Đăng nhập",
  register: "Đăng ký",
  forgot: "Quên mật khẩu",
  reset: "Đặt lại mật khẩu",
};

// Giải thích cơ chế ngay trên từng màn hình — người dùng không phải đoán.
const MODE_HINT = {
  register:
    "Tạo tài khoản mới. Email dùng để nhận link/mã xác minh — tài khoản phải xác minh email trước khi đăng nhập (trừ máy demo bật AUTO_VERIFY_EMAIL).",
  forgot_prod:
    "Nhập tên đăng nhập — hệ thống gửi mã đặt lại mật khẩu qua email (dùng một lần, hết hạn sau 1 giờ).",
  reset_prod:
    "Dán mã từ email + mật khẩu mới. Mã dùng một lần, hết hạn sau 1 giờ; sau khi đặt lại, mọi phiên đăng nhập cũ bị thoát.",
};

export default function Login({ onLogin, initialError = "" }) {
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [resetToken, setResetToken] = useState("");
  const [error, setError] = useState(initialError);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [googleEnabled, setGoogleEnabled] = useState(false);

  // Server chưa cấu hình OAuth client thì ẩn nút Google (endpoint public, chỉ trả { enabled })
  useEffect(() => {
    fetch(`${API_BASE}/auth/google/status`)
      .then((r) => r.json())
      .then((d) => setGoogleEnabled(!!d.enabled))
      .catch(() => {});
  }, []);

  const errorRef = useRef(null);
  const usernameRef = useRef(null);

  // Focus error message when it appears
  useEffect(() => {
    if (error && errorRef.current) {
      errorRef.current.focus();
    }
  }, [error]);

  const isLogin = mode === "login";
  const isRegister = mode === "register";
  const isForgot = mode === "forgot";
  const isReset = mode === "reset";
  const authHint =
    mode === "register"
      ? MODE_HINT.register
      : mode === "forgot" && !IS_DEV
        ? MODE_HINT.forgot_prod
        : mode === "reset"
          ? MODE_HINT.reset_prod
          : "";

  function gotoMode(next) {
    setMode(next);
    setError("");
    setNotice("");
  }

  async function submit(e) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setNotice("");
    try {
      if (isForgot) {
        // Dev: đặt lại TRỰC TIẾP — username + mật khẩu mới, không cần email/OTP.
        if (IS_DEV) {
          await api("/auth/dev-reset-password", {
            method: "POST",
            body: JSON.stringify({ username, new_password: password }),
          });
          setPassword("");
          setMode("login");
          setNotice("Mật khẩu đã được đặt lại (chế độ dev). Đăng nhập bằng mật khẩu mới.");
          return;
        }
        // Production: backend luôn trả thông báo generic (không lộ username có
        // tồn tại), gửi mail best-effort khi SMTP đã cấu hình.
        await api("/auth/forgot-password", {
          method: "POST",
          body: JSON.stringify({ username }),
        });
        setPassword("");
        setMode("reset");
        setNotice(
          "Nếu tên người dùng tồn tại, email chứa mã đặt lại mật khẩu đã được gửi. Dán mã vào ô bên dưới, mã hết hạn sau 1 giờ."
        );
        return;
      }
      if (isReset) {
        await api("/auth/reset-password", {
          method: "POST",
          body: JSON.stringify({ token: resetToken.trim(), new_password: password }),
        });
        setResetToken("");
        setPassword("");
        setMode("login");
        setNotice("Mật khẩu đã được đặt lại. Đăng nhập bằng mật khẩu mới.");
        return;
      }
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
      // Tài khoản đã xác minh sẵn (demo không có SMTP, AUTO_VERIFY_EMAIL=1) -> vào app ngay.
      if (res.verified) {
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
      <div className="auth-card">
        <form onSubmit={submit} noValidate>
          <h1>🍜 Quanh Ta</h1>
          <h2>{MODE_TITLE[mode]}</h2>

          {authHint && (
            <div className="msg info" role="note">
              {authHint}
            </div>
          )}

          {!isReset && (
            <div className="form-group">
              <label htmlFor="login-username" id="login-username-label">
                {isLogin
                  ? "Tên đăng nhập hoặc email"
                  : isForgot
                    ? "Tên đăng nhập"
                    : "Tên đăng nhập"}{" "}
                <span className="required" aria-hidden="true">*</span>
                <span className="sr-only">(bắt buộc)</span>
              </label>
              <input
                ref={usernameRef}
                id="login-username"
                type="text"
                placeholder={isLogin ? "Nhập tên đăng nhập hoặc email" : "Nhập tên đăng nhập"}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                aria-required="true"
                aria-invalid={!!error}
                aria-describedby={error ? "login-error" : "login-username-label"}
                autoComplete="username"
              />
            </div>
          )}

          {isReset && (
            <div className="form-group">
              <label htmlFor="login-reset-token" id="login-reset-token-label">
                Mã trong email <span className="required" aria-hidden="true">*</span>
                <span className="sr-only">(bắt buộc, hết hạn sau 1 giờ)</span>
              </label>
              <input
                id="login-reset-token"
                type="text"
                placeholder="Dán mã 43 ký tự từ email"
                value={resetToken}
                onChange={(e) => setResetToken(e.target.value)}
                required
                aria-required="true"
                aria-invalid={!!error}
                aria-describedby={error ? "login-error" : "login-reset-token-label"}
                autoComplete="off"
                spellCheck={false}
              />
            </div>
          )}

          {(!isForgot || IS_DEV) && (
            <div className="form-group">
              <label htmlFor="login-password" id="login-password-label">
                {isReset || (isForgot && IS_DEV) ? "Mật khẩu mới" : "Mật khẩu"}{" "}
                <span className="required" aria-hidden="true">*</span>
                <span className="sr-only">(bắt buộc, tối thiểu 6 ký tự)</span>
              </label>
              <input
                id="login-password"
                type="password"
                placeholder={isReset || (isForgot && IS_DEV) ? "Nhập mật khẩu mới" : "Nhập mật khẩu"}
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
          )}

          {isRegister && (
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
            {loading
              ? "Đang xử lý..."
              : isLogin
                ? "Đăng nhập"
                : isRegister
                  ? "Tạo tài khoản"
                  : isForgot
                    ? IS_DEV
                      ? "Đặt lại mật khẩu mới"
                      : "Gửi mã đặt lại mật khẩu"
                    : "Đặt lại mật khẩu"}
          </button>

          {isLogin && googleEnabled && (
            <>
              <div className="auth-divider" aria-hidden="true"><span>hoặc</span></div>
              <button
                type="button"
                className="google-button"
                onClick={() => { window.location.assign(GOOGLE_AUTH_URL); }}
                disabled={loading}
              >
                <span className="google-mark" aria-hidden="true">G</span>
                Đăng nhập bằng Google
              </button>
            </>
          )}

          <p className="toggle">
            {isLogin && (
              <>
                <button
                  type="button"
                  onClick={() => gotoMode("forgot")}
                  className="link-button"
                  aria-label="Chuyển sang chế độ quên mật khẩu"
                >
                  Quên mật khẩu
                </button>
                {" — "}
                <button
                  type="button"
                  onClick={() => gotoMode("register")}
                  className="link-button"
                  aria-label="Chuyển sang chế độ đăng ký"
                >
                  Đăng ký
                </button>
              </>
            )}
            {!isLogin && (
              <>
                Đã có tài khoản?{" "}
                <button
                  type="button"
                  onClick={() => gotoMode("login")}
                  className="link-button"
                  aria-label="Quay lại chế độ đăng nhập"
                >
                  Đăng nhập
                </button>
              </>
            )}
          </p>
        </form>
      </div>
    </div>
  );
}

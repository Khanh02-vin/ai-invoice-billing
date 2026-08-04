import { useState } from "react";
import { api, setToken } from "./api";
import Aurora from "./components/react-bits/Aurora.jsx";
import SpotlightCard from "./components/react-bits/SpotlightCard.jsx";

export default function Login({ onLogin }) {
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const { access_token } = await api("/auth/" + mode, {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      setToken(access_token);
      onLogin();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-wrap">
      <Aurora />
      <SpotlightCard className="auth-card">
        <form onSubmit={submit}>
          <h1>🧾 Invoice &amp; Billing</h1>
          <h2>{mode === "login" ? "Đăng nhập" : "Đăng ký"}</h2>
          <input placeholder="Tên đăng nhập" value={username}
            onChange={(e) => setUsername(e.target.value)} required />
          <input type="password" placeholder="Mật khẩu (tối thiểu 6 ký tự)" value={password}
            onChange={(e) => setPassword(e.target.value)} required minLength={6} />
          {error && <p className="err">{error}</p>}
          <button disabled={loading}>{loading ? "Đang xử lý..." : mode === "login" ? "Đăng nhập" : "Tạo tài khoản"}</button>
          <p className="toggle">
            {mode === "login" ? "Chưa có tài khoản? " : "Đã có tài khoản? "}
            <a onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
              {mode === "login" ? "Đăng ký" : "Đăng nhập"}
            </a>
          </p>
        </form>
      </SpotlightCard>
    </div>
  );
}

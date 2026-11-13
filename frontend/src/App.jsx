import { useEffect, useState } from "react";
import Login from "./Login";
import Discover from "./Discover";
import { api, clearToken, getToken, setToken } from "./api";

// Backend redirects about Google sign-in back here with a one-time code.
async function consumeGoogleCallback() {
  const url = new URL(window.location.href);
  const params = new URLSearchParams(url.search);
  const code = params.get("google_code");
  const error = params.get("google_error");

  if (!code && !error && !params.has("google_link")) return "";
  window.history.replaceState({}, "", url.pathname);
  if (error === "email_exists") {
    return "Email Google đã có tài khoản. Hãy đăng nhập bằng mật khẩu trước.";
  }
  if (!code) return "";

  try {
    const response = await api("/auth/google/exchange", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
    setToken(response.access_token);
    return "";
  } catch (err) {
    return err.message || "Đăng nhập Google thất bại. Vui lòng thử lại.";
  }
}

export default function App() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState("");

  useEffect(() => {
    let active = true;
    (async () => {
      const callbackError = await consumeGoogleCallback();
      if (!active) return;
      if (callbackError) setAuthError(callbackError);
      if (!getToken()) { setLoading(false); return; }
      api("/auth/me")
        .then((value) => { if (active) setUser(value); })
        .catch(() => { if (active) setUser(null); })
        .finally(() => { if (active) setLoading(false); });
    })();
    return () => { active = false; };
  }, []);

  // Token hết hạn → quay về màn đăng nhập
  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener("auth-expired", onExpired);
    return () => window.removeEventListener("auth-expired", onExpired);
  }, []);

  if (loading) return <div className="loading">Đang tải...</div>;
  if (!user) return <Login initialError={authError} onLogin={() => api("/auth/me").then(setUser).catch(() => setUser(null))} />;
  return (
    <Discover
      user={user}
      onLogout={() => {
        clearToken();
        setUser(null);
      }}
    />
  );
}

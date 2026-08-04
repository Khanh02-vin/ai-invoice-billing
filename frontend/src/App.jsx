import { useEffect, useState } from "react";
import Login from "./Login";
import Invoices from "./Invoices";
import { api, getToken } from "./api";

export default function App() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) { setLoading(false); return; }
    api("/auth/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  // Token hết hạn → quay về màn đăng nhập
  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener("auth-expired", onExpired);
    return () => window.removeEventListener("auth-expired", onExpired);
  }, []);

  if (loading) return <div className="loading">Đang tải...</div>;
  if (!user) return <Login onLogin={() => api("/auth/me").then(setUser).catch(() => setUser(null))} />;
  return <Invoices user={user} onLogout={() => setUser(null)} />;
}

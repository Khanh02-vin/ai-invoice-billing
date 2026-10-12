import { useEffect, useState } from "react";
import Login from "./Login";
import Invoices from "./Invoices";
import Ledger from "./Ledger";
import { api, getToken } from "./api";

export default function App() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState("invoices");

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
  return <><nav className="app-top-nav"><button className="btn-link" onClick={() => setPage("invoices")}>Hóa đơn</button><button className="btn-link" onClick={() => setPage("ledger")}>Thu chi</button></nav>{page === "ledger" ? <Ledger onBack={() => setPage("invoices")} onLogout={() => setUser(null)} /> : <Invoices user={user} onLogout={() => setUser(null)} />}</>;
}

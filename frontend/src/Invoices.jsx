import { useEffect, useMemo, useState } from "react";
import { api, clearToken } from "./api";
import LineSidebar from "./components/react-bits/LineSidebar.jsx";
import { CountUp, Sparkline } from "./components/mini";

const STATUS_LABEL = { unpaid: "Chưa thanh toán", paid: "Đã thanh toán", overdue: "Quá hạn", cancelled: "Đã hủy" };
const NAV = ["Tổng quan", "Báo cáo", "Cài đặt"];

const fmt = new Intl.NumberFormat("vi-VN");
const fmtNum = (v) => fmt.format(Math.round(v));
const money = (currency, v) => (currency === "VND" ? "₫" : currency + " ") + fmt.format(v);

// Icon SVG nhỏ, inline — không thêm thư viện
const Icon = {
  upload: <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M8 11V3m0 0L4.5 6.5M8 3l3.5 3.5M2.5 12.5h11" strokeLinecap="round" strokeLinejoin="round" /></svg>,
  search: <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5L14 14" strokeLinecap="round" /></svg>,
};

export default function Invoices({ user, onLogout }) {
  const [view, setView] = useState("Tổng quan");
  const [invoices, setInvoices] = useState([]);
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const [report, setReport] = useState(null);
  const [msg, setMsg] = useState(null);

  function showErr(e) { setMsg({ text: e.message, ok: false }); }
  function ok(text) { setMsg({ text, ok: true }); }

  async function load() {
    const q = filter ? "?status=" + filter : "";
    const rows = await api("/invoices" + q);
    setInvoices(rows);
  }

  useEffect(() => { load().catch(showErr); }, [filter]);

  async function upload(file) {
    const fd = new FormData();
    fd.append("file", file);
    const d = await api("/invoices/upload", { method: "POST", body: fd });
    ok(`Đã trích xuất: ${d.invoice_number} — ${d.vendor} — ${money(d.currency, d.total)} (độ tin cậy ${(d.confidence * 100).toFixed(0)}%)`);
    load();
  }

  async function markPaid(id) {
    await api("/invoices/" + id, { method: "PATCH", body: JSON.stringify({ status: "paid" }) });
    ok("Đã đánh dấu thanh toán");
    load();
  }

  async function del(id) {
    if (!confirm("Xóa hóa đơn này?")) return;
    await api("/invoices/" + id, { method: "DELETE" });
    ok("Đã xóa");
    load();
  }

  async function loadReport() {
    try {
      setReport(await api("/reports/monthly/" + month));
    } catch (e) {
      setReport(null);
      showErr(e);
    }
  }

  // Thống kê từ danh sách hóa đơn (bộ lọc hiện tại)
  const stats = useMemo(() => {
    const sum = (arr) => arr.reduce((a, b) => a + b, 0);
    const totals = invoices.map((i) => i.total);
    const byDate = [...invoices].sort((a, b) => (a.issue_date || a.created_at || "").localeCompare(b.issue_date || b.created_at || ""));
    const series = byDate.map((i) => i.total);
    return {
      total: sum(totals),
      paid: sum(invoices.filter((i) => i.status === "paid").map((i) => i.total)),
      unpaid: sum(invoices.filter((i) => ["unpaid", "overdue"].includes(i.status)).map((i) => i.total)),
      overdue: sum(invoices.filter((i) => i.status === "overdue").map((i) => i.total)),
      series,
      count: invoices.length,
    };
  }, [invoices]);

  const rows = useMemo(() => {
    if (!search.trim()) return invoices;
    const q = search.trim().toLowerCase();
    return invoices.filter((i) => (i.invoice_number + " " + i.vendor).toLowerCase().includes(q));
  }, [invoices, search]);

  const paidRatio = stats.total ? (stats.paid / stats.total) * 100 : 0;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="brand-dot" aria-hidden="true" />
          <strong>Invoice &amp; Billing</strong>
        </div>
        <LineSidebar
          items={NAV}
          defaultActive={0}
          accentColor="var(--accent)"
          textColor="var(--text-dim)"
          markerColor="var(--border)"
          markerLength={40}
          itemGap={6}
          fontSize={1}
          showIndex={false}
          onItemClick={(i) => setView(NAV[i])}
        />
        <div className="sidebar-user">
          <span className="avatar" aria-hidden="true">{user.username[0]?.toUpperCase()}</span>
          <div>
            <div className="sidebar-user-name">{user.username}</div>
            <div className="sidebar-user-role">Quản trị</div>
          </div>
        </div>
      </aside>

      <main className="content">
        <header className="content-header">
          <h1>{view === "Cài đặt" ? "Cài đặt" : view === "Báo cáo" ? "Báo cáo theo tháng" : "Tổng quan"}</h1>
          <div className="content-header-actions">
            {view === "Tổng quan" && (
              <>
                <div className="search-box">
                  {Icon.search}
                  <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Tìm hóa đơn..." aria-label="Tìm hóa đơn" />
                </div>
                <input type="file" id="file" accept=".pdf,.txt,.md,.log" className="file-input"
                  onChange={(e) => { const f = e.target.files[0]; if (f) upload(f).catch(showErr); e.target.value = ""; }} />
                <button className="btn-primary" onClick={() => document.getElementById("file").click()}>{Icon.upload} Upload hóa đơn</button>
              </>
            )}
          </div>
        </header>

        {msg && <div className={`msg ${msg.ok ? "ok" : "err"}`} role="status">{msg.text}</div>}

        {view === "Tổng quan" && (
          <>
            <section className="stat-grid" aria-label="Thống kê">
              <StatCard label="Tổng hóa đơn" value={stats.total} suffix={invoices[0]?.currency === "VND" ? "₫" : undefined} data={stats.series} accent="var(--accent)" fmt={fmtNum} />
              <StatCard label="Đã thu" value={stats.paid} suffix={invoices[0]?.currency === "VND" ? "₫" : undefined} data={stats.series} accent="var(--success)" fmt={fmtNum} />
              <StatCard label="Chưa thu" value={stats.unpaid} suffix={invoices[0]?.currency === "VND" ? "₫" : undefined} data={stats.series} accent="var(--warning)" fmt={fmtNum} />
              <StatCard label="Quá hạn" value={stats.overdue} suffix={invoices[0]?.currency === "VND" ? "₫" : undefined} data={stats.series} accent="var(--danger)" fmt={fmtNum} />
            </section>

            <section className="card">
              <div className="card-head">
                <h2>Danh sách hóa đơn</h2>
                <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Lọc trạng thái">
                  <option value="">Tất cả trạng thái</option>
                  <option value="unpaid">Chưa thanh toán</option>
                  <option value="paid">Đã thanh toán</option>
                  <option value="overdue">Quá hạn</option>
                  <option value="cancelled">Đã hủy</option>
                </select>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Số hóa đơn</th><th>Nhà cung cấp</th><th>Ngày</th><th>Tổng</th><th>Thuế</th><th>Chiết khấu</th><th>Trạng thái</th><th>Thao tác</th></tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 && (
                      <tr><td colSpan="8" className="empty">{invoices.length === 0 ? "Chưa có hóa đơn — upload file đầu tiên" : "Không tìm thấy"}</td></tr>
                    )}
                    {rows.map((i) => (
                      <tr key={i.id}>
                        <td className="mono">{i.invoice_number}</td>
                        <td>{i.vendor}</td>
                        <td>{i.issue_date || "—"}</td>
                        <td className="num">{money(i.currency, i.total)}</td>
                        <td className="num">{money(i.currency, i.tax)}</td>
                        <td className="num">{i.discount ? money(i.currency, i.discount) : "—"}</td>
                        <td><span className={`badge ${i.status}`}>{STATUS_LABEL[i.status] || i.status}</span></td>
                        <td className="actions">
                          {i.status !== "paid" && <button className="btn-small" onClick={() => markPaid(i.id).catch(showErr)}>✓ Paid</button>}
                          <button className="btn-small danger" onClick={() => del(i.id).catch(showErr)} aria-label="Xóa hóa đơn">✕</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}

        {view === "Báo cáo" && (
          <section className="card">
            <div className="card-head">
              <h2>Báo cáo theo tháng</h2>
              <div className="row">
                <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Chọn tháng" />
                <button className="btn-primary" onClick={loadReport}>Xem báo cáo</button>
              </div>
            </div>
            {report ? (
              <>
                <div className="report-big">
                  <div className="report-item">
                    <div className="report-label">Tổng doanh thu</div>
                    <div className="report-value">{money("VND", report.total_amount)}</div>
                  </div>
                  <div className="report-item">
                    <div className="report-label">Thuế</div>
                    <div className="report-value">{money("VND", report.total_tax)}</div>
                  </div>
                  <div className="report-item">
                    <div className="report-label">Chiết khấu</div>
                    <div className="report-value">{money("VND", report.total_discount)}</div>
                  </div>
                </div>
                <div className="report-bars">
                  <div className="bar-row">
                    <div className="bar-label">Đã thu <span>{report.paid_count} hóa đơn</span></div>
                    <div className="bar"><div className="bar-fill success" style={{ width: paidRatio + "%" }} /></div>
                    <div className="bar-num">{money("VND", report.paid_amount)}</div>
                  </div>
                  <div className="bar-row">
                    <div className="bar-label">Chưa thu <span>{report.unpaid_count} hóa đơn</span></div>
                    <div className="bar"><div className="bar-fill warning" style={{ width: (100 - paidRatio) + "%" }} /></div>
                    <div className="bar-num">{money("VND", report.unpaid_amount)}</div>
                  </div>
                </div>
                <div className="report-meta">{report.invoice_count} hóa đơn trong {report.period}</div>
              </>
            ) : (
              <p className="empty-note">Chọn tháng và bấm "Xem báo cáo".</p>
            )}
          </section>
        )}

        {view === "Cài đặt" && (
          <section className="card settings">
            <h2>Tài khoản</h2>
            <div className="settings-row">
              <span className="avatar large" aria-hidden="true">{user.username[0]?.toUpperCase()}</span>
              <div>
                <div className="settings-name">{user.username}</div>
                <div className="settings-meta">Tham gia {user.created_at?.slice(0, 10) || "—"} · Quản trị viên</div>
              </div>
            </div>
            <button className="btn-danger" onClick={() => { clearToken(); onLogout(); }}>Đăng xuất</button>
          </section>
        )}
      </main>
    </div>
  );
}

function StatCard({ label, value, suffix, data, accent, fmt }) {
  return (
    <div className="stat-card">
      <div className="stat-top">
        <span className="stat-label">{label}</span>
        <Sparkline data={data} stroke={accent} />
      </div>
      <div className="stat-num"><CountUp to={Math.round(value)} format={fmt} />{suffix || ""}</div>
      <div className="stat-sub">{value > 0 ? fmt(value) + (suffix || "") : "—"}</div>
    </div>
  );
}

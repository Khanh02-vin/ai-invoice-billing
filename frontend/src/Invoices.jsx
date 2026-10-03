import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { api, clearToken } from "./api";
import LineSidebar from "./components/react-bits/LineSidebar.jsx";
import { CountUp, Sparkline } from "./components/mini";
import { pushSupported, pushStatus, enablePush, disablePush, sendTestPush } from "./push.js";

const STATUS_LABEL = {
  unpaid: "Chưa thanh toán",
  paid: "Đã thanh toán",
  overdue: "Quá hạn",
  cancelled: "Đã hủy",
};
const NAV = ["Tổng quan", "Báo cáo", "Cài đặt"];

// Threshold review — ô nào confidence dưới mức này thì đánh dấu cần sửa (đỏ)
const CONF_WARN = 0.7;
const FIELD_LABELS = {
  invoice_number: "Số hóa đơn", vendor: "Nhà cung cấp", issue_date: "Ngày lập",
  due_date: "Ngày đến hạn", total: "Tổng", tax: "Thuế", currency: "Tiền tệ",
};
const PAGE_SIZE = 8;

const fmt = new Intl.NumberFormat("vi-VN");
const fmtNum = (v) => fmt.format(Math.round(v || 0));
const money = (currency, v) => (currency === "VND" ? "₫" : currency + " ") + fmt.format(v || 0);

// Icon SVG nhỏ, inline — không thêm thư viện
const Icon = {
  upload: <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M8 11V3m0 0L4.5 6.5M8 3l3.5 3.5M2.5 12.5h11" strokeLinecap="round" strokeLinejoin="round" /></svg>,
  search: <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5L14 14" strokeLinecap="round" /></svg>,
  empty: <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M9 13h6" /><path d="M9 17h6" /></svg>,
  users: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="8.5" cy="7" r="4" /><path d="M20 8l2 2-2 2" /></svg>,
  creditCard: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="2" y="5" width="20" height="14" rx="2" /><path d="M2 10h20" /></svg>,
  shield: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>,
  chevronDown: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 9l6 6 6-6" /></svg>,
};

export default function Invoices({ user, onLogout }) {
  const [view, setView] = useState("Tổng quan");
  const [invoices, setInvoices] = useState([]);
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const [report, setReport] = useState(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [msg, setMsg] = useState(null);
  const [reportExpanded, setReportExpanded] = useState(false);
  const [settingsSections, setSettingsSections] = useState({
    team: true,
    billing: true,
    security: true,
    notifications: true,
  });
  const [pendingTx, setPendingTx] = useState([]);

  // Push notification (PWA) — trạng thái thiết bị hiện tại
  const [pushState, setPushState] = useState(pushSupported() ? "off" : "unsupported");
  const [pushBusy, setPushBusy] = useState(false);

  useEffect(() => {
    pushStatus().then(setPushState).catch(() => {});
  }, []);

  const togglePush = async () => {
    setPushBusy(true);
    try {
      const next = pushState === "on" ? await disablePush() : await enablePush();
      setPushState(next);
      setMsg({ ok: true, text: next === "on" ? "Đã bật thông báo đẩy trên thiết bị này." : "Đã tắt thông báo đẩy." });
    } catch (e) {
      showErr(e);
    } finally {
      setPushBusy(false);
    }
  };

  const testPush = async () => {
    setPushBusy(true);
    try {
      const { sent } = await sendTestPush();
      setMsg({ ok: true, text: sent > 0 ? `Đã gửi thông báo thử tới ${sent} thiết bị.` : "Chưa có thiết bị nào nhận — hãy bấm Bật thông báo trước." });
    } catch (e) {
      showErr(e);
    } finally {
      setPushBusy(false);
    }
  };

  // Review panel: invoice được chọn để sửa field
  const [reviewInvoice, setReviewInvoice] = useState(null);
  const [saving, setSaving] = useState(false);

  const showErr = useCallback((e) => {
    setError(e.message || "Lỗi không xác định");
  }, []);
  const clearMsg = useCallback(() => setMsg(null), []);

  // Giao dịch ngân hàng/ví chưa có hóa đơn (widget phụ — fail thì im lặng)
  const loadPendingTx = useCallback(() => {
    api("/payments/transactions?status=pending_receipt")
      .then(setPendingTx)
      .catch(() => setPendingTx([]));
  }, []);

  const dismissTx = async (id) => {
    try {
      await api(`/payments/transactions/${id}/dismiss`, { method: "POST" });
      setPendingTx((prev) => prev.filter((t) => t.id !== id));
    } catch (e) {
      showErr(e);
    }
  };

  // Demo data for settings
  const [teamMembers] = useState([
    { id: 1, username: "nguyenvan_a", role: "owner", email: "nguyen@example.com" },
    { id: 2, username: "tranthib", role: "admin", email: "tran@example.com" },
    { id: 3, username: "lehoangc", role: "member", email: "le@example.com" },
  ]);

  const [billingInfo] = useState({
    plan: "Pro",
    status: "active",
    renewalDate: "2026-10-09",
    amount: "299,000₫",
  });

  const [securityInfo] = useState({
    mfaEnabled: true,
    lastPasswordChange: "2026-08-15",
  });

  // Move focus to error banner when error appears
  const errorRef = useRef(null);
  useEffect(() => {
    if (error && errorRef.current) {
      errorRef.current.focus();
    }
  }, [error]);

  const upload = async (file) => {
    setSubmitting(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const data = await api("/invoices/upload", {
        method: "POST",
        body: form,
      });
      setInvoices((prev) => [...prev, ...(data.invoices || [])]);
      setMsg({ ok: true, text: `Đã upload ${data.invoices?.length || 1} hóa đơn` });
      loadPendingTx(); // upload có thể đã ghép xong 1 giao dịch đang chờ
    } catch (e) {
      showErr(e);
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    let active = true;
    setLoading(true);
    api("/invoices")
      .then((data) => { if (active) setInvoices(data); })
      .catch((e) => { if (active) showErr(e); })
      .finally(() => { if (active) setLoading(false); });
    loadPendingTx();
    return () => { active = false; };
  }, [showErr, loadPendingTx]);

  // Field máy đọc không chắc: confidence dưới ngưỡng, hoặc chưa trích được (0)
  const needsReview = (inv) =>
    ["vendor", "issue_date", "total"].some(
      (k) => (inv.provenance?.[k]?.confidence ?? 0) < CONF_WARN
    );

  const saveReview = async (e) => {
    e.preventDefault();
    if (!reviewInvoice) return;
    const body = {};
    for (const k of Object.keys(FIELD_LABELS)) {
      const v = reviewInvoice[k];
      if (v === "" || v === null || v === undefined) continue;
      body[k] = k === "total" || k === "tax" ? Number(v) : v;
    }
    setSaving(true);
    try {
      const updated = await api(`/invoices/${reviewInvoice.id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setInvoices((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
      setReviewInvoice(null);
      setMsg({ ok: true, text: "Đã lưu chỉnh sửa — dùng làm mẫu đánh giá độ chính xác" });
    } catch (err) {
      showErr(err);
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    const handler = () => { setError(null); };
    window.addEventListener("auth-expired", handler);
    return () => window.removeEventListener("auth-expired", handler);
  }, []);

  const loadReport = () => {
    setError(null);
    setReport(null);
    api("/reports/monthly/" + month)
      .then((data) => setReport(data))
      .catch((e) => { showErr(e); });
  };

  // Thống kê từ danh sách hóa đơn (bộ lọc hiện tại)
  const stats = useMemo(() => {
    const sum = (arr) => arr.reduce((a, b) => a + (Number(b) || 0), 0);
    const totals = invoices.map((i) => i.total);
    const byDate = [...invoices].sort((a, b) =>
      (a.issue_date || a.created_at || "").localeCompare(b.issue_date || b.created_at || "")
    );
    return {
      total: sum(totals),
      paid: sum(invoices.filter((i) => i.status === "paid").map((i) => i.total)),
      unpaid: sum(invoices.filter((i) => ["unpaid", "overdue"].includes(i.status)).map((i) => i.total)),
      overdue: sum(invoices.filter((i) => i.status === "overdue").map((i) => i.total)),
      series: byDate.map((i) => i.total),
      count: invoices.length,
    };
  }, [invoices]);

  const rows = useMemo(() => {
    const all = search.trim()
      ? invoices.filter((i) =>
          (i.invoice_number + " " + (i.vendor || "")).toLowerCase().includes(search.trim().toLowerCase())
        )
      : invoices;
    return all.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  }, [invoices, search]);

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pagedRows = useMemo(
    () => rows.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [rows, safePage]
  );
  useEffect(() => { if (page !== safePage) setPage(safePage); }, [page, safePage]);
  const paidRatio = stats.total ? (stats.paid / stats.total) * 100 : 0;

  const toggleSection = (key) => {
    setSettingsSections((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <div className="app-shell">
      {/* Skip link for keyboard navigation */}
      <a href="#main-content" className="skip-link">Bỏ qua điều hướng, đến nội dung chính</a>

      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="brand-dot" aria-hidden="true" />
          <strong>Invoice &amp; Billing</strong>
        </div>
        <nav className="sidebar-nav" aria-label="Primary">
          {NAV.map((n) => (
            <button
              key={n}
              className={view === n ? "nav-item active" : "nav-item"}
              onClick={() => { setView(n); setError(null); clearMsg(); }}
              aria-current={view === n ? "page" : undefined}
            >
              {n}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="user-chip">
            <span className="user-avatar" aria-hidden="true">{(user?.username || "?").slice(0, 1).toUpperCase()}</span>
            <span className="user-name">{user?.username}</span>
          </div>
          <button
            className="btn-ghost"
            onClick={() => { clearToken(); onLogout && onLogout(); }}
            aria-label="Đăng xuất khỏi tài khoản"
          >
            Đăng xuất
          </button>
        </div>
      </aside>

      <main id="main-content" className="main" tabIndex={-1}>
        {/* Global message / error banner */}
        {error && (
          <div className="banner banner-error" role="alert" aria-live="assertive" ref={errorRef} tabIndex={-1}>
            <span>⚠ {error}</span>
            <button className="banner-close" onClick={() => setError(null)} aria-label="Đóng thông báo lỗi">×</button>
          </div>
        )}
        {msg && (
          <div className={msg.ok ? "banner banner-ok" : "banner banner-error"} role="status" aria-live="polite">
            <span>{msg.text}</span>
            <button className="banner-close" onClick={clearMsg} aria-label="Đóng thông báo">×</button>
          </div>
        )}

        {view === "Tổng quan" && (
          <>
            <header className="page-header">
              <div>
                <h1>Tổng quan</h1>
                <p className="muted">Theo dõi dòng tiền hóa đơn</p>
              </div>
              <div className="header-actions">
                <div className="search-wrap" role="search" aria-label="Tìm kiếm hóa đơn">
                  <span className="search-icon" aria-hidden="true">{Icon.search}</span>
                  <input
                    className="search-input"
                    placeholder="Tìm mã / nhà cung cấp…"
                    value={search}
                    onChange={(e) => { setSearch(e.target.value); setPage(1); }}
                    aria-label="Tìm kiếm hóa đơn theo mã hoặc nhà cung cấp"
                  />
                </div>
                <label className={"btn-primary" + (submitting ? " disabled" : "")}>
                  {submitting ? "Đang tải…" : (<>{Icon.upload} Upload</>)}
                  <input
                    type="file"
                    hidden
                    disabled={submitting}
                    aria-label="Tải lên hóa đơn mới"
                    onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
                  />
                </label>
              </div>
            </header>

            {/* Stats */}
            <section className="stats-grid" aria-label="Thống kê">
              <div className="stat-card accent-blue">
                <span className="stat-label">Tổng giá trị</span>
                <span className="stat-value"><CountUp to={stats.total} /></span>
                <Sparkline data={stats.series} />
              </div>
              <div className="stat-card accent-green">
                <span className="stat-label">Đã thanh toán</span>
                <span className="stat-value"><CountUp to={stats.paid} /></span>
                <div className="progress" role="progressbar" aria-valuenow={Math.round(paidRatio)} aria-valuemin="0" aria-valuemax="100" aria-label="Tỷ lệ thanh toán">
                  <div className="progress-fill" style={{ width: `${paidRatio}%` }} />
                </div>
              </div>
              <div className="stat-card accent-amber">
                <span className="stat-label">Chưa thanh toán</span>
                <span className="stat-value"><CountUp to={stats.unpaid} /></span>
              </div>
              <div className="stat-card accent-red">
                <span className="stat-label">Quá hạn</span>
                <span className="stat-value"><CountUp to={stats.overdue} /></span>
              </div>
            </section>

            {/* Giao dịch ngân hàng/ví chưa có hóa đơn */}
            {pendingTx.length > 0 && (
              <section className="card" aria-label="Giao dịch chờ hóa đơn">
                <h2>Giao dịch chờ hóa đơn ({pendingTx.length})</h2>
                <p className="muted">Tiền đã bị trừ nhưng chưa có bill — upload ảnh hóa đơn để đối soát, hoặc bỏ qua.</p>
                <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                  {pendingTx.map((t) => (
                    <li
                      key={t.id}
                      style={{
                        display: "flex", justifyContent: "space-between", alignItems: "center",
                        gap: 12, padding: "10px 0", borderTop: "1px solid var(--border)",
                      }}
                    >
                      <div>
                        <strong>{money(t.currency, t.amount)}</strong>
                        <span className="muted">
                          {" · "}{t.merchant || t.source}
                          {t.occurred_at ? ` · ${t.occurred_at.slice(0, 10)}` : ""}
                        </span>
                      </div>
                      <button className="btn-ghost" onClick={() => dismissTx(t.id)}>
                        Bỏ qua
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Filters */}
            <div className="filters" role="group" aria-label="Bộ lọc trạng thái hóa đơn">
              {[{ l: "Tất cả", v: "" }, { l: "Chưa thanh toán", v: "unpaid" }, { l: "Đã thanh toán", v: "paid" }, { l: "Quá hạn", v: "overdue" }].map((c) => (
                <button
                  key={c.v}
                  className={filter === c.v ? "chip active" : "chip"}
                  onClick={() => setFilter(c.v)}
                  aria-pressed={filter === c.v}
                >
                  {c.l}
                </button>
              ))}
            </div>

            {/* Invoice table */}
            <section className="card table-card" aria-label="Danh sách hóa đơn">
              {loading ? (
                <div className="state-wrap" role="status" aria-live="polite">
                  <div className="spinner" aria-hidden="true" />
                  <p>Đang tải hóa đơn…</p>
                </div>
              ) : rows.length === 0 ? (
                <div className="state-wrap" role="status">
                  <span className="empty-icon" aria-hidden="true">{Icon.empty}</span>
                  <p className="state-title">Chưa có hóa đơn</p>
                  <p className="muted">Upload hóa đơn đầu tiên hoặc thử bộ lọc khác.</p>
                  {filter || search ? (
                    <button className="btn-ghost" onClick={() => { setFilter(""); setSearch(""); }}>
                      Xóa bộ lọc
                    </button>
                  ) : (
                    <label className="btn-primary">
                      {Icon.upload} Upload hóa đơn
                      <input type="file" hidden aria-label="Tải lên hóa đơn" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
                    </label>
                  )}
                </div>
              ) : (
                <>
                  <div className="table-scroll">
                    <table className="data-table" role="table" aria-rowcount={rows.length}>
                      <thead>
                        <tr>
                          <th scope="col" aria-sort="none">Mã hóa đơn</th>
                          <th scope="col" aria-sort="none">Nhà cung cấp</th>
                          <th scope="col" aria-sort="none">Ngày</th>
                          <th scope="col" aria-sort="none">Tổng</th>
                          <th scope="col" aria-sort="none">Trạng thái</th>
                          <th scope="col" className="col-actions">Hành động</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pagedRows.map((i, idx) => (
                          <tr key={i.id} aria-rowindex={idx + 1}>
                            <td>
                              {needsReview(i) && (
                                <span className="dot-warn" title="Có field máy đọc không chắc — bấm Xem để sửa" />
                              )}
                              {i.invoice_number}
                            </td>
                            <td>{i.vendor || "-"}</td>
                            <td>{i.issue_date || i.created_at?.slice(0, 10) || "-"}</td>
                            <td>{money(i.currency, i.total)}</td>
                            <td>
                              <span className={`status status-${i.status}`}>
                                {STATUS_LABEL[i.status] || i.status}
                              </span>
                            </td>
                            <td className="col-actions">
                              <button
                                className="btn-ghost"
                                aria-label={`Xem chi tiết hóa đơn ${i.invoice_number}`}
                                onClick={() => setReviewInvoice({ ...i })}
                              >
                                Xem
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="pagination">
                    <button
                      className="btn-ghost"
                      disabled={safePage <= 1}
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                      aria-label="Trang trước"
                    >
                      ‹
                    </button>
                    <span className="page-info" aria-live="polite">
                      Trang {safePage} / {pageCount}
                    </span>
                    <button
                      className="btn-ghost"
                      disabled={safePage >= pageCount}
                      onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                      aria-label="Trang sau"
                    >
                      ›
                    </button>
                  </div>
                </>
              )}
            </section>
          </>
        )}

        {/* Modal review — sửa field máy đọc không chắc */}
        {reviewInvoice && (
          <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Sửa hóa đơn">
            <div className="modal" style={{ maxWidth: 560 }}>
              <h2>Sửa hóa đơn {reviewInvoice.invoice_number}</h2>
              <p className="muted" style={{ marginTop: 0 }}>
                Ô đỏ là field máy đọc confidence &lt; {CONF_WARN} — sửa rồi lưu;
                chỉnh sửa được dùng làm mẫu đánh giá độ chính xác.
              </p>
              <form onSubmit={saveReview}>
                <div className="modal-fields">
                  {Object.keys(FIELD_LABELS).map((k) => {
                    const prov = reviewInvoice.provenance?.[k];
                    const conf = prov?.confidence ?? 0;
                    const low = conf < CONF_WARN;
                    return (
                      <div className="form-row" key={k}>
                        <label>{FIELD_LABELS[k]}{prov?.source === "qr" ? " (QR)" : ""}</label>
                        <input
                          className={low ? "input-warn" : ""}
                          value={reviewInvoice[k] ?? ""}
                          onChange={(e) =>
                            setReviewInvoice({ ...reviewInvoice, [k]: e.target.value })
                          }
                        />
                        <small className="muted">
                          {prov
                            ? `confidence ${Math.round(conf * 100)}% · ${prov.source}`
                            : "máy không trích được"}
                        </small>
                      </div>
                    );
                  })}
                </div>
                <div className="modal-actions">
                  <button type="button" className="btn-ghost" onClick={() => setReviewInvoice(null)}>
                    Hủy
                  </button>
                  <button type="submit" className="btn-primary" disabled={saving}>
                    {saving ? "Đang lưu…" : "Lưu chỉnh sửa"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {view === "Báo cáo" && (
          <>
            <header className="page-header">
              <div>
                <h1>Báo cáo</h1>
                <p className="muted">Báo cáo tài chính theo tháng</p>
              </div>
            </header>
            <section className="card" aria-label="Báo cáo tháng">
              <div className="form-row">
                <label htmlFor="month-picker" id="month-picker-label">Chọn tháng</label>
                <input
                  id="month-picker"
                  type="month"
                  value={month}
                  onChange={(e) => setMonth(e.target.value)}
                  aria-describedby="month-picker-label"
                />
              </div>
              <button className="btn-primary" onClick={loadReport} aria-label="Xem báo cáo tháng đã chọn">Xem báo cáo</button>
              {report && (
                <div className="report-result" role="region" aria-live="polite" aria-label="Kết quả báo cáo">
                  <p>Tổng doanh thu: <strong>{money(report.currency, report.total_revenue)}</strong></p>
                  <a className="btn-ghost" href={"/reports/monthly/" + month + ".pdf"} target="_blank" rel="noopener noreferrer">
                    Tải PDF
                  </a>
                </div>
              )}
            </section>
          </>
        )}

        {view === "Cài đặt" && (
          <>
            <header className="page-header">
              <div>
                <h1>Cài đặt</h1>
                <p className="muted">Quản lý nhóm, thanh toán và bảo mật</p>
              </div>
            </header>

            <div className="settings-container">
              {/* Team Section */}
              <section className="card settings-card" aria-labelledby="settings-team-heading">
                <button
                  className="settings-header"
                  onClick={() => toggleSection("team")}
                  aria-expanded={settingsSections.team}
                  aria-controls="settings-team-content"
                  id="settings-team-heading"
                >
                  <span className="settings-icon" aria-hidden="true">{Icon.users}</span>
                  <h2>Đội nhóm</h2>
                  <span className="settings-chevron" aria-hidden="true">{Icon.chevronDown}</span>
                </button>
                {settingsSections.team && (
                  <div id="settings-team-content" role="region" aria-labelledby="settings-team-heading">
                    <p className="muted">Quản lý thành viên và quyền truy cập trong tổ chức.</p>
                    <div className="table-scroll">
                      <table className="data-table" role="table" aria-label="Danh sách thành viên đội nhóm">
                        <thead>
                          <tr>
                            <th scope="col">Thành viên</th>
                            <th scope="col">Email</th>
                            <th scope="col">Vai trò</th>
                            <th scope="col">Hành động</th>
                          </tr>
                        </thead>
                        <tbody>
                          {teamMembers.map((member) => (
                            <tr key={member.id}>
                              <td>{member.username}</td>
                              <td>{member.email}</td>
                              <td>
                                <span className={`role role-${member.role}`}>
                                  {member.role === "owner" ? "Chủ sở hữu" : member.role === "admin" ? "Quản trị viên" : "Thành viên"}
                                </span>
                              </td>
                              <td>
                                <button className="btn-ghost" aria-label={`Thay đổi quyền của ${member.username}`}>Thay đổi quyền</button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <button className="btn-primary" aria-label="Mời thành viên mới vào đội nhóm">
                      + Mời thành viên
                    </button>
                  </div>
                )}
              </section>

              {/* Billing Section */}
              <section className="card settings-card" aria-labelledby="settings-billing-heading">
                <button
                  className="settings-header"
                  onClick={() => toggleSection("billing")}
                  aria-expanded={settingsSections.billing}
                  aria-controls="settings-billing-content"
                  id="settings-billing-heading"
                >
                  <span className="settings-icon" aria-hidden="true">{Icon.creditCard}</span>
                  <h2>Thanh toán</h2>
                  <span className="settings-chevron" aria-hidden="true">{Icon.chevronDown}</span>
                </button>
                {settingsSections.billing && (
                  <div id="settings-billing-content" role="region" aria-labelledby="settings-billing-heading">
                    <p className="muted">Quản lý gói dịch vụ và phương thức thanh toán.</p>
                    <div className="billing-info" role="region" aria-label="Thông tin gói hiện tại">
                      <div className="billing-row">
                        <span>Gói hiện tại:</span>
                        <strong>{billingInfo.plan}</strong>
                      </div>
                      <div className="billing-row">
                        <span>Trạng thái:</span>
                        <span className={`status status-${billingInfo.status}`}>{billingInfo.status === "active" ? "Đang hoạt động" : "Đã dừng"}</span>
                      </div>
                      <div className="billing-row">
                        <span>Ngày gia hạn:</span>
                        <strong>{billingInfo.renewalDate}</strong>
                      </div>
                      <div className="billing-row">
                        <span>Số tiền:</span>
                        <strong>{billingInfo.amount}/tháng</strong>
                      </div>
                    </div>
                    <button className="btn-primary" aria-label="Nâng cấp gói dịch vụ">
                      Nâng cấp gói
                    </button>
                  </div>
                )}
              </section>

              {/* Security Section */}
              <section className="card settings-card" aria-labelledby="settings-security-heading">
                <button
                  className="settings-header"
                  onClick={() => toggleSection("security")}
                  aria-expanded={settingsSections.security}
                  aria-controls="settings-security-content"
                  id="settings-security-heading"
                >
                  <span className="settings-icon" aria-hidden="true">{Icon.shield}</span>
                  <h2>Bảo mật</h2>
                  <span className="settings-chevron" aria-hidden="true">{Icon.chevronDown}</span>
                </button>
                {settingsSections.security && (
                  <div id="settings-security-content" role="region" aria-labelledby="settings-security-heading">
                    <p className="muted">Quản lý xác thực hai yếu tố và mật khẩu.</p>
                    <div className="security-info" role="region" aria-label="Thông tin bảo mật">
                      <div className="security-row">
                        <span>Xác thực hai yếu tố (MFA):</span>
                        <span className={`status status-${securityInfo.mfaEnabled ? "paid" : "overdue"}`}>
                          {securityInfo.mfaEnabled ? "Đã bật" : "Đã tắt"}
                        </span>
                      </div>
                      <div className="security-row">
                        <span>Thay đổi mật khẩu gần nhất:</span>
                        <strong>{securityInfo.lastPasswordChange}</strong>
                      </div>
                    </div>
                    <button className="btn-ghost" aria-label="Thay đổi mật khẩu tài khoản">
                      Thay đổi mật khẩu
                    </button>
                  </div>
                )}
              </section>

              <section className="card settings-card" aria-labelledby="settings-notifications-heading">
                <button
                  className="settings-header"
                  onClick={() => toggleSection("notifications")}
                  aria-expanded={settingsSections.notifications}
                  aria-controls="settings-notifications-content"
                  id="settings-notifications-heading"
                >
                  <span className="settings-icon" aria-hidden="true">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
                      <path d="M13.7 21a2 2 0 0 1-3.4 0" />
                    </svg>
                  </span>
                  <h2>Thông báo</h2>
                  <span className="settings-chevron" aria-hidden="true">{Icon.chevronDown}</span>
                </button>
                {settingsSections.notifications && (
                  <div id="settings-notifications-content" role="region" aria-labelledby="settings-notifications-heading">
                    <p className="muted">Nhận thông báo ngay trên màn hình khóa khi có giao dịch chờ hóa đơn (PWA native push).</p>
                    {pushState === "unsupported" && (
                      <p className="muted">
                        Thiết bị/trình duyệt chưa hỗ trợ push. Trên điện thoại: mở app qua HTTPS, iPhone cần
                        "Thêm vào màn hình chính" (iOS 16.4+) rồi mở từ icon — xem README.
                      </p>
                    )}
                    {pushState === "blocked" && (
                      <p className="muted">
                        Quyền thông báo đang bị chặn — vào cài đặt trình duyệt để cho phép, rồi thử lại.
                      </p>
                    )}
                    {pushState !== "unsupported" && (
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button className="btn-ghost" onClick={togglePush} disabled={pushBusy}>
                          {pushState === "on" ? "🔕 Tắt thông báo đẩy" : "🔔 Bật thông báo đẩy (mobile)"}
                        </button>
                        {pushState === "on" && (
                          <button className="btn-ghost" onClick={testPush} disabled={pushBusy}>Gửi thử</button>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </section>
            </div>
          </>
        )}
      </main>
      <LineSidebar />
    </div>
  );
}

import { useState } from "react";

const NAV_ITEMS = [
  { id: "overview", label: "Tổng quan", short: "Tổng quan", icon: "📊" },
  { id: "ledger", label: "Sổ thu chi", short: "Thu chi", icon: "💳" },
  { id: "report", label: "Báo cáo & Thu chi", short: "Báo cáo", icon: "📈" },
  { id: "history", label: "Lịch sử & Lộ trình", short: "Lộ trình", icon: "🗺️", badge: "Mới" },
  { id: "purchases", label: "Mua hàng", short: "Mua hàng", icon: "🛒", badge: "Mới" },
  { id: "settings", label: "Cài đặt", short: "Cài đặt", icon: "⚙️" },
];

const Icon = {
  chevronDown: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 9l6 6 6-6" /></svg>,
  logout: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></svg>,
};

export default function Sidebar({ view, setView, user, onLogout }) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`} aria-label="Điều hướng chính">
      <div className="sidebar-header">
        <div className="sidebar-brand">
          <span className="brand-dot" aria-hidden="true" />
          {!collapsed && <strong>Invoice & Billing</strong>}
        </div>
        <button
          className="sidebar-toggle"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? "Mở rộng menu" : "Thu gọn menu"}
          aria-expanded={!collapsed}
        >
          {Icon.chevronDown}
        </button>
      </div>

      <nav className="sidebar-nav" aria-label="Menu chính">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            className={`nav-item ${view === item.id ? "active" : ""} ${item.badge ? "has-badge" : ""}`}
            onClick={() => setView(item.id)}
            data-short={item.short}
            aria-current={view === item.id ? "page" : undefined}
            title={collapsed ? item.label : undefined}
          >
            <span className="nav-icon" aria-hidden="true">{item.icon}</span>
            {!collapsed && (
              <>
                <span className="nav-label">{item.label}</span>
                {item.badge && <span className="nav-badge">{item.badge}</span>}
              </>
            )}
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="user-chip">
          <span className="user-avatar" aria-hidden="true">{(user?.username || "?").slice(0, 1).toUpperCase()}</span>
          {!collapsed && <span className="user-name">{user?.username}</span>}
        </div>
        <button
          className="btn-ghost"
          onClick={() => { onLogout && onLogout(); }}
          aria-label="Đăng xuất khỏi tài khoản"
          title={collapsed ? "Đăng xuất" : undefined}
        >
          {!collapsed && "Đăng xuất"}
          {collapsed && Icon.logout}
        </button>
      </div>
    </aside>
  );
}

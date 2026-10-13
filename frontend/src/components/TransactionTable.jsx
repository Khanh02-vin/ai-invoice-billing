import { useMemo, useState } from "react";
import { api } from "../api";

const STATUS_LABEL = {
  unpaid: "Chưa thanh toán",
  paid: "Đã thanh toán",
  overdue: "Quá hạn",
  cancelled: "Đã hủy",
};

const STATUS_ICON = {
  unpaid: "⏳",
  paid: "✅",
  overdue: "⚠️",
  cancelled: "❌",
};

const TRANSPORT_ICONS = {
  walk: "🚶",
  bike: "🚲",
  car: "🚗",
  grab: "🚕",
  bus: "🚌",
  "": "📍",
};

const Icon = {
  edit: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>,
  delete: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>,
  view: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>,
  location: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="3" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2" /></svg>,
};

const fmt = new Intl.NumberFormat("vi-VN");
const money = (currency, v) => (currency === "VND" ? "₫" : currency + " ") + fmt.format(v || 0);

export default function TransactionTable({
  invoices,
  filter,
  search,
  page,
  pageCount,
  safePage,
  setPage,
  setFilter,
  setSearch,
  onViewDetail,
  onEdit,
  onDelete,
  loading,
  needsReview,
}) {
  const [locationFilter, setLocationFilter] = useState(false);
  const [sortConfig, setSortConfig] = useState({ key: "created_at", direction: "desc" });

  const rows = useMemo(() => {
    let result = [...invoices];
    
    // Search filter
    if (search.trim()) {
      const term = search.trim().toLowerCase();
      result = result.filter((i) =>
        (i.invoice_number + " " + (i.vendor || "") + " " + (i.location_name || "")).toLowerCase().includes(term)
      );
    }
    
    // Status filter
    if (filter) {
      result = result.filter((i) => i.status === filter);
    }
    
    // Location filter
    if (locationFilter) {
      result = result.filter((i) => i.location_lat != null && i.location_lon != null);
    }
    
    // Sort
    result.sort((a, b) => {
      const aVal = a[sortConfig.key] || "";
      const bVal = b[sortConfig.key] || "";
      const dir = sortConfig.direction === "asc" ? 1 : -1;
      return aVal.localeCompare(bVal) * dir;
    });
    
    return result;
  }, [invoices, search, filter, locationFilter, sortConfig]);

  const pagedRows = useMemo(
    () => rows.slice((safePage - 1) * 8, safePage * 8),
    [rows, safePage]
  );

  const handleSort = (key) => {
    setSortConfig((prev) => ({
      key,
      direction: prev.key === key && prev.direction === "asc" ? "desc" : "asc",
    }));
  };

  const getSortIcon = (key) => {
    if (sortConfig.key !== key) return "↕";
    return sortConfig.direction === "asc" ? "↑" : "↓";
  };

  const formatLocation = (invoice) => {
    if (!invoice.location_lat || !invoice.location_lon) return null;
    const parts = [];
    if (invoice.location_name) parts.push(invoice.location_name);
    if (invoice.location_address) parts.push(invoice.location_address);
    if (invoice.transport_mode) parts.push(TRANSPORT_ICONS[invoice.transport_mode] + " " + invoice.transport_mode);
    return parts.join(" · ") || `📍 ${invoice.location_lat.toFixed(4)}, ${invoice.location_lon.toFixed(4)}`;
  };

  const handleViewDetail = (invoice, e) => {
    e?.stopPropagation();
    onViewDetail?.(invoice);
  };

  const handleEdit = (invoice, e) => {
    e?.stopPropagation();
    onEdit?.(invoice);
  };

  const handleDelete = (invoice, e) => {
    e?.stopPropagation();
    if (confirm(`Xóa hóa đơn ${invoice.invoice_number}?`)) {
      onDelete?.(invoice);
    }
  };

  if (loading) {
    return (
      <section className="card table-card" aria-label="Danh sách hóa đơn">
        <div className="state-wrap" role="status" aria-live="polite">
          <div className="spinner" aria-hidden="true" />
          <p>Đang tải hóa đơn…</p>
        </div>
      </section>
    );
  }

  if (rows.length === 0) {
    return (
      <section className="card table-card" aria-label="Danh sách hóa đơn">
        <div className="state-wrap" role="status">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" className="empty-icon" aria-hidden="true">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <path d="M14 2v6h6" />
            <path d="M9 13h6" />
            <path d="M9 17h6" />
          </svg>
          <p className="state-title">Chưa có hóa đơn</p>
          <p className="muted">Upload hóa đơn đầu tiên hoặc thử bộ lọc khác.</p>
          {(filter || search || locationFilter) && (
            <button className="btn-ghost" onClick={() => { setFilter(""); setSearch(""); setLocationFilter(false); }}>
              Xóa bộ lọc
            </button>
          )}
        </div>
      </section>
    );
  }

  return (
    <section className="card table-card" aria-label="Danh sách hóa đơn">
      {/* Filter Bar */}
      <div className="table-toolbar">
        <div className="filter-chips" role="group" aria-label="Bộ lọc trạng thái">
          <button
            className={`filter-chip ${filter === "" ? "active" : ""}`}
            onClick={() => setFilter("")}
            aria-pressed={filter === ""}
          >
            Tất cả
          </button>
          <button
            className={`filter-chip ${filter === "unpaid" ? "active" : ""}`}
            onClick={() => setFilter("unpaid")}
            aria-pressed={filter === "unpaid"}
          >
            {STATUS_ICON.unpaid} Chưa thanh toán
          </button>
          <button
            className={`filter-chip ${filter === "paid" ? "active" : ""}`}
            onClick={() => setFilter("paid")}
            aria-pressed={filter === "paid"}
          >
            {STATUS_ICON.paid} Đã thanh toán
          </button>
          <button
            className={`filter-chip ${filter === "overdue" ? "active" : ""}`}
            onClick={() => setFilter("overdue")}
            aria-pressed={filter === "overdue"}
          >
            {STATUS_ICON.overdue} Quá hạn
          </button>
          <button
            className={`filter-chip ${locationFilter ? "active" : ""}`}
            onClick={() => setLocationFilter(!locationFilter)}
            aria-pressed={locationFilter}
          >
            {Icon.location} Có vị trí
          </button>
        </div>
        
        <div className="search-wrap" role="search" aria-label="Tìm kiếm hóa đơn">
          <span className="search-icon" aria-hidden="true">🔍</span>
          <input
            className="search-input"
            placeholder="Tìm mã / nhà cung cấp / địa điểm…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            aria-label="Tìm kiếm hóa đơn theo mã, nhà cung cấp hoặc địa điểm"
          />
        </div>
      </div>

      {/* Table */}
      <div className="table-scroll">
        <table className="data-table" role="table" aria-rowcount={rows.length}>
          <thead>
            <tr>
              <th scope="col" aria-sort={sortConfig.key === "invoice_number" ? (sortConfig.direction === "asc" ? "ascending" : "descending") : "none"} onClick={() => handleSort("invoice_number")}>
                Mã hóa đơn {getSortIcon("invoice_number")}
              </th>
              <th scope="col" aria-sort={sortConfig.key === "vendor" ? (sortConfig.direction === "asc" ? "ascending" : "descending") : "none"} onClick={() => handleSort("vendor")}>
                Nhà cung cấp {getSortIcon("vendor")}
              </th>
              <th scope="col" aria-sort={sortConfig.key === "issue_date" ? (sortConfig.direction === "asc" ? "ascending" : "descending") : "none"} onClick={() => handleSort("issue_date")}>
                Ngày {getSortIcon("issue_date")}
              </th>
              <th scope="col" aria-sort={sortConfig.key === "total" ? (sortConfig.direction === "asc" ? "ascending" : "descending") : "none"} onClick={() => handleSort("total")}>
                Tổng {getSortIcon("total")}
              </th>
              <th scope="col" aria-sort={sortConfig.key === "status" ? (sortConfig.direction === "asc" ? "ascending" : "descending") : "none"} onClick={() => handleSort("status")}>
                Trạng thái {getSortIcon("status")}
              </th>
              <th scope="col" aria-sort={sortConfig.key === "location_name" ? (sortConfig.direction === "asc" ? "ascending" : "descending") : "none"} onClick={() => handleSort("location_name")}>
                Địa điểm / Lộ trình {getSortIcon("location_name")}
              </th>
              <th scope="col" className="col-actions">
                Hành động
              </th>
            </tr>
          </thead>
          <tbody>
            {pagedRows.map((invoice, idx) => (
              <tr key={invoice.id} aria-rowindex={idx + 1} onClick={() => handleViewDetail(invoice)}>
                <td>
                  {needsReview(invoice) && (
                    <span className="dot-warn" title="Có field máy đọc không chắc — bấm để sửa" />
                  )}
                  <span className="invoice-number">{invoice.invoice_number}</span>
                </td>
                <td>{invoice.vendor || "-"}</td>
                <td>{invoice.issue_date || invoice.created_at?.slice(0, 10) || "-"}</td>
                <td className="col-amount">{money(invoice.currency, invoice.total)}</td>
                <td>
                  <span className={`status status-${invoice.status}`}>
                    {STATUS_ICON[invoice.status]} {STATUS_LABEL[invoice.status] || invoice.status}
                  </span>
                </td>
                <td className="col-location">
                  {formatLocation(invoice) ? (
                    <span className="location-badge" title={formatLocation(invoice)}>
                      {formatLocation(invoice)}
                    </span>
                  ) : (
                    <span className="no-location">—</span>
                  )}
                </td>
                <td className="col-actions">
                  <div className="action-buttons">
                    <button
                      className="action-btn view"
                      onClick={(e) => handleViewDetail(invoice, e)}
                      aria-label={`Xem chi tiết ${invoice.invoice_number}`}
                      title="Xem chi tiết"
                    >
                      {Icon.view}
                    </button>
                    <button
                      className="action-btn edit"
                      onClick={(e) => handleEdit(invoice, e)}
                      aria-label={`Chỉnh sửa ${invoice.invoice_number}`}
                      title="Chỉnh sửa"
                    >
                      {Icon.edit}
                    </button>
                    <button
                      className="action-btn delete"
                      onClick={(e) => handleDelete(invoice, e)}
                      aria-label={`Xóa ${invoice.invoice_number}`}
                      title="Xóa"
                    >
                      {Icon.delete}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="pagination">
        <div className="pagination-info">
          Hiển thị {Math.min((safePage - 1) * 8 + 1, rows.length)}–{Math.min(safePage * 8, rows.length)} của {rows.length}
        </div>
        <div className="page-btns">
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
      </div>

      <style jsx>{`
        .table-toolbar {
          display: flex;
          gap: 12px;
          align-items: center;
          flex-wrap: wrap;
          margin-bottom: 16px;
          padding-bottom: 12px;
          border-bottom: 1px solid var(--border);
        }
        .filter-chips {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }
        .filter-chip {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 6px 12px;
          background: var(--surface-2);
          border: 1px solid var(--border);
          border-radius: 999px;
          font-size: var(--fs-xs);
          color: var(--text-dim);
          cursor: pointer;
          transition: all 0.15s;
          white-space: nowrap;
        }
        .filter-chip:hover {
          border-color: var(--accent);
          color: var(--text);
        }
        .filter-chip.active {
          background: var(--accent);
          border-color: var(--accent);
          color: white;
        }
        .search-wrap {
          flex: 1;
          min-width: 200px;
          max-width: 300px;
        }
        .search-icon {
          position: absolute;
          left: 10px;
          color: var(--text-dim);
          pointer-events: none;
        }
        .search-input {
          width: 100%;
          padding: 8px 10px 8px 36px;
          border: 1px solid var(--border);
          border-radius: var(--r-sm);
          background: var(--surface-2);
          color: var(--text);
          font-size: var(--fs-sm);
        }
        .search-input:focus {
          outline: none;
          border-color: var(--accent);
        }
        .table-scroll {
          overflow-x: auto;
        }
        .data-table {
          width: 100%;
          border-collapse: collapse;
          font-size: var(--fs-sm);
        }
        .data-table th,
        .data-table td {
          padding: 10px 12px;
          border-bottom: 1px solid var(--border);
          white-space: nowrap;
          text-align: left;
        }
        .data-table th {
          color: var(--text-dim);
          font-weight: 600;
          font-size: var(--fs-xs);
          text-transform: uppercase;
          letter-spacing: .04em;
          cursor: pointer;
          user-select: none;
          background: var(--surface-2);
        }
        .data-table th:hover {
          color: var(--text);
        }
        .data-table tbody tr {
          cursor: pointer;
          transition: background 0.1s;
        }
        .data-table tbody tr:hover {
          background: var(--surface-2);
        }
        .invoice-number {
          font-family: monospace;
          font-size: var(--fs-xs);
        }
        .dot-warn {
          display: inline-block;
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: var(--danger);
          margin-right: 6px;
          vertical-align: middle;
          flex-shrink: 0;
        }
        .col-amount {
          font-variant-numeric: tabular-nums;
          font-weight: 500;
        }
        .status {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          padding: 3px 8px;
          border-radius: 999px;
          font-size: var(--fs-xs);
          font-weight: 600;
        }
        .status-unpaid { background: rgba(234,179,8,.15); color: var(--warning); }
        .status-paid { background: rgba(34,197,94,.15); color: var(--success); }
        .status-overdue { background: rgba(239,68,68,.15); color: var(--danger); }
        .status-cancelled { background: rgba(100,116,139,.15); color: var(--neutral); }
        .col-location {
          max-width: 200px;
        }
        .location-badge {
          display: inline-block;
          max-width: 180px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          padding: 3px 8px;
          background: rgba(37, 99, 235, 0.1);
          border: 1px solid rgba(37, 99, 235, 0.2);
          border-radius: var(--r-sm);
          font-size: var(--fs-xs);
          color: var(--accent);
        }
        .no-location {
          color: var(--text-dim);
          font-size: var(--fs-xs);
        }
        .col-actions {
          width: 120px;
          text-align: right;
        }
        .action-buttons {
          display: inline-flex;
          gap: 4px;
        }
        .action-btn {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 32px;
          height: 32px;
          border: none;
          background: transparent;
          color: var(--text-dim);
          border-radius: var(--r-sm);
          cursor: pointer;
          transition: all 0.15s;
        }
        .action-btn:hover {
          background: var(--surface-2);
          color: var(--text);
        }
        .action-btn.view:hover { color: var(--accent); }
        .action-btn.edit:hover { color: var(--warning); }
        .action-btn.delete:hover { 
          background: rgba(239, 68, 68, 0.15);
          color: var(--danger);
        }
        .action-btn:focus-visible {
          outline: 2px solid var(--accent);
          outline-offset: 2px;
        }
        .pagination {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 12px 16px;
          border-top: 1px solid var(--border);
          font-size: var(--fs-sm);
          color: var(--text-dim);
        }
        .pagination-info {
          font-size: var(--fs-xs);
        }
        .page-btns {
          display: flex;
          gap: 8px;
        }

        @media (max-width: 760px) {
          .table-toolbar {
            flex-direction: column;
            align-items: stretch;
          }
          .filter-chips {
            justify-content: center;
          }
          .search-wrap {
            max-width: none;
          }
          .col-location {
            display: none;
          }
        }
      `}</style>
    </section>
  );
}
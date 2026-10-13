import { useEffect, useState, useCallback } from "react";
import { api } from "../api";
import JourneyMap from "../components/JourneyMap";
import PlaceSuggestions from "../components/PlaceSuggestions";
import TransactionTable from "../components/TransactionTable";

const STATUS_LABEL = {
  unpaid: "Chưa thanh toán",
  paid: "Đã thanh toán",
  overdue: "Quá hạn",
  cancelled: "Đã hủy",
};

export default function HistoryTimeline({ user, onBack }) {
  const [timelineData, setTimelineData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [dateRange, setDateRange] = useState({ from: "", to: "" });
  const [selectedInvoice, setSelectedInvoice] = useState(null);
  const [showInvoiceDetail, setShowInvoiceDetail] = useState(false);
  const [invoices, setInvoices] = useState([]);
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [locationFilter, setLocationFilter] = useState(false);

  const loadTimeline = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (dateRange.from) params.set("from_date", dateRange.from);
      if (dateRange.to) params.set("to_date", dateRange.to);
      const data = await api(`/payments/timeline?${params}`);
      setTimelineData(data);
    } catch (err) {
      setError(err.message || "Không thể tải lộ trình");
    } finally {
      setLoading(false);
    }
  }, [dateRange]);

  const loadInvoices = useCallback(async () => {
    try {
      const data = await api("/invoices?limit=200");
      setInvoices(data);
    } catch (err) {
      console.error("Load invoices error:", err);
    }
  }, []);

  useEffect(() => {
    loadTimeline();
    loadInvoices();
  }, [loadTimeline, loadInvoices]);

  // Get user's last known location for place suggestions
  const [userLocation, setUserLocation] = useState({ lat: 10.7769, lon: 106.7009 }); // Default HCMC

  useEffect(() => {
    if (timelineData?.points?.length > 0) {
      // Use the last point as current location
      const last = timelineData.points[timelineData.points.length - 1];
      setUserLocation({ lat: last.lat, lon: last.lon });
    } else if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        ({ coords }) => setUserLocation({ lat: coords.latitude, lon: coords.longitude }),
        () => {},
        { timeout: 5000 }
      );
    }
  }, [timelineData]);

  const handleDateChange = (field, value) => {
    setDateRange((prev) => ({ ...prev, [field]: value }));
  };

  const handleApplyDateFilter = () => {
    loadTimeline();
  };

  const needsReview = (inv) => {
    return ["vendor", "issue_date", "total"].some(
      (k) => (inv.provenance?.[k]?.confidence ?? 0) < 0.7
    );
  };

  const pageCount = Math.max(1, Math.ceil(invoices.filter(i => {
    if (filter && i.status !== filter) return false;
    if (search.trim() && !(i.invoice_number + " " + (i.vendor || "")).toLowerCase().includes(search.trim().toLowerCase())) return false;
    if (locationFilter && !(i.location_lat && i.location_lon)) return false;
    return true;
  }).length / 8));

  return (
    <main className="content">
      <header className="content-header">
        <div>
          <button className="btn-link" onClick={onBack}>
            ← Quay lại
          </button>
          <h1>Lịch sử & Lộ trình</h1>
        </div>
        <p className="muted">Theo dõi hành trình di chuyển gắn liền với chi tiêu</p>
      </header>

      {error && <div className="banner banner-error" role="alert">⚠ {error}</div>}

      {/* Date Range Filter */}
      <section className="card" aria-label="Bộ lọc thời gian">
        <div className="card-head">
          <h2>📅 Khoảng thời gian</h2>
        </div>
        <div className="date-filter-row">
          <label>
            Từ ngày
            <input
              type="date"
              value={dateRange.from}
              onChange={(e) => handleDateChange("from", e.target.value)}
            />
          </label>
          <label>
            Đến ngày
            <input
              type="date"
              value={dateRange.to}
              onChange={(e) => handleDateChange("to", e.target.value)}
            />
          </label>
          <button className="btn-primary" onClick={handleApplyDateFilter} disabled={loading}>
            {loading ? "Đang tải..." : "Áp dụng"}
          </button>
          <button className="btn-ghost" onClick={() => { setDateRange({ from: "", to: "" }); loadTimeline(); }}>
            Xóa lọc
          </button>
        </div>
      </section>

      {/* Journey Map */}
      {timelineData?.points?.length > 0 && (
        <section className="card" aria-label="Bản đồ hành trình">
          <JourneyMap timelineData={timelineData} height={450} />
        </section>
      )}

      {/* Place Suggestions */}
      <section className="card" aria-label="Đề xuất địa điểm">
        <PlaceSuggestions 
          lat={userLocation.lat} 
          lon={userLocation.lon} 
          radius={2000}
          onSelect={(place) => {
            // Could navigate to map or show details
            console.log("Selected place:", place);
          }}
        />
      </section>

      {/* Transaction Table */}
      <section className="card" aria-label="Danh sách giao dịch trong lộ trình">
        <div className="card-head">
          <h2>📋 Giao dịch trong lộ trình</h2>
        </div>
        <TransactionTable
          invoices={invoices}
          filter={filter}
          search={search}
          page={page}
          pageCount={pageCount}
          safePage={page}
          setPage={setPage}
          setFilter={setFilter}
          setSearch={setSearch}
          onViewDetail={(inv) => { setSelectedInvoice(inv); setShowInvoiceDetail(true); }}
          onEdit={(inv) => { setSelectedInvoice(inv); setShowInvoiceDetail(true); }}
          onDelete={async (inv) => {
            try {
              await api(`/invoices/${inv.id}`, { method: "DELETE" });
              setInvoices((prev) => prev.filter((i) => i.id !== inv.id));
              loadTimeline();
            } catch (err) {
              alert("Lỗi xóa: " + err.message);
            }
          }}
          loading={false}
          needsReview={needsReview}
        />
      </section>

      {/* Invoice Detail Modal */}
      {showInvoiceDetail && selectedInvoice && (
        <div className="modal-backdrop" onClick={() => { setShowInvoiceDetail(false); setSelectedInvoice(null); }}>
          <div className="modal invoice-detail-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Chi tiết hóa đơn: {selectedInvoice.invoice_number}</h2>
              <button className="modal-close" onClick={() => { setShowInvoiceDetail(false); setSelectedInvoice(null); }} aria-label="Đóng">×</button>
            </div>
            <div className="modal-body">
              <div className="detail-grid">
                <div className="detail-item">
                  <span className="detail-label">Nhà cung cấp</span>
                  <span className="detail-value">{selectedInvoice.vendor || "—"}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">Ngày lập</span>
                  <span className="detail-value">{selectedInvoice.issue_date || selectedInvoice.created_at?.slice(0, 10) || "—"}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">Tổng tiền</span>
                  <span className="detail-value">{new Intl.NumberFormat("vi-VN", { style: "currency", currency: selectedInvoice.currency || "VND", maximumFractionDigits: 0 }).format(selectedInvoice.total)}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">Trạng thái</span>
                  <span className={`detail-value status status-${selectedInvoice.status}`}>{STATUS_LABEL[selectedInvoice.status] || selectedInvoice.status}</span>
                </div>
                {selectedInvoice.location_lat && selectedInvoice.location_lon && (
                  <>
                    <div className="detail-item">
                      <span className="detail-label">Địa điểm</span>
                      <span className="detail-value">{selectedInvoice.location_name || "—"}</span>
                    </div>
                    <div className="detail-item">
                      <span className="detail-label">Địa chỉ</span>
                      <span className="detail-value">{selectedInvoice.location_address || "—"}</span>
                    </div>
                    <div className="detail-item">
                      <span className="detail-label">Phương tiện</span>
                      <span className="detail-value">{selectedInvoice.transport_mode || "—"}</span>
                    </div>
                    <div className="detail-item">
                      <span className="detail-label">Tọa độ</span>
                      <span className="detail-value">{selectedInvoice.location_lat.toFixed(6)}, {selectedInvoice.location_lon.toFixed(6)}</span>
                    </div>
                  </>
                )}
              </div>
              
              {Object.keys(selectedInvoice.provenance || {}).length > 0 && (
                <div className="detail-section">
                  <h4>Thông tin trích xuất (Confidence)</h4>
                  <div className="provenance-grid">
                    {Object.entries(selectedInvoice.provenance).map(([key, prov]) => (
                      <div key={key} className={`provenance-item ${(prov.confidence || 0) < 0.7 ? "low" : ""}`}>
                        <span className="prov-field">{key}</span>
                        <span className="prov-value">{prov.value}</span>
                        <span className="prov-conf" style={{ color: (prov.confidence || 0) < 0.7 ? "var(--danger)" : "var(--success)" }}>
                          {Math.round((prov.confidence || 0) * 100)}%
                        </span>
                        <span className="prov-source">{prov.source}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button className="btn-primary" onClick={() => { setShowInvoiceDetail(false); setSelectedInvoice(null); }}>
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}

      <style jsx>{`
        .content {
          padding: 20px 24px 40px;
        }
        .content-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 24px;
          flex-wrap: wrap;
        }
        .content-header h1 {
          font-size: var(--fs-xl);
          margin: 0;
        }
        .content-header .muted {
          margin: 4px 0 0;
        }
        .btn-link {
          background: none;
          border: none;
          color: var(--accent);
          cursor: pointer;
          font-size: var(--fs-sm);
          padding: 4px 0;
        }
        .btn-link:hover { text-decoration: underline; }
        .banner {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 10px 14px;
          border-radius: var(--r-md);
          margin-bottom: 16px;
          font-size: var(--fs-sm);
        }
        .banner-error {
          background: rgba(239,68,68,.12);
          border: 1px solid rgba(239,68,68,.3);
          color: #fecaca;
        }
        .card {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: var(--r-md);
          padding: 20px;
          margin-bottom: 20px;
        }
        .card-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          margin-bottom: 16px;
        }
        .card-head h2 {
          margin: 0;
          font-size: var(--fs-md);
        }
        .date-filter-row {
          display: flex;
          gap: 12px;
          align-items: end;
          flex-wrap: wrap;
        }
        .date-filter-row label {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: var(--fs-sm);
          color: var(--text-dim);
        }
        .date-filter-row input {
          padding: 8px 12px;
          border: 1px solid var(--border);
          border-radius: var(--r-sm);
          background: var(--surface-2);
          color: var(--text);
          font-size: var(--fs-md);
        }
        .date-filter-row input:focus {
          outline: none;
          border-color: var(--accent);
        }

        /* Detail Modal */
        .invoice-detail-modal {
          max-width: 600px;
        }
        .modal-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 16px 20px;
          border-bottom: 1px solid var(--border);
        }
        .modal-header h2 {
          margin: 0;
          font-size: var(--fs-lg);
        }
        .modal-close {
          background: none;
          border: none;
          font-size: 24px;
          color: var(--text-dim);
          cursor: pointer;
          line-height: 1;
        }
        .modal-close:hover { color: var(--text); }
        .modal-body {
          padding: 20px;
          max-height: 60vh;
          overflow-y: auto;
        }
        .detail-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 16px;
        }
        .detail-item {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .detail-label {
          font-size: var(--fs-xs);
          color: var(--text-dim);
          text-transform: uppercase;
          letter-spacing: .05em;
        }
        .detail-value {
          font-size: var(--fs-md);
          color: var(--text);
          font-weight: 500;
        }
        .detail-section {
          margin-top: 24px;
          padding-top: 16px;
          border-top: 1px solid var(--border);
        }
        .detail-section h4 {
          margin: 0 0 12px;
          font-size: var(--fs-sm);
          color: var(--text-dim);
        }
        .provenance-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 10px;
        }
        .provenance-item {
          display: flex;
          flex-direction: column;
          gap: 2px;
          padding: 10px;
          background: var(--surface-2);
          border-radius: var(--r-sm);
        }
        .provenance-item.low {
          border: 1px solid var(--danger);
        }
        .prov-field {
          font-size: var(--fs-xs);
          color: var(--text-dim);
          text-transform: uppercase;
        }
        .prov-value {
          font-size: var(--fs-sm);
          color: var(--text);
          font-family: monospace;
        }
        .prov-conf {
          font-size: var(--fs-xs);
          font-weight: 600;
        }
        .prov-source {
          font-size: var(--fs-xs);
          color: var(--text-dim);
        }
        .modal-footer {
          display: flex;
          justify-content: flex-end;
          gap: 12px;
          padding: 16px 20px;
          border-top: 1px solid var(--border);
        }

        @media (max-width: 760px) {
          .content { padding: 16px; }
          .date-filter-row { flex-direction: column; align-items: stretch; }
          .date-filter-row button { width: 100%; }
          .detail-grid { grid-template-columns: 1fr; }
        }
      `}</style>
    </main>
  );
}
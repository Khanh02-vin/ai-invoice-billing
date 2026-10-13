import { useEffect, useState, useCallback } from "react";
import { api } from "../api";
import PlaceSuggestions from "../components/PlaceSuggestions";
import TransactionTable from "../components/TransactionTable";

export default function PlaceRecommendations({ user, onBack }) {
  const [userLocation, setUserLocation] = useState({ lat: 10.7769, lon: 106.7009 });
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [radius, setRadius] = useState(2000);
  const [invoices, setInvoices] = useState([]);
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [locationFilter, setLocationFilter] = useState(true);

  const loadSuggestions = useCallback(async () => {
    if (!userLocation.lat || !userLocation.lon) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api(`/payments/places/suggestions?lat=${userLocation.lat}&lon=${userLocation.lon}&radius=${radius}`);
      setSuggestions(data.suggestions || []);
    } catch (err) {
      setError(err.message || "Không thể tải đề xuất");
    } finally {
      setLoading(false);
    }
  }, [userLocation, radius]);

  const loadInvoices = useCallback(async () => {
    try {
      const data = await api("/invoices?limit=200");
      setInvoices(data);
    } catch (err) {
      console.error("Load invoices error:", err);
    }
  }, []);

  useEffect(() => {
    loadSuggestions();
    loadInvoices();
    
    // Get current location
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        ({ coords }) => setUserLocation({ lat: coords.latitude, lon: coords.longitude }),
        () => {},
        { timeout: 5000 }
      );
    }
  }, [loadSuggestions, loadInvoices]);

  const handleLocationChange = () => {
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        ({ coords }) => {
          setUserLocation({ lat: coords.latitude, lon: coords.longitude });
          loadSuggestions();
        },
        (err) => alert("Không thể lấy vị trí: " + err.message),
        { timeout: 10000, enableHighAccuracy: true }
      );
    }
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

  const CATEGORY_ICONS = {
    cafe: "☕",
    restaurant: "🍽️",
    shop: "🏪",
    frequent: "⭐",
    shared: "👥",
    nearby: "📍",
  };

  const CATEGORY_LABELS = {
    cafe: "Cà phê",
    restaurant: "Nhà hàng",
    shop: "Cửa hàng",
    frequent: "Thường ghé",
    shared: "Cộng đồng",
    nearby: "Gần bạn",
  };

  const formatDistance = (m) => {
    if (m < 1000) return `${m}m`;
    return `${(m / 1000).toFixed(1)}km`;
  };

  const getConfidenceColor = (conf) => {
    if (conf >= 0.8) return "var(--success)";
    if (conf >= 0.6) return "var(--warning)";
    return "var(--text-dim)";
  };

  return (
    <main className="content">
      <header className="content-header">
        <div>
          <button className="btn-link" onClick={onBack}>← Quay lại</button>
          <h1>Đề xuất địa điểm</h1>
        </div>
        <p className="muted">AI gợi ý dựa trên thói quen & cộng đồng</p>
      </header>

      {error && <div className="banner banner-error" role="alert">⚠ {error}</div>}

      {/* Controls */}
      <section className="card" aria-label="Bộ lọc đề xuất">
        <div className="controls-row">
          <div className="control-group">
            <label>
              Bán kính tìm kiếm
              <select value={radius} onChange={(e) => { setRadius(parseInt(e.target.value)); loadSuggestions(); }}>
                <option value="500">500m</option>
                <option value="1000">1km</option>
                <option value="2000">2km</option>
                <option value="5000">5km</option>
              </select>
            </label>
          </div>
          <button className="btn-primary" onClick={handleLocationChange}>
            📍 Cập nhật vị trí hiện tại
          </button>
          <div className="location-display">
            Vị trí: {userLocation.lat.toFixed(6)}, {userLocation.lon.toFixed(6)}
          </div>
        </div>
      </section>

      {/* Suggestions List */}
      <section className="card" aria-label="Danh sách đề xuất">
        <div className="card-head">
          <h2>🤖 Gợi ý thông minh</h2>
          <span className="suggestion-count">{suggestions.length} địa điểm</span>
        </div>
        
        {loading && (
          <div className="suggestions-loading">
            <div className="spinner" />
            <p>Đang phân tích thói quen & tìm địa điểm...</p>
          </div>
        )}
        
        {!loading && error && (
          <div className="suggestions-error">
            <p>⚠ {error}</p>
          </div>
        )}
        
        {!loading && !error && suggestions.length === 0 && (
          <div className="suggestions-empty">
            <p>Chưa có đề xuất phù hợp</p>
            <p className="hint">Thử thay đổi bán kính hoặc cập nhật vị trí hiện tại</p>
          </div>
        )}
        
        {!loading && !error && suggestions.length > 0 && (
          <div className="suggestions-grid">
            {suggestions.map((place) => (
              <div 
                key={`${place.lat}-${place.lon}-${place.name}`}
                className="suggestion-card"
                onClick={() => {
                  // Open in maps
                  window.open(`https://www.openstreetmap.org/?mlat=${place.lat}&mlon=${place.lon}#map=17/${place.lat}/${place.lon}`, '_blank');
                }}
              >
                <div className="suggestion-card-header">
                  <span className="suggestion-icon">{CATEGORY_ICONS[place.kind] || CATEGORY_ICONS.nearby}</span>
                  <span className="suggestion-category">{CATEGORY_LABELS[place.kind] || place.kind}</span>
                </div>
                <h3 className="suggestion-name">{place.name}</h3>
                <div className="suggestion-meta">
                  <span className="suggestion-distance">📍 {formatDistance(place.distance_m)}</span>
                  <span className="suggestion-reason" style={{ color: getConfidenceColor(place.confidence) }}>
                    {place.reason}
                  </span>
                  {place.visit_count > 0 && (
                    <span className="suggestion-visits">👁 {place.visit_count} lượt</span>
                  )}
                </div>
                <div className="suggestion-confidence">
                  <div 
                    className="confidence-bar"
                    style={{ 
                      width: `${Math.round(place.confidence * 100)}%`,
                      background: getConfidenceColor(place.confidence)
                    }} 
                  />
                  <span className="confidence-text">{Math.round(place.confidence * 100)}% phù hợp</span>
                </div>
                <div className="suggestion-source">
                  Nguồn: {place.source === "history" ? "Lịch sử của bạn" : place.source === "shared" ? "Cộng đồng" : "Gần đây"}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Related Transactions */}
      <section className="card" aria-label="Giao dịch liên quan">
        <div className="card-head">
          <h2>📋 Giao dịch tại khu vực này</h2>
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
          onViewDetail={(inv) => window.dispatchEvent(new CustomEvent("view-invoice", { detail: inv }))}
          onEdit={(inv) => window.dispatchEvent(new CustomEvent("edit-invoice", { detail: inv }))}
          onDelete={async (inv) => {
            if (confirm(`Xóa hóa đơn ${inv.invoice_number}?`)) {
              try {
                await api(`/invoices/${inv.id}`, { method: "DELETE" });
                setInvoices((prev) => prev.filter((i) => i.id !== inv.id));
              } catch (err) {
                alert("Lỗi xóa: " + err.message);
              }
            }
          }}
          loading={false}
          needsReview={needsReview}
        />
      </section>

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
        .suggestion-count {
          font-size: var(--fs-sm);
          color: var(--text-dim);
          background: var(--surface-2);
          padding: 4px 10px;
          border-radius: 999px;
        }
        .controls-row {
          display: flex;
          gap: 16px;
          align-items: end;
          flex-wrap: wrap;
        }
        .control-group {
          flex: 1;
          min-width: 180px;
        }
        .control-group label {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: var(--fs-sm);
          color: var(--text-dim);
        }
        .control-group select {
          padding: 8px 12px;
          border: 1px solid var(--border);
          border-radius: var(--r-sm);
          background: var(--surface-2);
          color: var(--text);
          font-size: var(--fs-md);
        }
        .control-group select:focus {
          outline: none;
          border-color: var(--accent);
        }
        .location-display {
          font-size: var(--fs-sm);
          color: var(--text-dim);
          font-family: monospace;
          padding: 8px 12px;
          background: var(--surface-2);
          border-radius: var(--r-sm);
        }

        /* Suggestions Grid */
        .suggestions-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
          gap: 16px;
        }
        .suggestion-card {
          background: var(--surface-2);
          border: 1px solid var(--border);
          border-radius: var(--r-md);
          padding: 16px;
          cursor: pointer;
          transition: all 0.2s;
        }
        .suggestion-card:hover {
          border-color: var(--accent);
          transform: translateY(-2px);
          box-shadow: 0 4px 16px rgba(0,0,0,0.2);
        }
        .suggestion-card-header {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 8px;
        }
        .suggestion-icon {
          font-size: 20px;
        }
        .suggestion-category {
          font-size: var(--fs-xs);
          color: var(--accent);
          background: rgba(37, 99, 235, 0.15);
          padding: 2px 8px;
          border-radius: 999px;
        }
        .suggestion-name {
          margin: 0 0 10px;
          font-size: var(--fs-md);
          font-weight: 600;
        }
        .suggestion-meta {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          font-size: var(--fs-xs);
          color: var(--text-dim);
          margin-bottom: 10px;
        }
        .suggestion-reason {
          font-weight: 500;
        }
        .suggestion-visits {
          color: var(--warning);
        }
        .suggestion-confidence {
          display: flex;
          flex-direction: column;
          gap: 4px;
          margin-bottom: 8px;
        }
        .confidence-bar {
          height: 6px;
          background: var(--surface);
          border-radius: 999px;
          overflow: hidden;
        }
        .confidence-text {
          font-size: var(--fs-xs);
          color: var(--text-dim);
        }
        .suggestion-source {
          font-size: var(--fs-xs);
          color: var(--text-dim);
        }

        .suggestions-loading,
        .suggestions-error,
        .suggestions-empty {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          padding: 40px 16px;
          color: var(--text-dim);
          text-align: center;
          gap: 8px;
        }
        .suggestions-empty .hint {
          font-size: var(--fs-sm);
        }
        .spinner {
          width: 28px;
          height: 28px;
          border: 3px solid var(--border);
          border-top-color: var(--accent);
          border-radius: 50%;
          animation: spin .8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        @media (max-width: 760px) {
          .content { padding: 16px; }
          .controls-row { flex-direction: column; align-items: stretch; }
          .control-group { width: 100%; }
          .suggestions-grid { grid-template-columns: 1fr; }
        }
      `}</style>
    </main>
  );
}
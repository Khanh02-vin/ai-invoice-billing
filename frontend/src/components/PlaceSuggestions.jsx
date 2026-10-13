import { useEffect, useState } from "react";
import { api } from "../api";

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

export default function PlaceSuggestions({ lat, lon, radius = 2000, onSelect }) {
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    if (!lat || !lon) return;
    
    let mounted = true;
    setLoading(true);
    setError(null);

    api(`/payments/places/suggestions?lat=${lat}&lon=${lon}&radius=${radius}`)
      .then((data) => {
        if (mounted) {
          setSuggestions(data.suggestions || []);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (mounted) {
          setError(err.message || "Không thể tải đề xuất");
          setLoading(false);
        }
      });

    return () => { mounted = false; };
  }, [lat, lon, radius]);

  function formatDistance(m) {
    if (m < 1000) return `${m}m`;
    return `${(m / 1000).toFixed(1)}km`;
  }

  function getConfidenceColor(conf) {
    if (conf >= 0.8) return "var(--success)";
    if (conf >= 0.6) return "var(--warning)";
    return "var(--text-dim)";
  }

  if (loading) {
    return (
      <div className="place-suggestions">
        <div className="suggestions-header">
          <h3>📍 Đề xuất địa điểm</h3>
        </div>
        <div className="suggestions-loading">
          <div className="spinner" />
          <p>Đang tìm địa điểm phù hợp...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="place-suggestions">
        <div className="suggestions-header">
          <h3>📍 Đề xuất địa điểm</h3>
        </div>
        <div className="suggestions-error">
          <p>⚠ {error}</p>
        </div>
      </div>
    );
  }

  if (suggestions.length === 0) {
    return (
      <div className="place-suggestions">
        <div className="suggestions-header">
          <h3>📍 Đề xuất địa điểm</h3>
        </div>
        <div className="suggestions-empty">
          <p>Chưa có đề xuất phù hợp</p>
          <p className="hint">Di chuyển đến khu vực khác hoặc thêm giao dịch có vị trí</p>
        </div>
      </div>
    );
  }

  return (
    <div className="place-suggestions">
      <div className="suggestions-header">
        <h3>📍 Đề xuất địa điểm</h3>
        <button 
          className="suggestions-toggle"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
        >
          {expanded ? "▲" : "▼"}
        </button>
      </div>

      <div className={`suggestions-list ${!expanded ? "collapsed" : ""}`}>
        {suggestions.map((place, index) => (
          <div 
            key={`${place.lat}-${place.lon}-${place.name}`}
            className="suggestion-item"
            onClick={() => onSelect?.(place)}
          >
            <div className="suggestion-icon">
              {CATEGORY_ICONS[place.kind] || CATEGORY_ICONS.nearby}
            </div>
            <div className="suggestion-info">
              <div className="suggestion-name-row">
                <span className="suggestion-name">{place.name}</span>
                <span className="suggestion-category">{CATEGORY_LABELS[place.kind] || place.kind}</span>
              </div>
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
              </div>
            </div>
            <button 
              className="suggestion-action"
              onClick={(e) => { e.stopPropagation(); onSelect?.(place); }}
              aria-label={`Chọn ${place.name}`}
            >
              →
            </button>
          </div>
        ))}
      </div>

      <style jsx>{`
        .place-suggestions {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: var(--r-md);
          overflow: hidden;
        }
        .suggestions-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 14px 16px;
          border-bottom: 1px solid var(--border);
        }
        .suggestions-header h3 {
          margin: 0;
          font-size: var(--fs-md);
          font-weight: 600;
        }
        .suggestions-toggle {
          background: none;
          border: none;
          color: var(--text-dim);
          cursor: pointer;
          font-size: 12px;
          padding: 4px 8px;
          border-radius: var(--r-sm);
        }
        .suggestions-toggle:hover {
          background: var(--surface-2);
          color: var(--text);
        }
        .suggestions-list {
          display: flex;
          flex-direction: column;
          max-height: 400px;
          overflow-y: auto;
          transition: max-height 0.2s ease, opacity 0.2s ease;
        }
        .suggestions-list.collapsed {
          max-height: 0;
          opacity: 0;
          pointer-events: none;
        }
        .suggestion-item {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 12px 16px;
          border-bottom: 1px solid var(--border);
          cursor: pointer;
          transition: background 0.15s;
        }
        .suggestion-item:last-child {
          border-bottom: none;
        }
        .suggestion-item:hover {
          background: var(--surface-2);
        }
        .suggestion-icon {
          font-size: 20px;
          width: 36px;
          height: 36px;
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--surface-2);
          border-radius: var(--r-sm);
        }
        .suggestion-info {
          flex: 1;
          min-width: 0;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .suggestion-name-row {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .suggestion-name {
          font-weight: 500;
          font-size: var(--fs-sm);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .suggestion-category {
          font-size: var(--fs-xs);
          color: var(--accent);
          background: rgba(37, 99, 235, 0.15);
          padding: 2px 6px;
          border-radius: 999px;
          white-space: nowrap;
        }
        .suggestion-meta {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          font-size: var(--fs-xs);
          color: var(--text-dim);
        }
        .suggestion-reason {
          font-weight: 500;
        }
        .suggestion-visits {
          color: var(--warning);
        }
        .suggestion-confidence {
          height: 4px;
          background: var(--surface-2);
          border-radius: 999px;
          overflow: hidden;
          margin-top: 2px;
        }
        .confidence-bar {
          height: 100%;
          border-radius: 999px;
          transition: width 0.3s ease;
        }
        .suggestion-action {
          background: none;
          border: none;
          color: var(--text-dim);
          font-size: 16px;
          cursor: pointer;
          padding: 4px;
          opacity: 0;
          transition: opacity 0.15s;
        }
        .suggestion-item:hover .suggestion-action {
          opacity: 1;
        }
        .suggestions-loading,
        .suggestions-error,
        .suggestions-empty {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          padding: 32px 16px;
          color: var(--text-dim);
          text-align: center;
          gap: 8px;
        }
        .suggestions-empty .hint {
          font-size: var(--fs-sm);
        }
        .spinner {
          width: 24px;
          height: 24px;
          border: 2px solid var(--border);
          border-top-color: var(--accent);
          border-radius: 50%;
          animation: spin .8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        @media (max-width: 760px) {
          .suggestion-meta {
            flex-direction: column;
            gap: 4px;
          }
        }
      `}</style>
    </div>
  );
}
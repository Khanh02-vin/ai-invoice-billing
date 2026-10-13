import { useEffect, useRef, useState } from "react";

// Leaflet will be loaded dynamically
let L = null;
let leafletLoaded = false;
let leafletLoadPromise = null;

async function loadLeaflet() {
  if (leafletLoaded) return L;
  if (leafletLoadPromise) return leafletLoadPromise;

  leafletLoadPromise = (async () => {
    // Load Leaflet CSS
    if (!document.querySelector('link[href*="leaflet.css"]')) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
      link.integrity = "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=";
      link.crossOrigin = "";
      document.head.appendChild(link);
    }

    // Load Leaflet JS
    await new Promise((resolve, reject) => {
      if (window.L) {
        resolve();
        return;
      }
      const script = document.createElement("script");
      script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
      script.integrity = "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=";
      script.crossOrigin = "";
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });

    L = window.L;
    // Fix default marker icon
    delete L.Icon.Default.prototype._getIconUrl;
    L.Icon.Default.mergeOptions({
      iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
      iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
      shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
    });

    leafletLoaded = true;
    return L;
  })();

  return leafletLoadPromise;
}

const TRANSPORT_ICONS = {
  walk: "🚶",
  bike: "🚲",
  car: "🚗",
  grab: "🚕",
  bus: "🚌",
};

const TRANSPORT_LABELS = {
  walk: "Đi bộ",
  bike: "Xe máy/đạp",
  car: "Xe hơi",
  grab: "Grab/Taxi",
  bus: "Xe buýt",
};

export default function JourneyMap({ timelineData, height = 400 }) {
  const mapRef = useRef(null);
  const mapInstance = useRef(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedPoint, setSelectedPoint] = useState(null);
  const [showTimeline, setShowTimeline] = useState(true);

  useEffect(() => {
    let mounted = true;

    async function initMap() {
      try {
        setLoading(true);
        await loadLeaflet();
        
        if (!mounted || !mapRef.current) return;

        // Create map with dark tiles (CartoDB Dark Matter)
        const map = L.map(mapRef.current, {
          center: [10.7769, 106.7009], // Default: Ho Chi Minh City
          zoom: 13,
          zoomControl: false,
          attributionControl: false,
        });

        L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
          attribution: "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors &copy; <a href='https://carto.com/attributions'>CARTO</a>",
          subdomains: "abcd",
          maxZoom: 19,
        }).addTo(map);

        // Add zoom control to top-right
        L.control.zoom({ position: "topright" }).addTo(map);

        mapInstance.current = map;

        if (timelineData?.points?.length > 0) {
          renderTimeline(map, timelineData.points);
          fitBounds(map, timelineData.bounds);
        }

        setLoading(false);
      } catch (err) {
        console.error("Map init error:", err);
        if (mounted) {
          setError("Không thể tải bản đồ");
          setLoading(false);
        }
      }
    }

    initMap();

    return () => {
      mounted = false;
      if (mapInstance.current) {
        mapInstance.current.remove();
        mapInstance.current = null;
      }
    };
  }, []);

  // Re-render when timeline data changes
  useEffect(() => {
    if (mapInstance.current && timelineData?.points?.length > 0) {
      renderTimeline(mapInstance.current, timelineData.points);
      fitBounds(mapInstance.current, timelineData.bounds);
    }
  }, [timelineData]);

  function renderTimeline(map, points) {
    // Clear existing layers
    map.eachLayer((layer) => {
      if (layer instanceof L.Polyline || layer instanceof L.Marker || layer instanceof L.CircleMarker) {
        map.removeLayer(layer);
      }
    });

    if (points.length === 0) return;

    // Draw route line
    const latlngs = points.map((p) => [p.lat, p.lon]);
    const polyline = L.polyline(latlngs, {
      color: "#2563eb",
      weight: 3,
      opacity: 0.8,
      dashArray: "10, 10",
      lineCap: "round",
      lineJoin: "round",
    }).addTo(map);

    // Add gradient effect for direction
    if (L.polylineDecorator) {
      L.polylineDecorator(polyline, {
        patterns: [
          {
            offset: "5%",
            repeat: "10%",
            symbol: L.Symbol.arrowHead({
              pixelSize: 12,
              polygon: false,
              pathOptions: { color: "#2563eb", fillOpacity: 1, weight: 2 },
            }),
          },
        ],
      }).addTo(map);
    }

    // Add markers for each point
    points.forEach((point, index) => {
      const isStart = index === 0;
      const isEnd = index === points.length - 1;
      
      // Create custom marker
      const markerIcon = L.divIcon({
        className: "journey-marker",
        html: `
          <div class="marker-wrapper ${isStart ? "start" : ""} ${isEnd ? "end" : ""}">
            <div class="marker-pin">
              <span class="marker-number">${index + 1}</span>
            </div>
            <div class="marker-label">${point.name}</div>
          </div>
        `,
        iconSize: [40, 50],
        iconAnchor: [20, 50],
      });

      const marker = L.marker([point.lat, point.lon], { icon: markerIcon }).addTo(map);

      // Popup with details
      const transportIcon = TRANSPORT_ICONS[point.transport_mode] || "🚶";
      const transportLabel = TRANSPORT_LABELS[point.transport_mode] || "Đi bộ";
      
      const popupContent = `
        <div class="journey-popup">
          <h4>${point.name}</h4>
          <p class="popup-meta">
            ${point.merchant ? `<span>🏪 ${point.merchant}</span>` : ""}
            ${point.amount > 0 ? `<span>💰 ${formatMoney(point.amount, point.currency)}</span>` : ""}
            <span>${transportIcon} ${transportLabel}</span>
            <span>🕐 ${formatTime(point.occurred_at)}</span>
          </p>
          ${point.invoice_id ? `<button class="popup-btn" data-invoice="${point.invoice_id}">Xem hóa đơn</button>` : ""}
        </div>
      `;

      marker.bindPopup(popupContent, {
        className: "journey-popup-container",
        maxWidth: 280,
      });

      // Click handler
      marker.on("click", () => {
        setSelectedPoint(point);
      });
    });

    // Add start/end markers
    if (points.length > 0) {
      const start = points[0];
      const end = points[points.length - 1];

      L.circleMarker([start.lat, start.lon], {
        radius: 8,
        fillColor: "#22c55e",
        color: "#fff",
        weight: 2,
        fillOpacity: 1,
      }).addTo(map).bindTooltip("🏠 Điểm xuất phát", { permanent: false, direction: "top" });

      L.circleMarker([end.lat, end.lon], {
        radius: 8,
        fillColor: "#ef4444",
        color: "#fff",
        weight: 2,
        fillOpacity: 1,
      }).addTo(map).bindTooltip("🏁 Điểm kết thúc", { permanent: false, direction: "top" });
    }
  }

  function fitBounds(map, bounds) {
    if (!bounds) return;
    const padding = 0.01;
    map.fitBounds([
      [bounds.min_lat - padding, bounds.min_lon - padding],
      [bounds.max_lat + padding, bounds.max_lon + padding],
    ], { padding: [40, 40], maxZoom: 16 });
  }

  function formatMoney(amount, currency) {
    return new Intl.NumberFormat("vi-VN", {
      style: "currency",
      currency: currency || "VND",
      maximumFractionDigits: 0,
    }).format(amount);
  }

  function formatTime(isoString) {
    try {
      return new Date(isoString).toLocaleTimeString("vi-VN", {
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "";
    }
  }

  if (loading) {
    return (
      <div className="journey-map" style={{ height }}>
        <div className="map-loading">
          <div className="spinner" />
          <p>Đang tải bản đồ hành trình...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="journey-map" style={{ height }}>
        <div className="map-error">
          <p>⚠ {error}</p>
          <p className="map-error-hint">Cần ít nhất 2 giao dịch có vị trí để hiển thị lộ trình</p>
        </div>
      </div>
    );
  }

  return (
    <div className="journey-map-wrapper">
      <div className="journey-map-header">
        <h3>🗺️ Bản đồ hành trình</h3>
        {timelineData && (
          <div className="journey-stats">
            <span className="stat">
              <span className="stat-value">{timelineData.points?.length || 0}</span>
              <span className="stat-label">Điểm dừng</span>
            </span>
            <span className="stat">
              <span className="stat-value">{timelineData.total_distance_km?.toFixed(1) || 0} km</span>
              <span className="stat-label">Tổng quãng đường</span>
            </span>
            <span className="stat">
              <span className="stat-value">{formatMoney(timelineData.total_spent, timelineData.currency)}</span>
              <span className="stat-label">Tổng chi tiêu</span>
            </span>
          </div>
        )}
      </div>
      
      <div ref={mapRef} className="journey-map" style={{ height }} />
      
      {showTimeline && timelineData?.points?.length > 0 && (
        <div className="journey-timeline" role="region" aria-label="Dòng thời gian hành trình">
          <button 
            className="timeline-toggle" 
            onClick={() => setShowTimeline(!showTimeline)}
            aria-expanded={showTimeline}
            aria-controls="timeline-list"
          >
            {showTimeline ? "▲ Thu gọn" : "▼ Mở rộng"} dòng thời gian
          </button>
          
          <div id="timeline-list" className="timeline-list" hidden={!showTimeline}>
            {timelineData.points.map((point, index) => (
              <div 
                key={point.id} 
                className={`timeline-item ${selectedPoint?.id === point.id ? "active" : ""}`}
                onClick={() => setSelectedPoint(point)}
              >
                <div className="timeline-marker">
                  <span className="timeline-number">{index + 1}</span>
                  <span className="timeline-transport">{TRANSPORT_ICONS[point.transport_mode] || "🚶"}</span>
                </div>
                <div className="timeline-content">
                  <div className="timeline-name">{point.name}</div>
                  <div className="timeline-meta">
                    {point.merchant && <span>🏪 {point.merchant}</span>}
                    {point.amount > 0 && <span>💰 {formatMoney(point.amount, point.currency)}</span>}
                    <span>🕐 {formatTime(point.occurred_at)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {selectedPoint && (
        <div className="journey-detail-panel" role="dialog" aria-modal="true" aria-label="Chi tiết điểm dừng">
          <div className="detail-header">
            <h4>{selectedPoint.name}</h4>
            <button className="detail-close" onClick={() => setSelectedPoint(null)} aria-label="Đóng">×</button>
          </div>
          <div className="detail-content">
            {selectedPoint.merchant && (
              <div className="detail-row">
                <span className="detail-label">Nhà cung cấp</span>
                <span className="detail-value">{selectedPoint.merchant}</span>
              </div>
            )}
            {selectedPoint.amount > 0 && (
              <div className="detail-row">
                <span className="detail-label">Số tiền</span>
                <span className="detail-value">{formatMoney(selectedPoint.amount, selectedPoint.currency)}</span>
              </div>
            )}
            <div className="detail-row">
              <span className="detail-label">Thời gian</span>
              <span className="detail-value">{new Date(selectedPoint.occurred_at).toLocaleString("vi-VN")}</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Phương tiện</span>
              <span className="detail-value">
                {(TRANSPORT_ICONS[selectedPoint.transport_mode] || "🚶")} {TRANSPORT_LABELS[selectedPoint.transport_mode] || "Đi bộ"}
              </span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Tọa độ</span>
              <span className="detail-value">{selectedPoint.lat.toFixed(6)}, {selectedPoint.lon.toFixed(6)}</span>
            </div>
            {selectedPoint.invoice_id && (
              <button className="btn-primary detail-btn" onClick={() => window.dispatchEvent(new CustomEvent("view-invoice", { detail: selectedPoint.invoice_id }))}>
                Xem hóa đơn liên quan
              </button>
            )}
          </div>
        </div>
      )}

      <style jsx>{`
        .journey-map-wrapper {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: var(--r-md);
          overflow: hidden;
        }
        .journey-map-header {
          padding: 16px;
          border-bottom: 1px solid var(--border);
        }
        .journey-map-header h3 {
          margin: 0 0 12px;
          font-size: var(--fs-md);
        }
        .journey-stats {
          display: flex;
          gap: 16px;
          flex-wrap: wrap;
        }
        .stat {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .stat-value {
          font-size: var(--fs-md);
          font-weight: 700;
          color: var(--text);
        }
        .stat-label {
          font-size: var(--fs-xs);
          color: var(--text-dim);
          text-transform: uppercase;
        }
        .journey-map {
          width: 100%;
          height: 100%;
        }
        .map-loading, .map-error {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          height: 100%;
          color: var(--text-dim);
          padding: 20px;
          text-align: center;
        }
        .map-error-hint {
          font-size: var(--fs-sm);
          margin-top: 8px;
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

        /* Timeline */
        .journey-timeline {
          border-top: 1px solid var(--border);
          max-height: 300px;
          overflow: hidden;
        }
        .timeline-toggle {
          width: 100%;
          padding: 10px;
          background: var(--surface-2);
          border: none;
          color: var(--accent);
          cursor: pointer;
          font-size: var(--fs-sm);
          text-align: left;
        }
        .timeline-toggle:hover {
          background: var(--border);
        }
        .timeline-list {
          display: flex;
          flex-direction: column;
          max-height: 250px;
          overflow-y: auto;
          padding: 8px;
        }
        .timeline-item {
          display: flex;
          gap: 12px;
          padding: 10px;
          border-radius: var(--r-sm);
          cursor: pointer;
          transition: background 0.15s;
        }
        .timeline-item:hover {
          background: var(--surface-2);
        }
        .timeline-item.active {
          background: rgba(37, 99, 235, 0.15);
          border: 1px solid var(--accent);
        }
        .timeline-marker {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 4px;
          min-width: 40px;
        }
        .timeline-number {
          width: 24px;
          height: 24px;
          border-radius: 50%;
          background: var(--accent);
          color: white;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: var(--fs-xs);
          font-weight: 700;
        }
        .timeline-transport {
          font-size: 16px;
        }
        .timeline-content {
          flex: 1;
          min-width: 0;
        }
        .timeline-name {
          font-weight: 600;
          font-size: var(--fs-sm);
          margin-bottom: 4px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .timeline-meta {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          font-size: var(--fs-xs);
          color: var(--text-dim);
        }

        /* Detail Panel */
        .journey-detail-panel {
          position: absolute;
          bottom: 0;
          left: 0;
          right: 0;
          background: var(--surface);
          border-top: 1px solid var(--border);
          border-radius: var(--r-md) var(--r-md) 0 0;
          padding: 16px;
          max-height: 60vh;
          overflow-y: auto;
          box-shadow: 0 -4px 20px rgba(0,0,0,0.3);
          z-index: 100;
          animation: slideUp 0.2s ease;
        }
        @keyframes slideUp {
          from { transform: translateY(100%); opacity: 0; }
          to { transform: translateY(0); opacity: 1; }
        }
        .detail-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 12px;
        }
        .detail-header h4 {
          margin: 0;
          font-size: var(--fs-md);
        }
        .detail-close {
          background: none;
          border: none;
          font-size: 24px;
          color: var(--text-dim);
          cursor: pointer;
          line-height: 1;
        }
        .detail-close:hover { color: var(--text); }
        .detail-content {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .detail-row {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          padding-bottom: 8px;
          border-bottom: 1px solid var(--border);
        }
        .detail-label {
          color: var(--text-dim);
          font-size: var(--fs-sm);
        }
        .detail-value {
          color: var(--text);
          font-size: var(--fs-sm);
          font-weight: 500;
          text-align: right;
        }
        .detail-btn {
          margin-top: 8px;
          width: 100%;
        }

        /* Leaflet custom styles */
        .journey-marker {
          background: none;
          border: none;
        }
        .marker-wrapper {
          display: flex;
          flex-direction: column;
          align-items: center;
          pointer-events: none;
        }
        .marker-pin {
          width: 32px;
          height: 32px;
          border-radius: 50% 50% 50% 0;
          background: var(--accent);
          transform: rotate(-45deg);
          display: flex;
          align-items: center;
          justify-content: center;
          box-shadow: 0 2px 8px rgba(0,0,0,0.4);
          border: 3px solid var(--surface);
        }
        .marker-wrapper.start .marker-pin {
          background: #22c55e;
        }
        .marker-wrapper.end .marker-pin {
          background: #ef4444;
        }
        .marker-number {
          transform: rotate(45deg);
          color: white;
          font-size: 11px;
          font-weight: 700;
          line-height: 1;
        }
        .marker-label {
          margin-top: 4px;
          padding: 2px 6px;
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: var(--r-sm);
          font-size: 10px;
          white-space: nowrap;
          color: var(--text);
          box-shadow: 0 2px 8px rgba(0,0,0,0.3);
          transform: rotate(45deg);
          transform-origin: bottom center;
        }
        .journey-popup {
          padding: 4px 0;
        }
        .journey-popup h4 {
          margin: 0 0 8px;
          font-size: var(--fs-md);
        }
        .popup-meta {
          display: flex;
          flex-direction: column;
          gap: 4px;
          font-size: var(--fs-xs);
          color: var(--text-dim);
        }
        .popup-btn {
          margin-top: 8px;
          padding: 6px 12px;
          background: var(--accent);
          color: white;
          border: none;
          border-radius: var(--r-sm);
          cursor: pointer;
          font-size: var(--fs-xs);
        }
        .popup-btn:hover {
          background: var(--accent-hover);
        }

        @media (max-width: 760px) {
          .journey-stats {
            gap: 8px;
          }
          .stat-value {
            font-size: var(--fs-sm);
          }
          .timeline-meta {
            flex-direction: column;
            gap: 2px;
          }
        }
      `}</style>
    </div>
  );
}
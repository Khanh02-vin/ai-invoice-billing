import { useEffect, useRef, useState } from "react";
import { api, fmtVnd, SPEND_LABEL } from "../api";
import { tiktokSearchUrl } from "../labels";

let L = null;
let leafletLoaded = false;
let leafletLoadPromise = null;

async function loadLeaflet() {
  if (leafletLoaded) return L;
  if (leafletLoadPromise) return leafletLoadPromise;
  leafletLoadPromise = (async () => {
    if (!document.querySelector('link[href*="leaflet.css"]')) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
      link.integrity = "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=";
      link.crossOrigin = "";
      document.head.appendChild(link);
    }
    await new Promise((resolve, reject) => {
      if (window.L) { resolve(); return; }
      const script = document.createElement("script");
      script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
      script.integrity = "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=";
      script.crossOrigin = "";
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });
    L = window.L;
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

// CARTO giờ trả tile có watermark "API KEY REQUIRED" khi không có key → dùng tile OSM gốc,
// tối màu bằng CSS (xem .leaflet-tile trong styles.css).
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION = '&copy; OpenStreetMap contributors';

function popupNode(place, priced) {
  const wrap = document.createElement("div");
  const title = document.createElement("a");
  title.className = "place-link";
  title.href = tiktokSearchUrl(place.name);
  title.target = "_blank";
  title.rel = "noopener noreferrer";
  title.textContent = place.name;
  wrap.appendChild(title);
  const line = document.createElement("p");
  line.textContent = priced
    ? `${fmtVnd(place.spend_per_person)} / người — ${SPEND_LABEL}`
    : `Chưa có ${SPEND_LABEL}`;
  wrap.appendChild(line);
  return wrap;
}

export default function MapView({ coords, refresh }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const [places, setPlaces] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const params = new URLSearchParams({ lat: coords.lat, lon: coords.lon, radius_km: "5", limit: "50" });
    api(`/discover?${params}`)
      .then((data) => { if (active) setPlaces([...(data.results || []), ...(data.unpriced || [])]); })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [coords, refresh]);

  useEffect(() => {
    let active = true;
    if (places === null) return;
    loadLeaflet()
      .then((lib) => {
        if (!active || !containerRef.current) return;
        const map = mapRef.current
          ? mapRef.current
          : lib.map(containerRef.current, {
              center: [coords.lat, coords.lon],
              zoom: 13,
              zoomControl: false,
              attributionControl: false,
            });
        if (!mapRef.current) {
          lib.tileLayer(TILE_URL, { maxZoom: 20, attribution: TILE_ATTRIBUTION }).addTo(map);
          lib.control.zoom({ position: "topright" }).addTo(map);
          lib.control.attribution({ position: "bottomleft", prefix: false }).addTo(map);
          mapRef.current = map;
        }

        map.eachLayer((layer) => {
          if (layer !== mapRef.current && (layer instanceof lib.Marker || layer instanceof lib.CircleMarker)) {
            map.removeLayer(layer);
          }
        });

        const located = places.filter((place) => place.lat != null && place.lon != null);
        located.forEach((place) => {
          const priced = place.spend_per_person != null;
          if (priced) {
            lib.marker([place.lat, place.lon]).addTo(map).bindPopup(popupNode(place, true));
          } else {
            lib.circleMarker([place.lat, place.lon], {
              radius: 8,
              color: "#64748b",
              fillColor: "#64748b",
              fillOpacity: 0.8,
              weight: 2,
            }).addTo(map).bindPopup(popupNode(place, false));
          }
        });

        const center = [coords.lat, coords.lon];
        lib.circleMarker(center, {
          radius: 6,
          color: "#60a5fa",
          fillColor: "#2563eb",
          fillOpacity: 1,
          weight: 2,
        }).addTo(map).bindPopup("Vị trí của bạn");

        if (located.length) {
          const bounds = lib.latLngBounds(located.map((place) => [place.lat, place.lon]));
          bounds.extend(center);
          map.fitBounds(bounds, { padding: [36, 36], maxZoom: 16 });
        } else {
          map.setView(center, 13);
        }
      })
      .catch(() => { if (active) setError("Không tải được bản đồ (cần mạng để lấy Leaflet)."); });
    return () => { active = false; };
  }, [places, coords]);

  useEffect(() => () => {
    if (mapRef.current) {
      mapRef.current.remove();
      mapRef.current = null;
    }
  }, []);

  return (
    <section className="card map-card">
      <div className="card-head">
        <h2>Bản đồ quán đã ghi</h2>
        <span className="card-note">Marker xanh = đã có mức chi, chấm xám = chưa</span>
      </div>
      {error ? <p className="msg err" role="alert">{error}</p> : null}
      <div className="map-box" ref={containerRef} role="application" aria-label="Bản đồ địa điểm" />
      {places && !places.some((place) => place.lat != null) ? (
        <p className="empty-note">Chưa có quán nào có toạ độ. Ghi quán kèm vị trí để thấy trên bản đồ.</p>
      ) : null}
    </section>
  );
}

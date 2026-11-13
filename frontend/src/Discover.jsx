import { useEffect, useState } from "react";
import History from "./History";
import MapView from "./components/MapView";
import ScanView from "./ScanView";
import { api, fmtVnd, SPEND_LABEL } from "./api";
import { CATEGORY_LABELS, categoryLabel, distanceText, tiktokSearchUrl } from "./labels";

// Fallback khi chưa có quyền vị trí: trung tâm TP.HCM.
const DEFAULT_COORDS = { lat: 10.7769, lon: 106.7009, exact: false };

function usePosition() {
  const [coords, setCoords] = useState(DEFAULT_COORDS);

  useEffect(() => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => setCoords({ lat: pos.coords.latitude, lon: pos.coords.longitude, exact: true }),
      () => {},
      { timeout: 8000, maximumAge: 300000 },
    );
  }, []);

  return coords;
}

function BandCards({ bands }) {
  const rows = bands.filter((band) => band.low != null);
  if (!rows.length) {
    return (
      <p className="empty-note">
        Chưa đủ dữ liệu để biết mức chi của bạn. Ghi vài lần chi tiêu ở tab “Lịch sử”.
      </p>
    );
  }
  return (
    <div className="band-grid">
      {rows.map((band) => (
        <article className="band-card" key={band.category}>
          <p className="band-label">{categoryLabel(band.category)}</p>
          <p className="band-range">
            {fmtVnd(band.low)} – {fmtVnd(band.high)}
          </p>
          <p className="band-sub">
            {band.is_default ? "ước lượng ban đầu" : `${band.sample_size} lần ghi`}
            {band.median != null && !band.is_default ? ` · giữa ${fmtVnd(band.median)}` : ""}
          </p>
        </article>
      ))}
    </div>
  );
}

// Bấm tên quán → mở trang tìm kiếm TikTok của quán đó ở tab mới.
function PlaceName({ name }) {
  return (
    <h3>
      <a
        className="place-link"
        href={tiktokSearchUrl(name)}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`Tìm “${name}” trên TikTok (mở tab mới)`}
      >
        {name}
      </a>
    </h3>
  );
}

function PlaceCard({ place, onPick }) {
  return (
    <li className="place-card">
      <div className="place-main">
        <PlaceName name={place.name} />
        <p className="place-meta">
          {categoryLabel(place.category)}
          {place.distance_km != null ? ` · ${distanceText(place.distance_km)}` : ""}
          {place.visits ? ` · đã ghi ${place.visits} lần` : ""}
        </p>
        {place.address ? <p className="place-address">{place.address}</p> : null}
      </div>
      <div className="place-side">
        {place.spend_per_person == null ? (
          <p className="spend-note">Chưa có {SPEND_LABEL}</p>
        ) : (
          <>
            <p className="spend-num">{fmtVnd(place.spend_per_person)}</p>
            <p className="spend-note">{SPEND_LABEL} / người</p>
          </>
        )}
        {place.fits ? (
          <span className="badge badge-ok">Phù hợp mức chi tiêu</span>
        ) : place.band ? (
          <span className="badge badge-dim">
            {fmtVnd(place.band.low)} – {fmtVnd(place.band.high)}
          </span>
        ) : null}
        <button type="button" className="btn-ghost btn-small" onClick={() => onPick(place)}>
          Ghi chi tiêu
        </button>
      </div>
    </li>
  );
}

function Explore({ coords, refresh, onPick }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [bands, setBands] = useState([]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [osm, setOsm] = useState(null);
  const [osmBusy, setOsmBusy] = useState(false);

  useEffect(() => {
    let active = true;
    api("/discover/bands")
      .then((value) => { if (active) setBands(value.bands || []); })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [refresh]);

  useEffect(() => {
    const params = new URLSearchParams({
      lat: coords.lat,
      lon: coords.lon,
      radius_km: "3",
      limit: "20",
    });
    if (query.trim()) params.set("q", query.trim());
    if (category) params.set("category", category);

    let active = true;
    setLoading(true);
    setError("");
    const timer = setTimeout(() => {
      api(`/discover?${params}`)
        .then((value) => { if (active) setData(value); })
        .catch((err) => { if (active) setError(err.message); })
        .finally(() => { if (active) setLoading(false); });
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [query, category, coords, refresh]);

  async function loadOsm() {
    setOsmBusy(true);
    setError("");
    try {
      const params = new URLSearchParams({ lat: coords.lat, lon: coords.lon, radius_m: "2000" });
      setOsm(await api(`/places/nearby?${params}`));
    } catch (err) {
      setError(err.message);
    } finally {
      setOsmBusy(false);
    }
  }

  const results = data?.results || [];
  const unpriced = data?.unpriced || [];

  return (
    <>
      <section className="card">
        <div className="card-head">
          <h2>Mức chi tiêu của bạn</h2>
        </div>
        <BandCards bands={bands} />
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Có thể bạn sẽ thích</h2>
          <span className="card-note">{coords.exact ? "quanh vị trí hiện tại" : "quanh trung tâm TP.HCM"}</span>
        </div>

        <div className="filter-row">
          <div className="search-box">
            <label className="sr-only" htmlFor="discover-search">Tìm quán</label>
            <input
              id="discover-search"
              type="search"
              placeholder="Tìm theo tên quán…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <label className="sr-only" htmlFor="discover-category">Loại quán</label>
          <select
            id="discover-category"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            <option value="">Tất cả loại</option>
            {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>

        {error ? <p className="msg err" role="alert">{error}</p> : null}
        {loading && !results.length ? <p className="empty-note">Đang tìm…</p> : null}

        {!loading && !results.length && !unpriced.length ? (
          <p className="empty-note">
            Chưa có quán nào trong bán kính 3 km. Ghi chi tiêu ở tab “Lịch sử” để tạo dữ liệu.
          </p>
        ) : null}

        {results.length ? (
          <ul className="place-list">
            {results.map((place) => (
              <PlaceCard key={place.id} place={place} onPick={onPick} />
            ))}
          </ul>
        ) : null}

        {unpriced.length ? (
          <details className="unpriced">
            <summary>Chưa có {SPEND_LABEL} ({unpriced.length})</summary>
            <ul className="place-list">
              {unpriced.map((place) => (
                <PlaceCard key={place.id} place={place} onPick={onPick} />
              ))}
            </ul>
          </details>
        ) : null}

        <div className="osm-row">
          <button type="button" className="btn-ghost" onClick={loadOsm} disabled={osmBusy}>
            {osmBusy ? "Đang tìm…" : "Tìm quán lân cận chưa có trong dữ liệu"}
          </button>
          {osm ? (
            osm.places.length ? (
              <ul className="place-list">
                {osm.places.map((place) => (
                  <li className="place-card" key={`${place.name}-${place.lat}-${place.lon}`}>
                    <div className="place-main">
                      <PlaceName name={place.name} />
                      <p className="place-meta">
                        {place.kind || "địa điểm"} · {distanceText((place.distance_m || 0) / 1000)}
                      </p>
                      <p className="place-address">Nguồn OpenStreetMap · chưa rõ mức chi</p>
                    </div>
                    <div className="place-side">
                      <button
                        type="button"
                        className="btn-ghost btn-small"
                        onClick={() => onPick(place)}
                      >
                        Ghi chi tiêu
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-note">OpenStreetMap không có quán nào trong 2 km.</p>
            )
          ) : null}
        </div>
      </section>
    </>
  );
}

export default function Discover({ user, onLogout }) {
  const [view, setView] = useState("explore");
  const [refresh, setRefresh] = useState(0);
  const [draft, setDraft] = useState(null);
  const coords = usePosition();

  function pickPlace(place) {
    setDraft({ ...place, at: Date.now() });
    setView("history");
  }

  // QR đã đọc xong: điền sẵn form chi tiêu rồi chuyển sang tab Lịch sử để kiểm tra.
  function useScan(parsed) {
    const found = [];
    if (parsed.amount) found.push("số tiền");
    if (parsed.merchant_name) found.push("tên quán");
    setDraft({
      name: parsed.merchant_name || "",
      amount: parsed.amount ? String(parsed.amount) : "",
      party: parsed.party_size ? String(parsed.party_size) : "",
      scanNote: found.length
        ? `Đã đọc ${found.join(" và ")} từ QR. Kiểm tra lại trước khi lưu.`
        : "QR không có số tiền. Hãy nhập bằng tay.",
      at: Date.now(),
    });
    setView("history");
  }

  const tabs = [
    ["explore", "Khám phá"],
    ["map", "Bản đồ"],
    ["history", "Lịch sử"],
    ["scan", "Quét QR"],
  ];

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span aria-hidden="true">🍜</span>
          <div>
            <p className="brand-name">Quanh Ta</p>
            <p className="brand-sub">Khám phá quán theo mức chi tiêu</p>
          </div>
        </div>
        <div className="header-right">
          <span className="user-chip">{user.username}</span>
          <button type="button" className="btn-ghost btn-small" onClick={onLogout}>Đăng xuất</button>
        </div>
      </header>

      <nav className="tabbar" aria-label="Chế độ xem">
        {tabs.map(([value, label]) => (
          <button
            key={value}
            type="button"
            className="tab"
            aria-pressed={view === value}
            onClick={() => setView(value)}
          >
            {label}
          </button>
        ))}
      </nav>

      <main className="content">
        {view === "explore" ? (
          <Explore coords={coords} refresh={refresh} onPick={pickPlace} />
        ) : null}
        {view === "map" ? (
          <MapView coords={coords} refresh={refresh} onPick={pickPlace} />
        ) : null}
        {view === "history" ? (
          <History
            coords={coords}
            draft={draft}
            onScan={() => setView("scan")}
            onChanged={() => setRefresh((value) => value + 1)}
          />
        ) : null}
        {view === "scan" ? (
          <ScanView
            onUse={useScan}
            coords={coords}
            onChanged={() => setRefresh((value) => value + 1)}
          />
        ) : null}
      </main>
    </div>
  );
}

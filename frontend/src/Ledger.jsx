import { useEffect, useMemo, useState } from "react";
import { api, clearToken, getToken } from "./api";

const money = (tx) => new Intl.NumberFormat("vi-VN", { style: "currency", currency: tx.currency || "VND", maximumFractionDigits: 0 }).format(tx.amount || 0);
const dateValue = (value) => value ? new Date(value).toISOString().slice(0, 10) : "";

export default function Ledger({ onBack, onLogout }) {
  const [transactions, setTransactions] = useState([]);
  const [places, setPlaces] = useState([]);
  const [attachments, setAttachments] = useState([]);
  const [selected, setSelected] = useState(null);
  const [sharing, setSharing] = useState(false);
  const [token, setToken] = useState(getToken());
  const [direction, setDirection] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ amount: "", direction: "debit", merchant: "", category: "", note: "" });

  const load = async () => {
    try {
      setError("");
      const query = new URLSearchParams({ limit: "200" });
      if (direction) query.set("direction", direction);
      const data = await api(`/payments/transactions?${query}`);
      setTransactions(data.filter((tx) => {
        const day = dateValue(tx.occurred_at);
        return (!from || day >= from) && (!to || day <= to);
      }));
    } catch (e) { setError(e.message); }
  };
  useEffect(() => { load(); }, [direction, from, to]);
  useEffect(() => {
    api("/payments/preferences/place-sharing").then((data) => setSharing(data.share_place_visits)).catch(() => {});
  }, []);
  useEffect(() => {
    if (!selected) { setAttachments([]); return; }
    api(`/payments/transactions/${selected.id}/attachments`).then(setAttachments).catch((e) => setError(e.message));
  }, [selected?.id]);

  const totals = useMemo(() => transactions.reduce((sum, tx) => {
    if (tx.direction === "credit") sum.income += Number(tx.amount);
    else sum.expense += Number(tx.amount);
    return sum;
  }, { income: 0, expense: 0 }), [transactions]);

  const locate = () => new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => resolve({ location_lat: coords.latitude, location_lon: coords.longitude, location_captured_at: new Date().toISOString() }),
      () => resolve(null), { enableHighAccuracy: false, timeout: 8000, maximumAge: 60000 },
    );
  });

  const save = async (event) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const location = await locate();
      await api("/payments/transactions", { method: "POST", body: JSON.stringify({ ...form, amount: Number(form.amount), ...location }) });
      setForm({ amount: "", direction: "debit", merchant: "", category: "", note: "" });
      await load();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const clearLocation = async () => {
    try {
      const updated = await api(`/payments/transactions/${selected.id}`, { method: "PUT", body: JSON.stringify({ note: selected.note || "", category: selected.category || "", location_lat: null, location_lon: null, location_captured_at: null }) });
      setSelected(updated); await load();
    } catch (e) { setError(e.message); }
  };

  const mapLink = (tx) => `https://www.openstreetmap.org/?mlat=${tx.location_lat}&mlon=${tx.location_lon}#map=17/${tx.location_lat}/${tx.location_lon}`;
  const updateSelected = async (patch) => {
    try {
      const updated = await api(`/payments/transactions/${selected.id}`, { method: "PUT", body: JSON.stringify({ note: selected.note || "", category: selected.category || "", location_lat: selected.location_lat, location_lon: selected.location_lon, location_captured_at: selected.location_captured_at, ...patch }) });
      setSelected(updated); await load();
    } catch (e) { setError(e.message); }
  };
  const editLocation = async () => {
    const point = await locate();
    if (!point) { setError("Vị trí không khả dụng hoặc quyền truy cập đã bị từ chối."); return; }
    await updateSelected(point);
  };
  const toggleSharing = async () => {
    const enabled = !sharing;
    try {
      const data = await api(`/payments/preferences/place-sharing?enabled=${enabled}`, { method: "PUT" });
      setSharing(data.share_place_visits);
    } catch (e) { setError(e.message); }
  };
  const sharePlace = async (place) => {
    try {
      await api("/payments/places/share", { method: "POST", body: JSON.stringify({ name: place.name, kind: place.kind, lat: place.lat, lon: place.lon }) });
      setError("");
    } catch (e) { setError(e.message); }
  };
  const uploadAttachment = async (file) => {
    const body = new FormData(); body.append("file", file);
    try {
      const item = await api(`/payments/transactions/${selected.id}/attachments`, { method: "POST", body });
      setAttachments((items) => [item, ...items]);
    } catch (e) { setError(e.message); }
  };
  const suggestPlaces = async () => {
    setPlaces([]);
    const point = selected?.location_lat != null ? { latitude: selected.location_lat, longitude: selected.location_lon } : await new Promise((resolve) => {
      if (!navigator.geolocation) return resolve(null);
      navigator.geolocation.getCurrentPosition(({ coords }) => resolve(coords), () => resolve(null), { timeout: 8000, maximumAge: 60000 });
    });
    if (!point) { setError("Vị trí không khả dụng hoặc quyền truy cập đã bị từ chối."); return; }
    try {
      const data = await api(`/payments/places?lat=${point.latitude}&lon=${point.longitude}`);
      const shared = await api(`/payments/places/shared?lat=${point.latitude}&lon=${point.longitude}`).catch(() => ({ places: [] }));
      setPlaces([...(data.places || []).map((place) => ({ ...place, source: "OpenStreetMap" })), ...(shared.places || []).map((place) => ({ ...place, source: "Địa điểm được chia sẻ" }))]);
    } catch (e) { setError(e.message); }
  };

  return <main className="content">
    <header className="content-header"><div><button className="btn-link" onClick={onBack}>← Hóa đơn</button><h1>Lịch sử thu chi</h1></div><button className="btn-link" onClick={() => { clearToken(); onLogout?.(); }}>Đăng xuất</button></header>
    {error && <div className="msg err" role="alert">{error}</div>}
    <section className="stat-grid"><div className="stat-card"><div className="stat-label">Tổng thu trong kỳ</div><div className="stat-num">{new Intl.NumberFormat("vi-VN").format(totals.income)} ₫</div></div><div className="stat-card"><div className="stat-label">Tổng chi trong kỳ</div><div className="stat-num">{new Intl.NumberFormat("vi-VN").format(totals.expense)} ₫</div></div></section>
    <section className="card"><div className="card-head"><h2>Ghi khoản thu hoặc chi</h2></div><form className="ledger-form" onSubmit={save}>
      <label>Số tiền<input required type="number" min="1" step="any" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></label>
      <label>Loại<select value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}><option value="debit">Chi</option><option value="credit">Thu</option></select></label>
      <label>Người bán / nguồn<input value={form.merchant} onChange={(e) => setForm({ ...form, merchant: e.target.value })} maxLength="120" /></label>
      <label>Nhóm<input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} maxLength="64" /></label>
      <label className="ledger-wide">Ghi chú<input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} maxLength="1000" /></label>
      <p className="ledger-wide ledger-hint">Sau khi bấm lưu, ứng dụng có thể xin quyền vị trí một lần. Nếu từ chối hoặc không khả dụng, giao dịch vẫn được lưu bình thường.</p>
      <button className="btn-primary" disabled={busy}>{busy ? "Đang lưu…" : "Lưu khoản thu/chi"}</button>
    </form></section>
    <section className="card"><div className="card-head"><h2>Giao dịch</h2><div className="row"><label>Từ ngày <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label><label>Đến ngày <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label><label>Loại <select value={direction} onChange={(e) => setDirection(e.target.value)}><option value="">Tất cả</option><option value="credit">Thu</option><option value="debit">Chi</option></select></label></div></div>
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Ngày</th><th>Loại</th><th>Người bán / nguồn</th><th>Số tiền</th><th>Chứng từ</th><th></th></tr></thead><tbody>
        {transactions.map((tx) => <tr key={tx.id} onClick={() => setSelected(tx)} style={{ cursor: "pointer" }}><td>{tx.occurred_at ? new Date(tx.occurred_at).toLocaleString("vi-VN") : "—"}</td><td>{tx.direction === "credit" ? "Thu" : "Chi"}</td><td>{tx.merchant || "—"}</td><td>{money(tx)}</td><td>{tx.matched_invoice_id ? "Đã ghép hóa đơn" : tx.status === "pending_receipt" ? "Chờ chứng từ" : "—"}</td><td><button className="btn-link" onClick={(e) => { e.stopPropagation(); setSelected(tx); }}>Chi tiết</button></td></tr>)}
        {!transactions.length && <tr><td className="empty" colSpan="6">Chưa có giao dịch trong khoảng thời gian này.</td></tr>}
      </tbody></table></div></section>
    {selected && <div className="modal-backdrop" onClick={() => setSelected(null)}><section className="modal ledger-detail" role="dialog" aria-modal="true" aria-labelledby="ledger-detail-title" onClick={(e) => e.stopPropagation()}><div className="card-head"><h2 id="ledger-detail-title">Chi tiết giao dịch</h2><button className="btn-link" onClick={() => setSelected(null)}>Đóng</button></div>
      <dl className="ledger-details"><dt>Số tiền</dt><dd>{money(selected)}</dd><dt>Loại</dt><dd>{selected.direction === "credit" ? "Thu" : "Chi"}</dd><dt>Người bán / nguồn</dt><dd>{selected.merchant || "—"}</dd><dt>Ngày</dt><dd>{selected.occurred_at ? new Date(selected.occurred_at).toLocaleString("vi-VN") : "—"}</dd><dt>Nhóm</dt><dd>{selected.category || "—"}</dd><dt>Ghi chú</dt><dd>{selected.note || "—"}</dd><dt>Hóa đơn liên quan</dt><dd>{selected.matched_invoice_id || "Không có"}</dd>
    {selected.location_lat != null && <><dt>Vị trí</dt><dd>{selected.location_lat}, {selected.location_lon} · {selected.location_captured_at || ""}<br /><a href={mapLink(selected)} target="_blank" rel="noreferrer">Mở trên OpenStreetMap</a> <button className="btn-link" onClick={editLocation}>Cập nhật vị trí</button> <button className="btn-link danger" onClick={() => updateSelected({ location_lat: null, location_lon: null, location_captured_at: null })}>Xóa vị trí</button></dd></>}
      </dl>
      <div className="ledger-attachments"><h3>Ảnh / chứng từ đính kèm</h3><label className="btn-small">Chụp hoặc chọn ảnh<input type="file" accept="image/*,.pdf" capture="environment" hidden onChange={(e) => e.target.files?.[0] && uploadAttachment(e.target.files[0])} /></label>{attachments.map((item) => <div key={item.id}><a href={`/payments/attachments/${item.id}/content`} target="_blank" rel="noreferrer">{item.mime_type} · {Math.round(item.size / 1024)} KB</a></div>)}<p className="ledger-hint">Ảnh không phải hóa đơn chỉ là chứng từ tham khảo; thông tin kế toán cần bạn xác nhận.</p></div>
      <label className="ledger-share"><input type="checkbox" checked={sharing} onChange={toggleSharing} /> Cho phép dùng các địa điểm tôi chia sẻ để gợi ý cho người khác (mặc định tắt; không chia sẻ số tiền, giao dịch hay ảnh).</label>
      <div className="ledger-suggestions"><button className="btn-small" onClick={suggestPlaces}>Gợi ý địa điểm gần đây</button>{places.map((place) => <div key={`${place.source}-${place.name}-${place.lat}`}><a href={`https://www.openstreetmap.org/?mlat=${place.lat}&mlon=${place.lon}#map=17/${place.lat}/${place.lon}`} target="_blank" rel="noreferrer">{place.name}</a> <span>{place.kind} · {place.source}</span>{place.source === "OpenStreetMap" && sharing && <button className="btn-link" onClick={() => sharePlace(place)}>Chia sẻ lượt ghé</button>}</div>)}</div>
    </section></div>}
  </main>;
}

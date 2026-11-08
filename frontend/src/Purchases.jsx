import { useEffect, useState } from "react";
import { api } from "./api";
import JourneyMap from "./components/JourneyMap";

const money = (p) => new Intl.NumberFormat("vi-VN", { style: "currency", currency: p.currency || "VND", maximumFractionDigits: 0 }).format(p.amount || 0);

export default function Purchases({ onBack }) {
  const [purchases, setPurchases] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [draftFile, setDraftFile] = useState(null);
  const [paymentLocation, setPaymentLocation] = useState(null);
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftStatus, setDraftStatus] = useState("");
  const [form, setForm] = useState({ item: "", category: "", merchant: "", amount: "", occurred_at: "", notes: "", payment_lat: null, payment_lon: null, payment_captured_at: null, payment_accuracy_m: null, payment_source: null, location_address: "", purchase_context_type: "physical", merchant_lat: "", merchant_lon: "" });

  const load = async () => {
    try { setError(""); setPurchases(await api("/payments/purchases?limit=200")); }
    catch (e) { setError(e.message); }
  };
  useEffect(() => { load(); }, []);

  const recognizePhoto = async (file) => {
    if (!file) return;
    setDraftBusy(true); setError(""); setDraftStatus("");
    const body = new FormData(); body.append("file", file);
    try {
      const result = await api("/payments/image-draft", { method: "POST", body });
      setDraftFile(file);
      setForm((f) => ({ ...f, item: result.item || f.item, category: result.category || f.category, merchant: result.merchant || f.merchant, location_address: result.address || f.location_address }));
      setDraftStatus(result.message);
    } catch (e) { setError(e.message); }
    finally { setDraftBusy(false); }
  };

  const locate = () => new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => resolve({ payment_lat: coords.latitude, payment_lon: coords.longitude, payment_captured_at: new Date().toISOString(), payment_accuracy_m: coords.accuracy, payment_source: "gps" }),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: 8000 }
    );
  });

  const save = async (e) => {
    e.preventDefault();
    if (!form.amount || Number(form.amount) <= 0) { setError("Vui lòng nhập giá tiền."); return; }
    setBusy(true); setError("");
    try {
      const body = new FormData();
      if (draftFile) body.append("file", draftFile);
      body.append("item", form.item);
      body.append("category", form.category);
      body.append("merchant", form.merchant);
      body.append("amount", form.amount);
      body.append("currency", "VND");
      if (form.occurred_at) body.append("occurred_at", form.occurred_at);
      body.append("notes", form.notes);
      body.append("purchase_context_type", form.purchase_context_type);
      if (form.purchase_context_type === "physical" && form.merchant_lat !== "" && form.merchant_lon !== "") {
        body.append("merchant_lat", form.merchant_lat);
        body.append("merchant_lon", form.merchant_lon);
        body.append("merchant_name", form.merchant);
        body.append("merchant_verified_by_user", "true");
      }
      const location = paymentLocation || {};
      for (const [key, value] of Object.entries(location)) body.append(key, String(value));
      if (form.location_address) body.append("location_address", form.location_address);
      await api("/payments/purchases/manual", { method: "POST", body });
      setForm({ item: "", category: "", merchant: "", amount: "", occurred_at: "", notes: "", payment_lat: null, payment_lon: null, payment_captured_at: null, payment_accuracy_m: null, payment_source: null, location_address: "", purchase_context_type: "physical", merchant_lat: "", merchant_lon: "" });
      setPaymentLocation(null);
      setDraftFile(null);
      setDraftStatus("");
      await load();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const remove = async (id) => {
    if (!window.confirm("Xóa mua hàng này?")) return;
    try { await api(`/payments/purchases/${id}`, { method: "DELETE" }); await load(); }
    catch (e) { setError(e.message); }
  };

  const totalSpent = purchases.reduce((s, p) => s + (p.amount || 0), 0);
  // Purchase-location map: merchant coordinates only. Payment/device GPS and
  // legacy coordinates belong to the payment timeline, not to merchant location.
  const timelineData = {
    points: purchases.filter((p) => p.merchant_lat != null && p.merchant_lon != null).map((p) => ({
      id: p.id, name: p.merchant_name || p.merchant || p.item || "Mua hàng", lat: p.merchant_lat, lon: p.merchant_lon,
      amount: p.amount, currency: p.currency, occurred_at: p.occurred_at || p.created_at, merchant: p.merchant,
    })),
    total_distance_km: 0, total_spent: totalSpent, currency: "VND",
  };

  return (
    <div className="ledger">
      <div className="ledger-header">
        <button className="btn-link" onClick={onBack}>← Quay lại</button>
        <h2>Mua hàng thủ công (không hóa đơn)</h2>
      </div>
      {error && <div className="error-banner">{error}</div>}
      <p className="ledger-hint">Tổng: {money({ amount: totalSpent })} · {purchases.length} món</p>

      <div className="card">
        <h3>Chụp / chọn ảnh vật phẩm</h3>
        <label className="btn-small">Chọn ảnh<input type="file" accept="image/*" hidden onChange={(e) => e.target.files?.[0] && recognizePhoto(e.target.files[0])} /></label>
        {draftBusy && <span> Đang nhận diện…</span>}
        {draftStatus && <p className="ledger-hint">{draftStatus}</p>}
      </div>

      <form className="card" onSubmit={save}>
        <h3>Thông tin mua hàng</h3>
        <div className="form-row">
          <input placeholder="Tên sản phẩm / món" value={form.item} onChange={(e) => setForm({ ...form, item: e.target.value })} />
          <input placeholder="Loại" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} />
        </div>
        <div className="form-row">
          <input placeholder="Cửa hàng / quán" value={form.merchant} onChange={(e) => setForm({ ...form, merchant: e.target.value })} />
          <input type="number" placeholder="Giá tiền" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
        </div>
        <div className="form-row">
          <input type="date" value={form.occurred_at} onChange={(e) => setForm({ ...form, occurred_at: e.target.value })} />
          <input placeholder="Địa chỉ (tùy chọn)" value={form.location_address} onChange={(e) => setForm({ ...form, location_address: e.target.value })} />
        </div>
        <div className="form-row">
          <select aria-label="Ngữ cảnh mua hàng" value={form.purchase_context_type} onChange={(e) => setForm({ ...form, purchase_context_type: e.target.value })}>
            <option value="physical">Mua trực tiếp tại cửa hàng</option>
            <option value="online">Mua online</option>
            <option value="remote">Mua từ xa</option>
          </select>
          {form.purchase_context_type === "physical" ? (
            <input placeholder="Vĩ độ cửa hàng (tùy chọn)" type="number" step="any" value={form.merchant_lat} onChange={(e) => setForm({ ...form, merchant_lat: e.target.value })} />
          ) : null}
        </div>
        {form.purchase_context_type === "physical" && <div className="form-row">
          <input placeholder="Kinh độ cửa hàng (tùy chọn)" type="number" step="any" value={form.merchant_lon} onChange={(e) => setForm({ ...form, merchant_lon: e.target.value })} />
        </div>}
        {form.purchase_context_type === "physical" && <p className="ledger-hint">Tọa độ cửa hàng được nhập riêng; GPS thanh toán không tự điền vị trí cửa hàng. Nhập đủ cả vĩ độ và kinh độ để hiện trên bản đồ.</p>}
        <button type="button" className="btn-small" onClick={async () => { const point = await locate(); if (point) { setPaymentLocation(point); setError(""); } else setError("Không lấy được vị trí hoặc bạn đã từ chối quyền. Vẫn có thể lưu mua hàng không có GPS."); }}>{paymentLocation ? "Đã thêm GPS thanh toán" : "Thêm GPS thanh toán (tùy chọn)"}</button>
        <p className="ledger-hint">GPS chỉ ghi vị trí thiết bị khi bạn chủ động bấm nút; không chứng minh cửa hàng hoặc việc thanh toán. Có thể tiếp tục không có GPS.</p>
        <textarea placeholder="Ghi chú" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
        <button className="btn-primary" disabled={busy} type="submit">{busy ? "Đang lưu…" : "Lưu mua hàng"}</button>
      </form>

      <div className="card">
        <h3>Bản đồ mua hàng ({purchases.length})</h3>
        <p className="ledger-hint">Bản đồ này chỉ dùng tọa độ cửa hàng (không dùng vị trí thiết bị/GPS lúc thanh toán, cũng không dùng vị trí lịch sử cũ).</p>
        {timelineData.points.length > 0 ? <JourneyMap timelineData={timelineData} height={360} /> : <p className="ledger-hint">Chưa có mua hàng nào có vị trí.</p>}
      </div>

      <div className="card">
        <h3>Danh sách mua hàng</h3>
        <table className="tx-table">
          <thead><tr><th>Ngày</th><th>Sản phẩm</th><th>Cửa hàng</th><th>Số tiền</th><th>Vị trí thanh toán</th><th></th></tr></thead>
          <tbody>
            {purchases.map((p) => (
              <tr key={p.id}>
                <td>{p.occurred_at || p.created_at?.slice(0, 10) || "—"}</td>
                <td>{p.item || "—"}</td>
                <td>{p.merchant || "—"}</td>
                <td>{money(p)}</td>
                <td>{p.payment_lat != null && p.payment_lon != null ? `${p.payment_lat.toFixed(4)}, ${p.payment_lon.toFixed(4)} · GPS` : p.location_address || (p.location_lat != null ? `${p.location_lat.toFixed(4)}, ${p.location_lon.toFixed(4)} · lịch sử` : "—")}</td>
                <td><button className="btn-link danger" onClick={() => remove(p.id)}>Xóa</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

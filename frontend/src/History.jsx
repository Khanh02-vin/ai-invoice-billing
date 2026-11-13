import { useEffect, useState } from "react";
import { api, fmtVnd, SPEND_LABEL } from "./api";
import { CATEGORY_LABELS, categoryLabel, tiktokSearchUrl } from "./labels";

const EMPTY_FORM = {
  query: "",
  placeId: "",
  address: "",
  lat: null,
  lon: null,
  category: "cafe",
  amount: "",
  party: "1",
  note: "",
};

function parseDigits(value) {
  return value.replace(/[^\d]/g, "");
}

function formatDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
}

function draftToForm(draft) {
  if (!draft) return null;
  const category = CATEGORY_LABELS[draft.category]
    ? draft.category
    : CATEGORY_LABELS[draft.kind]
      ? draft.kind
      : "other";
  return {
    ...EMPTY_FORM,
    query: draft.name || "",
    placeId: draft.id || "",
    address: draft.address || "",
    lat: draft.lat ?? null,
    lon: draft.lon ?? null,
    category,
    amount: draft.amount || "",
    party: draft.party || EMPTY_FORM.party,
  };
}

export default function History({ coords, draft, onScan, onChanged }) {
  const [spends, setSpends] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  const [qrNote, setQrNote] = useState("");

  function reload() {
    setLoading(true);
    api("/spends")
      .then((data) => setSpends(data.spends || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(reload, []);

  // Chọn "Ghi chi tiêu" ở tab khám phá/bản đồ, hoặc vừa quét QR xong → điền sẵn.
  useEffect(() => {
    const next = draftToForm(draft);
    if (next) {
      setForm(next);
      setQrNote(draft.scanNote || "");
      setMessage("");
      setError("");
      setSuggestions([]);
    }
  }, [draft]);

  useEffect(() => {
    const query = form.query.trim();
    if (form.placeId || query.length < 2) {
      setSuggestions([]);
      return;
    }
    let active = true;
    const params = new URLSearchParams({ q: query });
    if (coords.exact) {
      params.set("lat", coords.lat);
      params.set("lon", coords.lon);
      params.set("radius_m", "20000");
    }
    const timer = setTimeout(() => {
      api(`/places?${params}`)
        .then((data) => { if (active) setSuggestions((data.places || []).slice(0, 5)); })
        .catch(() => { if (active) setSuggestions([]); });
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [form.query, form.placeId, coords]);

  function update(field, value) {
    setForm((current) => {
      const next = { ...current, [field]: value };
      if (field === "query") {
        next.placeId = "";
        next.address = "";
        next.lat = null;
        next.lon = null;
      }
      return next;
    });
  }

  function pickSuggestion(place) {
    setForm((current) => ({
      ...current,
      query: place.name,
      placeId: place.id,
      address: place.address || "",
      lat: place.lat,
      lon: place.lon,
      category: place.category || "other",
    }));
    setSuggestions([]);
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    setMessage("");

    const amount = Number(parseDigits(form.amount));
    const party = Number(form.party);
    if (!form.query.trim()) { setError("Nhập tên quán."); return; }
    if (!amount) { setError("Nhập số tiền đã trả."); return; }
    if (!Number.isInteger(party) || party < 1 || party > 50) {
      setError("Số người phải từ 1 đến 50.");
      return;
    }

    setBusy(true);
    try {
      let placeId = form.placeId;
      if (!placeId) {
        const created = await api("/places", {
          method: "POST",
          body: JSON.stringify({
            name: form.query.trim(),
            category: form.category,
            address: form.address.trim(),
            lat: form.lat ?? (coords.exact ? coords.lat : null),
            lon: form.lon ?? (coords.exact ? coords.lon : null),
          }),
        });
        placeId = created.place.id;
      }

      await api("/spends", {
        method: "POST",
        body: JSON.stringify({
          place_id: placeId,
          amount,
          party_size: party,
          note: form.note.trim(),
        }),
      });

      setForm(EMPTY_FORM);
      setQrNote("");
      setMessage("Đã ghi lần chi tiêu.");
      reload();
      if (onChanged) onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(spend) {
    if (!window.confirm(`Xoá lần chi ${fmtVnd(spend.amount)} tại ${spend.place_name}?`)) return;
    setError("");
    try {
      await api(`/spends/${spend.id}`, { method: "DELETE" });
      reload();
      if (onChanged) onChanged();
    } catch (err) {
      setError(err.message);
    }
  }

  const total = spends.reduce((sum, spend) => sum + spend.amount, 0);

  return (
    <>
      <section className="card">
        <div className="card-head">
          <h2>Ghi một lần chi tiêu</h2>
          <span className="card-note">
            Số tiền là tổng bill, chia cho số người → {SPEND_LABEL}
          </span>
        </div>

        <form className="form-grid" onSubmit={submit}>
          <div className="form-group place-query">
            <label htmlFor="spend-place">Tên quán</label>
            <input
              id="spend-place"
              type="text"
              value={form.query}
              onChange={(event) => update("query", event.target.value)}
              placeholder="Ví dụ: Cà phê Nhà Gỗ"
              autoComplete="off"
              required
            />
            {suggestions.length ? (
              <ul className="suggest-list">
                {suggestions.map((place) => (
                  <li key={place.id}>
                    <button type="button" onClick={() => pickSuggestion(place)}>
                      {place.name}
                      <span>{categoryLabel(place.category)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="form-group">
            <label htmlFor="spend-category">Loại quán</label>
            <select
              id="spend-category"
              value={form.category}
              onChange={(event) => update("category", event.target.value)}
            >
              {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="spend-amount">Tổng bill (VND)</label>
            <input
              id="spend-amount"
              type="text"
              inputMode="numeric"
              value={form.amount}
              onChange={(event) => update("amount", event.target.value)}
              placeholder="125000"
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="spend-party">Số người</label>
            <input
              id="spend-party"
              type="number"
              min="1"
              max="50"
              value={form.party}
              onChange={(event) => update("party", event.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="spend-note">Ghi chú</label>
            <input
              id="spend-note"
              type="text"
              maxLength="200"
              value={form.note}
              onChange={(event) => update("note", event.target.value)}
              placeholder="Không bắt buộc"
            />
          </div>

          <div className="form-actions">
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? "Đang lưu…" : "Lưu lần chi"}
            </button>
            <button type="button" className="qr-button" onClick={onScan}>
              Quét QR trên bill
            </button>
          </div>
        </form>

        {qrNote ? <p className="empty-note">{qrNote}</p> : null}
        {error ? <p className="msg err" role="alert">{error}</p> : null}
        {message ? <p className="msg ok" role="status">{message}</p> : null}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Lịch sử chi tiêu</h2>
          <span className="card-note">
            {spends.length ? `${spends.length} lần · tổng ${fmtVnd(total)}` : "chưa có lần nào"}
          </span>
        </div>

        {loading ? <p className="empty-note">Đang tải…</p> : null}
        {!loading && !spends.length ? (
          <p className="empty-note">Chưa ghi lần chi nào. Điền form ở trên để bắt đầu.</p>
        ) : null}

        {spends.length ? (
          <ul className="spend-list">
            {spends.map((spend) => (
              <li className="spend-row" key={spend.id}>
                <div className="spend-main">
                  <h3>
                    {spend.place_name ? (
                      <a
                        className="place-link"
                        href={tiktokSearchUrl(spend.place_name)}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label={`Tìm “${spend.place_name}” trên TikTok (mở tab mới)`}
                      >
                        {spend.place_name}
                      </a>
                    ) : (
                      "Địa điểm đã xoá"
                    )}
                  </h3>
                  <p className="place-meta">
                    {categoryLabel(spend.category)} · {formatDate(spend.occurred_at)}
                    {spend.note ? ` · ${spend.note}` : ""}
                  </p>
                </div>
                <div className="spend-side">
                  <p className="spend-num">{fmtVnd(spend.spend_per_person)}</p>
                  <p className="spend-note">
                    {SPEND_LABEL} / người
                    <br />
                    {fmtVnd(spend.amount)} ÷ {spend.party_size} người
                  </p>
                  <button
                    type="button"
                    className="btn-ghost btn-small"
                    onClick={() => remove(spend)}
                  >
                    Xoá
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </>
  );
}

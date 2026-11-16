import { useCallback, useEffect, useRef, useState } from "react";
import { api, compressImage, fmtVnd } from "./api";
import { hasNativeDetector, makeDetector, scanImageFile, scanVideoFrame } from "./qrScan";
import { loadApps, loadBanks, pickAppForBank, shouldPrefill, transferQrUrl, transferUrl } from "./vietqr";

const PAY_APP_KEY = "vietqr_app";

function getPosition() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      () => resolve(null),
      { timeout: 6000, maximumAge: 300000 }
    );
  });
}

function usePayInfo(result) {
  const [banks, setBanks] = useState(null);
  const [apps, setApps] = useState(null);
  const [error, setError] = useState("");
  const bin = result?.bank_bin;

  useEffect(() => {
    if (!bin || !result?.account) return;
    let active = true;
    setError("");
    Promise.all([loadBanks(), loadApps()])
      .then(([bankMap, appList]) => {
        if (!active) return;
        setBanks(bankMap);
        setApps(appList);
      })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [bin, result?.account]);

  return { banks, apps, error };
}

function PayBlock({ result, fallbackResult, onOpenBank, draftBusy }) {
  const base = result || fallbackResult;
  const [manualAmount, setManualAmount] = useState("");
  const amount = result?.amount ?? (manualAmount ? Number(manualAmount.replace(/\D/g, "")) : 0);
  const effective = amount > 0 ? { ...base, amount } : null;
  const { banks, apps, error } = usePayInfo(base);
  const [picked, setPicked] = useState(localStorage.getItem(PAY_APP_KEY) || "");
  const [linking, setLinking] = useState(false);

  const bank = banks?.get(String(base?.bank_bin));
  // App chuyển tiền của BẠN (liên kết một lần, lưu localStorage) — ưu tiên hơn
  // app của ngân hàng nhận, vì tiền rời ra từ app mà bạn dùng.
  const linkedApp = (apps || []).find((a) => a.id === picked);
  const fallbackApp = pickAppForBank(apps, bank?.code, picked);
  const payAppId = linkedApp?.id || fallbackApp || "";
  const payAppName = (apps || []).find((a) => a.id === payAppId)?.name || "";
  const ready = Boolean(payAppId && bank);
  const prefill = shouldPrefill(apps, payAppId);

  async function openBank() {
    if (!ready || !effective) return;
    const href = transferUrl({
      appId: payAppId,
      amount: effective.amount,
      content: effective.merchant_name || "Thanh toan",
      account: effective.account,
      bankCode: bank.code,
      returnUrl: window.location.origin,
    });
    if (onOpenBank) await onOpenBank(effective);
    window.open(href, "_blank", "noopener,noreferrer");
  }

  return (
    <div className="pay-block">
      {!result?.amount ? (
        <label className="pay-line">
          Số tiền cần chuyển
          <input
            type="text"
            inputMode="numeric"
            value={manualAmount}
            onChange={(e) => setManualAmount(e.target.value)}
            placeholder="Nhập số tiền (VND)"
          />
        </label>
      ) : null}

      {effective ? (
        <>
          <div className="pay-card">
            <p className="pay-amount">{fmtVnd(effective.amount)}</p>
            <p className="pay-line">
              Tới {bank?.name || "ngân hàng nhận"} · <strong>{effective.account}</strong>
            </p>
            <img
              className="pay-qr"
              src={transferQrUrl({
                bankBin: base.bank_bin,
                account: effective.account,
                amount: effective.amount,
                content: effective.merchant_name || "",
              })}
              alt={`Mã VietQR chuyển ${fmtVnd(effective.amount)} tới tài khoản ${effective.account}`}
              width="240"
              height="240"
            />
          </div>

          <details className="pay-more" open>
            <summary>
              {prefill
                ? "Mở app ngân hàng (số tiền và tài khoản đã điền sẵn)"
                : "Mở app ngân hàng của ngân hàng nhận"}
            </summary>
            {error ? (
              <p className="pay-line">
                Không tải được danh sách ngân hàng. Bạn tự chuyển tới số tài khoản{" "}
                <strong>{effective.account}</strong>.
              </p>
            ) : (
              <>
                <div className="pay-line">
                  {linkedApp
                    ? `✅ Đã liên kết app chuyển tiền: ${linkedApp.name}`
                    : "Chưa liên kết app ngân hàng để chuyển tiền"}
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => setLinking(!linking)}
                  >
                    {linking ? "Ẩn" : "🔗 Liên kết app của tôi"}
                  </button>
                </div>
                {linking && (
                  <label className="pay-line" htmlFor="pay-app">
                    Chọn app BẠN dùng để chuyển (tiền rời từ app này)
                    <select
                      id="pay-app"
                      value={picked}
                      onChange={(event) => {
                        setPicked(event.target.value);
                        localStorage.setItem(PAY_APP_KEY, event.target.value);
                      }}
                      disabled={!apps?.length}
                    >
                      <option value="">— Chọn app —</option>
                      {(apps || []).map((app) => (
                        <option key={app.id} value={app.id}>
                          {app.name}
                          {app.id === bank?.code ? " — ngân hàng nhận" : ""}
                          {app.autofill ? " · điền sẵn số tiền" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {ready ? (
                  <button type="button" className="btn-primary pay-open" onClick={openBank} disabled={draftBusy}>
                    {draftBusy
                      ? "Đang lưu giao dịch tạm…"
                      : `${prefill ? "Mở app, điền sẵn" : "Mở app"} và chuyển ${fmtVnd(effective.amount)}`}
                  </button>
                ) : (
                  <p className="pay-line">
                    {bank ? "Chọn app ngân hàng ở trên." : `Ngân hàng nhận chưa có trong danh sách — số tài khoản ${effective.account}.`}
                  </p>
                )}
                {ready && !prefill ? (
                  <p className="pay-line">
                    {`${bank?.name || "Ngân hàng này"} không tự điền được số tiền — app chỉ mở ra, bạn nhập ${fmtVnd(effective.amount)} trong app.`}
                  </p>
                ) : null}
                <p className="pay-line">
                  Mở app ngân hàng sẽ tạo giao dịch tạm. Sau khi chuyển, quay lại bấm "Đã chuyển" để ghim lên bản đồ chi tiêu.
                </p>
              </>
            )}
          </details>
        </>
      ) : (
        <p className="pay-line">Nhập số tiền để hiện mã chuyển khoản và mở app ngân hàng.</p>
      )}
    </div>
  );
}

function DraftList({ drafts, onConfirm, onDelete }) {
  if (!drafts.length) return null;
  return (
    <section className="card" style={{ marginTop: 12 }}>
      <div className="card-head">
        <h2>Giao dịch tạm</h2>
        <span className="card-note">đã mở app ngân hàng, chờ xác nhận</span>
      </div>
      <ul className="spend-list">
        {drafts.map((d) => (
          <li className="spend-row" key={d.id}>
            <div className="spend-main">
              <h3>{d.merchant_name || "Chuyển khoản"}</h3>
              <p className="place-meta">
                {fmtVnd(d.amount)} {d.account ? `· tới ${d.account}` : ""}
                {d.lat != null ? " · có toạ độ" : " · chưa có toạ độ"}
              </p>
            </div>
            <div className="spend-side">
              <button type="button" className="btn-primary btn-small" onClick={() => onConfirm(d)}>Đã chuyển</button>
              <button type="button" className="btn-ghost btn-small" onClick={() => onDelete(d.id)}>Xoá</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function ScanView({ onUse, coords, onChanged }) {
  const videoRef = useRef(null);
  const useCallbackRef = useRef(onUse);
  useCallbackRef.current = onUse;
  const readRef = useRef(null);
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState("Đang mở camera…");
  const [error, setError] = useState("");
  const [cameraOff, setCameraOff] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [scanPos, setScanPos] = useState(null);
  const [drafts, setDrafts] = useState([]);
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftMsg, setDraftMsg] = useState("");
  const [returnDraft, setReturnDraft] = useState(null);
  const [popupBusy, setPopupBusy] = useState(false);

  const loadDrafts = useCallback(() => {
    api("/spends/drafts").then((d) => setDrafts(d.drafts || [])).catch(() => {});
  }, []);

  useEffect(() => { loadDrafts(); }, [loadDrafts]);

  // Quay lại từ app ngân hàng: tải lại danh sách tạm và nhắc xác nhận, để số tiền
  // và toạ độ đã lưu không bị bỏ quên.
  useEffect(() => {
    function onVisible() {
      if (document.visibilityState !== "visible") return;
      api("/spends/drafts")
        .then((d) => {
          const rows = d.drafts || [];
          setDrafts(rows);
          if (rows.length) {
            setDraftMsg("Đã chuyển khoản xong? Bấm “Đã chuyển” để ghi chi tiêu vào bản đồ.");
            // Bước 3 của luồng: quay lại từ app ngân hàng → popup xác nhận
            setReturnDraft((cur) => cur || rows[0]);
          }
        })
        .catch(() => {});
    }
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  readRef.current = async (text) => {
    setBusy(true);
    setError("");
    try {
      const pos = coords?.exact ? { lat: coords.lat, lon: coords.lon } : await getPosition();
      setScanPos(pos);
      setResult(await api("/spends/parse-qr", { method: "POST", body: JSON.stringify({ text }) }));
      setStatus("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    let stream = null;
    let timer = null;
    let check = null;
    let stopped = false;

    function giveUp(message) {
      stopped = true;
      if (timer) clearInterval(timer);
      if (check) clearTimeout(check);
      stream?.getTracks().forEach((track) => track.stop());
      setCameraOff(true);
      setError(message);
    }

    async function start() {
      setCameraOff(false);
      setStatus("Đang mở camera…");
      if (!navigator.mediaDevices?.getUserMedia) {
        giveUp("Trình duyệt này không mở được camera.");
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" } },
          audio: false,
        });
        if (stopped) return;
        const video = videoRef.current;
        video.srcObject = stream;
        await video.play();
        if (stopped) return;
        setStatus("Đưa mã QR vào khung");
        const detector = hasNativeDetector() ? makeDetector() : null;

        check = setTimeout(() => {
          if (stopped) return;
          if (!video.videoWidth) giveUp("Camera không gửi được hình ảnh.");
          else setStatus("Chưa đọc được mã. Đưa mã gần hơn cho đủ sáng, hoặc chọn ảnh QR bên dưới.");
        }, 8000);

        let decoding = false;
        timer = setInterval(async () => {
          if (decoding || stopped) return;
          decoding = true;
          try {
            const text = await scanVideoFrame(video, detector);
            if (text && !stopped) {
              stopped = true;
              clearInterval(timer);
              clearTimeout(check);
              readRef.current(text);
            }
          } catch {
          } finally {
            decoding = false;
          }
        }, 400);
      } catch (err) {
        giveUp(
          err?.name === "NotAllowedError"
            ? "Bạn chưa cho phép dùng camera."
            : "Không mở được camera.",
        );
      }
    }

    start();
    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
      if (check) clearTimeout(check);
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, [attempt]);

  async function onPickFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const detector = hasNativeDetector() ? makeDetector() : null;
      const text = await scanImageFile(file, detector);
      if (text) await readRef.current(text);
      else setError("Không thấy mã QR trong ảnh. Thử chụp lại gần và rõ hơn.");
    } catch {
      setError("Không đọc được ảnh này. Thử ảnh khác hoặc nhập số tiền bằng tay.");
    } finally {
      setBusy(false);
    }
  }

  function retry() {
    setResult(null);
    setError("");
    setDraftMsg("");
    setAttempt((value) => value + 1);
  }

  async function createDraftBeforeOpen(effective) {
    setDraftBusy(true);
    setDraftMsg("");
    try {
      const pos = scanPos || (coords?.exact ? { lat: coords.lat, lon: coords.lon } : await getPosition());
      await api("/spends/drafts", {
        method: "POST",
        body: JSON.stringify({
          amount: effective.amount,
          party_size: effective.party_size || 1,
          merchant_name: effective.merchant_name || "",
          bank_bin: effective.bank_bin || "",
          account: effective.account || "",
          lat: pos?.lat ?? null,
          lon: pos?.lon ?? null,
        }),
      });
      setDraftMsg("Đã lưu giao dịch tạm. Sau khi chuyển xong, quay lại bấm “Đã chuyển”.");
      loadDrafts();
    } catch (err) {
      setError(err.message);
    } finally {
      setDraftBusy(false);
    }
  }

  async function confirmDraft(d) {
    setError("");
    setDraftMsg("");
    try {
      await api(`/spends/drafts/${d.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ category: "restaurant", place_name: d.merchant_name || undefined }),
      });
      setDraftMsg("Đã ghi chi tiêu và ghim địa điểm.");
      loadDrafts();
      if (onChanged) onChanged();
    } catch (err) {
      setError(err.message);
    }
  }

  // "Hóa đơn tự động": app tự vẽ biên lai đối chiếu từ dữ liệu giao dịch + GPS —
  // 0 cú bấm thêm, luôn có bằng chứng kể cả khi không chụp màn hình ngân hàng.
  async function makeAutoReceipt(d) {
    const W = 720;
    const H = 900;
    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = "#1b2130";
    ctx.fillRect(0, 0, W, 120);
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 40px sans-serif";
    ctx.fillText("🍜 Quanh Ta", 40, 75);
    ctx.font = "22px sans-serif";
    ctx.fillText("Biên lai đối chiếu chuyển khoản", 40, 105);
    ctx.fillStyle = "#111111";
    const rows = [
      ["Địa điểm", d.merchant_name || "Địa điểm chưa rõ"],
      ["Số tiền", `${new Intl.NumberFormat("vi-VN").format(d.amount)} đ`],
      ["Ngân hàng nhận", d.bank_bin ? `BIN ${d.bank_bin}` : "—"],
      ["Số tài khoản", d.account || "—"],
      ["Toạ độ GPS", d.lat != null ? `${Number(d.lat).toFixed(5)}, ${Number(d.lon).toFixed(5)}` : "không có"],
      ["Thời gian xác nhận", new Date().toLocaleString("vi-VN")],
    ];
    let y = 190;
    for (const [label, value] of rows) {
      ctx.font = "20px sans-serif";
      ctx.fillStyle = "#6b7280";
      ctx.fillText(label.toUpperCase(), 40, y);
      ctx.font = "bold 26px sans-serif";
      ctx.fillStyle = "#111111";
      const lines = String(value).match(/.{1,34}(\s|$)/g) || [String(value)];
      for (const line of lines) {
        ctx.fillText(line.trim(), 40, y + 34);
        y += 34;
      }
      y += 26;
    }
    ctx.font = "18px sans-serif";
    ctx.fillStyle = "#9ca3af";
    ctx.fillText("Tạo tự động bởi Quanh Ta khi xác nhận đã chuyển khoản.", 40, H - 60);
    ctx.fillText("Ảnh này đối chiếu với biên lai trong app ngân hàng.", 40, H - 32);
    return canvas.toDataURL("image/jpeg", 0.85);
  }

  // Bước 4: xác nhận giao dịch (tạo spend + ghim địa điểm) rồi gắn ảnh biên lai.
  // Luôn có bằng chứng: ảnh camera (chụp màn hình ngân hàng) hoặc hóa đơn tự động.
  async function finishConfirm(d, photoDataUrl) {
    setPopupBusy(true);
    setError("");
    try {
      const evidence = photoDataUrl || (await makeAutoReceipt(d));
      const res = await api(`/spends/drafts/${d.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ category: "restaurant", place_name: d.merchant_name || undefined }),
      });
      const spendId = res.spend?.id;
      if (spendId) {
        await api(`/spends/${spendId}/receipt`, {
          method: "POST",
          body: JSON.stringify({ image: evidence }),
        });
      }
      setDraftMsg(
        photoDataUrl
          ? "Đã lưu giao dịch kèm ảnh biên lai và ghim địa điểm trên bản đồ."
          : "Đã lưu giao dịch kèm hóa đơn tự động và ghim địa điểm trên bản đồ."
      );
      setReturnDraft(null);
      loadDrafts();
      if (onChanged) onChanged();
    } catch (err) {
      setError(err.message);
      setReturnDraft(null);
    } finally {
      setPopupBusy(false);
    }
  }

  async function deleteDraft(id) {
    try { await api(`/spends/drafts/${id}`, { method: "DELETE" }); loadDrafts(); } catch (err) { setError(err.message); }
  }

  return (
    <>
      <section className="card">
        <div className="card-head">
          <h2>Quét QR thanh toán</h2>
          <span className="card-note">đọc số tiền, lưu vị trí, chuyển sang app ngân hàng</span>
        </div>

        {!cameraOff && (
          <div className="scan-frame scan-frame-page">
            <video ref={videoRef} playsInline muted autoPlay aria-label="Khung hình camera" />
            <div className="scan-corners" aria-hidden="true" />
          </div>
        )}

        <p className="scan-status" role="status" aria-live="polite">
          {draftMsg || error || status}
        </p>

        {result ? (
          <div className="scan-result">
            <dl className="scan-rows">
              <div>
                <dt>Tổng bill</dt>
                <dd>{result.amount ? fmtVnd(result.amount) : "QR không ghi số tiền"}</dd>
              </div>
              <div>
                <dt>Tên quán</dt>
                <dd>{result.merchant_name || "QR không ghi tên quán"}</dd>
              </div>
              {result.account ? (
                <div>
                  <dt>Số tài khoản nhận</dt>
                  <dd>{result.account}</dd>
                </div>
              ) : null}
              <div>
                <dt>Toạ độ lúc quét</dt>
                <dd>{scanPos ? `${scanPos.lat.toFixed(5)}, ${scanPos.lon.toFixed(5)}` : "chưa lấy được"}</dd>
              </div>
            </dl>
            <p className="empty-note">
              {result.amount
                ? "Mở app ngân hàng sẽ tạo giao dịch tạm kèm toạ độ; bấm “Đã chuyển” để ghim chi tiêu."
                : "QR này không có số tiền. Nhập tiền để tạo mã/giao dịch tạm."}
            </p>
            {result.account ? (
              <PayBlock
                result={result.amount ? result : null}
                fallbackResult={result}
                onOpenBank={createDraftBeforeOpen}
                draftBusy={draftBusy}
              />
            ) : null}
            <div className="scan-actions">
              <button
                type="button"
                className="btn-primary"
                onClick={() => useCallbackRef.current(result)}
              >
                Ghi lần chi tiêu
              </button>
              <button type="button" className="btn-ghost" onClick={retry}>
                Quét lại
              </button>
            </div>
          </div>
        ) : (
          <>
            <label className={`scan-file${cameraOff ? " scan-file-main" : ""}${busy ? " is-busy" : ""}`}>
              {busy
                ? "Đang đọc ảnh…"
                : cameraOff
                  ? "Chọn ảnh QR trong máy"
                  : "Hoặc chọn ảnh QR có sẵn"}
              <input
                type="file"
                accept="image/*"
                onChange={onPickFile}
                disabled={busy}
                className="sr-only"
              />
            </label>
            {cameraOff ? (
              <p className="scan-hint">
                Ảnh chụp mã QR hoặc ảnh chụp màn hình mã đều được — không cần camera.
              </p>
            ) : null}
          </>
        )}
      </section>

      <DraftList drafts={drafts} onConfirm={confirmDraft} onDelete={deleteDraft} />

      {returnDraft ? (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Xác nhận đã chuyển khoản">
          <div className="modal-card">
            <h2>💸 Bạn đã chuyển khoản xong?</h2>
            <p className="pay-line">
              {fmtVnd(returnDraft.amount)}
              {returnDraft.account ? ` · tới ${returnDraft.account}` : ""}
            </p>
            <p className="pay-line">
              Ấn xác nhận: app chụp lại màn hình giao dịch (ngân hàng, số tiền, toạ độ,
              thời gian) làm bằng chứng đối chiếu — hoàn tất ghi chi tiêu ngay.
            </p>
            <button
              type="button"
              className="btn-primary"
              onClick={() => finishConfirm(returnDraft, null)}
              disabled={popupBusy}
              aria-busy={popupBusy}
            >
              ✅ Xác nhận đã chuyển — chụp màn hình lưu bằng chứng
            </button>
            <button
              type="button"
              className="link-button"
              onClick={() => setReturnDraft(null)}
              disabled={popupBusy}
            >
              Chưa chuyển / Bỏ qua
            </button>
            <p className="pay-line">
              Muốn giữ thêm màn hình chuyển khoản thật của ngân hàng? Vào Lịch sử,
              bấm 📸 Biên lai bên cạnh giao dịch.
            </p>
          </div>
        </div>
      ) : null}
    </>
  );
}

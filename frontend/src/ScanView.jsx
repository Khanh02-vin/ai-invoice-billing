import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmtVnd } from "./api";
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
  const [picked, setPicked] = useState("");

  const bank = banks?.get(String(base?.bank_bin));
  const stored = localStorage.getItem(PAY_APP_KEY);
  const appId = picked || pickAppForBank(apps, bank?.code, stored);
  const ready = Boolean(appId && bank);
  const prefill = shouldPrefill(apps, appId);

  async function openBank() {
    if (!ready || !effective) return;
    const href = transferUrl({
      appId,
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
                <label className="pay-line" htmlFor="pay-app">App của {bank?.name || "ngân hàng nhận"}</label>
                <select
                  id="pay-app"
                  value={appId}
                  onChange={(event) => {
                    setPicked(event.target.value);
                    localStorage.setItem(PAY_APP_KEY, event.target.value);
                  }}
                  disabled={!apps?.length}
                >
                  {(apps || []).filter((app) => app.id === bank?.code).map((app) => (
                    <option key={app.id} value={app.id}>{app.name}</option>
                  ))}
                </select>
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
          if (rows.length) setDraftMsg("Đã chuyển khoản xong? Bấm “Đã chuyển” để ghi chi tiêu vào bản đồ.");
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
          {error || draftMsg || status}
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
    </>
  );
}

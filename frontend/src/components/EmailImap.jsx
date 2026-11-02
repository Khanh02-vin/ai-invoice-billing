import { useEffect, useState } from "react";
import { api } from "../api";

const Icon = {
  check: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12" /></svg>,
  close: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>,
};

// Kết nối hộp thư IMAP để tự động nhận giao dịch từ email thanh toán.
// Nằm trong Cài đặt → Thanh toán; là chỗ duy nhất poll /payments/email-status.
export default function EmailImap() {
  const [status, setStatus] = useState("disconnected"); // disconnected, connecting, connected
  const [config, setConfig] = useState({ host: "", port: 993, username: "", password: "", folder: "INBOX" });
  const [showModal, setShowModal] = useState(false);

  useEffect(() => {
    api("/payments/email-status")
      .then((res) => {
        setStatus(res.connected ? "connected" : "disconnected");
        if (res.config) setConfig(res.config);
      })
      .catch(() => {});
  }, []);

  const handleConnect = async (e) => {
    e.preventDefault();
    setStatus("connecting");
    try {
      const res = await api("/payments/email-connect", {
        method: "POST",
        body: JSON.stringify(config),
      });
      if (res.ok) {
        setStatus("connected");
        setShowModal(false);
        alert(res.message);
      } else {
        setStatus("disconnected");
        alert("Lỗi: " + res.message);
      }
    } catch (err) {
      setStatus("disconnected");
      alert("Lỗi: " + err.message);
    }
  };

  return (
    <>
      <div className="settings-row settings-oauth-row">
        <div>
          <strong>Email nhận hóa đơn (IMAP)</strong>
          <p className="muted">
            {status === "connected"
              ? `Đã kết nối ${config.username || "hộp thư"} — hệ thống quét email định kỳ để lấy giao dịch.`
              : "Kết nối hộp thư IMAP để tự động nhận giao dịch từ email thanh toán."}
          </p>
        </div>
        <button className="btn-ghost" onClick={() => setShowModal(true)} disabled={status === "connecting"}>
          {status === "connected" ? "Cấu hình lại" : "Kết nối Email"}
        </button>
      </div>

      {showModal && (
        <div className="modal-backdrop" onClick={() => setShowModal(false)}>
          <div className="modal modal-email" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{status === "connected" ? "Cấu hình Email" : "Kết nối Email nhận bill"}</h2>
              <button className="modal-close" onClick={() => setShowModal(false)} aria-label="Đóng">
                {Icon.close}
              </button>
            </div>

            <form onSubmit={handleConnect}>
              <div className="modal-body">
                <p className="modal-description">
                  Cấu hình IMAP để tự động nhận hóa đơn chuyển khoản, thanh toán qua email.
                  Hệ thống sẽ quét email định kỳ và trích xuất giao dịch.
                </p>

                <div className="form-group">
                  <label htmlFor="email-host">IMAP Host <span className="required">*</span></label>
                  <input
                    id="email-host"
                    type="text"
                    value={config.host}
                    onChange={(e) => setConfig({ ...config, host: e.target.value })}
                    placeholder="imap.gmail.com"
                    required
                    disabled={status === "connecting"}
                  />
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label htmlFor="email-port">Port</label>
                    <input
                      id="email-port"
                      type="number"
                      value={config.port}
                      onChange={(e) => setConfig({ ...config, port: parseInt(e.target.value) || 993 })}
                      min="1"
                      max="65535"
                      disabled={status === "connecting"}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="email-folder">Thư mục</label>
                    <input
                      id="email-folder"
                      type="text"
                      value={config.folder}
                      onChange={(e) => setConfig({ ...config, folder: e.target.value })}
                      placeholder="INBOX"
                      disabled={status === "connecting"}
                    />
                  </div>
                </div>

                <div className="form-group">
                  <label htmlFor="email-username">Email <span className="required">*</span></label>
                  <input
                    id="email-username"
                    type="email"
                    value={config.username}
                    onChange={(e) => setConfig({ ...config, username: e.target.value })}
                    placeholder="your@email.com"
                    required
                    disabled={status === "connecting"}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="email-password">Mật khẩu ứng dụng <span className="required">*</span></label>
                  <input
                    id="email-password"
                    type="password"
                    value={config.password}
                    onChange={(e) => setConfig({ ...config, password: e.target.value })}
                    placeholder="Mật khẩu ứng dụng (không phải mật khẩu đăng nhập)"
                    required
                    disabled={status === "connecting"}
                    autoComplete="off"
                  />
                  <small className="form-hint">Dùng mật khẩu ứng dụng (App Password), không phải mật khẩu đăng nhập chính</small>
                </div>

                {status === "connected" && (
                  <div className="status-connected">
                    <span className="status-icon">{Icon.check}</span>
                    <span>Đã kết nối thành công</span>
                  </div>
                )}
              </div>

              <div className="modal-footer">
                <button type="button" className="btn-ghost" onClick={() => setShowModal(false)}>Hủy</button>
                <button type="submit" className="btn-primary" disabled={status === "connecting"}>
                  {status === "connecting" ? "Đang kết nối..." : status === "connected" ? "Lưu thay đổi" : "Kết nối và kiểm tra"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}

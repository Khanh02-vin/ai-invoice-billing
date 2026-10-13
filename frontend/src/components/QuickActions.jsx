import { useState, useRef, useEffect } from "react";
import { api } from "../api";

const Icon = {
  upload: <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M8 11V3m0 0L4.5 6.5M8 3l3.5 3.5M2.5 12.5h11" strokeLinecap="round" strokeLinejoin="round" /></svg>,
  email: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="2" y="4" width="20" height="16" rx="2" /><path d="M22 4L12 13 2 4" /></svg>,
  camera: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" /><circle cx="12" cy="12" r="4" /></svg>,
  check: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12" /></svg>,
  close: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>,
};

export default function QuickActions({ onUpload, onEmailConnect, onCamera }) {
  const [emailStatus, setEmailStatus] = useState("disconnected"); // disconnected, connecting, connected
  const [emailConfig, setEmailConfig] = useState({ host: "", port: 993, username: "", password: "", folder: "INBOX" });
  const [showEmailModal, setShowEmailModal] = useState(false);
  const [showCameraModal, setShowCameraModal] = useState(false);
  const [cameraStream, setCameraStream] = useState(null);
  const [capturedImage, setCapturedImage] = useState(null);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const [uploading, setUploading] = useState(false);

  // Check email connection status on mount
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const res = await api("/payments/email-status");
        setEmailStatus(res.connected ? "connected" : "disconnected");
        if (res.config) setEmailConfig(res.config);
      } catch (e) {
        // Ignore
      }
    };
    checkStatus();
  }, []);

  // Camera setup
  useEffect(() => {
    if (!showCameraModal) {
      if (cameraStream) {
        cameraStream.getTracks().forEach(track => track.stop());
        setCameraStream(null);
      }
      return;
    }

    const startCamera = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } },
        });
        setCameraStream(stream);
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
        }
      } catch (err) {
        alert("Không thể truy cập camera: " + err.message);
        setShowCameraModal(false);
      }
    };
    startCamera();

    return () => {
      if (cameraStream) {
        cameraStream.getTracks().forEach(track => track.stop());
      }
    };
  }, [showCameraModal]);

  const handleCapture = () => {
    if (!videoRef.current || !canvasRef.current) return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.9);
    setCapturedImage(dataUrl);
  };

  const handleCameraUpload = async () => {
    if (!capturedImage) return;
    setUploading(true);
    try {
      // Convert data URL to blob
      const response = await fetch(capturedImage);
      const blob = await response.blob();
      const file = new File([blob], `camera-${Date.now()}.jpg`, { type: "image/jpeg" });
      
      const form = new FormData();
      form.append("file", file);
      
      const data = await api("/payments/camera-upload", {
        method: "POST",
        body: form,
      });
      
      setShowCameraModal(false);
      setCapturedImage(null);
      setUploading(false);
      
      if (data.needs_review) {
        alert("Đã chụp và trích xuất! Hóa đơn cần xem lại do độ tin cậy thấp.");
      } else {
        alert("Đã chụp và trích xuất hóa đơn thành công!");
      }
      
      // Trigger refresh
      window.dispatchEvent(new CustomEvent("refresh-invoices"));
    } catch (err) {
      setUploading(false);
      alert("Lỗi: " + err.message);
    }
  };

  const handleEmailConnect = async (e) => {
    e.preventDefault();
    setEmailStatus("connecting");
    try {
      const res = await api("/payments/email-connect", {
        method: "POST",
        body: JSON.stringify(emailConfig),
      });
      if (res.ok) {
        setEmailStatus("connected");
        setShowEmailModal(false);
        alert(res.message);
      } else {
        setEmailStatus("disconnected");
        alert("Lỗi: " + res.message);
      }
    } catch (err) {
      setEmailStatus("disconnected");
      alert("Lỗi: " + err.message);
    }
  };

  const handleFileUpload = (e) => {
    const file = e.target.files?.[0];
    if (file && onUpload) {
      onUpload(file);
    }
    e.target.value = "";
  };

  return (
    <>
      {/* Quick Action Buttons */}
      <div className="quick-actions-bar" role="group" aria-label="Các cách nhận giao dịch">
        <label className="quick-action-btn primary" title="Tải hóa đơn lên (file/ảnh)">
          {Icon.upload}
          <span>Upload</span>
          <input
            type="file"
            accept="image/*,.pdf"
            capture="environment"
            hidden
            onChange={handleFileUpload}
            disabled={uploading}
            aria-label="Tải lên hóa đơn mới"
          />
        </label>
        
        <button
          className={`quick-action-btn ${emailStatus === "connected" ? "connected" : ""}`}
          onClick={() => setShowEmailModal(true)}
          title={emailStatus === "connected" ? "Đã kết nối Email - Nhấn để cấu hình lại" : "Kết nối Email (nhận bill tự động)"}
          disabled={uploading}
        >
          {Icon.email}
          <span>{emailStatus === "connected" ? "Email ✓" : "Email"}</span>
          {emailStatus === "connecting" && <span className="spinner-small" />}
        </button>
        
        <button
          className="quick-action-btn"
          onClick={() => setShowCameraModal(true)}
          title="Quét ảnh / AI (chụp hóa đơn hoặc sản phẩm)"
          disabled={uploading}
        >
          {Icon.camera}
          <span>Camera</span>
        </button>
      </div>

      {/* Email Connect Modal */}
      {showEmailModal && (
        <div className="modal-backdrop" onClick={() => setShowEmailModal(false)}>
          <div className="modal modal-email" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{emailStatus === "connected" ? "Cấu hình Email" : "Kết nối Email nhận bill"}</h2>
              <button className="modal-close" onClick={() => setShowEmailModal(false)} aria-label="Đóng">
                {Icon.close}
              </button>
            </div>
            
            <form onSubmit={handleEmailConnect}>
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
                    value={emailConfig.host}
                    onChange={(e) => setEmailConfig({...emailConfig, host: e.target.value})}
                    placeholder="imap.gmail.com"
                    required
                    disabled={emailStatus === "connecting"}
                  />
                </div>
                
                <div className="form-row">
                  <div className="form-group">
                    <label htmlFor="email-port">Port</label>
                    <input
                      id="email-port"
                      type="number"
                      value={emailConfig.port}
                      onChange={(e) => setEmailConfig({...emailConfig, port: parseInt(e.target.value) || 993})}
                      min="1"
                      max="65535"
                      disabled={emailStatus === "connecting"}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="email-folder">Thư mục</label>
                    <input
                      id="email-folder"
                      type="text"
                      value={emailConfig.folder}
                      onChange={(e) => setEmailConfig({...emailConfig, folder: e.target.value})}
                      placeholder="INBOX"
                      disabled={emailStatus === "connecting"}
                    />
                  </div>
                </div>
                
                <div className="form-group">
                  <label htmlFor="email-username">Email <span className="required">*</span></label>
                  <input
                    id="email-username"
                    type="email"
                    value={emailConfig.username}
                    onChange={(e) => setEmailConfig({...emailConfig, username: e.target.value})}
                    placeholder="your@email.com"
                    required
                    disabled={emailStatus === "connecting"}
                  />
                </div>
                
                <div className="form-group">
                  <label htmlFor="email-password">Mật khẩu ứng dụng <span className="required">*</span></label>
                  <input
                    id="email-password"
                    type="password"
                    value={emailConfig.password}
                    onChange={(e) => setEmailConfig({...emailConfig, password: e.target.value})}
                    placeholder="Mật khẩu ứng dụng (không phải mật khẩu đăng nhập)"
                    required
                    disabled={emailStatus === "connecting"}
                    autoComplete="off"
                  />
                  <small className="form-hint">Dùng mật khẩu ứng dụng (App Password), không phải mật khẩu đăng nhập chính</small>
                </div>
                
                {emailStatus === "connected" && (
                  <div className="status-connected">
                    <span className="status-icon">{Icon.check}</span>
                    <span>Đã kết nối thành công</span>
                  </div>
                )}
              </div>
              
              <div className="modal-footer">
                <button type="button" className="btn-ghost" onClick={() => setShowEmailModal(false)}>
                  Hủy
                </button>
                <button type="submit" className="btn-primary" disabled={emailStatus === "connecting"}>
                  {emailStatus === "connecting" ? "Đang kết nối..." : (emailStatus === "connected" ? "Lưu thay đổi" : "Kết nối và kiểm tra")}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Camera Modal */}
      {showCameraModal && (
        <div className="modal-backdrop" onClick={() => { setShowCameraModal(false); setCapturedImage(null); }}>
          <div className="modal modal-camera" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>📷 Quét ảnh / AI</h2>
              <button className="modal-close" onClick={() => { setShowCameraModal(false); setCapturedImage(null); }} aria-label="Đóng">
                {Icon.close}
              </button>
            </div>
            
            <div className="modal-body camera-body">
              {!capturedImage ? (
                <>
                  <div className="camera-preview">
                    <video ref={videoRef} autoPlay playsInline muted />
                    <canvas ref={canvasRef} hidden />
                  </div>
                  <p className="camera-hint">Đặt hóa đơn trong khung hình, nhấn Chụp để quét</p>
                  <div className="camera-controls">
                    <button className="btn-ghost" onClick={() => { setShowCameraModal(false); setCapturedImage(null); }}>
                      Hủy
                    </button>
                    <button className="btn-primary" onClick={handleCapture} disabled={!cameraStream}>
                      Chụp ảnh
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div className="camera-preview captured">
                    <img src={capturedImage} alt="Captured" />
                    <canvas ref={canvasRef} hidden />
                  </div>
                  <p className="camera-hint">Ảnh đã chụp - nhấn Gửi để trích xuất bằng AI</p>
                  <div className="camera-controls">
                    <button className="btn-ghost" onClick={() => setCapturedImage(null)}>
                      Chụp lại
                    </button>
                    <button className="btn-primary" onClick={handleCameraUpload} disabled={uploading}>
                      {uploading ? "Đang xử lý AI..." : "Gửi & Trích xuất"}
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      <style jsx>{`
        .quick-actions-bar {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
          margin-bottom: 16px;
        }
        .quick-action-btn {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 10px 16px;
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: var(--r-md);
          color: var(--text);
          font-size: var(--fs-sm);
          font-weight: 500;
          cursor: pointer;
          transition: all 0.15s;
        }
        .quick-action-btn:hover {
          border-color: var(--accent);
          background: var(--surface-2);
        }
        .quick-action-btn.primary {
          background: var(--accent);
          border-color: var(--accent);
          color: white;
        }
        .quick-action-btn.primary:hover {
          background: var(--accent-hover);
          border-color: var(--accent-hover);
        }
        .quick-action-btn.connected {
          border-color: var(--success);
          background: rgba(34, 197, 94, 0.1);
        }
        .quick-action-btn.connected:hover {
          background: rgba(34, 197, 94, 0.2);
        }
        .quick-action-btn:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }
        .spinner-small {
          width: 14px;
          height: 14px;
          border: 2px solid var(--border);
          border-top-color: var(--accent);
          border-radius: 50%;
          animation: spin .8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        /* Modal */
        .modal-backdrop {
          position: fixed;
          inset: 0;
          background: rgba(15, 17, 23, 0.9);
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 24px;
          z-index: 1000;
          animation: fadeIn 0.2s ease;
        }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        .modal {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: var(--r-lg);
          padding: 0;
          max-width: 480px;
          width: 100%;
          max-height: 90vh;
          overflow: hidden;
          box-shadow: 0 20px 60px rgba(0,0,0,0.5);
          animation: slideUp 0.2s ease;
        }
        @keyframes slideUp {
          from { transform: translateY(20px); opacity: 0; }
          to { transform: translateY(0); opacity: 1; }
        }
        .modal-email { max-width: 520px; }
        .modal-camera { max-width: 90vw; }
        .modal-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 16px 20px;
          border-bottom: 1px solid var(--border);
        }
        .modal-header h2 {
          margin: 0;
          font-size: var(--fs-lg);
        }
        .modal-close {
          background: none;
          border: none;
          color: var(--text-dim);
          cursor: pointer;
          padding: 4px;
          line-height: 1;
          border-radius: var(--r-sm);
        }
        .modal-close:hover {
          background: var(--surface-2);
          color: var(--text);
        }
        .modal-body {
          padding: 20px;
          overflow-y: auto;
          max-height: 60vh;
        }
        .modal-description {
          margin: 0 0 20px;
          color: var(--text-dim);
          font-size: var(--fs-sm);
          line-height: 1.5;
        }
        .form-group {
          margin-bottom: 16px;
        }
        .form-row {
          display: flex;
          gap: 12px;
        }
        .form-row .form-group {
          flex: 1;
        }
        .form-group label {
          display: block;
          margin-bottom: 6px;
          font-size: var(--fs-sm);
          color: var(--text);
          font-weight: 500;
        }
        .required {
          color: var(--danger);
        }
        .form-group input {
          width: 100%;
          padding: 10px 12px;
          border: 1px solid var(--border);
          border-radius: var(--r-sm);
          background: var(--surface-2);
          color: var(--text);
          font-size: var(--fs-md);
        }
        .form-group input:focus {
          outline: none;
          border-color: var(--accent);
        }
        .form-group input:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }
        .form-hint {
          display: block;
          margin-top: 4px;
          font-size: var(--fs-xs);
          color: var(--text-dim);
        }
        .status-connected {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 12px;
          background: rgba(34, 197, 94, 0.15);
          border: 1px solid var(--success);
          border-radius: var(--r-md);
          color: var(--success);
          font-size: var(--fs-sm);
          margin-top: 16px;
        }
        .modal-footer {
          display: flex;
          justify-content: flex-end;
          gap: 12px;
          padding: 16px 20px;
          border-top: 1px solid var(--border);
        }

        /* Camera Modal */
        .camera-body {
          padding: 0;
        }
        .camera-preview {
          width: 100%;
          aspect-ratio: 4/3;
          background: #000;
          overflow: hidden;
        }
        .camera-preview video,
        .camera-preview img {
          width: 100%;
          height: 100%;
          object-fit: cover;
        }
        .camera-preview.captured {
          border-radius: 0;
        }
        .camera-hint {
          padding: 12px 20px;
          margin: 0;
          color: var(--text-dim);
          font-size: var(--fs-sm);
          text-align: center;
          border-bottom: 1px solid var(--border);
        }
        .camera-controls {
          display: flex;
          justify-content: flex-end;
          gap: 12px;
          padding: 16px 20px;
        }

        @media (max-width: 760px) {
          .quick-actions-bar {
            flex-direction: column;
          }
          .quick-action-btn {
            justify-content: center;
          }
          .modal {
            margin: 12px;
            max-height: calc(100vh - 24px);
          }
          .modal-camera {
            margin: 0;
            max-width: 100%;
            max-height: 100vh;
            border-radius: 0;
          }
          .camera-preview {
            aspect-ratio: auto;
            height: 60vh;
          }
        }
      `}</style>
    </>
  );
}
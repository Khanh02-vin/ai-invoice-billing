import { useState, useRef, useEffect } from "react";
import { api } from "../api";

const Icon = {
  camera: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" /><circle cx="12" cy="12" r="4" /></svg>,
  close: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>,
};

// Nút + modal chụp hóa đơn bằng camera; upload qua /payments/camera-upload.
export default function CameraScan() {
  const [showModal, setShowModal] = useState(false);
  const [cameraStream, setCameraStream] = useState(null);
  const [capturedImage, setCapturedImage] = useState(null);
  const [uploading, setUploading] = useState(false);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);

  useEffect(() => {
    if (!showModal) {
      if (cameraStream) {
        cameraStream.getTracks().forEach((track) => track.stop());
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
        setShowModal(false);
      }
    };
    startCamera();

    return () => {
      if (cameraStream) {
        cameraStream.getTracks().forEach((track) => track.stop());
      }
    };
  }, [showModal]);

  const close = () => {
    setShowModal(false);
    setCapturedImage(null);
  };

  const handleCapture = () => {
    if (!videoRef.current || !canvasRef.current) return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0);
    setCapturedImage(canvas.toDataURL("image/jpeg", 0.9));
  };

  const handleUpload = async () => {
    if (!capturedImage) return;
    setUploading(true);
    try {
      const blob = await (await fetch(capturedImage)).blob();
      const file = new File([blob], `camera-${Date.now()}.jpg`, { type: "image/jpeg" });
      const form = new FormData();
      form.append("file", file);

      const data = await api("/payments/camera-upload", { method: "POST", body: form });
      setUploading(false);
      close();
      alert(
        data.needs_review
          ? "Đã chụp và trích xuất! Hóa đơn cần xem lại do độ tin cậy thấp."
          : "Đã chụp và trích xuất hóa đơn thành công!"
      );
      window.dispatchEvent(new CustomEvent("refresh-invoices"));
    } catch (err) {
      setUploading(false);
      alert("Lỗi: " + err.message);
    }
  };

  return (
    <>
      <button className="btn-ghost" onClick={() => setShowModal(true)} disabled={uploading} title="Chụp hóa đơn bằng camera">
        {Icon.camera} Camera
      </button>

      {showModal && (
        <div className="modal-backdrop" onClick={close}>
          <div className="modal modal-camera" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>📷 Quét hóa đơn</h2>
              <button className="modal-close" onClick={close} aria-label="Đóng">
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
                    <button className="btn-ghost" onClick={close}>Hủy</button>
                    <button className="btn-primary" onClick={handleCapture} disabled={!cameraStream}>
                      Chụp ảnh
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div className="camera-preview captured">
                    <img src={capturedImage} alt="Ảnh hóa đơn đã chụp" />
                    <canvas ref={canvasRef} hidden />
                  </div>
                  <p className="camera-hint">Ảnh đã chụp - nhấn Gửi để trích xuất bằng AI</p>
                  <div className="camera-controls">
                    <button className="btn-ghost" onClick={() => setCapturedImage(null)}>Chụp lại</button>
                    <button className="btn-primary" onClick={handleUpload} disabled={uploading}>
                      {uploading ? "Đang xử lý AI..." : "Gửi & Trích xuất"}
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

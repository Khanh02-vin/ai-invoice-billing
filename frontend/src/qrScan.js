// Đọc QR từ camera hoặc từ ảnh chụp.
//
// Ưu tiên BarcodeDetector (Chrome/Android có sẵn). Máy không có — điển hình là
// Safari trên iPhone — thì nạp jsQR theo nhu cầu: gói riêng ~47 KB nén, chỉ tải
// lần đầu bấm quét, nên máy Android không phải trả giá.
//
// BarcodeDetector có mặt nhưng hỏng (webview, Chrome bật cờ) là chuyện thật: khi
// đó detect() ném lỗi ở MỌI khung hình. Không chặn lại thì camera quay mãi mà
// không bao giờ báo gì — đúng kiểu "quét không được". Nên lỗi detector là lý do
// để chuyển hẳn sang jsQR, không phải lý do bỏ khung hình.

let jsqrPromise = null;

function loadJsQr() {
  if (!jsqrPromise) jsqrPromise = import("jsqr").then((mod) => mod.default || mod);
  return jsqrPromise;
}

// Một khi detector đã ném lỗi thì bỏ hẳn, khỏi thử lại 2,5 lần mỗi giây.
let detectorBroken = false;

export function isDetectorBroken() {
  return detectorBroken;
}

export function hasNativeDetector() {
  return typeof window !== "undefined" && "BarcodeDetector" in window && !detectorBroken;
}

export function makeDetector() {
  try {
    return new window.BarcodeDetector({ formats: ["qr_code"] });
  } catch {
    detectorBroken = true;
    return null;
  }
}

// Canvas dùng lại cho mọi lần đọc: jsQR cần ImageData, BarcodeDetector nhận thẳng canvas.
const canvas = document.createElement("canvas");
const ctx = canvas.getContext("2d", { willReadFrequently: true });

// Thu nhỏ khung hình trước khi giải mã: jsQR chạy trên CPU. 640px thì nhanh nhưng
// mã QR in trên bill (chỉ ~100px trong khung 1080p) mất hết ô trắng nên không đọc
// được; đo trên ảnh thật: 1280px đọc được mã từ 96px, 640px thì phải 200px mới đọc
// nổi. 1280 vẫn đủ nhanh (~35 ms/khung trên máy để bàn).
const MAX_DIM = 1280;

export function sourceSizeFor(width, height) {
  const longest = Math.max(width || 0, height || 0);
  if (!longest) return { width: MAX_DIM, height: MAX_DIM };
  const scale = Math.min(1, MAX_DIM / longest);
  return {
    width: Math.round(width * scale),
    height: Math.round(height * scale),
  };
}

function draw(source, width, height) {
  const size = sourceSizeFor(width, height);
  canvas.width = size.width;
  canvas.height = size.height;
  // Mặc định canvas nội suy "low": ảnh 12 MP co xuống 1280 bị răng cưa, bộ nhị
  // phân hoá của jsQR không thấy ô nào. "high" giữ được mép ô nên mã hiện ra.
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
}

// Tách riêng để test được mà không cần trình duyệt thật.
export async function decodeCanvasWith(detector, load) {
  if (detector && !detectorBroken) {
    try {
      const codes = await detector.detect(canvas);
      return codes.length ? codes[0].rawValue : null;
    } catch {
      // Máy này không dùng được detector — chuyển hẳn sang jsQR cho các lần sau.
      detectorBroken = true;
    }
  }
  const jsQR = await load();
  const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
  return jsQR(image.data, image.width, image.height, { inversionAttempts: "attemptBoth" })?.data || null;
}

export function decodeCanvas(detector) {
  return decodeCanvasWith(detector, loadJsQr);
}

// Một khung hình video hiện tại. Trả null khi chưa có hình hoặc không thấy mã.
export async function scanVideoFrame(video, detector) {
  if (!video.videoWidth || !video.videoHeight) return null;
  draw(video, video.videoWidth, video.videoHeight);
  return decodeCanvas(detector);
}

export async function scanImageFile(file, detector) {
  const bitmap = await createImageBitmap(file);
  try {
    draw(bitmap, bitmap.width, bitmap.height);
    return await decodeCanvas(detector);
  } finally {
    bitmap.close?.();
  }
}

// Đường giải mã QR: lỗi trình duyệt không được làm chết việc đọc.
//
// Chạy: node --test tests/qrscan.decode.test.mjs
// Cần `document` tối thiểu (canvas giả) — bài test tự tạo, không dùng jsdom.
import assert from "node:assert/strict";
import test from "node:test";

// Canvas giả: jsQR chỉ cần getImageData trả đúng số pixel theo width*height*4.
const canvases = [];
globalThis.document = {
  createElement() {
    const canvas = {
      width: 0,
      height: 0,
      // Trình duyệt thật trả về CÙNG một context cho mỗi canvas — module dựa vào đó.
      getContext() {
        canvas._ctx ??= {
          canvas,
          _pixels: null,
          imageSmoothingQuality: "low",
          _qualityWhenDrawn: null,
          drawImage() {
            const px = Math.max(1, (canvas.width | 0) * (canvas.height | 0) * 4);
            this._pixels = new Uint8ClampedArray(px);
            this._qualityWhenDrawn = this.imageSmoothingQuality;
          },
          getImageData(x, y, w, h) {
            return { data: this._pixels, width: w, height: h };
          },
        };
        return canvas._ctx;
      },
    };
    canvases.push(canvas);
    return canvas;
  },
};
globalThis.window = {};
globalThis.createImageBitmap = async () => {
  const shot = { width: 3024, height: 4032, close() {} };
  return shot;
};

const url = new URL("../src/qrScan.js", import.meta.url);
let fresh = 0;
// Mỗi bài một bản module mới: trạng thái "detector hỏng" là biến module.
const load = () => import(`${url.href}?case=${(fresh += 1)}`);

test("detector ném lỗi thì quay về jsQR, không ném ra ngoài", async () => {
  const mod = await load();
  const detector = { detect: async () => { throw new Error("detector unsupported"); } };
  const text = await mod.decodeCanvasWith(detector, () => (d, w, h) => ({ data: "CO_" + w + "x" + h }));
  assert.equal(text, "CO_0x0");
  assert.equal(mod.isDetectorBroken(), true, "lần sau bỏ qua detector");
});

test("detector chết thì lần sau không gọi lại nữa", async () => {
  const mod = await load();
  let calls = 0;
  const detector = { detect: async () => { calls += 1; throw new Error("x"); } };
  await mod.decodeCanvasWith(detector, () => () => ({ data: "fallback" }));
  const again = await mod.decodeCanvasWith(detector, () => () => ({ data: "fallback" }));
  assert.equal(calls, 1);
  assert.equal(again, "fallback");
});

test("detector sống thì dùng detector, không cần jsQR", async () => {
  const mod = await load();
  const detector = { detect: async () => [{ rawValue: "NATIVE" }] };
  let loaded = false;
  const text = await mod.decodeCanvasWith(detector, () => { loaded = true; return () => null; });
  assert.equal(text, "NATIVE");
  assert.equal(loaded, false);
});

test("thu nhỏ ảnh phải nội suy chất lượng cao, không dùng mặc định low", async () => {
  const mod = await load();
  // Ảnh chụp 12 MP (3024x4032): canvas vẽ ở 960x1280 với nội suy mặc định "low"
  // bị răng cưa, jsQR không đọc nổi — đo trên ảnh thật: low → null, high → có mã.
  const shot = { width: 3024, height: 4032, close() {} };
  globalThis.createImageBitmap = async () => shot;
  await mod.scanImageFile({}, null);
  const canvas = canvases.at(-1);
  const ctx = canvas.getContext("2d");
  assert.deepEqual([canvas.width, canvas.height], [960, 1280]);
  assert.equal(ctx._qualityWhenDrawn, "high", "phải đặt chất lượng cao trước khi vẽ");
});

test("khung 1080p thu về 1280, không teo còn 640", async () => {
  const mod = await load();
  // Mã in trên bill chỉ ~100px trong khung 1080p. Thu về 640 thì mất ô trắng,
  // jsQR không đọc được; 1280 thì còn đọc được mã từ ~96px (đo trên ảnh thật).
  assert.deepEqual(mod.sourceSizeFor(1920, 1080), { width: 1280, height: 720 });
  assert.deepEqual(mod.sourceSizeFor(1280, 720), { width: 1280, height: 720 });
  // Ảnh nhỏ thì giữ nguyên, không phóng to.
  assert.deepEqual(mod.sourceSizeFor(800, 600), { width: 800, height: 600 });
});

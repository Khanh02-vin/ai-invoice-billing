// Mã QR dựng trong app phải mang đúng số tiền — người nhận quét là chuyển đúng,
// không phải tự nhập lại.
import assert from "node:assert/strict";
import test from "node:test";

import { transferQrUrl } from "../src/vietqr.js";

function paramsOf(url) {
  return new URL(url).searchParams;
}

test("ảnh VietQR có BIN, số tài khoản và số tiền", () => {
  const url = transferQrUrl({
    bankBin: "970426",
    account: "1031262998",
    amount: 120000,
    content: "Thanh toan",
  });
  assert.match(url, /^https:\/\/img\.vietqr\.io\/image\/970426-1031262998-[a-z0-9]+\.png\?/);
  assert.equal(paramsOf(url).get("amount"), "120000");
  assert.equal(paramsOf(url).get("addInfo"), "Thanh toan");
});

test("nội dung có dấu cách và dấu tiếng Việt phải được mã hoá", () => {
  const url = transferQrUrl({
    bankBin: "970426",
    account: "SEPAAAAAAAAAA99889",
    amount: 55000,
    content: "Cà phê sáng",
  });
  assert.equal(paramsOf(url).get("addInfo"), "Cà phê sáng");
  assert.ok(!url.includes(" "), "URL không được chứa khoảng trắng");
});

test("không có nội dung thì bỏ hẳn addInfo, không gửi chuỗi rỗng", () => {
  const url = transferQrUrl({ bankBin: "970426", account: "123456", amount: 30000, content: "" });
  assert.equal(paramsOf(url).has("addInfo"), false);
  assert.equal(paramsOf(url).get("amount"), "30000");
});

// App ngân hàng nhận mã app VietQR phải khớp ngân hàng nhận (BIN → code).
// Chọn sai app thì deep link mở app khác, người dùng phải tự nhập lại từ đầu.
import assert from "node:assert/strict";
import test from "node:test";

import { pickAppForBank, shouldPrefill } from "../src/vietqr.js";

const APPS = [
  { id: "icb", name: "VietinBank iPay", install: 200000, autofill: true },
  { id: "mb", name: "MB Bank", install: 500000, autofill: true },
  { id: "vcb", name: "VCB Digibank", install: 400000, autofill: false },
  { id: "timo", name: "Timo", install: 10, autofill: false },
];

test("ưu tiên app của đúng ngân hàng nhận", () => {
  assert.equal(pickAppForBank(APPS, "mb", null), "mb");
});

test("không có app ngân hàng đó thì không chọn bừa app khác", () => {
  assert.equal(pickAppForBank(APPS, "vpb", null), "");
});

test("app ngân hàng đó không có autofill vẫn được chọn, không nhảy sang app khác", () => {
  assert.equal(pickAppForBank(APPS, "vcb", null), "vcb");
});

test("chọn app đã lưu lần trước nếu nó thuộc đúng ngân hàng", () => {
  assert.equal(pickAppForBank(APPS, "mb", "mb"), "mb");
});

test("app đã lưu của ngân hàng khác thì bỏ, không dùng", () => {
  assert.equal(pickAppForBank(APPS, "mb", "vcb"), "mb");
});

test("chưa có app nào thì trả rỗng, không đoán", () => {
  assert.equal(pickAppForBank([], "mb", null), "");
});

test("autofill chỉ đúng với app VietQR khai báo", () => {
  assert.equal(shouldPrefill(APPS, "mb"), true);
  assert.equal(shouldPrefill(APPS, "vcb"), false);
  assert.equal(shouldPrefill(APPS, "icb"), true);
});
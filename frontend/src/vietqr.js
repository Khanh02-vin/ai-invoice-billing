// Thanh toán bằng chuyển khoản. Trang này KHÔNG chuyển tiền: nó hiện mã VietQR
// có sẵn số tiền để người nhận quét, hoặc mở app ngân hàng với số tiền điền sẵn
// (BankDeeplink chính thức của VietQR) để người dùng tự bấm xác nhận trong app.

const BANKS_URL = "https://api.vietqr.io/v2/banks";
const APPS_URL = "https://api.vietqr.io/v2/android-app-deeplinks";
const IOS_APPS_URL = "https://api.vietqr.io/v2/ios-app-deeplinks";
const TIMEOUT_MS = 8000;

let banksPromise = null;
let appsPromise = null;

async function getJson(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
  if (!response.ok) throw new Error(`VietQR trả lỗi ${response.status}`);
  return response.json();
}

// BIN ("970422") → mã app ("mb"). Deeplink nhận mã app ngân hàng, không nhận BIN.
export function loadBanks() {
  banksPromise ??= getJson(BANKS_URL)
    .then((body) => new Map(
      (body.data || []).map((bank) => [
        String(bank.bin),
        { code: String(bank.code || "").toLowerCase(), name: bank.shortName || bank.name || "" },
      ]),
    ))
    .catch((err) => {
      banksPromise = null;
      throw err;
    });
  return banksPromise;
}

export function loadApps() {
  const url = /Android/i.test(navigator.userAgent) ? APPS_URL : IOS_APPS_URL;
  appsPromise ??= getJson(url)
    .then((body) => (body.apps || [])
      .map((app) => ({
        id: app.appId,
        name: app.appName || app.appId,
        install: Number(app.monthlyInstall) || 0,
        autofill: Boolean(app.autofill),
      }))
      .sort((a, b) => b.install - a.install))
    .catch((err) => {
      appsPromise = null;
      throw err;
    });
  return appsPromise;
}

// App phải là app của đúng ngân hàng nhận: bankCode là mã app VietQR ("mb"),
// trùng với bank.code lấy từ danh sách BIN. Không có app của ngân hàng đó thì
// trả "" để người dùng tự chuyển theo số tài khoản, không mở nhầm app khác.
export function pickAppForBank(apps, bankCode, saved) {
  const mine = (apps || []).filter((app) => app.id === bankCode);
  if (!mine.length) return "";
  return mine.find((app) => app.id === saved)?.id || mine[0].id;
}

// VietQR chỉ điền sẵn số tiền cho app có autofill; app khác chỉ được mở ra.
export function shouldPrefill(apps, appId) {
  return Boolean((apps || []).find((app) => app.id === appId)?.autofill);
}

// Ảnh VietQR dựng sẵn số tiền + nội dung. Máy nào quét mã này thì app ngân hàng
// của máy đó điền sẵn đúng số tiền — dùng để trả hộ / quét bằng máy thứ hai.
export function transferQrUrl({ bankBin, account, amount, content }) {
  const params = new URLSearchParams({ amount: String(amount) });
  if (content) params.set("addInfo", content);
  return `https://img.vietqr.io/image/${bankBin}-${account}-compact2.png?${params}`;
}

// Tất cả tham số trừ `holder` đều bắt buộc theo hợp đồng của VietQR.
export function transferUrl({ appId, amount, content, account, bankCode, returnUrl }) {
  const params = new URLSearchParams({
    app: appId,
    am: String(amount),
    tn: content,
    ba: `${account}@${bankCode}`,
    url: returnUrl,
  });
  return `https://dl.vietqr.io/pay?${params}`;
}

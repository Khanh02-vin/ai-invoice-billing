# Đưa Quanh Ta lên mobile (PWA + APK/IPA)

Trạng thái: **PWA hoàn chỉnh** (cài từ trình duyệt) + **dự án Capacitor đã sinh**
(`frontend/android`, `frontend/ios`) và **APK debug đã build thành công** (4.1 MB).

## 1. PWA (mọi máy, không cần store)

- File: `frontend/public/manifest.webmanifest`, `sw.js`, `icons/*`
- SW chỉ cache shell tĩnh; **API luôn network-only** (không cache JWT/dữ liệu chi tiêu).
- Chỉ cài được khi truy cập qua **HTTPS** hoặc **localhost** (giới hạn trình duyệt).

## 2. APK Android — build

Đã cài sẵn toolchain cục bộ (không cần root):

```
~/androidsdk/jdk-21.0.5+11      (JDK 21 — Capacitor 7 đòi Java 21)
~/androidsdk/platforms/android-34, build-tools/34.0.0, platform-tools
```

Build (script tự đặt env, tự build web, tự sync, tự gradle):

```bash
export VITE_API_BASE=https://quanhta.example.com   # backend HTTPS thật
./scripts/build-apk.sh
# → frontend/android/app/build/outputs/apk/debug/app-debug.apk
```

Cài thử: copy APK vào điện thoại → bật *Cho phép cài từ nguồn không rõ* → cài.
APK debug này **chưa gọi được backend** nếu build không đặt `VITE_API_BASE`
(API để đường tương đối, WebView `https://localhost` không có backend).

## 3. APK bản phát hành (ký bằng keystore)

```bash
keytool -genkeypair -v -keystore quanh-ta.keystore \
  -alias quanh-ta -keyalg RSA -keysize 2048 -validity 10000
# thêm signingConfig vào android/app/build.gradle rồi:
cd frontend/android && ./gradlew assembleRelease
# → app/build/outputs/apk/release/app-release.apk
```

## 4. IPA iOS — bắt buộc macOS + Xcode

Linux **không build được IPA**. Đường đi:

1. `npx cap open ios` trên Mac (hoặc mở `frontend/ios/App/App.xcworkspace` bằng Xcode).
2. Chọn team, bật **Automatically manage signing** (cần Apple Developer — TestFlight/AdHoc $99/năm; chỉ chạy simulator thì miễn phí).
3. `Product → Archive` → Distribute (App Store / AdHoc / Development).
4. Hoặc CI: GitHub Actions runner `macos-latest` (vẫn cần tài khoản Apple để ký).

## 5. Lưu ý kỹ thuật

- **Backend phải HTTPS**: WebView chặn gọi HTTP từ trang `https://localhost`. Đặt `VITE_API_BASE` trước khi build.
- **Deeplink ngân hàng** (`dl.vietqr.io`, `img.vietqr.io`, `api.vietqr.io`, `nominatim.openstreetmap.org`) đã thêm vào `server.allowNavigation` trong `capacitor.config.ts`.
- **Quyền** đã khai trong `AndroidManifest.xml`: CAMERA, ACCESS_FINE/COARSE_LOCATION.
- **Live reload khi dev trên điện thoại**: `npx cap run android --livereload --external` (máy và điện thoại cùng LAN).
- Thêm platform: `npx cap add android|ios`; đồng bộ sau khi sửa web: `npx cap sync`.

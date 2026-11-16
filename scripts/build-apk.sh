#!/usr/bin/env bash
# Build APK từ app web hiện tại.
#
# Backend phải là HTTPS thật (WebView không cho gọi HTTP từ trang https).
# Trước khi build, đặt địa chỉ backend public:
#
#   export VITE_API_BASE=https://quanhta.example.com
#   ./scripts/build-apk.sh
#
# APK ra tại: frontend/android/app/build/outputs/apk/debug/app-debug.apk
# (debug — tự ký bằng debug keystore, cài thử được ngay).
set -euo pipefail
cd "$(dirname "$0")/.."
export JAVA_HOME="${JAVA_HOME:-/home/khanh/androidsdk/jdk-21.0.5+11}"
export ANDROID_HOME="${ANDROID_HOME:-/home/khanh/androidsdk}"
export PATH="$JAVA_HOME/bin:$PATH"

API_BASE="${VITE_API_BASE:-}"
echo "==> Build web (VITE_API_BASE='${API_BASE}')"
if [ -n "$API_BASE" ]; then
  VITE_API_BASE="$API_BASE" npm --prefix frontend run build
else
  echo "    (để trống → gọi API tương đối; APK sẽ KHÔNG gọi được backend thật)"
  npm --prefix frontend run build
fi

echo "==> Đồng bộ web vào project Android"
npx --prefix frontend cap sync android

echo "==> Gradle assembleDebug"
cd frontend/android
./gradlew assembleDebug --no-daemon

echo
echo "APK: frontend/android/app/build/outputs/apk/debug/app-debug.apk"
ls -lh app/build/outputs/apk/debug/app-debug.apk

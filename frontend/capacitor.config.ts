import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.quanh.ta",
  appName: "Quanh Ta",
  webDir: "dist",
  android: {
    allowMixedContent: false,
  },
  server: {
    // Deeplink mở app ngân hàng/ví (dl.vietqr.io, img.vietqr.io, api.vietqr.io,
    // nominatim.openstreetmap.org) phải được phép ra ngoài WebView.
    allowNavigation: [
      "dl.vietqr.io",
      "img.vietqr.io",
      "api.vietqr.io",
      "nominatim.openstreetmap.org",
    ],
  },
};

export default config;

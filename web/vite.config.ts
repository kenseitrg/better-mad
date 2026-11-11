/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server (pwb pattern): /api is proxied to the hub so the browser talks
// to one origin during development — hub REST/SSE and the /api/chat/* proxy
// alike. The worker's live-document port is hit cross-origin directly by the
// embed (autoload.js sends CORS *, handoff §5) — only the WebSocket needs the
// hub's --dev-origin allowlist (trap T1). Prod: `npm run build` → the hub
// serves dist/ itself.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.BETTER_MAD_HUB ?? "http://127.0.0.1:5006",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The frontend talks to the workflow server on :8000 via the /api prefix.
// In dev we proxy so the browser can hit same-origin /api and avoid CORS.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        // SSE needs the connection kept open; the proxy handles this by default.
      },
    },
  },
});

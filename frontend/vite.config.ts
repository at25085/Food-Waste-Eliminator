import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI backend. Override with API_TARGET=http://host:port npm run dev
const target = process.env.API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 900 },
  server: {
    port: 5173,
    proxy: { "/api": { target, changeOrigin: true } },
  },
  preview: {
    proxy: { "/api": { target, changeOrigin: true } },
  },
});

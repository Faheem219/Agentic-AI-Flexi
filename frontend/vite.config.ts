import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API is proxied to the local FastAPI server.
// In production (Vercel) vercel.json rewrites /api/* to the backend VM.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
});

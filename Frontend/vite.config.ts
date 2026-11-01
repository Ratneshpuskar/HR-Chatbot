import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // In development, forward API calls to the FastAPI backend so the browser stays same-origin.
    proxy: {
      "/api": "http://localhost:8000",
      "/readyz": "http://localhost:8000",
    },
  },
  build: {
    sourcemap: false,
  },
});

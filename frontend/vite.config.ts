import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Deployed to GitHub Pages project site: https://hyyyyyyz.github.io/Xuanzang/
// so production assets must live under the "/Xuanzang/" base path. In dev we
// serve from "/" and proxy the API to the local (Mac) backend.
export default defineConfig(({ mode }) => ({
  base: mode === "production" ? "/Xuanzang/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8848", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
}));

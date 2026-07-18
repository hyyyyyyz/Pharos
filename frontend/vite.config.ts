import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Deployed to GitHub Pages project site: https://hyyyyyyz.github.io/Pharos/
// so production assets must live under the "/Pharos/" base path. In dev we
// serve from "/" and proxy the API to the local (Mac) backend.
export default defineConfig(({ mode }) => ({
  base: mode === "production" ? "/Pharos/" : "/",
  plugins: [react()],
  // Ensure a single React instance (avoids "Invalid hook call" from libraries
  // like @tanstack/react-query when Vite pre-bundles them separately).
  resolve: { dedupe: ["react", "react-dom"] },
  optimizeDeps: { include: ["react", "react-dom", "@tanstack/react-query", "zustand"] },
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

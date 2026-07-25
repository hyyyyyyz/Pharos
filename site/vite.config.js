import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  build: {
    target: "es2022",
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 650,
    rollupOptions: {
      output: {
        assetFileNames: (assetInfo) => {
          const sourceName = assetInfo.names?.[0] ?? assetInfo.name ?? "";
          return /\.(?:png|webp)$/i.test(sourceName)
            ? "assets/[name][extname]"
            : "assets/[name]-[hash][extname]";
        },
      },
    },
  },
});

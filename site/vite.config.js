import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

const siteRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  base: "./",
  build: {
    target: "es2022",
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 650,
    rollupOptions: {
      input: {
        main: `${siteRoot}index.html`,
        download: `${siteRoot}download.html`,
      },
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

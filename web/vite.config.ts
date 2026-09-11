/// <reference types="vitest/config" />

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig(() => ({
  base: "/",
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "prompt",
      injectRegister: null,
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      devOptions: { enabled: false },
      includeManifestIcons: false,
      manifest: {
        id: "/",
        name: "Second Brain",
        short_name: "Second Brain",
        description:
          "Личная система знаний для захвата, проверки и переиспользования собственных материалов.",
        lang: "ru",
        dir: "ltr",
        start_url: "/",
        scope: "/",
        display: "standalone",
        orientation: "any",
        background_color: "#050407",
        theme_color: "#050407",
        categories: ["productivity"],
        prefer_related_applications: false,
        icons: [
          {
            src: "/icons/pwa-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icons/pwa-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icons/pwa-512-maskable.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
        shortcuts: [
          {
            name: "Новая заметка",
            short_name: "Новая заметка",
            description: "Открыть добавление материала",
            url: "/#capture",
            icons: [{ src: "/icons/shortcut-add.png", sizes: "96x96", type: "image/png" }],
          },
          {
            name: "Поиск",
            short_name: "Поиск",
            description: "Найти заметку в личной системе знаний",
            url: "/#search",
            icons: [{ src: "/icons/shortcut-search.png", sizes: "96x96", type: "image/png" }],
          },
        ],
      },
      injectManifest: {
        globPatterns: [
          "assets/**/*.{js,css,webp,woff2}",
          "icons/**/*.{png,webp,ico}",
          "manifest.webmanifest",
          "offline.html",
          "offline.css",
        ],
      },
    }),
  ],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8123",
      "/healthz": "http://127.0.0.1:8123",
    },
  },
  build: {
    // Keep lazy raster art out of the initial JavaScript payload.
    assetsInlineLimit: 0,
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
}));

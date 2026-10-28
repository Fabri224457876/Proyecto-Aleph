import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Consola simple de Aleph. La API se toma de ALEPH_API_TARGET (por defecto, la instancia de desarrollo 8200).
// No usar los puertos de la demo (8100 API, 8101 consola): ver README.
const apiTarget = process.env.ALEPH_API_TARGET ?? "http://127.0.0.1:8200";

const proxy = {
  "/api": { target: apiTarget, changeOrigin: false },
  "/health": { target: apiTarget, changeOrigin: false },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5180,
    strictPort: true,
    proxy,
  },
  preview: {
    host: "127.0.0.1",
    port: 5181,
    strictPort: true,
    proxy,
  },
  build: {
    target: "es2022",
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
});

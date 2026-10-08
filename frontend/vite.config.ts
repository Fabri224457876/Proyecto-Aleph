import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// La API de Aleph escucha en 127.0.0.1:8100. El proxy de /api evita CORS en desarrollo.
// Para apuntar a otra instancia: ALEPH_API_TARGET=http://host:puerto npm run dev
const apiTarget = process.env.ALEPH_API_TARGET ?? "http://127.0.0.1:8100";

const proxy = {
  "/api": {
    target: apiTarget,
    changeOrigin: false,
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy,
  },
  preview: {
    host: "127.0.0.1",
    port: 8101,
    strictPort: true,
    proxy,
  },
  build: {
    target: "es2022",
    sourcemap: false,
    // Cytoscape pesa; el grafo se carga en un chunk aparte (ver GraphTab, import diferido).
    chunkSizeWarningLimit: 1200,
  },
});

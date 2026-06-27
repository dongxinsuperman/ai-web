import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 开发态把 /api 与 /files 代理到后端，避免跨域。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 8010,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8009", changeOrigin: true },
      "/files": { target: "http://127.0.0.1:8009", changeOrigin: true },
    },
  },
});

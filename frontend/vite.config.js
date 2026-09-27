import { defineConfig } from "vite"
import vue from "@vitejs/plugin-vue"

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      // dev 下 rank 贴图等运行时资源也由后端提供（/assets 优先项目根 assets/）
      "/assets": "http://127.0.0.1:8000",
    },
  },
})
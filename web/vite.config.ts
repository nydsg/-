import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建产物直接落到 Flask 的 static/spa，由后端统一托管
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../static/spa',
    emptyOutDir: true,
    // 配合代码分割，把大依赖拆成可长期缓存的 vendor chunk
    chunkSizeWarningLimit: 400,
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'query-vendor': ['@tanstack/react-query'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/uploads': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/static/uploads': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})

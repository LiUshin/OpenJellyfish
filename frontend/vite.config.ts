import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            if (req.url?.includes('/chat')) {
              proxyReq.setHeader('Accept', 'text/event-stream');
            }
          });
          proxy.on('proxyRes', (proxyRes, req) => {
            if (req.url?.includes('/chat')) {
              proxyRes.headers['cache-control'] = 'no-cache';
              proxyRes.headers['x-accel-buffering'] = 'no';
            }
          });
        },
      },
      '/s/': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/wc': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      input: {
        // Admin SPA — 主聊天 / 设置 / 服务管理 etc.
        main: 'index.html',
        // Service-chat — 消费者打开的独立聊天页（被 FastAPI /s/{id} 引用）
        // 复用 admin 的 markdown.ts / StreamingMessage 组件，避免双份维护。
        'service-chat': 'service-chat.html',
      },
      output: {
        manualChunks: {
          'antd-vendor': ['antd', '@ant-design/icons'],
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'markdown': ['marked', 'dompurify', 'highlight.js'],
        },
      },
    },
  },
});

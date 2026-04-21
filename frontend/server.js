import express from 'express';
import { createProxyMiddleware } from 'http-proxy-middleware';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const app = express();
const PORT = parseInt(process.env.FRONTEND_PORT || '3000', 10);
const API_TARGET = process.env.API_TARGET || 'http://localhost:8000';

app.use(createProxyMiddleware({
  target: API_TARGET,
  changeOrigin: true,
  ws: true,
  pathFilter: '/api',
  onProxyReq: (proxyReq, req, res) => {
    if (req.url.includes('/chat')) {
      proxyReq.setHeader('Accept', 'text/event-stream');
    }
  },
  onProxyRes: (proxyRes, req, res) => {
    if (req.url.includes('/chat')) {
      proxyRes.headers['cache-control'] = 'no-cache';
      proxyRes.headers['x-accel-buffering'] = 'no';
    }
  },
}));

app.use(createProxyMiddleware({
  target: API_TARGET,
  changeOrigin: true,
  pathFilter: '/s/',
}));

app.use(createProxyMiddleware({
  target: API_TARGET,
  changeOrigin: true,
  pathFilter: '/wc/',
}));

app.use(express.static(join(__dirname, 'dist')));

// SPA fallback：所有未命中的 GET 请求都返回 index.html 让 React Router 接管。
// 用 app.use 兜底而不是 app.get('/{*path}', ...) —— 后者在 Express 5 + path-to-regexp v8
// 下对 /a/b/c 这类深层路径匹配不稳定，会触发 "Cannot GET /xxx/yyy"。
app.use((req, res, next) => {
  if (req.method !== 'GET' && req.method !== 'HEAD') return next();
  // 不接管显式请求 API/JSON/资源类（理论上都被前面的 proxy/static 处理过；
  // 这里再保险一道，避免静态资源 404 时被吞为 HTML）
  if (
    req.path.startsWith('/api') ||
    req.path.startsWith('/s/') ||
    req.path.startsWith('/wc/') ||
    req.path.startsWith('/assets/')
  ) {
    return next();
  }
  res.sendFile(join(__dirname, 'dist', 'index.html'));
});

const server = app.listen(PORT, '0.0.0.0', () => {
  console.log(`Frontend server running at http://localhost:${PORT}`);
  console.log(`API proxy -> ${API_TARGET}`);
  console.log(`WebSocket proxy enabled for /api/voice/realtime`);
});

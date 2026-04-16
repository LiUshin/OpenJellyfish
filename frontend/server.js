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
  pathFilter: '/wc',
}));

app.use(express.static(join(__dirname, 'dist')));

app.get('/{*path}', (req, res) => {
  res.sendFile(join(__dirname, 'dist', 'index.html'));
});

const server = app.listen(PORT, '0.0.0.0', () => {
  console.log(`Frontend server running at http://localhost:${PORT}`);
  console.log(`API proxy -> ${API_TARGET}`);
  console.log(`WebSocket proxy enabled for /api/voice/realtime`);
});

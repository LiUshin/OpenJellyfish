/**
 * service-chat React entry point.
 *
 * 由 vite multi-entry 构建，输出 `dist/service-chat.html`。
 * 后端 consumer_ui.py 在返回该 HTML 前注入 `<script>window.__SVC__={...}</script>`，
 * 把 service_id / welcome_message / quick_questions 等运行时配置传给前端。
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import ServiceChatApp, { type ServiceConfig } from './ServiceChatApp';
import '../styles/global.css';
import 'highlight.js/styles/github-dark.css';

declare global {
  interface Window {
    __SVC__?: ServiceConfig;
  }
}

function readConfig(): ServiceConfig {
  const injected = window.__SVC__;
  if (injected && injected.service_id) {
    return injected;
  }
  // dev 兜底：如果没注入（例如直接打开 vite dev 的 service-chat.html），
  // 从 URL ?service_id= 读，方便本地开发。
  const params = new URLSearchParams(window.location.search);
  const id = params.get('service_id') || '';
  return {
    service_id: id,
    service_name: id ? `Service ${id}` : 'Service Chat (dev)',
    service_desc: '',
    welcome_message: '',
    quick_questions: [],
  };
}

const config = readConfig();
document.title = config.service_name || 'Chat';

const root = document.getElementById('service-root');
if (!root) {
  throw new Error('service-chat: #service-root not found in DOM');
}

createRoot(root).render(
  <StrictMode>
    <ServiceChatApp config={config} />
  </StrictMode>,
);

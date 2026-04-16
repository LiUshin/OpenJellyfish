# ============================================================
# JellyfishBot Docker Image  (multi-stage)
# Stage 1 — Node.js: build React (Vite + TypeScript)
# Stage 2 — Python 3.11 + Node.js 20: production runtime
# ============================================================

# -------------------- Stage 1: Frontend Build --------------------
FROM node:20-alpine AS frontend-builder

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# -------------------- Stage 2: Production --------------------
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ffmpeg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- Python 依赖 ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- 复制应用代码（.dockerignore 排除 node_modules / dist 等）----
COPY . .

# ---- 前端生产环境设置 ----
# 删除 React 源码和开发文件，只保留 server.js + public/（FastAPI 模板依赖）
# 安装 Express 运行时依赖，然后复制构建产物
RUN cd frontend \
    && rm -rf node_modules src index.html \
              *.ts *.tsx tsconfig*.json vite.config.ts \
              package.json package-lock.json \
    && npm init -y \
    && npm install express@4 http-proxy-middleware

COPY --from=frontend-builder /build/dist /app/frontend/dist

RUN mkdir -p /app/data /app/users \
    && chmod +x /app/start.sh

EXPOSE 3000 8000

CMD ["/app/start.sh"]

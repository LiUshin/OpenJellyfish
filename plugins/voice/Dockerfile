# OpenJellyfish 语音前台 Worker —— 独立部署单元
FROM python:3.11-slim

WORKDIR /app

# 系统依赖:silero VAD / 音频处理可能需要
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY jellyfish_voice ./jellyfish_voice

RUN pip install --no-cache-dir .

# 预下载 turn-detector / silero 模型(可选,加速首通)。失败不致命。
RUN python -m jellyfish_voice.agent download-files || true

# 生产模式:注册 worker,等待 SFU 派发 job
CMD ["python", "-m", "jellyfish_voice.agent", "start"]

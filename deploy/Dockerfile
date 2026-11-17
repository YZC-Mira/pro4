# ============ AlgoAgent 单应用镜像（任务 10 / 选型第 6 节） ============
# 多阶段构建：前端 dist + FastAPI + Playwright Chromium 同镜像；
# 容器内不装 Nginx，单进程单端口（uvicorn:8000），docker run -p 80:8000 即用。
# 构建：docker build -f deploy/Dockerfile -t algoagent .   （context = 项目根目录）
#
# 基础镜像走国内镜像源（Docker Hub 直连被墙/DNS 污染时镜像源仍可达）。
# 默认 DaoCloud；如失效可换：--build-arg REGISTRY_MIRROR=<其他镜像源>/library
#   备选：mirror.swr.myhuaweicloud.com（华为云，路径可能不同）
#         <你的阿里云专属加速器>.mirror.aliyuncs.com（最稳，cr.console.aliyun.com 获取）

ARG REGISTRY_MIRROR=docker.m.daocloud.io/library

# ---------- 阶段 1：前端构建 ----------
FROM ${REGISTRY_MIRROR}/node:20-alpine AS frontend
WORKDIR /build
# 国内镜像源加速
RUN npm config set registry https://registry.npmmirror.com
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：运行时 ----------
FROM ${REGISTRY_MIRROR}/python:3.12-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 时区：容器默认 UTC，datetime.now() 存库会差 8 小时——设为东八区
# （运行时可覆盖：docker run -e TZ=Asia/Tokyo 等）
ENV TZ=Asia/Shanghai

# Python 依赖（华为云镜像源加速；官方源可去掉 -i 参数）
COPY backend/requirements.txt .
RUN pip install -r requirements.txt -i https://repo.huaweicloud.com/repository/pypi/simple/ \
    || pip install -r requirements.txt

# Playwright Chromium 系统依赖（手动按 Debian 包名装——python:3.12-slim 为 Debian trixie，
# playwright --with-deps 走 Ubuntu 清单会因 ttf-unifont 等包已淘汰而失败）。
# tzdata：TZ 环境变量生效的前提（zoneinfo 数据库）。
RUN apt-get update && apt-get install -y --no-install-recommends \
      libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 \
      libcups2 libdbus-1-3 libexpat1 libgbm1 libglib2.0-0 libnspr4 libnss3 \
      libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 \
      libxfixes3 libxkbcommon0 libxrandr2 ca-certificates \
      fonts-liberation fonts-unifont tzdata \
    && rm -rf /var/lib/apt/lists/*

# Playwright Chromium 浏览器本体（走官方 CDN，内置多 host 自动回退）。
# 国内构建慢可加：--build-arg PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
# （注：npmmirror 可能缺 headless-shell 包 404，届时去掉该参数重试官方源）
ARG PLAYWRIGHT_DOWNLOAD_HOST=""
ENV PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST}
RUN playwright install chromium

# 后端代码 + 前端产物
COPY backend/ ./
COPY --from=frontend /build/dist ./static

# 数据卷（SQLite + 生成页面 + 运行时配置），部署时 -v algoagent_data:/data
ENV ALGOAGENT_DATA_DIR=/data \
    ALGOAGENT_PAGES_DIR=/data/pages
VOLUME /data

EXPOSE 8000

# SECRET_KEY 必填（缺失即 fail-fast 退出）——运行时通过 -e 或 --env-file 注入
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
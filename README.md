# AlgoAgent —— 教师的教学智能体

教师用自然语言下达教学任务，智能体自主生成交互式算法可视化页面（单步执行、可调参数），交付免登录分享链接供学生自主学习。

- 产品需求：`产品需求文档.md`
- 技术选型：`技术选型文档.md`
- 开发排期：`开发任务清单.md`

## 目录结构

```
pro4/
├── backend/          # FastAPI 后端
│   ├── app/
│   │   └── main.py   # 应用入口（含 SECRET_KEY fail-fast 校验、/api/health）
│   └── requirements.txt
├── frontend/         # Vite + Vue3 + TypeScript + Element Plus 前端
├── deploy/           # Docker 部署交付物（Dockerfile / .env.example / compose）
└── README.md
```

## 本地启动

### 方式一：一键启动脚本（推荐）

```powershell
# 首次运行（自动建 venv、装依赖、起前后端、跑冒烟验证）：
powershell -ExecutionPolicy Bypass -File .\qidong.ps1 -First

# 日常启动（跳过依赖安装）：
powershell -ExecutionPolicy Bypass -File .\qidong.ps1
```

脚本会：检查 python/node → 准备后端 venv → 两个独立窗口分别启动 uvicorn 与 vite → 自动探测 `/api/health` 并汇报结果。依赖安装失败会自动切换华为云 pip 镜像源；npm 全局缓存不可写时自动改用项目内缓存。

### 方式二：手动启动

#### 1. 后端（FastAPI + uvicorn）

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows；Linux 用 source .venv/bin/activate
pip install -r requirements.txt
# 国内网络慢可用镜像：pip install -r requirements.txt -i https://repo.huaweicloud.com/repository/pypi/simple/

# 必填：SECRET_KEY（缺失时启动直接报错退出，不提供默认值）
# git-bash:   export SECRET_KEY=dev-secret-key
# PowerShell: $env:SECRET_KEY="dev-secret-key"

uvicorn app.main:app --reload --port 8000
```

验证：浏览器或 curl 访问 `http://127.0.0.1:8000/api/health`，返回 `{"status":"ok"}`。
交互式 API 文档：`http://127.0.0.1:8000/docs`。

### 2. 前端（Vite + Vue3 + TS + Element Plus）

```bash
cd frontend
npm install
npm run dev
```

访问 `http://localhost:5173`，页面显示骨架标题与 Element Plus 按钮。

> 前后端连通已预置：Vite dev server 将 `/api` 请求代理到 `http://127.0.0.1:8000`（见 `frontend/vite.config.ts`）。

## Docker 部署

单容器即完整产品（FastAPI + 前端托管 + Playwright 沙箱），支持任意 Linux 服务器部署。完整步骤见 **`deploy/部署指南.md`**：

```bash
# 本地构建并导出镜像（服务器无需外网）
docker build -f deploy/Dockerfile -t algoagent:latest .
docker save -o algoagent.tar algoagent:latest
scp .\algoagent.tar root@<服务器IP>:/opt/

# 服务器上装载并启动
docker load -i /opt/algoagent.tar
docker run -d --name algoagent -p 80:8000 -v algoagent_data:/data \
  --env-file /opt/algoagent/.env --restart unless-stopped algoagent:latest
```
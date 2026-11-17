# AlgoAgent 本地一键启动脚本（PowerShell 版）
# 用法：
#   首次运行（含依赖安装）：  powershell -ExecutionPolicy Bypass -File .\qidong.ps1 -First
#   日常启动（跳过安装）：    powershell -ExecutionPolicy Bypass -File .\qidong.ps1
#   也可在 PowerShell 里直接： .\qidong.ps1          （日常）
#                            .\qidong.ps1 -First    （首次）

param(
    [switch]$First  # 加上此开关 = 安装依赖（首次或依赖损坏时用）
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

function Write-Step($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Fail($msg)  { Write-Host "    [FAIL] $msg" -ForegroundColor Red }
function Write-Info($msg)  { Write-Host "    $msg" -ForegroundColor Yellow }

# ---------- 0. 基础检查 ----------
Write-Step "检查运行环境"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Fail "未找到 python，请先安装 Python 3.12+ 并加入 PATH"
    exit 1
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Fail "未找到 node，请先安装 Node 18+ 并加入 PATH"
    exit 1
}
Write-Ok "python / node 已就绪"

if (-not $env:SECRET_KEY) {
    $env:SECRET_KEY = 'dev-secret-key-123'   # 仅本地开发默认值；生产必须显式设置
    Write-Info "SECRET_KEY 未设置，已使用本地开发默认值（服务端代码的 fail-fast 校验不受影响）"
}

# ---------- 1. 后端 ----------
Write-Step "准备后端（backend/）"

Push-Location "$Root\backend"
if (-not (Test-Path '.venv')) {
    Write-Info "创建虚拟环境 .venv ..."
    python -m venv .venv
}
Write-Info "激活虚拟环境"
& "$Root\backend\.venv\Scripts\Activate.ps1"
Write-Ok "虚拟环境已激活"

if ($First) {
    Write-Info "安装后端依赖（慢会自动切华为云镜像源）..."
    pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Info "官方源失败，切换华为云镜像源重试 ..."
        pip install -r requirements.txt -i https://repo.huaweicloud.com/repository/pypi/simple/
        if ($LASTEXITCODE -ne 0) { Write-Fail "后端依赖安装失败，请把上面的报错贴给 AI 助手"; exit 1 }
    }
    Write-Ok "后端依赖安装完成"
}

Write-Info "启动后端 uvicorn（窗口标题：AlgoAgent-Backend）..."
$backendCmd = "cd `"$Root\backend`"; & `"$Root\backend\.venv\Scripts\Activate.ps1`"; `$env:SECRET_KEY='$env:SECRET_KEY'; uvicorn app.main:app --reload --port 8000"
Start-Process powershell -ArgumentList '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $backendCmd
Pop-Location
Write-Ok "后端已在独立窗口启动：http://127.0.0.1:8000/api/health"

# ---------- 2. 前端 ----------
Write-Step "准备前端（frontend/）"

Push-Location "$Root\frontend"
if ($First) {
    if (-not (Test-Path 'node_modules') -or (Get-ChildItem 'node_modules' -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) {
        Write-Info "npm 缓存目录不可用时自动改用项目内缓存 ..."
        $useCache = ''
        try {
            $cacheDir = npm config get cache
            New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
            Set-Location $cacheDir
            $testFile = Join-Path $cacheDir '.wtest'
            'x' | Out-File $testFile -ErrorAction Stop
            Remove-Item $testFile -Force
            Set-Location "$Root\frontend"
            Write-Info "全局缓存目录可写：$cacheDir"
        } catch {
            Set-Location "$Root\frontend"
            $useCache = '--cache=./.npm-cache'
            Write-Info "全局缓存不可写，改用项目内缓存 .npm-cache"
        }
        Write-Info "安装前端依赖（可能需要几分钟）..."
        npm install $useCache --no-audit --no-fund --loglevel=error
        if ($LASTEXITCODE -ne 0) { Write-Fail "npm install 失败，请把上面的报错贴给 AI 助手"; exit 1 }
        Write-Ok "前端依赖安装完成"
    } else {
        Write-Ok "node_modules 已存在，跳过安装"
    }
}

Write-Info "启动前端 vite dev（窗口标题：AlgoAgent-Frontend）..."
$frontendCmd = "cd `"$Root\frontend`"; npm run dev"
Start-Process powershell -ArgumentList '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $frontendCmd
Pop-Location
Write-Ok "前端已在独立窗口启动：http://localhost:5173"

# ---------- 3. 冒烟验证 ----------
Write-Step "冒烟验证（等后端就绪后自动探测）"

$ok = $false
foreach ($i in 1..15) {
    Start-Sleep -Seconds 2
    try {
        $resp = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
        if ($resp.status -eq 'ok') { $ok = $true; break }
    } catch { }
    Write-Info "等待后端就绪 ...（$i/15）"
}

if ($ok) { Write-Ok "后端健康检查通过：/api/health => ok" }
else     { Write-Fail "后端 30 秒内未就绪，请查看 AlgoAgent-Backend 窗口报错并贴给 AI 助手" }

Write-Host "`n=================================================" -ForegroundColor Cyan
Write-Host "  后端：http://127.0.0.1:8000  （API 文档 /docs）" -ForegroundColor White
Write-Host "  前端：http://localhost:5173" -ForegroundColor White
Write-Host "  停止服务：关闭 AlgoAgent-Backend / AlgoAgent-Frontend 两个窗口" -ForegroundColor White
Write-Host "=================================================`n" -ForegroundColor Cyan
# AlgoAgent 任务5（对话会话管理）一键验收脚本
# 用法（项目根目录下）：
#   powershell -ExecutionPolicy Bypass -File .\test_chat.ps1 -ApiKey "你的key"   # 全量（含 1 次真生成，2-5 分钟）
#   powershell -ExecutionPolicy Bypass -File .\test_chat.ps1 -NoLlm              # 仅 REST 验收（秒级）

param(
    [string]$ApiKey = "",
    [switch]$NoLlm
)

$Root = $PSScriptRoot

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "    [FAIL] $msg" -ForegroundColor Red }
function Write-Info($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# ---------- 1. 环境 ----------
Write-Step "检查环境"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Write-Fail "未找到 python"; exit 1 }
if (-not (Test-Path "$Root\backend\.venv")) { Write-Fail "虚拟环境不存在，请先跑 test_llm.ps1"; exit 1 }
& "$Root\backend\.venv\Scripts\Activate.ps1"
Write-Ok "虚拟环境已激活"

# ---------- 2. 依赖 ----------
Write-Step "检查依赖（复用既有依赖，已装秒过）"

& python -m pip install --progress-bar off -r "$Root\backend\requirements.txt"
if ($LASTEXITCODE -ne 0) {
    Write-Info "官方源失败，切换华为云镜像源 ..."
    & python -m pip install --progress-bar off -r "$Root\backend\requirements.txt" -i https://repo.huaweicloud.com/repository/pypi/simple/
    if ($LASTEXITCODE -ne 0) { Write-Fail "依赖安装失败，把上方报错贴给 AI 助手"; exit 1 }
}
Write-Ok "依赖已就绪"

# ---------- 3. 环境变量 ----------
Write-Step "组装环境变量"

if ($ApiKey -ne "") {
    $env:LLM_API_KEY = $ApiKey
    Write-Info "使用参数传入的 ApiKey"
} elseif (-not $env:LLM_API_KEY) {
    $env:LLM_API_KEY = 'sk-local'
    Write-Info "LLM_API_KEY 未设置，使用内网兜底值 sk-local"
} else {
    Write-Info "沿用当前会话已设置的 LLM_API_KEY"
}

# ---------- 4. 运行验收 ----------
$extraArgs = @()
if (-not $NoLlm) { $extraArgs += "--with-llm" }

if ($NoLlm) {
    Write-Step "运行任务 5 验收（仅 REST，秒级）"
} else {
    Write-Step "运行任务 5 验收（REST + 1 次真生成挂会话，2-5 分钟）"
    Write-Info "生成期间有流式心跳输出（每 10 秒一行），请耐心等待 ..."
}

& python "$Root\backend\scripts\test_chat.py" @extraArgs
$code = $LASTEXITCODE

# ---------- 5. 汇总 ----------
Write-Host "`n=================================================" -ForegroundColor Cyan
if ($code -eq 0) {
    Write-Host "  结果：任务 5 验收全部通过（阶段一 7 项任务全部收官）" -ForegroundColor Green
} else {
    Write-Host "  结果：存在失败项（退出码 $code），把上方输出贴给 AI 助手" -ForegroundColor Red
}
Write-Host "=================================================`n" -ForegroundColor Cyan
exit $code
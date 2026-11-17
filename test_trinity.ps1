# AlgoAgent 任务0（三件套输出能力实测）一键脚本
# 用法（项目根目录下）：
#   powershell -ExecutionPolicy Bypass -File .\test_trinity.ps1 -ApiKey "你的key"
#   可选：-Rounds 3   每类算法跑 3 轮（默认 2）

param(
    [string]$ApiKey = "",
    [int]$Rounds = 2
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
Write-Step "检查依赖（复用任务2/3依赖，已装秒过）"

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

# ---------- 4. 运行实测 ----------
Write-Step "运行三件套实测（3 类算法 × $Rounds 轮，每轮含生成+沙箱校验，总计约 10-20 分钟）"
Write-Info "单轮 = LLM 生成（1-3 分钟）+ Chromium 校验（秒级），请耐心等待逐轮输出 ..."

& python "$Root\backend\scripts\test_llm_output.py" --rounds $Rounds
$code = $LASTEXITCODE

# ---------- 5. 汇总 ----------
Write-Host "`n=================================================" -ForegroundColor Cyan
if ($code -eq 0) {
    Write-Host "  实测完成：报告见 backend\任务0三件套实测报告.md" -ForegroundColor Green
    Write-Host "  把上方汇总（一次通过率/结论行）贴给 AI 助手定稿生成规范" -ForegroundColor Yellow
} else {
    Write-Host "  实测脚本异常退出（$code），把输出贴给 AI 助手" -ForegroundColor Red
}
Write-Host "=================================================`n" -ForegroundColor Cyan
exit $code
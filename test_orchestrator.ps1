# AlgoAgent 任务4（生成编排与自修复循环）一键验收脚本
# 用法（项目根目录下）：
#   powershell -ExecutionPolicy Bypass -File .\test_orchestrator.ps1 -ApiKey "你的key"
#   可选：-Task "自定义教学任务"  -Quick（测试 B 只注一种错）

param(
    [string]$ApiKey = "",
    [string]$Task = "",
    [switch]$Quick
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

# ---------- 4. 运行验收 ----------
Write-Step "运行任务 4 验收（测试 A 端到端 + 测试 B 注错自修复）"
Write-Info "预计 15-40 分钟：测试 A 1-3 轮生成；测试 B 每例 1-3 轮修复；每轮 2-5 分钟"
Write-Info "期间逐轮打印进度，请勿中断（Ctrl+C 会丢掉已完成轮次的报告）..."

$extraArgs = @()
if ($Task -ne "") { $extraArgs += @("--task", $Task) }
if ($Quick) { $extraArgs += "--quick" }

& python "$Root\backend\scripts\test_orchestrator.py" @extraArgs
$code = $LASTEXITCODE

# ---------- 5. 汇总 ----------
Write-Host "`n=================================================" -ForegroundColor Cyan
if ($code -eq 0) {
    Write-Host "  结果：任务 4 全部验收通过（里程碑 A 核心链路收官）" -ForegroundColor Green
    Write-Host "  报告：backend\任务4编排自修复验收报告.md" -ForegroundColor White
} else {
    Write-Host "  结果：存在失败项（退出码 $code），把上方输出贴给 AI 助手" -ForegroundColor Red
}
Write-Host "=================================================`n" -ForegroundColor Cyan
exit $code
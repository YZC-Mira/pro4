# AlgoAgent 任务2（LLM 统一接入层）一键测试脚本
# 用法（项目根目录下）：
#   默认（api_key 用 sk-local 兜底，适合内网端点）：
#     powershell -ExecutionPolicy Bypass -File .\test_llm.ps1
#   指定真实 key：
#     powershell -ExecutionPolicy Bypass -File .\test_llm.ps1 -ApiKey "sk-xxxx"
#   临时换端点/模型（验证环境变量覆盖链路）：
#     powershell -ExecutionPolicy Bypass -File .\test_llm.ps1 -BaseUrl "http://x.x.x.x/v1" -Model "deepseek-v4-flash"

param(
    [string]$ApiKey = "",
    [string]$BaseUrl = "",
    [string]$Model = ""
)


$Root = $PSScriptRoot

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "    [FAIL] $msg" -ForegroundColor Red }
function Write-Info($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# ---------- 1. 环境检查 ----------
Write-Step "检查环境"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Fail "未找到 python"; exit 1
}

if (-not (Test-Path "$Root\backend\.venv")) {
    Write-Info "未发现虚拟环境，创建中 ..."
    Push-Location "$Root\backend"
    python -m venv .venv
    Pop-Location
}
& "$Root\backend\.venv\Scripts\Activate.ps1"
Write-Ok "虚拟环境已激活"

# ---------- 2. 依赖检查 ----------
Write-Step "检查依赖（openai / pyyaml）"

# 说明：不用 $ErrorActionPreference='Stop'（PS5.1 会把 python 的 stderr 当异常终止脚本）
$needInstall = $false
foreach ($pkg in @('openai', 'yaml')) {
    & python -c "import $pkg" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { $needInstall = $true }
}

if ($needInstall) {
    Write-Info "安装依赖（requirements.txt，失败自动切华为云镜像）..."
    & pip install -q -r "$Root\backend\requirements.txt" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Info "官方源失败，切换华为云镜像源 ..."
        & pip install -q -r "$Root\backend\requirements.txt" -i https://repo.huaweicloud.com/repository/pypi/simple/ 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Fail "依赖安装失败，把上方报错贴给 AI 助手"; exit 1 }
    }
    Write-Ok "依赖安装完成"
} else {
    Write-Ok "依赖已就绪"
}

# ---------- 3. 组装环境变量 ----------
Write-Step "组装测试环境变量"

if ($ApiKey -ne "") {
    $env:LLM_API_KEY = $ApiKey
    Write-Info "使用参数传入的 ApiKey"
} elseif (-not $env:LLM_API_KEY) {
    $env:LLM_API_KEY = 'sk-local'
    Write-Info "LLM_API_KEY 未设置，使用内网兜底值 sk-local（真实 key 可用 -ApiKey 传入）"
} else {
    Write-Info "沿用当前会话已设置的 LLM_API_KEY"
}

if ($BaseUrl -ne "") { $env:LLM_BASE_URL = $BaseUrl; Write-Info "覆盖 base_url = $BaseUrl" }
if ($Model  -ne "") { $env:LLM_MODEL   = $Model;  Write-Info "覆盖 model    = $Model" }

# ---------- 4. 运行连通性测试 ----------
Write-Step "运行连通性测试（scripts/test_llm_connectivity.py）"

python "$Root\backend\scripts\test_llm_connectivity.py"
$code = $LASTEXITCODE

# ---------- 5. 汇总 ----------
Write-Host "`n=================================================" -ForegroundColor Cyan
if ($code -eq 0) {
    Write-Host "  结果：连通性测试全部通过（任务 2 验收完成）" -ForegroundColor Green
} else {
    Write-Host "  结果：测试失败，退出码 $code" -ForegroundColor Red
    Write-Host "  常见原因：" -ForegroundColor Yellow
    Write-Host "   1. 不在校园网（172.31.2.220 是内网地址）" -ForegroundColor Yellow
    Write-Host "   2. key 无效（用 -ApiKey 传真实 key 重试）" -ForegroundColor Yellow
    Write-Host "   3. 服务端未启动，把上方报错贴给 AI 助手" -ForegroundColor Yellow
}
Write-Host "=================================================`n" -ForegroundColor Cyan
exit $code
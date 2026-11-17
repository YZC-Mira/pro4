# AlgoAgent 任务3（沙箱验证模块）一键测试脚本
# 用法（项目根目录下）：
#   powershell -ExecutionPolicy Bypass -File .\test_sandbox.ps1
#
# 自动完成：venv 检查 → 依赖安装（含 playwright，失败切华为云镜像）
#         → Chromium 安装（走 npmmirror 镜像提速，已装时秒过）
#         → 运行 scripts/test_sandbox.py 四项验收

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

# ---------- 2. 依赖安装 ----------
Write-Step "检查依赖（fastapi / openai / pyyaml / playwright）"

Write-Info "首次安装 playwright 约 40MB，看到 Collecting/Downloading 即在正常下载，请耐心等待 ..."
& python -m pip install --progress-bar off -r "$Root\backend\requirements.txt"
if ($LASTEXITCODE -ne 0) {
    Write-Info "官方源失败，切换华为云镜像源 ..."
    & python -m pip install --progress-bar off -r "$Root\backend\requirements.txt" -i https://repo.huaweicloud.com/repository/pypi/simple/
    if ($LASTEXITCODE -ne 0) { Write-Fail "依赖安装失败，把上方报错贴给 AI 助手"; exit 1 }
}
Write-Ok "依赖已就绪"

# ---------- 3. Chromium 安装 ----------
Write-Step "检查 Chromium（已装秒过；首次约 150MB，走 npmmirror 镜像）"

$env:PLAYWRIGHT_DOWNLOAD_HOST = 'https://npmmirror.com/mirrors/playwright/'
Write-Info "下载镜像：$env:PLAYWRIGHT_DOWNLOAD_HOST"
& python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    Write-Info "镜像下载失败，回退官方源重试 ..."
    Remove-Item Env:\PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
    & python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { Write-Fail "Chromium 安装失败，把上方报错贴给 AI 助手"; exit 1 }
}
Write-Ok "Chromium 已就绪"

# ---------- 4. 运行沙箱验收测试 ----------
Write-Step "运行沙箱四项验收（scripts/test_sandbox.py）"

& python "$Root\backend\scripts\test_sandbox.py"
$code = $LASTEXITCODE

# ---------- 5. 汇总 ----------
Write-Host "`n=================================================" -ForegroundColor Cyan
if ($code -eq 0) {
    Write-Host "  结果：沙箱验证模块四项验收全部通过（任务 3 完成）" -ForegroundColor Green
} else {
    Write-Host "  结果：存在失败项，退出码 $code，把上方输出贴给 AI 助手" -ForegroundColor Red
}
Write-Host "=================================================`n" -ForegroundColor Cyan
exit $code
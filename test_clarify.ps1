# AlgoAgent 澄清接口一键测试（PS 5.1 编码安全版）
# 用法（项目根目录，需后端已启动）：
#   powershell -ExecutionPolicy Bypass -File .\test_clarify.ps1

$Root = $PSScriptRoot
$Api = 'http://127.0.0.1:8000/api/chat/clarify'

# 让控制台正确显示 UTF-8 中文
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

function Test-Clarify($label, $task) {
    Write-Step $label
    Write-Info "任务：$task"
    $json = '{"task":"' + $task + '"}'
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    try {
        $resp = Invoke-RestMethod -Uri $Api -Method Post -ContentType 'application/json; charset=utf-8' -Body $bytes
    } catch {
        Write-Host "    [FAIL] 请求失败：$($_.Exception.Message)" -ForegroundColor Red
        Write-Info "请确认后端已启动（uvicorn app.main:app --port 8000）"
        return
    }
    if ($resp.need_clarify) {
        Write-Ok "触发澄清（$($resp.questions.Count) 个问题）"
        $i = 1
        foreach ($q in $resp.questions) { Write-Host "      $i. $q" -ForegroundColor White; $i++ }
        if ($resp.reason) { Write-Info "判断依据：$($resp.reason)" }
    } else {
        Write-Ok "信息充足，直接放行生成（无需澄清）"
        if ($resp.reason) { Write-Info "判断依据：$($resp.reason)" }
    }
}

# ---------- 前置检查 ----------
Write-Step "检查后端连通性"
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 3
    Write-Ok "后端在线（/api/health => ok）"
} catch {
    Write-Host "    [FAIL] 后端未启动，请先在 backend 目录用 uvicorn 启动" -ForegroundColor Red
    exit 1
}

# ---------- 测试用例 ----------
Test-Clarify '用例 1：模糊任务（预期触发澄清提问）' '帮我做个排序动画'
Test-Clarify '用例 2：具体任务（预期直接放行）' '为大二学生生成冒泡排序教学页面，逐步高亮比较与交换过程'
Test-Clarify '用例 3：半具体任务（预期只追问缺失项）' '为大二学生生成排序教学页面'

Write-Host "`n=================================================" -ForegroundColor Cyan
Write-Host "  测试完成：需澄清=问问题，信息充足=放行" -ForegroundColor White
Write-Host "  更真实的测法：浏览器打开 http://localhost:5173" -ForegroundColor Yellow
Write-Host "  直接发'帮我做个排序动画'看工作台澄清交互" -ForegroundColor Yellow
Write-Host "=================================================`n" -ForegroundColor Cyan
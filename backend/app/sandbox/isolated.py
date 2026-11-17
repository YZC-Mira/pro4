"""子进程隔离的沙箱校验（任务 6 修复）。

通过 subprocess 调 verify_cli.py，在独立 Python 进程内跑 Playwright——
与任务 4/5 验收通过的运行环境完全一致，规避 uvicorn worker 线程内的
Playwright sync 死锁；同时获得超时保护与崩溃隔离。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .verifier import VerifyResult

_CLI_PATH = Path(__file__).resolve().parent / "verify_cli.py"
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_SUBPROCESS_TIMEOUT_S = 120


def verify_page_isolated(html_path: str | Path) -> VerifyResult:
    """在独立子进程中执行四项校验，返回 VerifyResult。

    子进程异常/超时/输出损坏时返回结构化失败（而非抛异常），
    保证自修复循环能拿到 error_summary 回传 LLM。
    """
    html_path = str(Path(html_path).resolve())
    if not Path(html_path).exists():
        result = VerifyResult()
        result.error_summary = f"生成页文件不存在：{html_path}"
        return result

    try:
        proc = subprocess.run(
            [sys.executable, str(_CLI_PATH), html_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SUBPROCESS_TIMEOUT_S,
            cwd=str(_BACKEND_DIR),
        )
    except subprocess.TimeoutExpired:
        result = VerifyResult()
        result.error_summary = f"沙箱校验子进程超时（{_SUBPROCESS_TIMEOUT_S}s），已终止"
        return result
    except Exception as e:  # 启动失败等
        result = VerifyResult()
        result.error_summary = f"沙箱校验子进程启动失败：{type(e).__name__}: {e}"
        return result

    payload = _parse_last_json_line(proc.stdout)
    if payload is None:
        stderr_tail = (proc.stderr or "").strip().splitlines()[-3:]
        detail = "；".join(stderr_tail) if stderr_tail else f"exit={proc.returncode}"
        result = VerifyResult()
        result.error_summary = f"沙箱校验子进程输出异常（{detail}）"
        return result

    return VerifyResult(
        ok=bool(payload.get("ok")),
        js_errors=list(payload.get("js_errors", [])),
        missing_controls=list(payload.get("missing_controls", [])),
        verify_entry_exists=bool(payload.get("verify_entry_exists")),
        has_test_cases=bool(payload.get("has_test_cases")),
        test_case_results=list(payload.get("test_case_results", [])),
        timed_out=bool(payload.get("timed_out")),
        error_summary=str(payload.get("error_summary", "")),
    )


def _parse_last_json_line(stdout: str) -> dict | None:
    """取 stdout 中最后一行合法 JSON（容忍 Playwright 警告混入）。"""
    if not stdout:
        return None
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None
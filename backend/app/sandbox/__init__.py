"""沙箱验证模块（技术选型第 4 节）。

两种执行方式：
- verify_page：进程内直调（CLI 脚本场景：test_sandbox / test_orchestrator 等）。
- verify_page_isolated：独立子进程执行（Web 服务场景）。Playwright sync API
  在 uvicorn 进程的 worker 线程内会死锁（2026-10-25 实测：sandbox_check 后
  无任何输出），子进程完全复刻任务 4/5 已验证的运行环境，且自带超时保护
  与崩溃隔离——orchestrator 统一走本入口。
"""

from .verifier import VerifyResult, verify_page
from .isolated import verify_page_isolated

__all__ = ["verify_page", "verify_page_isolated", "VerifyResult"]
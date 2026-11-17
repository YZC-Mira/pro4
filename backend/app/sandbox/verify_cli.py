"""沙箱校验 CLI 入口（任务 6 修复：进程隔离执行）。

背景：Playwright sync API 在 uvicorn 进程的 worker 线程内会死锁
（服务环境下 sandbox_check 之后无任何输出/返回）。任务 4/5 已验证
独立进程跑 verify_page 100% 正常，故 Web 服务路径统一走子进程：
    python app/sandbox/verify_cli.py <html_path>
stdout 输出单行 JSON（ensure_ascii=True，规避 Windows 编码差异）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.sandbox.verifier import verify_page  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error_summary": "用法：verify_cli.py <html_path>"}))
        return 2
    html_path = sys.argv[1]
    if not Path(html_path).exists():
        print(json.dumps({"ok": False, "error_summary": f"生成页文件不存在：{html_path}"}))
        return 2

    result = verify_page(html_path)
    print(json.dumps(result.to_dict(), ensure_ascii=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
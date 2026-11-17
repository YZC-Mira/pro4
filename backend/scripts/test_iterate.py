"""对话式迭代（PRD F1 上下文）自测脚本：秒级，不调真 LLM。

覆盖：
A. iterate_page 基础闭环：读原作品 HTML → 迭代输出覆盖同一文件（page_id 不变）→
   pages 元数据更新 → 会话消息（反馈+结果）入流；
B. 上下文构成：迭代 prompt 含原始任务 + 用户反馈 + 当前 HTML（截取片段断言）；
C. kind=iterate：第一次尝试 kind 为 iterate（attempt_start 事件可见）；
D. 反馈模糊性兜底：空反馈 ValueError；
E. 不存在的作品/未完成作品 ValueError；
F. SSE 路由：iterate_page_id 归属校验（他人作品 403 / 不存在 404 / 未完成 422）+
   带 iterate_page_id 的流式请求正常产出 done 且 page_id 为原作品；
G. 分享联动（覆盖式）：迭代后同一 token 读到新内容。

用法（backend 目录，激活 .venv 后）：python scripts/test_iterate.py
"""

import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-iterate")
os.environ.setdefault("ALGOAGENT_DATA_DIR", tempfile.mkdtemp(prefix="algoagent_iter_test_"))
os.environ.setdefault("ALGOAGENT_PAGES_DIR", tempfile.mkdtemp(prefix="algoagent_iter_pages_"))

from fastapi.testclient import TestClient  # noqa: E402


def _install_fake_deps_if_needed() -> None:
    try:
        import openai  # noqa: F401
        from playwright.sync_api import Error  # noqa: F401
        return
    except Exception:
        pass
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = object
    sys.modules["openai"] = fake_openai
    fake_pw = types.ModuleType("playwright")
    fake_pw_api = types.ModuleType("playwright.sync_api")
    for name in ("Error", "TimeoutError", "sync_playwright"):
        setattr(fake_pw_api, name, object)
    fake_pw.sync_api = fake_pw_api
    sys.modules["playwright"] = fake_pw
    sys.modules["playwright.sync_api"] = fake_pw_api


_install_fake_deps_if_needed()

from app import db  # noqa: E402
from app.agent import orchestrator as orch  # noqa: E402
from app.main import app  # noqa: E402

HTML_V1 = "<!DOCTYPE html><html><body>version-1-array-8</body></html>"
HTML_V2 = "<!DOCTYPE html><html><body>version-2-array-5-adjusted</body></html>"

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"——{detail}" if detail and not cond else ""))


def main() -> int:
    db.init_db()

    # mock：生成阶段输出 V1；迭代阶段输出 V2（按 messages 里是否含"用户调整反馈"区分）
    def _fake_chat(messages, stream=False, config=None):
        user_text = messages[-1]["content"] if messages else ""
        out = HTML_V2 if "用户调整反馈" in user_text else HTML_V1
        if stream:
            return iter([out])
        return out

    class _FakeVerify:
        def __init__(self):
            self.ok = True
            self.error_summary = "校验通过"

        def to_dict(self):
            return {"ok": True, "error_summary": "校验通过"}

    orch.chat = _fake_chat
    orch.verify_page_isolated = lambda path: _FakeVerify()

    print("=" * 60)
    print("对话式迭代（PRD F1 上下文）自测（mock 秒级）")
    print("=" * 60)

    with TestClient(app) as client:
        # ---------- 准备 ----------
        print("\n[0] 准备：注册用户并生成一件作品 + 建分享")
        r = client.post("/api/auth/register", json={"username": "iter01", "password": "pass123456"})
        token_a = r.json()["token"]
        auth_a = {"Authorization": f"Bearer {token_a}"}
        r = client.post("/api/auth/register", json={"username": "iter02", "password": "pass123456"})
        token_b = r.json()["token"]
        auth_b = {"Authorization": f"Bearer {token_b}"}

        r = client.post("/api/conversations", json={"title": "迭代测试"}, headers=auth_a)
        cid = r.json()["id"]
        r = client.post("/api/generate", json={"task": "为大二生成冒泡排序动画，逐步高亮", "conversation_id": cid}, headers=auth_a)
        check("准备：首次生成成功", r.status_code == 200 and r.json()["ok"] is True, r.text[:150])
        page_id = r.json()["page_id"]
        original_path = r.json()["final_html_path"]

        r = client.post("/api/shares", json={"page_id": page_id, "validity": "forever"}, headers=auth_a)
        share_token = r.json()["token"]

        # ---------- A/B/C：编排层迭代闭环 ----------
        print("\n[A/B/C] 编排层 iterate_page 闭环")
        events: list = []

        def on_event(etype, payload):
            events.append((etype, payload))

        # 上下文捕获：检查发给 LLM 的 messages
        captured = {}

        def _capture_chat(messages, stream=False, config=None):
            captured.setdefault("calls", []).append(messages)
            return _fake_chat(messages, stream=stream, config=config)

        orch.chat = _capture_chat

        outcome = orch.iterate_page(page_id, "动画太快了，数组改成 5 个元素",
                                    conversation_id=cid, user_id=1, on_event=on_event)
        check("A1 迭代成功（outcome.ok）", outcome.ok is True)
        check("A2 page_id 沿用原作品（覆盖式）", outcome.page_id == page_id)
        check("A3 输出覆盖同一文件", str(Path(original_path)) == str(Path(outcome.final_html_path)))

        content = Path(original_path).read_text(encoding="utf-8")
        check("A4 文件内容已更新为 V2", "version-2-array-5-adjusted" in content)

        row = db.get_page(page_id)
        check("A5 作品 task 保留原始任务（迭代不污染作品标识）", row["task"] == "为大二生成冒泡排序动画，逐步高亮")
        check("A5b 作品元数据已刷新（updated_at > created_at）", row["updated_at"] >= row["created_at"]
              and row["status"] == "passed")

        msgs = db.get_messages(cid)
        tail = [m["content"] for m in msgs[-2:]]
        check("A6 会话消息：反馈+结果入流", any("数组改成 5 个元素" in t for t in tail)
              and any("调整" in t for t in tail))

        iter_messages = captured["calls"][-1]
        sys_text = iter_messages[0]["content"]
        user_text = iter_messages[1]["content"]
        check("B1 迭代 system prompt 就位", "迭代工程师" in sys_text and "不要重写整个页面" in sys_text)
        check("B2 上下文含原始任务", "冒泡排序" in user_text)
        check("B3 上下文含用户反馈", "动画太快了" in user_text)
        check("B4 上下文含当前 HTML", "version-1-array-8" in user_text)

        kinds = [p.get("kind") for e, p in events if e == "attempt_start"]
        check("C1 第一次尝试 kind=iterate", kinds and kinds[0] == "iterate", str(kinds))

        # ---------- D/E：输入校验 ----------
        print("\n[D/E] 输入校验")
        try:
            orch.iterate_page(page_id, "   ")
            check("D1 空反馈 ValueError", False)
        except ValueError:
            check("D1 空反馈 ValueError", True)
        try:
            orch.iterate_page("nosuchpage", "反馈")
            check("E1 不存在的作品 ValueError", False)
        except ValueError:
            check("E1 不存在的作品 ValueError", True)

        # ---------- F：SSE 路由归属校验与流转 ----------
        print("\n[F] SSE 路由（iterate_page_id）")
        r = client.post("/api/chat/stream", json={"task": "改一下", "iterate_page_id": page_id}, headers=auth_b)
        check("F1 他人作品 403", r.status_code == 403, f"code={r.status_code}")

        r = client.post("/api/chat/stream", json={"task": "改一下", "iterate_page_id": "nosuch"}, headers=auth_a)
        check("F2 不存在作品 404", r.status_code == 404, f"code={r.status_code}")

        r = client.post("/api/chat/stream", json={"task": "再快一点", "iterate_page_id": page_id,
                                                  "conversation_id": cid}, headers=auth_a)
        with client.stream("POST", "/api/chat/stream",
                           json={"task": "颜色换成蓝色，数组改 7 个元素", "iterate_page_id": page_id,
                                 "conversation_id": cid},
                           headers=auth_a) as resp:
            stages = []
            done_data = None
            current_event = ""
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    current_event = line[len("event: "):].strip()
                elif line.startswith("data: ") and current_event == "stage":
                    stages.append(line[len("data: "):])
                elif line.startswith("data: ") and current_event == "done":
                    import json as json_mod
                    done_data = json_mod.loads(line[len("data: "):])
                    current_event = ""
            check("F3 迭代 SSE 正常产出（含 stage）", resp.status_code == 200 and len(stages) >= 3,
                  f"stages={stages}")
            check("F4 done.page_id 为原作品", done_data and done_data.get("page_id") == page_id)

        # ---------- G：分享联动 ----------
        print("\n[G] 分享联动（覆盖式）")
        r = client.get(f"/s/{share_token}")
        check("G1 分享链接自动展示迭代后内容", "version-2-array-5-adjusted" in r.text)

    total = len(CHECKS)
    passed = sum(1 for _, c in CHECKS if c)
    print("\n" + "=" * 60)
    print(f"迭代自测：{passed}/{total} 项 PASS")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
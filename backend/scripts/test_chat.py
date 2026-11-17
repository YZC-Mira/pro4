"""任务 5 验收脚本：对话会话管理（对应微调版任务清单验收标准）。

验收项：
1. 创建会话 → 追加 3 轮消息 → 拉取历史返回完整有序消息列表（默认模式，秒级）；
2. 生成任务可挂到指定会话下（--with-llm：POST /api/generate 带 conversation_id，
   断言消息流多出 user/assistant 两条 + pages.conversation_id 挂载正确）。

用法（backend 目录，激活 .venv 后）：
  python scripts/test_chat.py                 # 仅 REST 验收（秒级，无 LLM 调用）
  $env:LLM_API_KEY="key"; python scripts/test_chat.py --with-llm   # 全量（含 1 次真生成，2-5 分钟）

测试数据写入临时目录（ALGOAGENT_DATA_DIR），不污染开发库。
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

# 必须先设环境变量再 import app（db.py 在模块级读取 ALGOAGENT_DATA_DIR；
# main.py 在模块级校验 SECRET_KEY）
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-chat")
os.environ.setdefault("ALGOAGENT_DATA_DIR", tempfile.mkdtemp(prefix="algoagent_chat_test_"))
os.environ.setdefault("ALGOAGENT_PAGES_DIR", tempfile.mkdtemp(prefix="algoagent_chat_pages_"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

GEN_TASK = "为大一学生生成冒泡排序教学页面：数组长度可调（默认 6 个元素），柱状图可视化，每步高亮正在比较的两个元素，讲解文字说明比较与交换。"


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"——{detail}" if detail and not cond else ""))
    return cond


def rest_acceptance(client: TestClient) -> tuple[bool, int]:
    """验收项 1：会话 CRUD + 3 轮消息 + 有序历史。返回 (是否全过, 会话id)。"""
    print("\n[验收 1] 会话管理 REST（创建 → 3 轮消息 → 有序历史）")
    ok = True

    r = client.get("/api/health")
    ok &= check("健康检查可用", r.status_code == 200 and r.json()["status"] == "ok")

    r = client.post("/api/conversations", json={"title": "冒泡排序备课"})
    ok &= check("创建会话返回 200", r.status_code == 200, f"code={r.status_code}")
    cid = r.json()["id"]

    rounds = [
        ("user", "帮我准备下周冒泡排序的课，面向大一学生"),
        ("assistant", "好的，请问希望突出哪些要点？例如比较与交换过程、时间复杂度？"),
        ("user", "突出每一步的比较与交换，数组长度可调"),
    ]
    for role, content in rounds:
        r = client.post(f"/api/conversations/{cid}/messages", json={"role": role, "content": content})
        ok &= check(f"追加消息 [{role}] 返回 200", r.status_code == 200, f"code={r.status_code}")

    r = client.get(f"/api/conversations/{cid}/messages")
    msgs = r.json()
    ok &= check("拉取历史返回 200", r.status_code == 200)
    ok &= check("历史共 3 条", len(msgs) == 3, f"实际 {len(msgs)} 条")
    ok &= check("顺序为 user→assistant→user", [m["role"] for m in msgs] == ["user", "assistant", "user"])
    ok &= check("内容与写入一致且有序", [m["content"] for m in msgs] == [c for _, c in rounds])
    ids = [m["id"] for m in msgs]
    ok &= check("消息 id 严格递增（时间有序）", ids == sorted(ids) and len(set(ids)) == 3)

    r = client.get("/api/conversations")
    ok &= check("会话列表包含新会话", any(c["id"] == cid for c in r.json()))

    r = client.get("/api/conversations/99999/messages")
    ok &= check("不存在的会话返回 404", r.status_code == 404, f"code={r.status_code}")

    r = client.post(f"/api/conversations/{cid}/messages", json={"role": "system", "content": "x"})
    ok &= check("非法 role 返回 422", r.status_code == 422, f"code={r.status_code}")

    r = client.post(f"/api/conversations/{cid}/messages", json={"role": "user", "content": "   "})
    ok &= check("空内容返回 422", r.status_code == 422, f"code={r.status_code}")

    return ok, cid


def llm_acceptance(client: TestClient, cid: int) -> bool:
    """验收项 2：生成挂到指定会话（1 次真生成，2-5 分钟）。"""
    print("\n[验收 2] 生成任务挂到会话（含 1 次真生成，2-5 分钟，流式心跳见控制台）")
    before = len(db.get_messages(cid))

    print(f"  ▶ POST /api/generate（conversation_id={cid}）...")
    r = client.post("/api/generate", json={"task": GEN_TASK, "conversation_id": cid})
    ok = check("生成接口返回 200", r.status_code == 200, f"code={r.status_code}")
    if r.status_code != 200:
        print(f"    响应：{r.text[:200]}")
        return False

    body = r.json()
    ok &= check(f"生成闭环成功（{body.get('seconds')}s，{len(body.get('attempts', []))} 次尝试）", body.get("ok") is True)

    msgs = db.get_messages(cid)
    ok &= check(f"会话消息从 {before} 增至 {before + 2}（任务+结果自动入流）", len(msgs) == before + 2)
    if len(msgs) >= 2:
        tail = msgs[-2:]
        ok &= check("末两条为 user→assistant", [m["role"] for m in tail] == ["user", "assistant"])
        ok &= check("user 消息即教学任务", GEN_TASK in tail[0]["content"])
        ok &= check("assistant 消息含生成结果摘要", "沙箱校验" in tail[1]["content"] or "校验" in tail[1]["content"])

    row = db.get_page(body["page_id"])
    ok &= check("pages.conversation_id 挂载正确", row is not None and row["conversation_id"] == cid)

    print(f"    最终版页面：{body.get('final_html_path')}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-llm", action="store_true", help="含 1 次真生成（验收 2，2-5 分钟）")
    args = parser.parse_args()

    print("=" * 60)
    print("任务 5 验收：对话会话管理（测试数据在临时目录，不污染开发库）")
    print("=" * 60)

    with TestClient(app) as client:
        ok1, cid = rest_acceptance(client)
        ok2 = True
        if args.with_llm:
            ok2 = llm_acceptance(client, cid)
        else:
            print("\n[验收 2] 跳过（--with-llm 可启用：生成任务挂载会话验证）")

    all_pass = ok1 and ok2
    print("\n" + "=" * 60)
    print(f"验收 1 会话 REST：{'✅ PASS' if ok1 else '❌ FAIL'}")
    if args.with_llm:
        print(f"验收 2 生成挂会话：{'✅ PASS' if ok2 else '❌ FAIL'}")
    print(f"任务 5 总验收：{'✅ 全部通过' if all_pass else '❌ 存在失败项'}")
    print("=" * 60)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
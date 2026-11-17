"""AgentStage 状态机 + 需求澄清规则兜底 自测脚本（不调真 LLM / 不跑 Chromium，秒级）。

覆盖：
A. AgentStage 枚举六阶段值与顺序；
B. clarify_task 规则兜底：LLM 未问 + 缺必澄清项 → 强制追问；LLM 正常提问 → 采纳；
   LLM 服务异常 → 规则兜底；任务已含必澄清信息 → 放行；
C. 编排 stage 事件序列（mock chat/verify）：一次通过路径与"失败→自修复→成功"路径；
   repairing 事件携带 attempt 字段。

用法（backend 目录，激活 .venv 后）：
  python scripts/test_agent_stage.py
真 LLM 集成自测（可选，见脚本尾部说明）：--with-llm 跑 clarify_task 真判断。
"""

import argparse
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 必须先设环境变量再 import app（main.py 模块级校验 SECRET_KEY；db.py 模块级读数据目录）
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-stage")
os.environ.setdefault("ALGOAGENT_DATA_DIR", tempfile.mkdtemp(prefix="algoagent_stage_test_"))
os.environ.setdefault("ALGOAGENT_PAGES_DIR", tempfile.mkdtemp(prefix="algoagent_stage_pages_"))


def _install_fake_deps() -> None:
    """沙箱/无依赖环境下 mock openai 与 playwright（orchestrator import 链需要）。"""
    if "openai" in sys.modules and "playwright" in sys.modules:
        return
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


_install_fake_deps()

from app import db  # noqa: E402
from app.agent import orchestrator as orch  # noqa: E402

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"——{detail}" if detail and not cond else ""))


# ---------- A. AgentStage 枚举 ----------

def test_stage_enum() -> None:
    print("\n[A] AgentStage 枚举")
    expected = ["clarifying", "generating", "verifying", "repairing", "done", "failed"]
    values = [s.value for s in orch.AgentStage]
    check("六阶段值齐全且有序", values == expected, f"实际 {values}")


# ---------- B. clarify_task 规则兜底 ----------

class _FakeVerify:
    """构造沙箱校验结果（对齐 VerifyResult 的 ok/error_summary/to_dict 接口）。"""

    def __init__(self, ok: bool, summary: str = ""):
        self.ok = ok
        self.error_summary = summary or ("校验通过" if ok else "校验失败")

    def to_dict(self) -> dict:
        return {"ok": self.ok, "error_summary": self.error_summary}


def test_clarify_rules() -> None:
    print("\n[B] clarify_task 规则兜底（mock LLM）")
    real_chat = orch.chat

    # B1: LLM 未提问 + 任务缺两项必澄清项 → 强制追问 2 问
    orch.chat = lambda *a, **k: '{"need_clarify": false, "questions": [], "reason": "信息充足"}'
    r = orch.clarify_task("生成冒泡排序动画")
    check("B1 LLM未问+缺必澄清项 → 强制追问", r.need_clarify and len(r.questions) == 2, str(r))
    check("B1 追问的就是必澄清项", any("学段" in q for q in r.questions) and any("演示形式" in q for q in r.questions))

    # B2: 任务含面向对象但缺演示形式 → 只追问演示形式
    r = orch.clarify_task("为大二学生生成冒泡排序动画")
    check("B2 已含学段 → 只追问缺失项", r.need_clarify and len(r.questions) == 1 and "演示形式" in r.questions[0], str(r))

    # B3: 任务含两项必澄清信息 + LLM 判定充足 → 放行
    r = orch.clarify_task("为大二学生生成冒泡排序教学页面，逐步高亮比较与交换过程")
    check("B3 必澄清项齐 + LLM 判充足 → 放行", not r.need_clarify and r.questions == [], str(r))

    # B4: LLM 正常提问 → 采纳（截断至 3）
    orch.chat = lambda *a, **k: '{"need_clarify": true, "questions": ["图多少节点?", "边权随机吗?", "第三个问题?", "第四个多余?"], "reason": "缺数据规模"}'
    r = orch.clarify_task("生成 Dijkstra 动画")
    check("B4 LLM 提问被采纳且截断至 3", r.need_clarify and len(r.questions) == 3 and r.source == "llm", str(r))

    # B5: LLM 服务异常 + 缺必澄清项 → 规则兜底强制追问
    def _boom(*a, **k):
        raise RuntimeError("connection refused")

    orch.chat = _boom
    r = orch.clarify_task("生成排序动画")
    check("B5 LLM 异常 → 规则兜底追问", r.need_clarify and len(r.questions) == 2, str(r))
    check("B5 source 标记 service_fallback", r.source == "service_fallback", r.source)

    # B6: LLM 异常但任务信息齐全 → 放行（不误伤）
    r = orch.clarify_task("为大二学生生成排序页面，用状态表格逐步呈现")
    check("B6 LLM 异常但信息齐全 → 放行", not r.need_clarify, str(r))

    orch.chat = real_chat


# ---------- C. 编排 stage 事件序列 ----------

def _collect_events(events: list) -> "callable":
    def on_event(etype: str, payload: dict) -> None:
        events.append((etype, payload))
    return on_event


def test_stage_events() -> None:
    print("\n[C] 编排 stage 事件序列（mock chat / verify_page_isolated）")
    real_chat, real_verify = orch.chat, orch.verify_page_isolated
    good_html = "<!DOCTYPE html><html><body>ok</body></html>"
    db.init_db()

    # C1: 一次通过路径：clarifying → generating → verifying → done
    events: list = []
    good_html = "<!DOCTYPE html><html><body>ok</body></html>"
    # chat(stream=True) 返回生成器（逐块产出），chat(stream=False) 返回完整文本
    def _fake_chat(messages, stream=False, config=None):
        if stream:
            return iter([good_html])
        return good_html
    orch.chat = _fake_chat
    orch.verify_page_isolated = lambda path: _FakeVerify(True)

    outcome = orch.generate_page("为大二学生生成冒泡排序页面，逐步高亮", on_event=_collect_events(events))
    stages = [(p["stage"], p.get("attempt")) for e, p in events if e == "stage"]
    check("C1 一次通过：outcome.ok", outcome.ok)
    check("C1 stage 序列 = clarifying→generating→verifying→done",
          stages == [("clarifying", None), ("generating", None), ("verifying", None), ("done", None)], str(stages))

    # C2: 失败→自修复→成功：clarifying → generating → verifying → repairing(2) → verifying → done
    events2: list = []
    verify_calls = {"n": 0}

    def _flaky_verify(path):
        verify_calls["n"] += 1
        return _FakeVerify(True) if verify_calls["n"] >= 2 else _FakeVerify(False, "JS 报错：xxx is not defined")

    orch.verify_page_isolated = _flaky_verify
    outcome2 = orch.generate_page("为大二学生生成二分查找页面，逐步高亮", on_event=_collect_events(events2))
    stages2 = [(p["stage"], p.get("attempt")) for e, p in events2 if e == "stage"]
    check("C2 自修复：outcome.ok（2 次尝试）", outcome2.ok and len(outcome2.attempts) == 2)
    check("C2 stage 序列含 repairing(2)",
          stages2 == [("clarifying", None), ("generating", None), ("verifying", None),
                      ("repairing", 2), ("verifying", None), ("done", None)], str(stages2))
    repairing_events = [(e, p) for e, p in events2 if e == "stage" and p["stage"] == "repairing"]
    check("C2 repairing 事件带 attempt=2 字段", repairing_events and repairing_events[0][1]["attempt"] == 2)

    # C3: 三次全失败：…repairing(2)→verifying→repairing(3)→verifying→failed
    events3: list = []
    orch.verify_page_isolated = lambda path: _FakeVerify(False, "自测断言未通过")
    outcome3 = orch.generate_page("为大二学生生成页面，逐步高亮", on_event=_collect_events(events3))
    stages3 = [(p["stage"], p.get("attempt")) for e, p in events3 if e == "stage"]
    check("C3 三次失败：outcome.ok=False", not outcome3.ok and len(outcome3.attempts) == 3)
    check("C3 终态为 failed",
          stages3 == [("clarifying", None), ("generating", None), ("verifying", None),
                      ("repairing", 2), ("verifying", None), ("repairing", 3),
                      ("verifying", None), ("failed", None)], str(stages3))

    # C4: 既有事件（attempt_start/done 等）与 stage 并存，前端兼容
    etypes = {e for e, _ in events}
    check("C4 既有事件未破坏（attempt_start/done 并存）", {"attempt_start", "sandbox_check", "attempt_done", "done", "stage"} <= etypes, str(etypes))

    # C5: on_event=None（CLI/同步 REST 路径）零副作用
    try:
        orch.generate_page("为大二学生生成页面，逐步高亮")
        check("C5 on_event=None 路径正常（无事件不报错）", True)
    except Exception as e:
        check("C5 on_event=None 路径正常（无事件不报错）", False, str(e))

    orch.chat, orch.verify_page_isolated = real_chat, real_verify


# ---------- D.（可选）真 LLM 澄清判断 ----------

def test_real_llm() -> None:
    print("\n[D] 真 LLM 澄清判断（--with-llm；两次调用，各约 1-3 秒）")
    from app.llm.config import get_effective_config

    cfg = get_effective_config()
    print(f"    端点：{cfg.base_url}  模型：{cfg.model}")
    r = orch.clarify_task("帮我做个排序动画")
    print(f"    模糊任务 → need_clarify={r.need_clarify} source={r.source}")
    for q in r.questions:
        print(f"      - {q}")
    r2 = orch.clarify_task("为大二学生生成 Dijkstra 教学页面，逐步高亮松弛操作，图 6 节点 8 条边")
    print(f"    具体任务 → need_clarify={r2.need_clarify} source={r2.source} questions={r2.questions}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-llm", action="store_true", help="附加真 LLM 澄清判断（需 LLM_API_KEY）")
    args = parser.parse_args()

    print("=" * 60)
    print("AgentStage 状态机 + 需求澄清 自测（mock，秒级）")
    print("=" * 60)

    test_stage_enum()
    test_clarify_rules()
    test_stage_events()
    if args.with_llm:
        test_real_llm()

    total = len(CHECKS)
    passed = sum(1 for _, c in CHECKS if c)
    print("\n" + "=" * 60)
    print(f"自测结果：{passed}/{total} 项 PASS")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
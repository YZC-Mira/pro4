"""任务 4 验收脚本：生成编排与自修复循环（对应 PRD 成功标准第 2/4 条）。

测试 A（端到端）：脚本发起"冒泡排序教学页面"任务 → generate_page 完整闭环
  → 断言最终 HTML 通过沙箱四项校验，pages 元数据入库。
测试 B（注错自修复）：取测试 A 的成功页面，人为注入两类典型错误
  （B1 删除/改名 window.__verify__ 自检入口；B2 塞入未定义变量语法错），
  逐一验证 repair_page 能在最多 3 次内自动修复（PRD 成功标准第 4 条）。

用法（backend 目录，激活 .venv 后）：
  $env:LLM_API_KEY="你的key"; python scripts/test_orchestrator.py
可选：--task "自定义教学任务"   --quick（测试 B 只注一种错）

注意：本地模型单轮生成 2-5 分钟，测试 A + 测试 B 全量约 15-40 分钟，请耐心等待。
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.agent import generate_page, repair_page
from app.sandbox import verify_page

REPORT_PATH = Path(__file__).resolve().parents[1] / "任务4编排自修复验收报告.md"
BROKEN_DIR = Path(__file__).resolve().parents[1] / "pages" / "broken_test"


def inject_error_remove_verify(html: str) -> str:
    """注错 B1：改名自检入口（模拟"缺 __verify__"型生成失败）。"""
    return html.replace("window.__verify__", "window.__broken_verify__")


def inject_error_js_typo(html: str) -> str:
    """注错 B2：塞入未定义变量调用（模拟 JS 报错型失败）。"""
    marker = "<script>"
    idx = html.rfind(marker)
    if idx == -1:
        return html + "\n<script>window.__force_typo__();</script>\n"
    insert_at = idx + len(marker)
    return html[:insert_at] + "\n  try { window.__force_typo__(); } catch (e) { throw e; }\n" + html[insert_at:]


def run_repair_case(broken_html: str, name: str, desc: str, task_hint: str) -> dict:
    BROKEN_DIR.mkdir(parents=True, exist_ok=True)
    broken_path = BROKEN_DIR / f"{name}.html"
    broken_path.write_text(broken_html, encoding="utf-8")

    pre = verify_page(broken_path)
    print(f"    注错后校验：{'失败（符合预期）' if not pre.ok else '警告：注入未生效！'}")
    print(f"      摘要：{pre.error_summary[:100]}")
    if pre.ok:
        return {"case": desc, "injected": False, "repaired": False, "outcome": None}

    print(f"    启动自修复循环（最多 3 次）...")
    t0 = time.time()
    outcome = repair_page(broken_path, task=task_hint)
    elapsed = round(time.time() - t0, 1)
    n_attempts = len(outcome.attempts)
    print(f"    修复结果：{'✅ 成功' if outcome.ok else '❌ 失败'}"
          f"（{n_attempts} 次尝试，{elapsed}s，最终版：{outcome.final_html_path}）")
    for a in outcome.attempts:
        print(f"      - 第{a.attempt}次[{a.kind}] stage={a.stage or 'sandbox'} ok={a.ok}"
              + (f" err={a.error[:80]}" if a.error and not a.ok else ""))
    return {"case": desc, "injected": True, "repaired": outcome.ok, "outcome": outcome.to_dict()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="为大一学生生成冒泡排序教学页面：数组长度可调（默认 8 个元素），柱状图可视化，每步高亮正在比较的两个元素，讲解文字说明比较与交换。")
    parser.add_argument("--quick", action="store_true", help="测试 B 只注一种错")
    args = parser.parse_args()

    print("=" * 60)
    print("任务 4 验收：生成编排与自修复循环")
    print("=" * 60)

    # ---------- 测试 A：端到端生成 ----------
    print("\n[测试 A] 端到端生成（含沙箱四项校验，2-5 分钟/轮）...")
    t0 = time.time()
    outcome = generate_page(args.task)
    a_seconds = round(time.time() - t0, 1)
    test_a_pass = outcome.ok
    if test_a_pass:
        final_check = verify_page(outcome.final_html_path)
        test_a_pass = final_check.ok
        print(f"  ✅ 端到端通过（{len(outcome.attempts)} 次尝试，{a_seconds}s）")
        print(f"     最终版：{outcome.final_html_path}")
    else:
        print(f"  ❌ 端到端失败（{len(outcome.attempts)} 次尝试，{a_seconds}s）")
        print(f"     失败摘要：{outcome.error_summary[:150]}")
        print("     注：若为模型偶发失败可重跑本脚本；连续失败则需评估切换模型")

    row = db.get_page(outcome.page_id)
    db_ok = row is not None and row["status"] == ("passed" if outcome.ok else "failed") and row["attempt_count"] == len(outcome.attempts)
    print(f"  pages 元数据入库：{'✅' if db_ok else '❌'}（page_id={outcome.page_id}, status={row['status'] if row else '无'}, attempts={row['attempt_count'] if row else '-'}）")

    good_html = None
    if outcome.ok and outcome.final_html_path:
        good_html = Path(outcome.final_html_path).read_text(encoding="utf-8")

    # ---------- 测试 B：注错自修复 ----------
    results_b = []
    if good_html:
        print("\n[测试 B] 注错自修复（每例修复 1-3 轮，每轮 2-5 分钟）...")
        task_hint = args.task
        print("  [B1] 注错：改名自检入口 window.__verify__（模拟入口缺失型失败）")
        results_b.append(run_repair_case(inject_error_remove_verify(good_html), "b1_no_verify", "B1 缺自检入口", task_hint))
        if not args.quick:
            print("  [B2] 注错：塞入未定义变量调用（模拟 JS 报错型失败）")
            results_b.append(run_repair_case(inject_error_js_typo(good_html), "b2_js_typo", "B2 JS 变量笔误", task_hint))
    else:
        print("\n[测试 B] 跳过（测试 A 未产出成功页面，无基线可注错）")

    test_b_pass = bool(results_b) and all(r["injected"] and r["repaired"] for r in results_b)

    # ---------- 汇总 ----------
    all_pass = test_a_pass and db_ok and test_b_pass
    print("\n" + "=" * 60)
    print(f"测试 A 端到端生成：{'✅ PASS' if test_a_pass else '❌ FAIL'}")
    print(f"pages 元数据入库：{'✅ PASS' if db_ok else '❌ FAIL'}")
    for r in results_b:
        print(f"自修复 {r['case']}：{'✅ PASS（3 次内修复）' if r['repaired'] else '❌ FAIL'}")
    print(f"任务 4 总验收：{'✅ 全部通过' if all_pass else '❌ 存在失败项'}")
    print("=" * 60)

    lines = [
        "# 任务 4：生成编排与自修复循环验收报告",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M')}  模型：{outcome.model}",
        f"- 教学任务：{args.task[:60]}...",
        "",
        f"## 测试 A：端到端生成",
        f"- 结果：{'通过' if test_a_pass else '失败'}（{len(outcome.attempts)} 次尝试，{a_seconds}s）",
        f"- 元数据入库：{'通过' if db_ok else '失败'}（page_id={outcome.page_id}）",
        f"- 最终页面：{outcome.final_html_path}",
        "",
        f"## 测试 B：注错自修复（对应 PRD 成功标准第 4 条）",
    ]
    for r in results_b:
        if r["outcome"]:
            n = len(r["outcome"]["attempts"])
            secs = r["outcome"]["seconds"]
            lines.append(f"- {r['case']}：{'3 次内修复成功' if r['repaired'] else '修复失败'}（{n} 次尝试，{secs}s）")
        else:
            lines.append(f"- {r['case']}：注错未生效（跳过）")
    lines += ["", f"**总验收：{'全部通过' if all_pass else '存在失败项'}**", ""]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入：{REPORT_PATH}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
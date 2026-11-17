"""任务 9：三类算法稳定性测试（完整产品链路，挂机脚本）。

与任务 0（test_llm_output.py，单次生成直测）不同：本测试走 generate_page
完整编排闭环（生成 → 沙箱校验 → 失败自修复最多 3 次），统计的是
**产品真实成功率**——用户在界面上点"发送"背后的那条链路。

测试矩阵：冒泡排序 / Dijkstra / 二分查找 × 每类 N 轮（默认 3）
任务描述均为完整形态（含学段+演示形式，模拟澄清完成后的任务文本）。

统计口径：
- 一次通过率（第 1 次尝试即成功）
- 综合成功率（≤3 次内自修复兜底成功）——PRD 成功标准 2 的真实口径
- 每轮耗时 / 平均耗时；全流程 ≤10 分钟校验（PRD 成功标准 1，含生成+校验）
报告：backend/任务9稳定性测试报告.md（PPT 测试报告素材）

用法（backend 目录，激活 .venv 后）：
  $env:LLM_API_KEY="你的key"; python scripts/test_stability.py
可选：--rounds 3   每类轮数（默认 3，总轮数 = 3 × rounds）
预计耗时：9 轮 × 2-5 分钟 ≈ 30-60 分钟（无人值守，逐轮实时打印进度）。
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.agent.orchestrator import generate_page
from app.llm.config import get_effective_config

TASKS = {
    "冒泡排序": "为大二学生生成冒泡排序教学页面：数组长度可调（默认 8 个元素），柱状图可视化，逐步高亮正在比较的两个元素，讲解文字说明比较与交换过程。",
    "Dijkstra": "为大二学生生成 Dijkstra 最短路径教学页面：小规模图（6 个节点、8 条边，边权随机生成），节点+边的可视化，每步展示当前被松弛的边与 dist 数组变化，讲解松弛操作。",
    "二分查找": "为大一学生生成二分查找教学页面：有序数组（元素个数可调），每步高亮 low/mid/high 指针位置，讲解查找范围的收缩过程，查找目标值可指定。",
}

REPORT_PATH = Path(__file__).resolve().parents[1] / "任务9稳定性测试报告.md"
OUT_DIR = Path(__file__).resolve().parents[1] / "pages" / "stability_test"


def run_round(name: str, task: str, seq: int) -> dict:
    t0 = time.time()
    print(f"\n>>> [{name}] 第 {seq} 轮（完整编排闭环，含自修复兜底）")
    outcome = generate_page(task)
    seconds = round(time.time() - t0, 1)
    n_attempts = len(outcome.attempts)
    first_pass = n_attempts == 1 and outcome.ok

    if outcome.ok:
        detail = outcome.attempts[-1]
        print(f"    [成功] {n_attempts} 次尝试 · {seconds}s · 最终版：{Path(outcome.final_html_path).name}")
        if first_pass:
            print(f"    ↳ 一次通过（无自修复）")
        else:
            fails = [a.error[:60] for a in outcome.attempts[:-1] if a.error]
            for f in fails:
                print(f"    ↳ 前次失败：{f}")
    else:
        print(f"    [失败] {n_attempts} 次尝试均未通过 · {seconds}s")
        print(f"    ↳ 失败原因：{outcome.error_summary[:120]}")

    return {
        "task": name,
        "seq": seq,
        "ok": outcome.ok,
        "first_pass": first_pass,
        "attempts": n_attempts,
        "seconds": seconds,
        "page_id": outcome.page_id,
        "final_html_path": outcome.final_html_path,
        "error_summary": outcome.error_summary if not outcome.ok else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3, help="每类算法的轮数（默认 3）")
    args = parser.parse_args()

    cfg = get_effective_config()
    started = datetime.now()
    print("=" * 64)
    print("任务 9：三类算法稳定性测试（完整产品链路：生成→校验→自修复）")
    print(f"  模型端点：{cfg.base_url}  模型：{cfg.model}")
    print(f"  矩阵：{len(TASKS)} 类 × {args.rounds} 轮 = {len(TASKS) * args.rounds} 轮")
    print(f"  开始时间：{started.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  预计耗时：约 {len(TASKS) * args.rounds * 3} 分钟（无人值守，可离开）")
    print("=" * 64)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()

    records: list[dict] = []
    for name, task in TASKS.items():
        for seq in range(1, args.rounds + 1):
            records.append(run_round(name, task, seq))

    # ---------- 汇总 ----------
    total = len(records)
    ok_count = sum(1 for r in records if r["ok"])
    first_count = sum(1 for r in records if r["first_pass"])
    avg_seconds = round(sum(r["seconds"] for r in records) / total, 1) if total else 0
    max_seconds = max((r["seconds"] for r in records), default=0)
    per_task = {}
    for name in TASKS:
        rs = [r for r in records if r["task"] == name]
        per_task[name] = {
            "ok": sum(1 for r in rs if r["ok"]),
            "first": sum(1 for r in rs if r["first_pass"]),
            "n": len(rs),
            "avg": round(sum(r["seconds"] for r in rs) / len(rs), 1) if rs else 0,
        }

    finished = datetime.now()
    print("\n" + "=" * 64)
    print(f"汇总：综合成功率 {ok_count}/{total} = {ok_count / total:.0%}"
          f"（一次通过 {first_count}/{total} = {first_count / total:.0%}）")
    for name, s in per_task.items():
        print(f"  {name}: 成功 {s['ok']}/{s['n']}（一次通过 {s['first']}）· 平均 {s['avg']}s")
    print(f"  全轮平均 {avg_seconds}s · 最长一轮 {max_seconds}s（≤600s 达标：{'✓' if max_seconds <= 600 else '✗'}）")
    ok_all_first = all(s["first"] >= 1 for s in per_task.values())
    print(f"  每类至少 1 次一次通过（验收标准）：{'✓ 达标' if ok_all_first else '✗ 未达标'}")
    print(f"  结束时间：{finished.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 64)

    # ---------- 报告 ----------
    lines = [
        "# 任务 9：三类算法稳定性测试报告",
        "",
        f"- 时间：{started.strftime('%Y-%m-%d %H:%M')} ~ {finished.strftime('%H:%M')}（历时 {round((finished - started).total_seconds() / 60, 1)} 分钟）",
        f"- 模型：`{cfg.model}` @ `{cfg.base_url}`",
        f"- 链路：完整产品编排（生成 → 沙箱四项校验 → 自修复 ≤3 次）",
        f"- 矩阵：{len(TASKS)} 类算法 × {args.rounds} 轮 = {total} 轮",
        "",
        "| 算法 | 轮次 | 结果 | 尝试次数 | 耗时 | 备注 |",
        "|---|---|---|---|---|---|",
    ]
    for r in records:
        note = "一次通过" if r["first_pass"] else (f"自修复第 {r['attempts']} 次成功" if r["ok"] else r["error_summary"][:40])
        lines.append(
            f"| {r['task']} | {r['seq']} | {'✅ 成功' if r['ok'] else '❌ 失败'} | {r['attempts']} | {r['seconds']}s | {note} |"
        )
    lines += [
        "",
        f"**综合成功率：{ok_count}/{total} = {ok_count / total:.0%}**（一次通过率 {first_count}/{total} = {first_count / total:.0%}）",
        "",
        "逐算法："
        + "；".join(
            f"{k} 成功 {s['ok']}/{s['n']}（一次通过 {s['first']}，平均 {s['avg']}s）"
            for k, s in per_task.items()
        ),
        "",
        f"全轮平均 {avg_seconds}s，最长一轮 {max_seconds}s（PRD 标准 ≤10 分钟/轮：{'达标' if max_seconds <= 600 else '超标'}）",
        f"每类至少 1 次一次通过（验收标准）：{'达标' if ok_all_first else '未达标'}",
        "",
        "生成样本：`backend/pages/`（page_id 对应 HTML 文件）",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入：{REPORT_PATH}")

    (OUT_DIR / "records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"原始数据：{OUT_DIR / 'records.json'}")
    return 0 if ok_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
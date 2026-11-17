"""任务 0：三件套输出能力实测脚本（第一优先验证项①）。

实测内容：本地模型能否稳定输出"动画 + 自测断言 + window.__verify__ 自检入口"三件套。
- 三类演示算法（冒泡排序 / Dijkstra / 二分查找）× 默认各 2 轮；
- 每轮：调 LLM 生成 → 提取 HTML → 截断检测 → 沙箱四项客观校验；
- 汇总：三件套完整率、一次通过率、截断情况、耗时；
- 产出 markdown 报告（留作 PPT 测试报告素材）。

用法（backend 目录，激活 .venv 后）：
  PowerShell:  $env:LLM_API_KEY="你的key"; python scripts/test_llm_output.py
  可选参数：  --rounds 2   每类算法的轮数（默认 2）
前置：任务 2/3 已验收（llm + playwright chromium 就绪）。
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import REPAIR_SYSTEM_PROMPT, SPEC_SYSTEM_PROMPT, SPEC_USER_TEMPLATE, extract_html, looks_truncated
from app.agent.orchestrator import _chat_collect
from app.llm import get_effective_config
from app.sandbox import verify_page

TASKS = {
    "冒泡排序": "为大一学生生成冒泡排序教学页面：数组长度可调（默认 8 个元素），柱状图可视化，每步高亮正在比较的两个元素，讲解文字说明比较与交换。",
    "Dijkstra": "为大二学生生成 Dijkstra 最短路径教学页面：小规模图（6 个节点、8 条边，边权可随机生成），节点+边的可视化，每步展示当前被松弛的边与 dist 数组变化，讲解松弛操作。",
    "二分查找": "为大一学生生成二分查找教学页面：有序数组（元素个数可调），每步高亮 low/mid/high 指针位置，讲解查找范围的收缩，查找目标值可指定。",
}

REPORT_PATH = Path(__file__).resolve().parents[1] / "任务0三件套实测报告.md"


def run_one(task_name: str, task_desc: str, out_dir: Path, seq: int) -> dict:
    """跑一轮生成+校验，返回该轮记录。"""
    t0 = time.time()
    record = {"task": task_name, "seq": seq}

    messages = [
        {"role": "system", "content": SPEC_SYSTEM_PROMPT},
        {"role": "user", "content": SPEC_USER_TEMPLATE.format(task=task_desc)},
    ]
    try:
        raw = _chat_collect(get_effective_config(), messages, f"[{task_name}] 第 {seq} 轮 LLM 流式生成")
    except Exception as e:
        ename = type(e).__name__
        stage = "llm_timeout" if "Timeout" in ename or "timeout" in str(e).lower() else "llm_call"
        record.update(ok=False, stage=stage, error=f"{ename}: {e}", seconds=round(time.time() - t0, 1))
        return record

    record["llm_seconds"] = round(time.time() - t0, 1)
    print("      提取 HTML ...", flush=True)
    html = extract_html(raw)
    record["truncated"] = looks_truncated(html)

    if html is None:
        record.update(ok=False, stage="extract", error="输出中找不到完整 HTML 文档")
        return record

    html_path = out_dir / f"{task_name}_r{seq}.html"
    html_path.write_text(html, encoding="utf-8")
    record["html_path"] = str(html_path)
    record["html_lines"] = html.count("\n") + 1

    print(f"      沙箱校验中（headless Chromium 四项校验，约 10 秒）：{html_path.name}", flush=True)
    result = verify_page(html_path)
    record["verify"] = result.to_dict()
    record["ok"] = result.ok
    record["seconds"] = round(time.time() - t0, 1)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=2, help="每类算法的生成轮数（默认 2）")
    args = parser.parse_args()

    cfg = get_effective_config()
    print("=" * 60)
    print("任务 0：三件套输出能力实测")
    print(f"  模型端点：{cfg.base_url}  模型：{cfg.model}")
    print(f"  max_tokens：{cfg.max_tokens}  temperature：{cfg.temperature}")
    print(f"  任务：{len(TASKS)} 类 × {args.rounds} 轮")
    print("=" * 60)

    if not cfg.api_key or cfg.api_key == "EMPTY":
        print("[警告] LLM_API_KEY 未设置（内网端点可继续，正式端点会 401）\n")

    out_dir = Path(__file__).resolve().parents[1] / "pages" / "trinity_test"
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    for task_name, desc in TASKS.items():
        for seq in range(1, args.rounds + 1):
            print(f"\n>>> [{task_name}] 第 {seq}/{args.rounds} 轮生成中 ...")
            r = run_one(task_name, desc, out_dir, seq)
            records.append(r)
            if r["ok"]:
                v = r["verify"]
                print(f"    [PASS] 一次通过（LLM {r['llm_seconds']}s / 全程 {r['seconds']}s，"
                      f"HTML {r['html_lines']} 行，断言 {len(v['test_case_results'])} 条全过）")
            else:
                stage = r.get("stage", "sandbox")
                err = r.get("error") or r.get("verify", {}).get("error_summary", "")
                print(f"    [FAIL] 失败于 {stage} 阶段：{err[:120]}")

    # ---------- 汇总 ----------
    total = len(records)
    passed = sum(1 for r in records if r["ok"])
    truncated = sum(1 for r in records if r.get("truncated"))
    timeouts = sum(1 for r in records if r.get("stage") == "llm_timeout")
    per_task = {}
    for name in TASKS:
        rs = [r for r in records if r["task"] == name]
        per_task[name] = (sum(1 for r in rs if r["ok"]), len(rs))

    print("\n" + "=" * 60)
    print(f"汇总：一次通过 {passed}/{total}"
          + (f"（客户端超时 {timeouts} 次 / 截断 {truncated} 次）" if (timeouts or truncated) else "（无截断）"))
    for name, (p, n) in per_task.items():
        print(f"  {name}: {p}/{n}")
    rate = passed / total if total else 0
    print(f"三件套完整率（一次通过率）：{rate:.0%}")
    if timeouts > total - passed:
        print("结论：失败主因是客户端超时（非模型质量）——timeout 默认已调大到 600s；")
        print("      若本次仍超时，设 $env:LLM_TIMEOUT=\"900\" 再复测；超时消除后再评估三件套质量。")
    elif rate >= 0.67:
        print("结论：本地模型三件套输出可用（≥67%），生成规范 v1 可定稿，任务 4 按本地模型实现。")
    elif rate >= 0.34:
        print("结论：成功率中等——先调整生成规范再复测，仍不稳则任务 4 接入自修复后综合评估。")
    else:
        print("结论：本地模型不稳定（<34%）——决策：优先调 prompt；复测仍低则切换华为云 MaaS（改配置即可）。")
    print("=" * 60)

    # ---------- 写报告（PPT 测试报告素材） ----------
    lines = [
        "# 任务 0：三件套输出能力实测报告",
        "",
        f"- 日期：2026-10-24（生成时间戳 {time.strftime('%Y-%m-%d %H:%M')}）",
        f"- 模型端点：`{cfg.base_url}`  模型：`{cfg.model}`  max_tokens：{cfg.max_tokens}",
        f"- 生成规范：v1（backend/app/agent/spec.py）",
        f"- 实测矩阵：{len(TASKS)} 类算法 × {args.rounds} 轮 = {total} 次",
        "",
        "| 算法 | 轮次 | 结果 | HTML行数 | 断言条数 | LLM耗时 | 全程耗时 | 失败摘要 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        v = r.get("verify", {})
        n_cases = len(v.get("test_case_results", []))
        summary = (r.get("error") or v.get("error_summary", ""))[:60].replace("|", "/")
        lines.append(
            f"| {r['task']} | {r['seq']} | {'✅通过' if r['ok'] else '❌失败'}"
            f"{'（截断）' if r.get('truncated') else ''} | {r.get('html_lines', '-')} | {n_cases} |"
            f" {r.get('llm_seconds', '-')}s | {r.get('seconds', '-')}s | {summary} |"
        )
    lines += [
        "",
        f"**一次通过率：{passed}/{total} = {rate:.0%}**（截断 {truncated} 次）",
        "",
        "逐算法：" + "；".join(f"{k} {p}/{n}" for k, (p, n) in per_task.items()),
        "",
        "**三件套判定**：" + ("动画 + 自测断言 + window.__verify__ 自检入口三件套输出"
                           + ("稳定" if rate >= 0.67 else "中等" if rate >= 0.34 else "不稳定")),
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入：{REPORT_PATH}")
    print(f"生成页样本目录：{out_dir}")

    (out_dir / "records.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
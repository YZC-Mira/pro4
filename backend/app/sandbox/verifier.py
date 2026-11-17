"""Playwright 沙箱验证模块（技术选型第 4 节）。

验证与修复职责分离：
- 生成侧（智能体）决定"如何验证"：随动画生成自测断言用例 window.__TEST_CASES__，
  并把算法核心逻辑暴露为标准自检入口 window.__verify__；
- 执行侧（本模块）决定"对不对"：只做客观执行与采集，不含任何算法级判断，
  也不依赖任何预设校验脚本。

通过标准（四项全部满足，缺一即失败）：
1. 无 JS 报错（pageerror + console.error）；
2. 关键控件存在（生成规范约定 ID：btn-prev / btn-step / btn-next）；
3. 标准自检入口 window.__verify__ 存在且可调用（缺失视为生成失败）；
4. 自测断言全部通过（__TEST_CASES__ 用例逐条执行并比对）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# —— 生成规范硬约定（与任务 4 的生成规范 prompt 保持一致）——
VERIFY_ENTRY = "window.__verify__"
TEST_CASES_ENTRY = "window.__TEST_CASES__"
REQUIRED_CONTROL_IDS = ("btn-prev", "btn-step", "btn-next")

NAV_TIMEOUT_MS = 10_000   # domcontentloaded 超时（选型 4 节）
SETTLE_DELAY_MS = 500     # domcontentloaded 后静默等待，让页面初始化脚本跑完

_JS_RUN_TESTS = """() => {
    const results = [];
    const cases = window.__TEST_CASES__ || [];
    for (const tc of cases) {
        let actual = null;
        let error = null;
        try {
            actual = window.__verify__(tc.input);
        } catch (e) {
            error = String(e && e.message ? e.message : e);
        }
        let pass = false;
        if (error === null) {
            try {
                pass = JSON.stringify(actual) === JSON.stringify(tc.expected);
            } catch (e) {
                pass = false;
            }
        }
        results.push({ input: tc.input, expected: tc.expected, actual: actual, error: error, pass: pass });
    }
    return results;
}"""


@dataclass
class VerifyResult:
    """结构化校验结果；error_summary 供自修复循环回传 LLM 使用。"""

    ok: bool = False
    js_errors: list[str] = field(default_factory=list)
    missing_controls: list[str] = field(default_factory=list)
    verify_entry_exists: bool = False
    has_test_cases: bool = False
    test_case_results: list[dict] = field(default_factory=list)
    timed_out: bool = False
    error_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "js_errors": self.js_errors,
            "missing_controls": self.missing_controls,
            "verify_entry_exists": self.verify_entry_exists,
            "has_test_cases": self.has_test_cases,
            "test_case_results": self.test_case_results,
            "timed_out": self.timed_out,
            "error_summary": self.error_summary,
        }


def _finalize(result: VerifyResult) -> VerifyResult:
    """汇总四项标准，生成自修复用的中文错误摘要。"""
    problems: list[str] = []
    if result.timed_out:
        problems.append("页面加载超时：10 秒内未触发 domcontentloaded")
    if result.js_errors:
        shown = "；".join(result.js_errors[:5])
        more = f"（另有 {len(result.js_errors) - 5} 条）" if len(result.js_errors) > 5 else ""
        problems.append(f"JS 报错：{shown}{more}")
    if result.missing_controls:
        problems.append(
            "缺少关键控件按钮：" + "、".join(result.missing_controls)
            + "（生成规范约定 ID：btn-prev / btn-step / btn-next）"
        )
    if not result.verify_entry_exists:
        problems.append(f"缺少标准自检入口 {VERIFY_ENTRY}（生成规范硬约定，必须暴露）")
    elif not result.has_test_cases:
        problems.append(f"缺少自测断言用例 {TEST_CASES_ENTRY}（生成规范要求至少 1 条）")
    else:
        failed = [t for t in result.test_case_results if not t.get("pass")]
        if failed:
            detail = "；".join(
                f"输入 {t.get('input')!r} 期望 {t.get('expected')!r} 实际 {t.get('actual')!r}"
                + (f"（执行异常：{t.get('error')}）" if t.get("error") else "")
                for t in failed[:3]
            )
            problems.append(
                f"自测断言未通过（{len(failed)}/{len(result.test_case_results)} 条失败）：{detail}"
            )
    result.ok = not problems
    result.error_summary = (
        "；".join(problems)
        if problems
        else "校验通过：无 JS 报错，控件齐全，自检入口可调用，断言全部通过"
    )
    return result


def verify_page(html_path: str | Path) -> VerifyResult:
    """在 headless Chromium 中打开生成页并执行四项客观校验。"""
    result = VerifyResult()
    path = Path(html_path)
    if not path.exists():
        result.error_summary = f"生成页文件不存在：{path}"
        return result

    url = path.resolve().as_uri()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()

                def _on_pageerror(err):
                    result.js_errors.append(f"pageerror: {err}")

                def _on_console(msg):
                    if msg.type == "error":
                        result.js_errors.append(f"console.error: {msg.text}")

                page.on("pageerror", _on_pageerror)
                page.on("console", _on_console)

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    result.timed_out = True

                page.wait_for_timeout(SETTLE_DELAY_MS)

                if not result.timed_out:
                    # 标准 3：自检入口存在且为函数
                    result.verify_entry_exists = page.evaluate(
                        f"typeof {VERIFY_ENTRY} === 'function'"
                    )
                    # 标准 2：关键控件
                    for cid in REQUIRED_CONTROL_IDS:
                        if not page.evaluate(f"!!document.getElementById('{cid}')"):
                            result.missing_controls.append(cid)
                    # 标准 4：自测断言（有入口才有用例可跑）
                    if result.verify_entry_exists:
                        result.has_test_cases = page.evaluate(
                            f"Array.isArray({TEST_CASES_ENTRY}) && {TEST_CASES_ENTRY}.length > 0"
                        )
                        if result.has_test_cases:
                            result.test_case_results = page.evaluate(_JS_RUN_TESTS)
            finally:
                browser.close()
    except PlaywrightError as e:
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            result.error_summary = "Chromium 未安装：请先在 backend 目录执行 playwright install chromium"
        else:
            result.error_summary = f"沙箱启动失败：{msg}"
        return result

    return _finalize(result)
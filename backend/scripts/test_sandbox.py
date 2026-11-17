"""沙箱验证模块测试脚本（任务 3 验收标准）。

构造 4 个典型页面 fixture，验证 VerifyResult 的判定与错误摘要：
1. good_page        —— 四项标准全满足 → 通过
2. js_error_page    —— 含运行时 JS 报错 → 失败 + 摘要含"JS 报错"
3. no_verify_page   —— 缺 window.__verify__ → 失败 + 摘要含"标准自检入口"
4. assert_fail_page —— 断言不通过 → 失败 + 摘要含期望/实际对比

用法（backend 目录，激活 .venv 后）：
  python scripts/test_sandbox.py
前置：已执行 pip install -r requirements.txt 与 playwright install chromium
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sandbox import verify_page  # noqa: E402

BASE_HTML = """<!doctype html>
<html lang="zh">
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
  <div id="array-view">排序演示</div>
  <button id="btn-prev">上一步</button>
  <button id="btn-step">单步执行</button>
  <button id="btn-next">下一步</button>
  <script>
{script}
  </script>
</body>
</html>"""

SCRIPT_GOOD = """
    function bubbleSort(arr) {
      const a = arr.slice();
      for (let i = 0; i < a.length - 1; i++)
        for (let j = 0; j < a.length - 1 - i; j++)
          if (a[j] > a[j + 1]) { const t = a[j]; a[j] = a[j + 1]; a[j + 1] = t; }
      return a;
    }
    window.__verify__ = (input) => bubbleSort(input);
    window.__TEST_CASES__ = [
      { input: [5, 2, 9, 1], expected: [1, 2, 5, 9] },
      { input: [3, 3, 1], expected: [1, 3, 3] },
      { input: [], expected: [] }
    ];
    document.getElementById('array-view').textContent = '冒泡排序演示就绪';
"""

SCRIPT_JS_ERROR = SCRIPT_GOOD + "\n    null.notExist;\n"

SCRIPT_NO_VERIFY = """
    document.getElementById('array-view').textContent = '缺少自检入口的页面';
"""

SCRIPT_ASSERT_FAIL = """
    window.__verify__ = (input) => input.slice().reverse();
    window.__TEST_CASES__ = [
      { input: [5, 2, 9, 1], expected: [1, 2, 5, 9] },
      { input: [1, 2], expected: [1, 2] }
    ];
    document.getElementById('array-view').textContent = '断言必失败演示';
"""

CASES = [
    ("good_page", "正确页面", SCRIPT_GOOD,
     lambda r: r.ok),
    ("js_error_page", "含 JS 报错", SCRIPT_JS_ERROR,
     lambda r: not r.ok and "JS 报错" in r.error_summary),
    ("no_verify_page", "缺自检入口", SCRIPT_NO_VERIFY,
     lambda r: not r.ok and "标准自检入口" in r.error_summary),
    ("assert_fail_page", "断言失败", SCRIPT_ASSERT_FAIL,
     lambda r: not r.ok and "自测断言" in r.error_summary and "实际" in r.error_summary),
]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="algoagent_sandbox_test_"))
    all_pass = True
    print("fixture 目录：", tmp)
    print()
    for name, desc, script, check in CASES:
        html_path = tmp / f"{name}.html"
        html_path.write_text(BASE_HTML.format(title=desc, script=script), encoding="utf-8")
        r = verify_page(html_path)
        passed = check(r)
        all_pass = all_pass and passed
        tag = "PASS" if passed else "FAIL"
        print(f"[{tag}] {desc}（{name}）")
        print(f"       ok={r.ok} 控件缺失={r.missing_controls} 自检入口={r.verify_entry_exists} "
              f"用例数={len(r.test_case_results)}")
        print(f"       摘要：{r.error_summary}")
        print()
    print("任务 3 沙箱验证模块：" + ("全部验收通过" if all_pass else "存在失败项"))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
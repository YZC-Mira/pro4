"""从 LLM 原始输出中提取 HTML 文档（任务 0 / 任务 4 共用）。"""

from __future__ import annotations

import re

_HTML_FENCE = re.compile(r"```(?:html)?\s*\n(.*?)```", re.DOTALL)
_DOCTYPE = re.compile(r"<!DOCTYPE html>", re.IGNORECASE)


def extract_html(raw: str) -> str | None:
    """提取策略（逐级降级）：
    1. 若有 Markdown 围栏，取最长的一段围栏内容；
    2. 否则取 <!DOCTYPE html> 起到 </html> 止的子串；
    3. 找不到完整结构返回 None（调用方按生成失败处理）。
    """
    if not raw or not raw.strip():
        return None

    fences = _HTML_FENCE.findall(raw)
    if fences:
        best = max(fences, key=len).strip()
        if _DOCTYPE.search(best):
            return best

    m = re.search(r"<!DOCTYPE html>.*?</html>", raw, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(0)

    return None


def looks_truncated(html: str | None) -> bool:
    """截断检测：完整文档必须以 </html> 结尾（容忍尾部空白）。"""
    if not html:
        return True
    return not html.rstrip().endswith("</html>")

def extract_json(raw: str) -> dict | None:
    """从 LLM 原始输出中提取 JSON 对象（澄清轮等轻量结构化输出用）。

    容错策略：优先取 ```json 围栏；否则取首个平衡的 {...} 子串。
    解析失败返回 None（调用方兜底）。
    """
    if not raw or not raw.strip():
        return None

    m = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
    candidates = [m.group(1).strip()] if m else []
    first = raw.find("{")
    if first != -1:
        candidates.append(raw[first:])
    for cand in candidates:
        brace = cand.find("{")
        if brace == -1:
            continue
        depth = 0
        for i in range(brace, len(cand)):
            if cand[i] == "{":
                depth += 1
            elif cand[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        import json

                        return json.loads(cand[brace : i + 1])
                    except Exception:
                        break
    return None
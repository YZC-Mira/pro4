"""LLM 连通性测试脚本（任务 2 / 选型第 3 节验证项）。

测试目标：本地 DeepSeek（OpenAI 兼容端点）
  base_url = http://172.31.2.220:31558/v1
  model    = deepseek-v4-flash
  api_key  = 从环境变量 LLM_API_KEY 读取

用法（backend 目录下，激活 .venv 后）：
  PowerShell:  $env:LLM_API_KEY="你的key"; python scripts/test_llm_connectivity.py
  git-bash:    LLM_API_KEY=你的key python scripts/test_llm_connectivity.py

  注：若端点为内网部署、不校验 key，可设任意值（如 sk-local）。
  通过环境变量 LLM_BASE_URL / LLM_MODEL 可临时覆盖端点与模型（验证优先级链路）。
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import chat, get_effective_config


def main() -> int:
    if not os.environ.get("LLM_API_KEY"):
        print("[提示] 未设置 LLM_API_KEY（内网端点可设任意值，如 sk-local），继续尝试 ...")

    cfg = get_effective_config()
    print("=" * 50)
    print("当前生效配置（yaml < 环境变量 < 数据库[占位]）：")
    print(f"  base_url    = {cfg.base_url}")
    print(f"  model       = {cfg.model}")
    print(f"  api_key     = {'已设置' if cfg.api_key else '未设置(EMPTY 兜底)'}")
    print(f"  temperature = {cfg.temperature}")
    print(f"  max_tokens  = {cfg.max_tokens}")
    print(f"  vision      = {cfg.capabilities.vision}")
    print(f"  streaming   = {cfg.capabilities.streaming}")
    print("=" * 50)

    messages = [{"role": "user", "content": "连通性测试：请只回复两个字：OK"}]

    print("\n[1/2] 非流式请求 ...")
    t0 = time.time()
    try:
        text = chat(messages, stream=False)
        print(f"  响应内容：{text!r}")
        print(f"  耗时：{time.time() - t0:.1f}s")
        print("  => PASS")
    except Exception as e:
        print(f"  => FAIL：{type(e).__name__}: {e}")
        return 1

    print("\n[2/2] 流式请求 ...")
    t0 = time.time()
    try:
        chunks = list(chat(messages, stream=True))
        full = "".join(chunks)
        print(f"  增量块数：{len(chunks)}")
        print(f"  拼接响应：{full!r}")
        print(f"  耗时：{time.time() - t0:.1f}s")
        print("  => PASS")
    except Exception as e:
        print(f"  => FAIL：{type(e).__name__}: {e}")
        return 1

    print("\n连通性测试全部通过，LLM 统一接入层（任务 2）验收完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""OpenAI Chat Completions 统一客户端（选型第 3 节）。

全系统只通过本模块调用大模型；切换模型 = 改配置，不改代码。
chat() 默认每次调用前读取生效配置（下次请求即生效，选型 3.6 节）；
调用方也可显式传入 LLMConfig 快照（orchestrator 用它在同一自修复
循环内锁定同一模型，选型第 4 节）。
"""

from collections.abc import Generator
from typing import Optional, Union

from openai import OpenAI

from .config import LLMConfig, get_effective_config

_MESSAGES = list  # list[dict]：{"role": ..., "content": ...}


def _build_client(base_url: str, api_key: str, timeout: float) -> OpenAI:
    # api_key 缺省时用 "EMPTY"：OpenAI 兼容端点（如 vLLM/本地部署）的通行占位惯例
    # timeout 走配置（默认 600s）：长 HTML 生成在本地推理端点上可达数分钟
    return OpenAI(
        base_url=base_url,
        api_key=api_key or "EMPTY",
        timeout=timeout,
    )


def chat(
    messages: _MESSAGES,
    stream: bool = False,
    config: Optional[LLMConfig] = None,
) -> Union[str, Generator[str, None, None]]:
    """调用 Chat Completions。

    - stream=False：返回完整文本（str）
    - stream=True：返回生成器，逐块 yield 文本增量（供 SSE 转发）
    - config=None：每次现读生效配置；传入快照：整次调用锁定该配置
    """
    cfg = config or get_effective_config()
    if not cfg.base_url or not cfg.model:
        raise RuntimeError("LLM 配置不完整：base_url / model 为空，请检查 config.yaml 与环境变量")

    client = _build_client(cfg.base_url, cfg.api_key, cfg.timeout)
    kwargs = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "stream": stream,
    }

    if not stream:
        try:
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content
        finally:
            client.close()

    def _stream_gen() -> Generator[str, None, None]:
        try:
            resp = client.chat.completions.create(**kwargs)
            for chunk in resp:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        finally:
            client.close()

    return _stream_gen()
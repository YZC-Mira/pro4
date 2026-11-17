"""LLM 统一配置加载（选型 3.2 / 3.6 节）。

优先级：数据库（占位）> 环境变量 > config.yaml（出厂默认）。
每次调用 get_effective_config() 重新读取生效配置，即"下次请求即生效"。
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

BACKEND_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = BACKEND_DIR / "config.yaml"

_ENV_VAR_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass
class Capabilities:
    vision: bool = False
    streaming: bool = True


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.3
    max_tokens: int = 8192
    timeout: float = 600.0
    capabilities: Capabilities = field(default_factory=Capabilities)


def get_db_overrides() -> dict:
    """数据库覆盖项（settings 表，F12 运行时模型配置——2026-10-26 落地）。

    最高优先级层：DB > 环境变量 > config.yaml。每次调用实时读表
    （get_effective_config 每请求重读 → 改配置下次请求即生效，无需重启）。
    仅返回表内存在的键；api_key 明文仅供 client 内部使用（接口层负责掩码）。
    """
    try:
        from .. import db

        raw = db.get_all_settings()
    except Exception:
        return {}  # DB 不可用（如 CLI 环境未初始化）时回落到 env/yaml

    overrides: dict = {}
    if raw.get("base_url"):
        overrides["base_url"] = raw["base_url"]
    if raw.get("api_key"):
        overrides["api_key"] = raw["api_key"]
    if raw.get("model"):
        overrides["model"] = raw["model"]
    if raw.get("temperature"):
        try:
            overrides["temperature"] = float(raw["temperature"])
        except ValueError:
            pass
    if raw.get("max_tokens"):
        try:
            overrides["max_tokens"] = int(raw["max_tokens"])
        except ValueError:
            pass
    if raw.get("timeout"):
        try:
            overrides["timeout"] = float(raw["timeout"])
        except ValueError:
            pass
    return overrides


def _load_yaml_defaults() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("llm", {}) or {}


def _resolve_placeholders(value):
    """把 "${VAR}" 形式的占位符解析为对应环境变量值。"""
    if isinstance(value, dict):
        return {k: _resolve_placeholders(v) for k, v in value.items()}
    if isinstance(value, str):
        m = _ENV_VAR_PATTERN.match(value.strip())
        if m:
            return os.environ.get(m.group(1), "")
    return value


def _parse_bool(raw: str):
    lowered = raw.strip().lower()
    if lowered in ("true", "1", "yes", "on"):
        return True
    if lowered in ("false", "0", "no", "off"):
        return False
    return None


def _env_overrides() -> dict:
    overrides: dict = {}
    scalar_map = {
        "LLM_BASE_URL": ("base_url", str),
        "LLM_API_KEY": ("api_key", str),
        "LLM_MODEL": ("model", str),
        "LLM_TEMPERATURE": ("temperature", float),
        "LLM_MAX_TOKENS": ("max_tokens", int),
        "LLM_TIMEOUT": ("timeout", float),
    }
    for env_name, (key, cast) in scalar_map.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        try:
            overrides[key] = cast(raw)
        except (TypeError, ValueError):
            continue
    for env_name, cap_key in (("LLM_VISION", "vision"), ("LLM_STREAMING", "streaming")):
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        parsed = _parse_bool(raw)
        if parsed is not None:
            overrides.setdefault("capabilities", {})[cap_key] = parsed
    return overrides


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        elif v is not None:
            result[k] = v
    return result


def get_effective_config() -> LLMConfig:
    """返回当前生效配置：yaml 默认 < 环境变量 < 数据库（占位）。"""
    merged = _load_yaml_defaults()
    merged = _resolve_placeholders(merged)
    merged = _deep_merge(merged, _env_overrides())
    merged = _deep_merge(merged, get_db_overrides())
    caps = merged.get("capabilities") or {}
    return LLMConfig(
        base_url=str(merged.get("base_url") or ""),
        api_key=str(merged.get("api_key") or ""),
        model=str(merged.get("model") or ""),
        temperature=float(merged.get("temperature", 0.3)),
        max_tokens=int(merged.get("max_tokens", 8192)),
        timeout=float(merged.get("timeout", 600.0)),
        capabilities=Capabilities(
            vision=bool(caps.get("vision", False)),
            streaming=bool(caps.get("streaming", True)),
        ),
    )
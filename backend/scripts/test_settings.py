"""F12 模型配置自测（mock LLM，秒级）：settings 落库 + 优先级链 + 接口守卫。

覆盖：
A. settings CRUD（set/get/delete/get_all，含 UPSERT）；
B. 优先级链：DB > env > yaml（get_effective_config 实时反映 DB 修改——下次请求即生效）；
C. GET /api/admin/settings：生效配置 + 来源标注 + api_key 掩码；
D. PUT：留空不覆盖语义（api_key 留空不动）；
E. DELETE：清除覆盖回落 env/yaml；
F. 测试连接接口（mock chat 成功/失败两路）；
G. admin 守卫（普通用户 403）。

用法（backend 目录，激活 .venv 后）：python scripts/test_settings.py
"""

import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-settings")
os.environ.setdefault("ALGOAGENT_DATA_DIR", tempfile.mkdtemp(prefix="algoagent_set_test_"))
os.environ.setdefault("ALGOAGENT_PAGES_DIR", tempfile.mkdtemp(prefix="algoagent_set_pages_"))

from fastapi.testclient import TestClient  # noqa: E402


def _install_fake_deps_if_needed() -> None:
    try:
        import openai  # noqa: F401
        from playwright.sync_api import Error  # noqa: F401
        return
    except Exception:
        pass
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


_install_fake_deps_if_needed()

from app import db  # noqa: E402
from app.agent import orchestrator as orch  # noqa: E402
from app.llm.config import get_effective_config  # noqa: E402
from app.main import app  # noqa: E402

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"——{detail}" if detail and not cond else ""))


def main() -> int:
    db.init_db()

    # mock chat：测试连接接口用
    def _fake_chat(messages, stream=False, config=None):
        if stream:
            return iter(["OK"])
        return "OK"

    orch.chat = _fake_chat

    print("=" * 60)
    print("F12 模型配置自测（mock LLM，秒级）")
    print("=" * 60)

    # ---------- A. settings CRUD ----------
    print("\n[A] settings CRUD")
    db.set_setting("base_url", "https://api.example.com/v1")
    assert db.get_setting("base_url") == "https://api.example.com/v1"
    db.set_setting("base_url", "https://api2.example.com/v1")  # UPSERT 覆盖
    assert db.get_setting("base_url") == "https://api2.example.com/v1"
    db.set_setting("model", "Test-Model-X")
    db.set_setting("api_key", "sk-abcdef1234567890")
    all_settings = db.get_all_settings()
    assert all_settings["model"] == "Test-Model-X"
    db.delete_setting("model")
    assert db.get_setting("model") is None
    check("A1 set/get/delete/get_all + UPSERT", True)

    # ---------- B. 优先级链 ----------
    print("\n[B] 优先级链：DB > env > yaml（下次请求即生效）")
    db.delete_setting("base_url")
    db.delete_setting("api_key")
    os.environ["LLM_API_KEY"] = "sk-from-env-key"
    cfg = get_effective_config()
    env_base = cfg.base_url
    check("B1 无 DB 覆盖时 env/yaml 生效（api_key=env 值）", cfg.api_key == "sk-from-env-key")

    db.set_setting("base_url", "https://db-override.example.com/v1")
    db.set_setting("api_key", "sk-db-override-key-9999")
    cfg2 = get_effective_config()
    check("B2 DB 覆盖 base_url（优先于 yaml）", cfg2.base_url == "https://db-override.example.com/v1" and cfg2.base_url != env_base)
    check("B3 DB 覆盖 api_key（优先于 env）", cfg2.api_key == "sk-db-override-key-9999")
    check("B4 未覆盖字段回落（model 来自 yaml/env 默认）", cfg2.model != "")

    db.delete_setting("base_url")
    db.delete_setting("api_key")
    cfg3 = get_effective_config()
    check("B5 删除覆盖后回落 env/yaml", cfg3.base_url == env_base and cfg3.api_key == "sk-from-env-key")

    # ---------- C-G. HTTP 接口 ----------
    print("\n[C-G] HTTP 接口")
    with TestClient(app) as client:
        r = client.post("/api/auth/register", json={"username": "setadmin", "password": "pass123456"})
        token_admin = r.json()["token"]
        auth_admin = {"Authorization": f"Bearer {token_admin}"}
        r = client.post("/api/auth/register", json={"username": "setuser", "password": "pass123456"})
        token_user = r.json()["token"]
        auth_user = {"Authorization": f"Bearer {token_user}"}

        r = client.get("/api/admin/settings", headers=auth_user)
        check("G1 普通用户 403", r.status_code == 403, f"code={r.status_code}")

        db.set_setting("api_key", "sk-abcdef1234567890")
        r = client.get("/api/admin/settings", headers=auth_admin)
        body = r.json()
        check("C1 GET 200 含 effective/sources/db_overrides", r.status_code == 200
              and "effective" in body and "sources" in body and "db_overrides" in body)
        check("C2 api_key 掩码（不明文回传）", "abcdef" not in str(body.get("db_overrides", {}))
              and "1234" not in str(body.get("db_overrides", {}).get("api_key", ""))
              and body["db_overrides"].get("api_key", "").startswith("sk-a"))
        check("C3 来源标注 db", body["sources"].get("api_key") == "db")

        r = client.put("/api/admin/settings", headers=auth_admin,
                       json={"base_url": "https://new.example.com/v1", "api_key": "", "model": ""})
        check("D1 PUT 空字段不覆盖（changed 仅 base_url）", r.status_code == 200
              and r.json()["changed"] == ["base_url"])
        check("D2 api_key 留空保持原值", db.get_setting("api_key") == "sk-abcdef1234567890")
        check("D3 新配置即生效", get_effective_config().base_url == "https://new.example.com/v1")

        r = client.post("/api/admin/settings/test", headers=auth_admin,
                        json={"base_url": "", "model": ""})
        check("F1 测试连接成功（回落当前生效配置）", r.status_code == 200 and r.json()["ok"] is True
              and r.json()["reply"] == "OK")

        # mock 失败路径
        def _boom(messages, stream=False, config=None):
            raise RuntimeError("connection refused")

        orch.chat = _boom
        r = client.post("/api/admin/settings/test", headers=auth_admin,
                        json={"base_url": "https://bad.example.com/v1"})
        check("F2 测试连接失败返回错误信息", r.json()["ok"] is False and "connection refused" in r.json()["error"])
        orch.chat = _fake_chat

        r = client.delete("/api/admin/settings", headers=auth_admin)
        check("E1 重置清除全部覆盖", r.status_code == 200 and db.get_all_settings() == {})
        check("E2 回落 env（api_key）", get_effective_config().api_key == "sk-from-env-key")

    total = len(CHECKS)
    passed = sum(1 for _, c in CHECKS if c)
    print("\n" + "=" * 60)
    print(f"模型配置自测：{passed}/{total} 项 PASS")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
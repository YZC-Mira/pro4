"""任务 7 验收脚本：用户登录与权限（对应任务清单验收标准，秒级，不调真 LLM）。

覆盖：
A. 注册：首个用户自动 admin / 第二个 registered / 重复用户名 409 / 弱密码与短用户名 422；
B. 登录：正确凭据返回 JWT / 错误密码与不存在用户均 401；
C. 生成接口鉴权（核心验收）：无 token 401 / 伪造 token 401 / 过期 token 401 /
   携合法 token 调 /api/generate 与 /api/chat/stream 成功（LLM 与沙箱已 mock）；
D. /api/auth/me：携 token 正确返回 / 无 token 401；
E. 免登录路由不受影响：/api/health、/api/chat/clarify、会话创建仍开放
   （前端登录页接入前的工作台兼容边界）；
F. JWT 单元级：issue/decode 往返一致 / 篡改签名拒绝。

用法（backend 目录，激活 .venv 后）：
  python scripts/test_auth.py
测试数据写入临时目录（ALGOAGENT_DATA_DIR），不污染开发库。
"""

import os
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-auth")
os.environ.setdefault("ALGOAGENT_DATA_DIR", tempfile.mkdtemp(prefix="algoagent_auth_test_"))
os.environ.setdefault("ALGOAGENT_PAGES_DIR", tempfile.mkdtemp(prefix="algoagent_auth_pages_"))

# 先真实加载 fastapi.testclient（其加载链含 starlette/httpx）。
# 必须先于任何 sys.modules 注入：注入的 fake playwright（无 __path__ 的普通模块）
# 与该加载链组合会导致后续 from playwright.sync_api import ... 解析失败
# （2026-10-25 本地实测坑：ImportError cannot import name 'Error' (unknown location)）。
from fastapi.testclient import TestClient  # noqa: E402


def _install_fake_deps_if_needed() -> None:
    """真依赖可用时不注入：本地 venv 直接走真实 openai/playwright import 链，
    生成链路由 _mock_generation() 以"模块属性替换"方式 mock（稳定，已验证）。
    仅当依赖缺失时（沙箱/裸环境）注入 fake 模块兜底。"""
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
from app.auth import decode_token, issue_token  # noqa: E402
from app.main import app  # noqa: E402

GOOD_HTML = "<!DOCTYPE html><html><body>ok</body></html>"
CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"——{detail}" if detail and not cond else ""))


def _mock_generation() -> None:
    """mock LLM 流式输出与沙箱校验：一次通过，秒级。"""

    def _fake_chat(messages, stream=False, config=None):
        if stream:
            return iter([GOOD_HTML])
        return GOOD_HTML

    class _FakeVerify:
        ok = True
        error_summary = "校验通过"

        def to_dict(self):
            return {"ok": True, "error_summary": "校验通过"}

    orch.chat = _fake_chat
    orch.verify_page_isolated = lambda path: _FakeVerify()


def main() -> int:
    db.init_db()
    _mock_generation()

    print("=" * 60)
    print("任务 7 验收：用户登录与权限（mock 生成链路，秒级）")
    print("=" * 60)

    with TestClient(app) as client:
        # ---------- A. 注册 ----------
        print("\n[A] 注册（注册即用；首个用户自动 admin）")
        r = client.post("/api/auth/register", json={"username": "teacher01", "password": "pass123456"})
        check("A1 首个注册用户自动成为 admin", r.status_code == 200 and r.json()["role"] == "admin", r.text[:120])
        admin_token = r.json().get("token")

        r = client.post("/api/auth/register", json={"username": "teacher02", "password": "pass123456"})
        check("A2 第二个注册用户为 registered", r.status_code == 200 and r.json()["role"] == "registered", r.text[:120])
        user_token = r.json().get("token")

        r = client.post("/api/auth/register", json={"username": "teacher01", "password": "pass123456"})
        check("A3 重复用户名返回 409", r.status_code == 409, f"code={r.status_code}")

        r = client.post("/api/auth/register", json={"username": "ab", "password": "pass123456"})
        check("A4 短用户名返回 422", r.status_code == 422, f"code={r.status_code}")

        r = client.post("/api/auth/register", json={"username": "teacher03", "password": "123"})
        check("A5 弱密码返回 422", r.status_code == 422, f"code={r.status_code}")

        # ---------- B. 登录 ----------
        print("\n[B] 登录")
        r = client.post("/api/auth/login", json={"username": "teacher01", "password": "pass123456"})
        check("B1 正确凭据登录成功并返回 token", r.status_code == 200 and r.json().get("token"), r.text[:120])

        r = client.post("/api/auth/login", json={"username": "teacher01", "password": "wrong-pass"})
        check("B2 错误密码返回 401", r.status_code == 401, f"code={r.status_code}")

        r = client.post("/api/auth/login", json={"username": "nobody", "password": "pass123456"})
        check("B3 不存在用户返回 401", r.status_code == 401, f"code={r.status_code}")

        # ---------- C. 生成接口鉴权（任务 7 核心验收） ----------
        print("\n[C] 生成接口鉴权（游客不能生成——PRD F6）")
        body = {"task": "为大二学生生成冒泡排序教学页面，逐步高亮比较与交换过程"}

        r = client.post("/api/generate", json=body)
        check("C1 无 token 调 /api/generate 被拒（401）", r.status_code == 401, f"code={r.status_code}")

        r = client.post("/api/generate", json=body, headers={"Authorization": "Bearer fake.token.value"})
        check("C2 伪造 token 被拒（401）", r.status_code == 401, f"code={r.status_code}")


        # 构造已过期 token：手写 payload（exp 在过去），签名仍用真 SECRET_KEY
        import base64
        import hashlib
        import hmac as hmac_mod
        import json as json_mod

        def _b64e(b):
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

        header = _b64e(json_mod.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64e(json_mod.dumps({"sub": 1, "username": "teacher01", "role": "admin",
                                        "iat": int(time.time()) - 999, "exp": int(time.time()) - 3600}).encode())
        sig = _b64e(hmac_mod.new(os.environ["SECRET_KEY"].encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        expired = f"{header}.{payload}.{sig}"

        r = client.post("/api/generate", json=body, headers={"Authorization": f"Bearer {expired}"})
        check("C3 过期 token 被拒（401，提示重新登录）", r.status_code == 401 and "过期" in r.json().get("detail", ""), r.text[:120])

        r = client.post("/api/generate", json=body, headers={"Authorization": f"Bearer {user_token}"})
        check("C4 携合法 token 调 /api/generate 成功", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])

        # SSE 流式接口同样受保护
        r = client.post("/api/chat/stream", json=body)
        check("C5 无 token 调 /api/chat/stream 被拒（401）", r.status_code == 401, f"code={r.status_code}")

        with client.stream("POST", "/api/chat/stream", json=body,
                           headers={"Authorization": f"Bearer {admin_token}"}) as resp:
            stages = []
            for line in resp.iter_lines():
                if line.startswith("event: stage"):
                    stages.append(line)
            check("C6 携合法 token 的 SSE 正常产出（含 stage 事件）", resp.status_code == 200 and len(stages) >= 3,
                  f"stages={stages}")

        # ---------- D. /api/auth/me ----------
        print("\n[D] 当前用户信息")
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {user_token}"})
        check("D1 携 token 获取 me 正确", r.status_code == 200 and r.json()["username"] == "teacher02"
              and r.json()["role"] == "registered", r.text[:120])
        r = client.get("/api/auth/me")
        check("D2 无 token 调 me 被拒（401）", r.status_code == 401, f"code={r.status_code}")

        # ---------- E. 免登录路由不受影响 ----------
        print("\n[E] 免登录路由（游客可见范围）")
        r = client.get("/api/health")
        check("E1 /api/health 免登录", r.status_code == 200)

        r = client.post("/api/chat/clarify", json={"task": "帮我做个排序动画"})
        check("E2 /api/chat/clarify 暂免登录（前端登录页接入后统一收口）", r.status_code == 200, f"code={r.status_code}")

        r = client.post("/api/conversations", json={"title": "游客会话"})
        check("E3 会话创建暂免登录（工作台兼容边界）", r.status_code == 200, f"code={r.status_code}")

        # ---------- F. JWT 单元级 ----------
        print("\n[F] JWT 签发/校验")
        token = issue_token(42, "unituser", "registered")
        payload = decode_token(token)
        check("F1 issue/decode 往返一致", payload["sub"] == 42 and payload["username"] == "unituser"
              and payload["role"] == "registered")
        tampered = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
        try:
            decode_token(tampered)
            check("F2 篡改签名被拒绝", False)
        except Exception:
            check("F2 篡改签名被拒绝", True)

    total = len(CHECKS)
    passed = sum(1 for _, c in CHECKS if c)
    print("\n" + "=" * 60)
    print(f"任务 7 验收：{passed}/{total} 项 PASS")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
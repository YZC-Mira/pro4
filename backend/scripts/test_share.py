"""任务 8 验收脚本：分享交付 + 我的作品（覆盖式作品模型全语义，秒级，不调真 LLM）。

覆盖（对应任务清单验收标准 + 作品模型六点）：
A. 创建分享：1d/7d/forever 三种有效期 → token/url/qrcode_url/expires_at 正确；
B. 免登录访问 /s/{token}：200 且内容=作品当前 HTML；
C. 过期分享 → 410 提示页；无效 token → 404 提示页（PRD 成功标准第 3 条）；
D. 二维码 /s/{token}/qrcode.svg：200 + image/svg+xml + 含 <svg（服务端生成）；
E. 我的作品：本人列表含作品、他人列表不含（用户隔离）；
F. 覆盖式语义（作品模型②③）：直接覆写作品文件模拟"保存"→ 同一 token 访问返回新内容；
G. 归属校验：非所有者创建分享 → 403；
H. 生成落盘单文件化（作品模型①）：generate 后仅存在 {page_id}.html，无 _v1/_v2 残留。

用法（backend 目录，激活 .venv 后）：
  python scripts/test_share.py
前置：pip install -r requirements.txt（新增 qrcode==7.4.2）。
测试数据写入临时目录（ALGOAGENT_DATA_DIR），不污染开发库。
"""

import os
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-share")
os.environ.setdefault("ALGOAGENT_DATA_DIR", tempfile.mkdtemp(prefix="algoagent_share_test_"))
os.environ.setdefault("ALGOAGENT_PAGES_DIR", tempfile.mkdtemp(prefix="algoagent_share_pages_"))
os.environ.setdefault("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# 先真实加载 fastapi.testclient（必须先于任何 sys.modules 兜底注入）
from fastapi.testclient import TestClient  # noqa: E402


def _install_fake_deps_if_needed() -> None:
    """检测式兜底：真依赖可用（本地 venv）直接走真实 import 链。"""
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
from app.main import app  # noqa: E402

GOOD_HTML = "<!DOCTYPE html><html><body>ok-version-1</body></html>"
GOOD_HTML_V2 = "<!DOCTYPE html><html><body>ok-version-2-covered</body></html>"
CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"——{detail}" if detail and not cond else ""))


def _mock_generation() -> None:
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
    print("任务 8 验收：分享交付 + 我的作品（覆盖式作品模型，mock 秒级）")
    print("=" * 60)

    with TestClient(app) as client:
        # ---------- 准备：两个用户 + 一件作品 ----------
        print("\n[0] 准备：注册两个用户，用户 A 生成一件作品")
        r = client.post("/api/auth/register", json={"username": "owner01", "password": "pass123456"})
        token_a = r.json()["token"]
        r = client.post("/api/auth/register", json={"username": "other02", "password": "pass123456"})
        token_b = r.json()["token"]
        auth_a = {"Authorization": f"Bearer {token_a}"}
        auth_b = {"Authorization": f"Bearer {token_b}"}

        r = client.post("/api/generate", json={"task": "为大二学生生成冒泡排序教学页面，逐步高亮"}, headers=auth_a)
        check("准备：用户 A 生成作品成功", r.status_code == 200 and r.json()["ok"] is True, r.text[:150])
        page_id = r.json()["page_id"]
        final_path = r.json()["final_html_path"]

        # H. 单文件化（作品模型①：一份 page 一份 HTML）
        print("\n[H] 生成落盘单文件化（覆盖式作品模型①）")
        html_file = Path(final_path)
        check("H1 final_html_path 为 {page_id}.html", html_file.name == f"{page_id}.html", html_file.name)
        siblings = [p.name for p in html_file.parent.glob(f"{page_id}*")]
        check("H2 无 _v1/_v2 多版本残留", siblings == [f"{page_id}.html"], str(siblings))

        # ---------- A. 创建分享 ----------
        print("\n[A] 创建分享（F5：有效期 1天/7天/永久）")
        r = client.post("/api/shares", json={"page_id": page_id, "validity": "1d"}, headers=auth_a)
        check("A1 1d 分享创建成功", r.status_code == 200 and r.json().get("token"), r.text[:150])
        share_1d = r.json()
        check("A2 1d 返回 url 与 qrcode_url", share_1d["url"].endswith(f"/s/{share_1d['token']}")
              and share_1d["qrcode_url"].endswith(".svg"))
        check("A3 1d expires_at 非空（约 24h 后）", share_1d["expires_at"] is not None)

        r = client.post("/api/shares", json={"page_id": page_id, "validity": "7d"}, headers=auth_a)
        check("A4 7d 分享创建成功", r.status_code == 200, r.text[:120])

        r = client.post("/api/shares", json={"page_id": page_id, "validity": "forever"}, headers=auth_a)
        check("A5 永久分享创建成功且 expires_at 为空", r.status_code == 200 and r.json()["expires_at"] is None)
        share_forever = r.json()

        r = client.post("/api/shares", json={"page_id": page_id, "validity": "99d"}, headers=auth_a)
        check("A6 非法有效期 422", r.status_code == 422, f"code={r.status_code}")

        r = client.post("/api/shares", json={"page_id": page_id, "validity": "forever"})
        check("A7 未登录创建分享 401", r.status_code == 401, f"code={r.status_code}")

        # ---------- B. 免登录访问 ----------
        print("\n[B] 学生免登录访问 /s/{token}")
        r = client.get(f"/s/{share_forever['token']}")
        check("B1 无需登录 200", r.status_code == 200)
        check("B2 返回作品 HTML 内容", "ok-version-1" in r.text)
        check("B3 Content-Type 为 text/html", "text/html" in r.headers.get("content-type", ""))

        # ---------- C. 过期与无效 token ----------
        print("\n[C] 过期/无效分享（PRD 成功标准第 3 条）")
        expired_token = "expiredtoken000"
        db.create_share(token=expired_token, page_id=page_id,
                        expires_at="2000-01-01T00:00:00", created_by=1)
        r = client.get(f"/s/{expired_token}")
        check("C1 过期分享 410 提示页", r.status_code == 410 and "已过期" in r.text)

        r = client.get("/s/nosuchtoken12345")
        check("C2 无效 token 404 提示页", r.status_code == 404 and "无效" in r.text)

        # ---------- D. 二维码 ----------
        print("\n[D] 二维码（服务端 SVG 生成，与大模型无关）")
        r = client.get(f"/s/{share_forever['token']}/qrcode.svg")
        check("D1 二维码 200", r.status_code == 200)
        check("D2 Content-Type 为 image/svg+xml", "image/svg+xml" in r.headers.get("content-type", ""))
        check("D3 内容为 SVG", "<svg" in r.text[:200])
        r = client.get(f"/s/{expired_token}/qrcode.svg")
        check("D4 过期分享二维码 404", r.status_code == 404, f"code={r.status_code}")

        # ---------- E. 我的作品 ----------
        print("\n[E] 我的作品（F7：用户隔离）")
        r = client.get("/api/pages/my", headers=auth_a)
        mine = r.json()
        check("E1 用户 A 列表含该作品", r.status_code == 200 and any(p["page_id"] == page_id for p in mine))
        check("E2 列表含 task/status/时间字段", mine and all(k in mine[0] for k in ("task", "status", "created_at")))
        r = client.get("/api/pages/my", headers=auth_b)
        check("E3 用户 B 列表不含他人作品", all(p["page_id"] != page_id for p in r.json()))
        r = client.get("/api/pages/my")
        check("E4 未登录 401", r.status_code == 401, f"code={r.status_code}")

        # ---------- F. 覆盖式语义 ----------
        print("\n[F] 覆盖式作品模型（②③：保存覆盖后链接自动显示新内容）")
        Path(final_path).write_text(GOOD_HTML_V2, encoding="utf-8")  # 模拟"保存覆盖"
        r = client.get(f"/s/{share_forever['token']}")
        check("F1 同一 token 返回覆盖后新内容", "ok-version-2-covered" in r.text)
        r = client.get(f"/s/{share_1d['token']}")
        check("F2 1d 分享同样自动更新（token 不失效）", "ok-version-2-covered" in r.text)

        # ---------- G. 归属校验 ----------
        print("\n[G] 分享归属校验")
        r = client.post("/api/shares", json={"page_id": page_id, "validity": "forever"}, headers=auth_b)
        check("G1 非所有者创建分享 403", r.status_code == 403, f"code={r.status_code}")
        r = client.post("/api/shares", json={"page_id": "nosuchpage", "validity": "forever"}, headers=auth_a)
        check("G2 不存在的作品 404", r.status_code == 404, f"code={r.status_code}")

    total = len(CHECKS)
    passed = sum(1 for _, c in CHECKS if c)
    print("\n" + "=" * 60)
    print(f"任务 8 验收：{passed}/{total} 项 PASS")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
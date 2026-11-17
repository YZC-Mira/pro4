import json
import os
import queue as queue_mod
import secrets
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from app import db
from app.agent.orchestrator import (
    clarify_task,
    default_model_cfg,
    generate_page,
    iterate_page,
    no_model_configured,
)
from app.auth import hash_password, issue_token, require_jwt, verify_password

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    sys.stderr.write(
        "[启动失败] 未设置 SECRET_KEY 环境变量。\n"
        "该变量用于 JWT 签名与 api_key 加密，为必填项，不提供默认值。\n"
        "设置方式：\n"
        "  git-bash:   export SECRET_KEY=<一段足够长的随机字符串>\n"
        "  PowerShell: $env:SECRET_KEY=\"<一段足够长的随机字符串>\"\n"
        "  Linux:      SECRET_KEY=<...> 写入环境或 .env 后注入\n"
    )
    sys.exit(1)

app = FastAPI(title="AlgoAgent", version="0.1.0")

# 进行中的生成任务注册表：gen_id → cancel_event（显式停止接口按此查找；
# SSE 断开不再触发取消——刷新/HMR 重载让生成继续，恢复轮询拿结果）
_ACTIVE_GENERATIONS: dict[str, threading.Event] = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    db.init_db()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- 认证（任务 7：PRD F6；注册即用，首个注册用户自动成为 admin） ----------

class RegisterRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
def register(body: RegisterRequest) -> dict:
    """注册（无身份门槛，注册即用；重复用户名 409）。"""
    username = (body.username or "").strip()
    password = body.password or ""
    if not (3 <= len(username) <= 32):
        raise HTTPException(status_code=422, detail="用户名长度须为 3-32 个字符")
    if len(password) < 6:
        raise HTTPException(status_code=422, detail="密码至少 6 位")
    if db.get_user_by_username(username):
        raise HTTPException(status_code=409, detail="用户名已存在")

    role = "admin" if db.count_users() == 0 else "registered"  # 首个注册用户自动成为 admin
    user_id = db.create_user(username, hash_password(password), role)
    token = issue_token(user_id, username, role)
    return {"id": user_id, "username": username, "role": role, "token": token, "created_at": _now()}


@app.post("/api/auth/login")
def login(body: RegisterRequest) -> dict:
    """登录：用户名/密码正确返回 JWT（7 天有效期）；错误 401。"""
    username = (body.username or "").strip()
    user = db.get_user_by_username(username)
    if not user or not verify_password(body.password or "", user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = issue_token(user["id"], user["username"], user["role"])
    return {"id": user["id"], "username": user["username"], "role": user["role"], "token": token}


@app.get("/api/auth/me")
def me(user: dict = Depends(require_jwt)) -> dict:
    """当前登录用户信息（前端会话恢复用）。"""
    return {"id": user["sub"], "username": user["username"], "role": user["role"]}


# ---------- 管理端（仅 admin：用户列表/重置密码/全部作品） ----------

def require_admin(user: dict = Depends(require_jwt)) -> dict:
    """管理路由专用：非 admin 一律 403。"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


@app.get("/api/admin/users")
def admin_list_users(user: dict = Depends(require_admin)) -> list[dict]:
    """用户列表（用户名/角色/注册时间/作品数；不含敏感字段）。"""
    return db.list_all_users()


class AdminResetPassword(BaseModel):
    password: str


@app.patch("/api/admin/users/{user_id}/password")
def admin_reset_password(user_id: int, body: AdminResetPassword,
                         admin: dict = Depends(require_admin)) -> dict:
    """管理员重置用户密码（pbkdf2 加密落库；不能重置自己——避免误操作把自己锁死）。"""
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target["id"] == admin["sub"]:
        raise HTTPException(status_code=422, detail="不能在此重置自己的密码（请在登录页修改）")
    password = body.password or ""
    if len(password) < 6:
        raise HTTPException(status_code=422, detail="新密码至少 6 位")
    db.update_user_password(user_id, hash_password(password))
    return {"id": user_id, "username": target["username"], "reset": True}


@app.get("/api/admin/pages")
def admin_list_pages(admin: dict = Depends(require_admin)) -> list[dict]:
    """全部作品列表（含作者；管理端可预览/删除，删除复用 /api/pages/{id} DELETE 的 admin 豁免）。"""
    return db.list_all_pages()


# ---------- 模型配置（F12 提前落地：运行时切换 LLM，云部署刚需） ----------

_SETTING_KEYS = ("base_url", "api_key", "model", "temperature", "max_tokens", "timeout")


def _mask_key(key: str | None) -> str:
    """api_key 掩码：仅首尾可见（管理页展示用，不明文回传）。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


@app.get("/api/admin/settings")
def admin_get_settings(admin: dict = Depends(require_admin)) -> dict:
    """当前生效 LLM 配置 + 各字段来源（db/env/yaml）；api_key 掩码显示。"""
    from app.llm.config import get_effective_config

    raw = db.get_all_settings()
    cfg = get_effective_config()
    import os

    def source(field: str, env_name: str) -> str:
        if raw.get(field):
            return "db"
        return "env" if os.environ.get(env_name) else "yaml"

    return {
        "effective": {
            "base_url": cfg.base_url,
            "api_key_masked": _mask_key(cfg.api_key),
            "model": cfg.model,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            "timeout": cfg.timeout,
        },
        "sources": {
            "base_url": source("base_url", "LLM_BASE_URL"),
            "api_key": source("api_key", "LLM_API_KEY"),
            "model": source("model", "LLM_MODEL"),
        },
        "db_overrides": {
            k: (_mask_key(v) if k == "api_key" else v) for k, v in raw.items() if k in _SETTING_KEYS
        },
    }


class SettingsUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None       # 掩码表单：None/空串 = 保持不变（选型 3.6"留空提交不覆盖"）
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: float | None = None


@app.put("/api/admin/settings")
def admin_update_settings(body: SettingsUpdate, admin: dict = Depends(require_admin)) -> dict:
    """写入运行时覆盖项（settings 表，下次请求即生效，无需重启）。

    语义：None/空串 = 不修改该字段（api_key 亦然——表单掩码无法回填原值）；
    想恢复默认 → 用 reset 接口清除全部覆盖。
    """
    data = body.model_dump()
    changed = []
    for key in _SETTING_KEYS:
        value = data.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        db.set_setting(key, str(value))
        changed.append(key)
    return {"changed": changed, "note": "已写入，下次请求即生效（无需重启）"}


@app.delete("/api/admin/settings")
def admin_reset_settings(admin: dict = Depends(require_admin)) -> dict:
    """清除全部运行时覆盖（回落到环境变量 / config.yaml）。"""
    for key in _SETTING_KEYS:
        db.delete_setting(key)
    return {"reset": True, "note": "已清除覆盖项，回落到环境变量/出厂配置"}


class SettingsTest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


@app.post("/api/admin/settings/test")
def admin_test_settings(body: SettingsTest, admin: dict = Depends(require_admin)) -> dict:
    """用提交的配置（缺省回落当前生效值）发一条最小连通性测试。"""
    import time as time_mod

    from app.llm import chat
    from app.llm.config import LLMConfig, get_effective_config

    cur = get_effective_config()
    cfg = LLMConfig(
        base_url=(body.base_url or "").strip() or cur.base_url,
        api_key=(body.api_key or "").strip() or cur.api_key,
        model=(body.model or "").strip() or cur.model,
        temperature=cur.temperature,
        max_tokens=64,
        timeout=60.0,
    )
    t0 = time_mod.time()
    try:
        reply = chat([{"role": "user", "content": "连通性测试：请只回复两个字：OK"}],
                     stream=False, config=cfg)
        return {"ok": True, "reply": (reply or "")[:50], "seconds": round(time_mod.time() - t0, 1)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "seconds": round(time_mod.time() - t0, 1)}


# ---------- 模型注册表（多模型：platform=全员可用 / user=自定义仅自己；对话中可选） ----------

def _model_cfg_from_row(row: dict):
    """models 表行 → LLMConfig 快照（生成/迭代时锁定该模型）。"""
    from app.llm.config import LLMConfig

    return LLMConfig(
        base_url=row["base_url"],
        api_key=row.get("api_key") or "EMPTY",
        model=row["model_id"],
    )


@app.get("/api/models")
def list_models(user: dict = Depends(require_jwt)) -> list[dict]:
    """用户可选模型：未停用的内置模型 + 自己的自定义模型（api_key 掩码；含默认标记）。

    admin 额外看到停用的内置模型（含 enabled 标记，便于管理/启用回来）。
    """
    is_admin = user.get("role") == "admin"
    rows = db.list_models_for_user(user["sub"], include_disabled=is_admin)
    default_row = db.get_default_model()
    default_id = default_row["id"] if default_row else None
    for r in rows:
        r["api_key_masked"] = _mask_key(r.get("api_key")) if r.get("api_key") else ""
        r.pop("api_key", None)
        r["mine"] = r["scope"] == "user"
        r["is_default"] = r["id"] == default_id
    return rows


@app.get("/api/models/availability")
def models_availability(user: dict = Depends(require_jwt)) -> dict:
    """平台模型可用性（前端禁用对话用）：no_model=true = 平台从未配置模型
    （无启用的内置模型且环境变量未设 LLM_*）——此时管理员也不应能发起对话
    （env 兜底不存在，任何请求都会失败）。
    """
    return {"no_model": no_model_configured()}


class ModelCreate(BaseModel):
    name: str
    base_url: str
    api_key: str | None = None
    model_id: str
    scope: str = "user"  # user=自定义模型；platform 仅 admin 可指定


@app.post("/api/models")
def create_model(body: ModelCreate, user: dict = Depends(require_jwt)) -> dict:
    """注册模型：普通用户创建自己的自定义模型；admin 可创建平台模型（scope=platform）。"""
    name = (body.name or "").strip()
    base_url = (body.base_url or "").strip()
    model_id = (body.model_id or "").strip()
    if not name or not base_url or not model_id:
        raise HTTPException(status_code=422, detail="名称、接口地址、模型名均不能为空")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="接口地址须以 http(s):// 开头")

    scope = body.scope if body.scope in ("user", "platform") else "user"
    if scope == "platform" and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可创建平台模型")

    owner = user["sub"] if scope == "user" else None
    mid = db.create_model(name, base_url, (body.api_key or "").strip() or None, model_id, scope, owner)
    return {"id": mid, "name": name, "scope": scope, "created_at": _now()}


@app.delete("/api/models/{mid}")
def delete_model(mid: int, user: dict = Depends(require_jwt)) -> dict:
    """删除模型：自定义模型仅归属者；平台模型仅 admin。删除的是默认模型时默认标记一并清除。"""
    row = db.get_model(mid)
    if not row:
        raise HTTPException(status_code=404, detail="模型不存在")
    if row["scope"] == "platform":
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="平台模型仅管理员可删除")
    elif row.get("owner_id") != user["sub"]:
        raise HTTPException(status_code=403, detail="只能删除自己的自定义模型")
    db.delete_model(mid)
    default_row = db.get_default_model()
    if default_row is None:
        db.delete_setting("default_model_id")  # 默认模型已被删 → 清除悬空标记
    return {"deleted": mid}


class ModelUpdate(BaseModel):
    name: str
    base_url: str
    model_id: str
    api_key: str | None = None  # 空/None = 保持原值（掩码不回填）


@app.patch("/api/models/{mid}")
def update_model(mid: int, body: ModelUpdate, user: dict = Depends(require_jwt)) -> dict:
    """编辑模型（scope/归属不可改）：自定义仅归属者；内置仅 admin。api_key 留空 = 不变。"""
    row = db.get_model(mid)
    if not row:
        raise HTTPException(status_code=404, detail="模型不存在")
    if row["scope"] == "platform":
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="内置模型仅管理员可编辑")
    elif row.get("owner_id") != user["sub"]:
        raise HTTPException(status_code=403, detail="只能编辑自己的自定义模型")

    name = (body.name or "").strip()
    base_url = (body.base_url or "").strip()
    model_id = (body.model_id or "").strip()
    if not name or not base_url or not model_id:
        raise HTTPException(status_code=422, detail="名称、接口地址、模型名均不能为空")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="接口地址须以 http(s):// 开头")

    db.update_model(mid, name, base_url, model_id, (body.api_key or "").strip() or None)
    return {"id": mid, "name": name, "updated": True}


@app.post("/api/models/{mid}/default")
def set_default_model(mid: int, user: dict = Depends(require_jwt)) -> dict:
    """将某内置模型设为默认（对话未选择模型时使用；仅 admin，仅内置模型）。"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可设置默认模型")
    if not db.set_default_model(mid):
        raise HTTPException(status_code=422, detail="只能将内置模型设为默认")
    row = db.get_model(mid)
    return {"default_model_id": mid, "name": row["name"] if row else ""}


@app.post("/api/models/{mid}/toggle")
def toggle_model(mid: int, user: dict = Depends(require_jwt)) -> dict:
    """停用/启用内置模型（仅 admin）——公网部署控制开放范围（防滥用）。

    停用后：普通用户不可见不可选；默认链跳过；admin 仍可显式选择（便于测试）。
    """
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可停用或启用模型")
    row = db.get_model(mid)
    if not row:
        raise HTTPException(status_code=404, detail="模型不存在")
    if row["scope"] != "platform":
        raise HTTPException(status_code=422, detail="只能停用或启用内置模型")
    new_enabled = not bool(row.get("enabled", 1))
    if not db.set_model_enabled(mid, new_enabled):
        raise HTTPException(status_code=404, detail="模型不存在")
    return {"id": mid, "enabled": new_enabled}


class ModelTest(BaseModel):
    base_url: str
    api_key: str | None = None
    model_id: str


@app.post("/api/models/test")
def test_model(body: ModelTest, user: dict = Depends(require_jwt)) -> dict:
    """模型连通性测试（所有登录用户可用：注册自定义模型前先测）。"""
    import time as time_mod

    from app.llm import chat
    from app.llm.config import LLMConfig

    cfg = LLMConfig(
        base_url=body.base_url.strip(),
        api_key=(body.api_key or "").strip() or "EMPTY",
        model=body.model_id.strip(),
        max_tokens=64,
        timeout=60.0,
    )
    t0 = time_mod.time()
    try:
        reply = chat([{"role": "user", "content": "连通性测试：请只回复两个字：OK"}],
                     stream=False, config=cfg)
        return {"ok": True, "reply": (reply or "")[:50], "seconds": round(time_mod.time() - t0, 1)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "seconds": round(time_mod.time() - t0, 1)}


def _default_model_cfg():
    """默认模型链（统一走 orchestrator.default_model_cfg：标记默认 → 第一个启用的内置 → env/yaml）。"""
    return default_model_cfg()


def _resolve_model_cfg(model_row_id: int | None, user: dict):
    """请求指定的模型 → LLMConfig（含归属/停用校验）。

    None（未选择模型）的解析链：
    1. 默认内置模型（settings 标记，未停用）；
    2. admin：环境变量/出厂配置兜底（部署时 -e 注入的端点仅管理员可用——公网防滥用）；
    3. 普通用户且无任何可用模型 → 422 友好提示。
    显式选择的停用内置模型：仅 admin 可用（普通用户列表已过滤，此处防御 API 直调）。
    """
    if model_row_id is None:
        default_cfg = _default_model_cfg()
        if default_cfg is not None:
            return default_cfg
        if user.get("role") == "admin":
            if not no_model_configured():
                return None  # admin 走 env/yaml 兜底（env 已显式配置或本地开发场景）
            # 平台从未配置模型（无内置 + 无 env）：yaml 出厂内网地址在云上必然挂超时——
            # 快速失败并引导配置，绝不让请求挂 600s
            raise HTTPException(
                status_code=422,
                detail="平台暂未配置模型：请管理员到「管理 → 模型配置」添加内置模型，或在 .env 设置 LLM_BASE_URL",
            )
        raise HTTPException(
            status_code=422,
            detail="暂无可用模型：请管理员启用内置模型，或在个人中心「模型」页添加自定义模型",
        )
    row = db.get_model_credentials(model_row_id)
    if not row:
        raise HTTPException(status_code=404, detail="所选模型不存在")
    if row["scope"] != "platform" and row.get("owner_id") != user["sub"]:
        raise HTTPException(status_code=403, detail="只能使用平台模型或自己的自定义模型")
    if row["scope"] == "platform" and row.get("enabled", 1) == 0 and user.get("role") != "admin":
        raise HTTPException(status_code=422, detail="该内置模型已停用")
    return _model_cfg_from_row(row)


# ---------- 会话管理（任务 5；2026-10-25 多会话切换：路由组统一补 JWT + 用户归属） ----------

class ConversationCreate(BaseModel):
    title: str = "新对话"


@app.post("/api/conversations")
def create_conversation(body: ConversationCreate, user: dict = Depends(require_jwt)) -> dict:
    """创建会话（PRD F1：多轮对话载体；归属当前用户）。"""
    cid = db.create_conversation(title=body.title, user_id=user["sub"])
    return {"id": cid, "title": body.title, "created_at": _now()}


@app.get("/api/conversations")
def list_conversations(user: dict = Depends(require_jwt)) -> list[dict]:
    """当前用户的会话列表（多会话切换；倒序）。"""
    return db.list_conversations(user_id=user["sub"])


@app.get("/api/conversations/{conversation_id}/latest-page")
def get_latest_page(conversation_id: int, include_pending: bool = False,
                    user: dict = Depends(require_jwt)) -> dict:
    """会话内最近一件作品。

    默认（include_pending=False）：仅成功作品（切换会话恢复预览——迭代依赖）。
    include_pending=True：任意状态（刷新恢复检测"生成中"用）——status 一并返回。
    """
    _ensure_conversation(conversation_id)
    page = db.get_latest_page_by_conversation(conversation_id, include_pending=include_pending)
    if not page:
        raise HTTPException(status_code=404, detail="该会话暂无作品")
    return {
        "page_id": page["page_id"],
        "task": page["task"],
        "status": page["status"],
        "created_at": page["created_at"],
    }


class ConversationRename(BaseModel):
    title: str


def _ensure_own_conversation(conversation_id: int, user: dict) -> dict:
    """会话存在 + 归属当前用户（或 admin）校验，返回会话行。"""
    conv = db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail=f"会话不存在：{conversation_id}")
    if conv.get("user_id") != user["sub"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只能操作自己的会话")
    return conv


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: int, body: ConversationRename,
                        user: dict = Depends(require_jwt)) -> dict:
    """重命名会话（标题 ≤60 字；空标题 422）。"""
    _ensure_own_conversation(conversation_id, user)
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="标题不能为空")
    db.rename_conversation(conversation_id, title)
    return {"id": conversation_id, "title": title}


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, user: dict = Depends(require_jwt)) -> dict:
    """删除会话（消息级联删除；作品保留——会话是对话过程，作品是成果资产）。"""
    _ensure_own_conversation(conversation_id, user)
    db.delete_conversation(conversation_id)
    return {"deleted": conversation_id}


class PageRename(BaseModel):
    title: str


@app.patch("/api/pages/{page_id}")
def rename_page(page_id: str, body: PageRename, user: dict = Depends(require_jwt)) -> dict:
    """重命名作品（标题 ≤60 字；空标题 422）。"""
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="作品不存在")
    if page.get("user_id") != user["sub"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只能操作自己的作品")
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="标题不能为空")
    db.rename_page(page_id, title)
    return {"page_id": page_id, "title": title}


@app.delete("/api/pages/{page_id}")
def delete_page(page_id: str, user: dict = Depends(require_jwt)) -> dict:
    """删除作品：pages 行 + 分享链接级联清理 + HTML 文件删除。"""
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="作品不存在")
    if page.get("user_id") != user["sub"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只能操作自己的作品")

    html_path = page.get("final_html_path")
    db.delete_page(page_id)
    if html_path:
        path = Path(html_path).resolve()
        if str(path).startswith(str(db.PAGES_DIR.resolve())) and path.exists():
            try:
                path.unlink()
            except OSError:
                pass  # 文件删除失败不阻塞（DB 已清理，残留文件无害）
    return {"deleted": page_id}


class MessageCreate(BaseModel):
    role: str
    content: str


@app.post("/api/conversations/{conversation_id}/messages")
def add_message(conversation_id: int, body: MessageCreate, user: dict = Depends(require_jwt)) -> dict:
    """追加一轮消息（role: user / assistant；附件引用字段预留未启用）。"""
    _ensure_conversation(conversation_id)
    try:
        mid = db.add_message(conversation_id, body.role, body.content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"id": mid, "conversation_id": conversation_id, "created_at": _now()}


@app.get("/api/conversations/{conversation_id}/messages")
def get_messages(conversation_id: int, user: dict = Depends(require_jwt)) -> list[dict]:
    """按会话拉取历史（按 id 升序，即时间有序）。"""
    _ensure_conversation(conversation_id)
    return db.get_messages(conversation_id)


# ---------- 生成（任务 4 接口 + 任务 5 会话挂载） ----------

class GenerateRequest(BaseModel):
    task: str
    conversation_id: int | None = None
    iterate_page_id: str | None = None  # 对话式迭代（PRD F1 上下文）：按反馈修改该作品
    model_id: int | None = None         # 模型注册表行 id（对话中选择的模型）；None = 平台默认


@app.post("/api/generate")
def generate(body: GenerateRequest, user: dict = Depends(require_jwt)) -> dict:
    """生成接口（任务 7 起要求 JWT：注册即可用，游客不能生成——PRD F6）。

    同步阻塞式：本地模型长 HTML 生成可达数分钟，SSE 流式版本见 /api/chat/stream。
    带 conversation_id 时：任务与结果摘要自动写入该会话消息流，页面挂载到会话。
    带 iterate_page_id 时：进入迭代模式（按 task 作为反馈修改既有作品）。
    """
    task = (body.task or "").strip()
    if not task:
        raise HTTPException(status_code=422, detail="task 不能为空")
    if body.conversation_id is not None and not db.conversation_exists(body.conversation_id):
        raise HTTPException(status_code=404, detail=f"会话不存在：{body.conversation_id}")

    model_cfg = _resolve_model_cfg(body.model_id, user)  # 归属校验（平台/自己的）

    iterate_page_id = (body.iterate_page_id or "").strip() or None
    if iterate_page_id is not None:
        page = db.get_page(iterate_page_id)
        if not page:
            raise HTTPException(status_code=404, detail="作品不存在")
        if page.get("user_id") != user["sub"] and user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="只能调整自己的作品")
        if not page.get("final_html_path"):
            raise HTTPException(status_code=422, detail="作品尚未生成完成，无法调整")
        outcome = iterate_page(
            iterate_page_id, task,
            conversation_id=body.conversation_id,
            user_id=user["sub"],
            model_cfg=model_cfg,
        )
        return outcome.to_dict()

    outcome = generate_page(task, conversation_id=body.conversation_id,
                            user_id=user["sub"], model_cfg=model_cfg)
    return outcome.to_dict()


class ClarifyRequest(BaseModel):
    task: str
    model_id: int | None = None  # 对话中选择的模型（澄清用同一模型——普通用户的自定义模型也能驱动澄清）


@app.post("/api/chat/clarify")
def clarify(body: ClarifyRequest, user: dict = Depends(require_jwt)) -> dict:
    """需求澄清阶段（PRD F1 / 选型第 4 节"需求澄清"行；编排模块 clarify_task 的 HTTP 薄壳）。

    智能体判断任务信息是否充足（LLM 判断 + 后端规则兜底——LLM 未问且缺
    必澄清项[演示形式/面向对象]时强制追问）：
    - 充足 → {need_clarify: false, questions: []}，前端直接进入生成；
    - 不足 → {need_clarify: true, questions: [...], reason: ...}，回答后合并生成。
    模型：请求携带 model_id（对话当前选择）优先；未携带 → 内置默认链 → env/yaml。
    轻量调用（输出几十 token，秒级）；服务异常时规则兜底，不阻塞主流程。
    """
    task = (body.task or "").strip()
    if not task:
        raise HTTPException(status_code=422, detail="task 不能为空")

    if body.model_id is not None:
        cfg = _resolve_model_cfg(body.model_id, user)  # 归属/停用校验（同生成链）
    else:
         cfg = default_model_cfg()  # 内置默认链（无则 None → env/yaml）
    result = clarify_task(task, model_cfg=cfg)
    return {
        "need_clarify": result.need_clarify,
        "questions": result.questions,
        "reason": result.reason,
        "out_of_scope": result.out_of_scope,
        "error": result.error,
    }


@app.post("/api/chat/stream")
def chat_stream(body: GenerateRequest, user: dict = Depends(require_jwt)) -> StreamingResponse:
    """SSE 流式生成（任务 6：教师工作台的实时进度通道；任务 7 起要求 JWT——PRD F6）。

    事件序列（text/event-stream）：
      start          → {task}
      stage          → {"stage":"clarifying|generating|verifying|repairing|done|failed"}
                       过程状态演示（PRD F3）；repairing 额外带 attempt 字段：
                       {"stage":"repairing","attempt":2}
      attempt_start  → {attempt, kind}            kind: generate / repair
      progress       → {kb, seconds}              每 ~2s，LLM 流式接收量
      sandbox_check  → {attempt, file}            沙箱四项校验开始
      attempt_done   → {attempt, kind, ok, error_summary, llm_seconds}
      done           → {ok, page_id, seconds, attempts_count, error_summary}
      error          → {message}                  编排异常（非校验失败）
    生成线程独立于事件循环（Playwright sync API 需要纯同步上下文）。
    """
    task = (body.task or "").strip()
    if not task:
        raise HTTPException(status_code=422, detail="task 不能为空")
    if body.conversation_id is not None and not db.conversation_exists(body.conversation_id):
        raise HTTPException(status_code=404, detail=f"会话不存在：{body.conversation_id}")

    # 迭代模式（PRD F1 对话上下文）：携带 iterate_page_id = 按反馈修改该作品
    iterate_page_id = (body.iterate_page_id or "").strip() or None
    if iterate_page_id is not None:
        page = db.get_page(iterate_page_id)
        if not page:
            raise HTTPException(status_code=404, detail="作品不存在")
        if page.get("user_id") != user["sub"] and user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="只能调整自己的作品")
        if not page.get("final_html_path"):
            raise HTTPException(status_code=422, detail="作品尚未生成完成，无法调整")

    # 模型选择（对话中）：请求指定 model_id = 用该注册模型；None = 平台默认
    model_cfg = _resolve_model_cfg(body.model_id, user)

    gen_id = secrets.token_urlsafe(9)  # 生成任务 ID（前端显式停止时携带）
    q: queue_mod.Queue = queue_mod.Queue()
    cancel_event = threading.Event()

    # 注册到全局表：显式取消接口按 gen_id 找到本次生成。
    # SSE 断开不再等于取消（刷新/HMR 重载/网络抖动会让生成继续跑，
    # 用户回来经恢复轮询拿结果；只有点「停止」才真正取消）。
    _ACTIVE_GENERATIONS[gen_id] = cancel_event

    def on_event(etype: str, payload: dict) -> None:
        q.put_nowait((etype, payload))

    def worker() -> None:
        try:
            if iterate_page_id is not None:
                iterate_page(
                    iterate_page_id, task,
                    conversation_id=body.conversation_id,
                    user_id=user["sub"], on_event=on_event,
                    model_cfg=model_cfg,
                    cancel_event=cancel_event,
                )
            else:
                generate_page(task, conversation_id=body.conversation_id, on_event=on_event,
                              user_id=user["sub"], model_cfg=model_cfg,
                              cancel_event=cancel_event)
        except Exception as e:  # 编排级异常（校验失败不走这里，走 done.ok=False）
            q.put_nowait(("error", {"message": f"{type(e).__name__}: {e}"}))
        finally:
            q.put_nowait(None)  # 结束哨兵
            _ACTIVE_GENERATIONS.pop(gen_id, None)  # 生成结束，注销

    q.put_nowait(("start", {"task": task, "gen_id": gen_id}))
    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        try:
            while True:
                item = q.get()
                if item is None:
                    break
                etype, payload = item
                yield f"event: {etype}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except (GeneratorExit, ConnectionResetError, BrokenPipeError):
            # 客户端断开（刷新/HMR/关页/网络抖动）：不取消生成——worker 继续跑完，
            # 结果照常入库，用户回来由恢复轮询展示。显式取消走 /api/generations/{gen_id}/cancel。
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/generations/{gen_id}/cancel")
def cancel_generation(gen_id: str, user: dict = Depends(require_jwt)) -> dict:
    """显式取消生成（用户点「停止」）：按 SSE start 事件下发的 gen_id 置取消标志。

    与 SSE 断开的区别：断开（刷新等）不取消；本接口才是真正的用户停止。
    """
    event = _ACTIVE_GENERATIONS.get(gen_id)
    if event is None:
        return {"cancelled": False, "note": "生成已结束或不存在"}
    event.set()
    return {"cancelled": True}


@app.get("/api/pages/{page_id}/html")
def get_page_html(page_id: str) -> FileResponse:
    """生成页 HTTP 预览（任务 6：工作台 iframe src / 后续分享页复用）。"""
    row = db.get_page(page_id)
    if not row or not row["final_html_path"]:
        raise HTTPException(status_code=404, detail="页面不存在或尚未生成完成")
    path = Path(row["final_html_path"]).resolve()
    if not str(path).startswith(str(db.PAGES_DIR.resolve())):
        raise HTTPException(status_code=403, detail="非法路径")
    if not path.exists():
        raise HTTPException(status_code=404, detail="生成文件缺失")
    return FileResponse(path, media_type="text/html")


@app.get("/api/pages/{page_id}/status")
def get_page_status(page_id: str, user: dict = Depends(require_jwt)) -> dict:
    """作品生成状态轮询（刷新恢复用）：pending（生成中）/ passed / failed / cancelled。"""
    row = db.get_page(page_id)
    if not row:
        raise HTTPException(status_code=404, detail="作品不存在")
    if row.get("user_id") != user["sub"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只能查询自己的作品")
    return {
        "page_id": page_id,
        "status": row["status"],
        "error_summary": row.get("error_summary"),
        "conversation_id": row.get("conversation_id"),
        "updated_at": row.get("updated_at"),
    }


def _ensure_conversation(conversation_id: int) -> None:
    if not db.conversation_exists(conversation_id):
        raise HTTPException(status_code=404, detail=f"会话不存在：{conversation_id}")

# ---------- 作品与分享（任务 8：F5 分享交付 + F7 我的作品，覆盖式作品模型） ----------

VALIDITY_CHOICES = {"1d": 24 * 3600, "7d": 7 * 24 * 3600, "forever": None}


def _public_base_url() -> str:
    """分享链接对外地址：环境变量 PUBLIC_BASE_URL，缺省本地开发地址。"""
    return os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _share_expired(share: dict) -> bool:
    if not share.get("expires_at"):
        return False
    return share["expires_at"] < datetime.now().isoformat(timespec="seconds")


class ShareCreateRequest(BaseModel):
    page_id: str
    validity: str = "forever"  # 1d / 7d / forever


@app.post("/api/shares")
def create_share(body: ShareCreateRequest, user: dict = Depends(require_jwt)) -> dict:
    """创建分享（F5）：生成免登录 token 与链接；有效期 1 天 / 7 天 / 永久。

    覆盖式作品模型：token 指向作品当前内容——作品被覆盖保存后，
    已分享链接自动展示新内容，链接不失效、不重新生成。
    归属校验：仅作品所有者（或 admin）可创建分享。
    """
    if body.validity not in VALIDITY_CHOICES:
        raise HTTPException(status_code=422, detail="validity 必须是 1d / 7d / forever")

    page = db.get_page(body.page_id)
    if not page:
        raise HTTPException(status_code=404, detail="作品不存在")
    if page.get("user_id") != user["sub"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只能分享自己的作品")

    ttl = VALIDITY_CHOICES[body.validity]
    expires_at = (
        datetime.fromtimestamp(datetime.now().timestamp() + ttl).isoformat(timespec="seconds")
        if ttl else None
    )
    token = secrets.token_urlsafe(12)
    db.create_share(token=token, page_id=body.page_id, expires_at=expires_at, created_by=user["sub"])
    return {
        "token": token,
        "url": f"{_public_base_url()}/s/{token}",
        "qrcode_url": f"{_public_base_url()}/s/{token}/qrcode.svg",
        "validity": body.validity,
        "expires_at": expires_at,
    }


@app.get("/api/pages/my")
def my_pages(user: dict = Depends(require_jwt)) -> list[dict]:
    """我的作品列表（F7）：pages 即作品（覆盖式），按创建时间倒序。"""
    return db.list_pages_by_user(user["sub"])


_SHARE_STATUS_PAGE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f7fa;color:#606266}}.card{{text-align:center;padding:40px 60px;background:#fff;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08)}}h1{{font-size:20px;margin:0 0 8px}}p{{color:#909399;margin:0}}</style></head>
<body><div class="card"><h1>{title}</h1><p>{message}</p></div></body></html>"""


@app.get("/s/{token}")
def share_view(token: str) -> HTMLResponse:
    """学生访问入口（F5）：免登录，打开即用。

    token 有效 → 返回作品当前 HTML 内容（覆盖保存后自动是新内容）；
    过期 → 410 提示页；无效 → 404 提示页。
    """
    share = db.get_share_by_token(token)
    if not share:
        return HTMLResponse(
            _SHARE_STATUS_PAGE.format(title="链接无效", message="分享链接不存在，请向老师确认。"),
            status_code=404,
        )
    if _share_expired(share):
        return HTMLResponse(
            _SHARE_STATUS_PAGE.format(title="链接已过期", message="该分享已超过有效期，请联系老师重新分享。"),
            status_code=410,
        )
    page = db.get_page(share["page_id"])
    if not page or not page.get("final_html_path"):
        return HTMLResponse(
            _SHARE_STATUS_PAGE.format(title="作品暂不可用", message="作品尚未生成完成，请稍后再试。"),
            status_code=404,
        )
    path = Path(page["final_html_path"]).resolve()
    if not str(path).startswith(str(db.PAGES_DIR.resolve())) or not path.exists():
        return HTMLResponse(
            _SHARE_STATUS_PAGE.format(title="作品暂不可用", message="作品文件缺失，请联系老师。"),
            status_code=404,
        )
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/s/{token}/qrcode.svg")
def share_qrcode(token: str) -> Response:
    """分享二维码（F5）：服务端生成 SVG（qrcode 纯 Python 实现，与大模型无关）。"""
    import io

    import qrcode
    import qrcode.image.svg

    share = db.get_share_by_token(token)
    if not share or _share_expired(share):
        raise HTTPException(status_code=404, detail="分享不存在或已过期")

    url = f"{_public_base_url()}/s/{token}"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=12)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")

# ---------- SPA 静态托管（任务 10：Docker 部署——前端 dist 拷贝至 backend/static；本地开发走 vite dev server） ----------

_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

if (_STATIC_DIR / "index.html").exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        """SPA 路由 fallback（/login /work /center 直接访问或刷新）；API 与分享路径未匹配 → 404。"""
        if full_path.startswith(("api/", "s/")):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (_STATIC_DIR / full_path).resolve()
        if full_path and str(candidate).startswith(str(_STATIC_DIR.resolve())) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
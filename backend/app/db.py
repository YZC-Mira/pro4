"""SQLite 数据层（选型第 5 节）。

pages（任务 4）+ conversations / messages（任务 5）。
阶段一暂不鉴权：conversations.user_id 预留 NULL，任务 7 补 auth 后写入。
数据目录可用环境变量 ALGOAGENT_DATA_DIR 覆盖（Docker 部署时指向 /data 卷）。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("ALGOAGENT_DATA_DIR", str(BACKEND_DIR / "data")))
DB_PATH = DATA_DIR / "algoagent.db"
PAGES_DIR = Path(os.environ.get("ALGOAGENT_PAGES_DIR", str(BACKEND_DIR / "pages")))

MESSAGE_ROLES = ("user", "assistant")


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_id TEXT UNIQUE NOT NULL,
                conversation_id INTEGER,
                task TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                final_html_path TEXT,
                error_summary TEXT,
                model TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT NOT NULL DEFAULT '新会话',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                attachment_ref TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'registered',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                page_id TEXT NOT NULL,
                expires_at TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages (conversation_id, id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT,
                model_id TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'user',
                owner_id INTEGER,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        # 迁移：pages.user_id（作品归属）+ pages.title（作品命名，默认取任务摘要）
        page_cols = [r["name"] for r in conn.execute("PRAGMA table_info(pages)")]
        if "user_id" not in page_cols:
            conn.execute("ALTER TABLE pages ADD COLUMN user_id INTEGER")
        if "title" not in page_cols:
            conn.execute("ALTER TABLE pages ADD COLUMN title TEXT")
            conn.execute("UPDATE pages SET title = substr(task, 1, 30) WHERE title IS NULL")
        # 迁移：models.enabled（管理员停用内置模型——公网部署控制开放范围）
        model_cols = [r["name"] for r in conn.execute("PRAGMA table_info(models)")]
        if "enabled" not in model_cols:
            conn.execute("ALTER TABLE models ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")


# ---------- models（模型注册表：platform=平台模型全员可用 / user=自定义模型仅归属者） ----------

def create_model(name: str, base_url: str, api_key: str | None, model_id: str,
                 scope: str, owner_id: int | None) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO models (name, base_url, api_key, model_id, scope, owner_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name[:60], base_url, api_key or None, model_id[:120], scope, owner_id, now),
        )
        return int(cur.lastrowid)


def get_model(mid: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM models WHERE id = ?", (mid,)).fetchone()
        return dict(row) if row else None


def list_models_for_user(user_id: int, include_disabled: bool = False) -> list[dict]:
    """用户可选模型：内置模型 + 自己的自定义模型（不含 api_key 明文）。

    include_disabled=False（普通用户）：停用的内置模型不可见。
    include_disabled=True（admin 管理视角）：全部返回，含 enabled 标记。
    """
    where = "(scope = 'platform' OR owner_id = ?)"
    if not include_disabled:
        where += " AND (enabled = 1 OR scope = 'user')"
    with _conn() as conn:
        rows = conn.execute(
            f"""SELECT id, name, base_url, model_id, scope, owner_id, enabled, created_at
                FROM models WHERE {where}
                ORDER BY scope ASC, id ASC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def set_model_enabled(mid: int, enabled: bool) -> bool:
    """停用/启用内置模型（admin）。返回 False = 模型不存在。"""
    with _conn() as conn:
        row = conn.execute("SELECT id FROM models WHERE id = ?", (mid,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE models SET enabled = ? WHERE id = ?", (1 if enabled else 0, mid))
        return True


def delete_model(mid: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM models WHERE id = ?", (mid,))


def get_model_credentials(mid: int) -> dict | None:
    """含 api_key 的完整凭据（仅后端构造 LLMConfig 用，接口层不外露）。"""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM models WHERE id = ?", (mid,)).fetchone()
        return dict(row) if row else None


def update_model(mid: int, name: str, base_url: str, model_id: str,
                 api_key: str | None = None) -> bool:
    """编辑模型（scope/owner 不可改）。api_key 为 None = 保持原值（表单掩码不回填）。

    返回 False = 模型不存在。
    """
    with _conn() as conn:
        row = conn.execute("SELECT id FROM models WHERE id = ?", (mid,)).fetchone()
        if not row:
            return False
        if api_key is not None and api_key.strip():
            conn.execute(
                "UPDATE models SET name = ?, base_url = ?, model_id = ?, api_key = ? WHERE id = ?",
                (name[:60], base_url, model_id[:120], api_key.strip(), mid),
            )
        else:
            conn.execute(
                "UPDATE models SET name = ?, base_url = ?, model_id = ? WHERE id = ?",
                (name[:60], base_url, model_id[:120], mid),
            )
        return True


def get_default_model() -> dict | None:
    """当前默认内置模型（settings.default_model_id → models 行；无标记/已停用返回 None）。"""
    raw = get_setting("default_model_id")
    if not raw:
        return None
    try:
        row = get_model_credentials(int(raw))
    except (ValueError, TypeError):
        return None
    if row and row.get("enabled", 1) == 0:
        return None  # 默认模型被停用 → 默认链失效（admin 仍可显式选择它）
    return row


def set_default_model(mid: int) -> bool:
    """标记某内置模型为默认（对话未选择模型时使用它）。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, scope FROM models WHERE id = ?", (mid,)
        ).fetchone()
        if not row or row["scope"] != "platform":
            return False
    set_setting("default_model_id", str(mid))
    return True


# ---------- settings（F12 运行时模型配置：DB > env > config.yaml 优先级链的 DB 层） ----------

def get_setting(key: str) -> str | None:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        conn.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, value, now),
        )


def delete_setting(key: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def get_all_settings() -> dict[str, str]:
    with _conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


# ---------- pages ----------

def insert_page(page_id: str, task: str, model: str, conversation_id: int | None = None,
                user_id: int | None = None) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        conn.execute(
            """INSERT INTO pages (page_id, conversation_id, user_id, task, title, model, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (page_id, conversation_id, user_id, task, task[:30], model, now, now),
        )


def update_page(
    page_id: str,
    status: str,
    attempt_count: int,
    final_html_path: str | None,
    error_summary: str,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        conn.execute(
            """UPDATE pages SET status = ?, attempt_count = ?, final_html_path = ?,
               error_summary = ?, updated_at = ? WHERE page_id = ?""",
            (status, attempt_count, final_html_path, error_summary, now, page_id),
        )


def get_page(page_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM pages WHERE page_id = ?", (page_id,)).fetchone()
        return dict(row) if row else None


def conversation_exists(conversation_id: int) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return row is not None


# ---------- conversations / messages ----------

def create_conversation(title: str = "新会话", user_id: int | None = None) -> int:
    title = (title or "").strip() or "新会话"
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (user_id, title, created_at) VALUES (?, ?, ?)",
            (user_id, title[:200], now),
        )
        return int(cur.lastrowid)


def list_conversations(user_id: int | None = None) -> list[dict]:
    """会话列表；user_id 非空时只返回该用户的会话（多会话切换，2026-10-25）。"""
    with _conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE user_id = ? ORDER BY id DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM conversations ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def get_latest_page_by_conversation(conversation_id: int, include_pending: bool = False) -> dict | None:
    """会话内最近一件作品。

    include_pending=False（默认）：仅成功作品（切换会话恢复预览用——迭代依赖 currentPageId）。
    include_pending=True：任意状态最近一件（刷新恢复检测"生成中"用——前端按 status 判断）。
    """
    where = "conversation_id = ?" + ("" if include_pending else " AND status = 'passed'")
    with _conn() as conn:
        row = conn.execute(
            f"""SELECT page_id, task, status, final_html_path, created_at, updated_at
                FROM pages WHERE {where}
                ORDER BY id DESC LIMIT 1""",
            (conversation_id,),
        ).fetchone()
        return dict(row) if row else None


def rename_conversation(conversation_id: int, title: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title[:60], conversation_id),
        )


def get_conversation(conversation_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return dict(row) if row else None


def delete_conversation(conversation_id: int) -> None:
    """删除会话：conversations 行 + messages 级联删除。

    作品不删（会话=对话过程，作品=成果资产，两者解耦——删除会话不删作品）。
    """
    with _conn() as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))


def add_message(conversation_id: int, role: str, content: str, attachment_ref: str | None = None) -> int:
    if role not in MESSAGE_ROLES:
        raise ValueError(f"role 必须是 {'/'.join(MESSAGE_ROLES)}，收到：{role!r}")
    content = (content or "").strip()
    if not content:
        raise ValueError("消息内容不能为空")
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO messages (conversation_id, role, content, attachment_ref, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (conversation_id, role, content, attachment_ref, now),
        )
        return int(cur.lastrowid)


def get_messages(conversation_id: int) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]

# ---------- users（任务 7：F6 注册即用；首个注册用户自动成为 admin，PRD 第 3 节） ----------

def count_users() -> int:
    with _conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])


def create_user(username: str, password_hash: str, role: str) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, now),
        )
        return int(cur.lastrowid)


def get_user_by_username(username: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def update_user_password(user_id: int, password_hash: str) -> None:
    """管理员重置用户密码（auth.py pbkdf2 哈希后传入）。"""
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )


def list_all_users() -> list[dict]:
    """用户列表（含作品数统计，管理端用；不返回密码哈希）。"""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT u.id, u.username, u.role, u.created_at,
                      (SELECT COUNT(*) FROM pages p WHERE p.user_id = u.id) AS page_count
               FROM users u ORDER BY u.id ASC"""
        ).fetchall()
        return [dict(r) for r in rows]

# ---------- 作品（F7 我的作品：pages 即作品，覆盖式） ----------

def list_pages_by_user(user_id: int, limit: int = 200) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT page_id, conversation_id, user_id, task, title, status, attempt_count,
                      final_html_path, error_summary, model, created_at, updated_at
               FROM pages WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def rename_page(page_id: str, title: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        conn.execute(
            "UPDATE pages SET title = ?, updated_at = ? WHERE page_id = ?",
            (title[:60], now, page_id),
        )


def list_all_pages(limit: int = 500) -> list[dict]:
    """全部作品列表（管理端用，含作者名；user_id 为 NULL 的旧数据显示"-"）。"""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT p.page_id, p.conversation_id, p.user_id, p.task, p.title, p.status,
                      p.attempt_count, p.final_html_path, p.error_summary, p.model,
                      p.created_at, p.updated_at,
                      COALESCE(u.username, '-') AS author_name
               FROM pages p LEFT JOIN users u ON u.id = p.user_id
               ORDER BY p.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_page(page_id: str) -> None:
    """删除作品：pages 行 + 该作品的全部分享链接（级联清理，防死链）。

    HTML 文件由路由层负责删除（db 层不碰文件系统）。
    """
    with _conn() as conn:
        conn.execute("DELETE FROM pages WHERE page_id = ?", (page_id,))
        conn.execute("DELETE FROM shares WHERE page_id = ?", (page_id,))


def list_share_files_by_page(page_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM shares WHERE page_id = ?", (page_id,)).fetchall()
        return [dict(r) for r in rows]


# ---------- shares（F5 分享交付：token 指向作品当前内容，覆盖式联动） ----------

def create_share(token: str, page_id: str, expires_at: str | None, created_by: int) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO shares (token, page_id, expires_at, created_by, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (token, page_id, expires_at, created_by, now),
        )
        return int(cur.lastrowid)


def get_share_by_token(token: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM shares WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None
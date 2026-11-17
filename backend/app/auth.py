"""认证模块（任务 7：PRD F6 + 选型 auth 模块）。

设计：
- JWT：HS256 手写实现（base64url + hmac + hashlib + json，全标准库），
  零第三方依赖——规避部署环境 pip 安装风险；SECRET_KEY 复用应用级必填
  环境变量（选型 6.1：JWT 签名用）。
- 密码哈希：hashlib.pbkdf2_hmac(sha256, 12 万次迭代 + 16 字节随机盐)，
  存储格式 "pbkdf2_sha256$iterations$salt_hex$hash_hex"。
- 角色模型：admin > registered（注册即用，无身份门槛）> anonymous（游客）。
  首个注册用户自动成为 admin（PRD 第 3 节）。
- FastAPI 依赖项 require_jwt：保护生成类接口；/s/{token} 与静态资源免登录。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 登录态 7 天
_PBKDF2_ITERATIONS = 120_000

_bearer = HTTPBearer(auto_error=False)


# ---------- 密码哈希（pbkdf2，标准库） ----------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        ).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, AttributeError):
        return False


# ---------- JWT（HS256 手写，零依赖） ----------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(seg: str) -> bytes:
    padding = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + padding)


def _secret() -> str:
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        raise RuntimeError("SECRET_KEY 未设置（应用启动层已 fail-fast，此处为防御性兜底）")
    return secret


def issue_token(user_id: int, username: str, role: str) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url_encode(
        json.dumps(
            {
                "sub": user_id,
                "username": username,
                "role": role,
                "iat": int(time.time()),
                "exp": int(time.time()) + TOKEN_TTL_SECONDS,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = _b64url_encode(hmac.new(_secret().encode("utf-8"), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def decode_token(token: str) -> dict:
    """校验签名与过期时间；无效/过期抛 HTTPException 401。"""
    try:
        header_seg, payload_seg, signature_seg = token.split(".")
    except ValueError:
        raise HTTPException(status_code=401, detail="token 格式无效")

    signing_input = f"{header_seg}.{payload_seg}".encode("ascii")
    expected = hmac.new(_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        actual = _b64url_decode(signature_seg)
    except (ValueError, base64.binascii.Error):
        raise HTTPException(status_code=401, detail="token 签名无效")
    if not hmac.compare_digest(actual, expected):
        raise HTTPException(status_code=401, detail="token 签名无效")

    try:
        payload = json.loads(_b64url_decode(payload_seg))
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="token 载荷无效")

    if not isinstance(payload.get("exp"), int) or payload["exp"] < int(time.time()):
        raise HTTPException(status_code=401, detail="token 已过期，请重新登录")
    if not isinstance(payload.get("sub"), int):
        raise HTTPException(status_code=401, detail="token 载荷无效")
    return payload


# ---------- FastAPI 依赖项 ----------

def require_jwt(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """保护生成类接口（PRD F6：游客不能生成）；免登录路由不挂本依赖。"""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="未登录：生成功能需要登录（注册即可用）")
    return decode_token(credentials.credentials)
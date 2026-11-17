"""智能体编排：需求澄清、生成、沙箱验证与自修复（选型第 4 节）。

编排闭环：需求澄清 → 生成 → 沙箱客观校验 →（失败）只携带「当前版本代码 +
错误摘要」回传 LLM 修复（不带全量对话历史，控制 token，选型 3.5 节）→
最多 3 次 → 仍失败则返回最后一版 + 失败原因。

需求澄清（生成前置显式阶段，PRD F1）：
- clarify_task() 由 LLM 判断任务缺失哪些关键信息并提问（一次最多 2-3 问，
  已说明的信息不重复问）；经 /api/chat/clarify（REST）完成，用户回答合并
  进任务文本后再进入生成（generate_page）。
- 后端规则兜底：LLM 未发起澄清且任务缺必澄清项（演示形式/面向对象）时，
  强制追问缺失项——保证"澄清不全不得进入生成"由确定性规则保证。

过程状态演示（PRD F3 / 选型第 4 节 stage 事件）：
- AgentStage 六阶段枚举：clarifying / generating / verifying / repairing /
  done / failed；每进入一个阶段经 on_event 通道推送 ("stage", {...})，
  由 SSE 路由转为 event: stage 帧（复用既有 SSE 通道，不新增连接）。
- repairing 阶段携带 attempt 字段：{"stage":"repairing","attempt":2}。

同一自修复循环内锁定同一模型：循环开始时对生效配置做快照，
后续所有 LLM 调用强制使用该快照（配置中途被改只对新会话生效）。
"""

from __future__ import annotations

import enum
import os
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import db
from ..llm import chat
from ..llm.config import LLMConfig, get_effective_config
from ..sandbox import verify_page_isolated
from .extract import extract_html, extract_json, looks_truncated
from .spec import (
    CLARIFY_SYSTEM_PROMPT,
    ITERATE_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    SPEC_SYSTEM_PROMPT,
    SPEC_USER_TEMPLATE,
)

MAX_ATTEMPTS = 3


class AgentStage(str, enum.Enum):
    """agent 执行阶段状态机（选型第 4 节；只做这 6 个大阶段，不做过细拆分）。"""

    CLARIFYING = "clarifying"
    GENERATING = "generating"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    DONE = "done"
    FAILED = "failed"


def _emit_stage(on_event, stage: AgentStage, attempt: int | None = None) -> None:
    """经 on_event 通道推送 stage 事件（SSE 路由转为 event: stage 帧）。"""
    if on_event is None:
        return
    payload = {"stage": stage.value}
    if attempt is not None:
        payload["attempt"] = attempt
    on_event("stage", payload)


# ---------- 需求澄清（生成前置显式阶段，PRD F1 / 选型第 4 节） ----------

_AUDIENCE_PATTERN = re.compile(
    r"大[一二三四五]|研究生|中小学|初中|高中|入门|初学|新手|专业课|学段|面向.{0,8}学生|教.{0,4}年级"
)
_PRESENTATION_PATTERN = re.compile(
    r"高亮|并排|对比|状态表|轨迹|时间线|点亮|渐进|呈现|演示形式|逐步|并显|表格展示"
)

_FALLBACK_QUESTIONS = {
    "audience": "页面面向哪个学段的学生（如大一入门 / 大二专业课）？",
    "presentation": "演示形式希望哪种（逐步高亮 / 多步状态并排对比 / 状态表格 / 动画轨迹）？",
}


@dataclass
class ClarifyResult:
    """澄清阶段结果；字段名与前端 ClarifyResult 接口一致。"""

    need_clarify: bool = False
    questions: list[str] = field(default_factory=list)
    reason: str = ""
    out_of_scope: bool = False  # 非算法教学任务（闲聊/日常请求）：不生成，返回引导话术
    error: str = ""              # no_model：平台从未配置模型（前端显示引导提示，不进入生成）
    source: str = "llm"  # llm / rule_fallback / service_fallback / no_model


def no_model_configured() -> bool:
    """平台级"从未配置模型"判定：无启用的内置模型，且环境变量未设 LLM_BASE_URL/LLM_API_KEY。

    yaml 出厂默认的内网端点仅本地开发可达；云部署未配模型时任何 LLM 调用都会
    挂到 600s 超时（UI 卡死）——本判定用于快速失败并引导管理员去配置。
    """
    try:
        rows = db.list_models_for_user(0, include_disabled=False)
        if any(m["scope"] == "platform" for m in rows):
            return False
    except Exception:
        pass
    return not (os.environ.get("LLM_BASE_URL") or os.environ.get("LLM_API_KEY"))


def _clarify_cfg(model_cfg: LLMConfig | None) -> LLMConfig:
    """澄清调用配置：短超时 + 小 max_tokens（轻量 JSON 判定，防端点不可达时长时间挂起）。"""
    base = model_cfg or default_model_cfg() or get_effective_config()
    return replace(base, timeout=30.0, max_tokens=512)


def _missing_required_dimensions(task: str) -> list[str]:
    """规则兜底：检测任务文本是否缺必澄清项（演示形式 + 面向对象）。"""
    missing = []
    if not _AUDIENCE_PATTERN.search(task):
        missing.append("audience")
    if not _PRESENTATION_PATTERN.search(task):
        missing.append("presentation")
    return missing


_SCOPE_GUIDE = (
    "我是算法可视化智能体，可以生成冒泡排序、最短路径这类算法的交互式教学动画。"
    "描述一个算法教学需求试试？比如「为大二学生生成 Dijkstra 最短路径的教学动画」。"
)

# LLM 可能输出的内部判断语汇（用户不该看到）——命中即整体替换为标准引导话术
_SCOPE_INTERNAL_TERMS = ("闲聊", "不触发澄清", "范围外", "超出", "无法生成", "任务描述过于", "不属于算法", "不是算法")


def _friendly_scope_reason(reason: str) -> str:
    """净化 out_of_scope 的 reason：空 / 含内部判断语汇 → 标准友好引导话术。

    LLM 偶尔会把内部分析（"属于闲聊，不触发澄清"）直接写进 reason——
    确定性兜底保证用户永远不会看到这类话。
    """
    reason = (reason or "").strip()
    if not reason:
        return _SCOPE_GUIDE
    if any(t in reason for t in _SCOPE_INTERNAL_TERMS):
        return _SCOPE_GUIDE
    return reason


def default_model_cfg() -> LLMConfig | None:
    """平台默认模型 → LLMConfig（澄清/生成兜底共用的模型解析）。

    解析顺序：
    1. settings.default_model_id 标记的内置模型（未停用）；
    2. 第一个启用的内置模型（管理员添加了内置模型即视为平台 LLM 可用——
       哪怕忘记点「设为默认」，澄清与生成也能正常工作）；
    3. None → 调用方回落 env/yaml（部署环境变量兜底）。

    背景（2026-10-26 bug）：模型全部配在 models 表而 settings/env 为空时，
    clarify_task 直调 chat() 走 env/yaml → 无凭据 → 澄清必然失败触发规则兜底，
    闲聊输入也会被误追问（out_of_scope 判断失效）。
    """
    try:
        row = db.get_default_model()
        if not row:
            platform_rows = [
                m for m in db.list_models_for_user(0, include_disabled=False)
                if m["scope"] == "platform" and m.get("enabled", 1)
            ]
            if platform_rows:
                row = db.get_model_credentials(platform_rows[0]["id"])
    except Exception:
        row = None
    if not row or row.get("scope") != "platform":
        return None
    return LLMConfig(
        base_url=row["base_url"],
        api_key=row.get("api_key") or "EMPTY",
        model=row["model_id"],
    )


def clarify_task(task: str, model_cfg: LLMConfig | None = None) -> ClarifyResult:
    """需求澄清：LLM 判断（范围判断/缺哪些关键信息，一次最多 2-3 问），规则兜底补必澄清项。

    - LLM 判定 out_of_scope（非算法教学任务）→ 直接返回引导话术，不生成不追问；
    - LLM 正常返回 → 采纳其问题（截断至 3 个）；
    - LLM 未提问 / 调用失败 / 输出损坏 → 规则兜底：缺必澄清项则强制追问；
    - 两条路径合并后仍无问题 → need_clarify=false，放行生成。
    轻量调用（输出几十 token，秒级）。模型走 default_model_cfg（内置注册优先）。
    """
    task = (task or "").strip()
    if not task:
        return ClarifyResult(reason="任务为空", source="rule_fallback")

    messages = [
        {"role": "system", "content": CLARIFY_SYSTEM_PROMPT},
        {"role": "user", "content": f"教学任务：{task}"},
    ]
    llm_ok = True
    try:
        raw = chat(messages, stream=False, config=_clarify_cfg(model_cfg))
    except Exception:
        raw = None
        llm_ok = False

    # 平台从未配置模型（无内置 + 无 env）且未显式选模型 → 明确报错引导配置，
    # 不走规则兜底（否则用户干等超时）。
    # ※ 显式选了模型（如自定义模型）时连接失败属该模型自身问题（地址/Key 错误），
    #   不归类为"平台未配置"——走常规兜底，避免误导用户去找管理员。
    if not llm_ok and model_cfg is None and no_model_configured():
        return ClarifyResult(
            need_clarify=False,
            questions=[],
            reason="平台暂未配置模型：请管理员到「管理 → 模型配置」添加内置模型后再试",
            error="no_model",
            source="no_model",
        )

    questions: list[str] = []
    reason = ""
    out_of_scope = False
    source = "llm"
    data = extract_json(raw) if raw else None
    if isinstance(data, dict):
        reason = str(data.get("reason", ""))[:200]
        out_of_scope = bool(data.get("out_of_scope"))
        for q in data.get("questions", []):
            q = str(q).strip()
            if q:
                questions.append(q)
        questions = questions[:3]
        if data.get("need_clarify") and not questions:
            questions = []
    else:
        source = "service_fallback"
        reason = ""  # 兜底提问已自说明，不再向用户暴露服务状态术语

    # 范围外（非算法教学任务）：直接返回引导话术——不追问必澄清项（那是算法任务专属规则）
    if out_of_scope:
        friendly = _friendly_scope_reason(reason)
        return ClarifyResult(
            need_clarify=False,
            questions=[],
            reason=friendly,
            out_of_scope=True,
            source=source,
        )

    # 规则兜底：LLM 未问 且 缺必澄清项 → 强制追问缺失项（PRD F1 / 选型第 4 节）
    if not questions:
        missing = _missing_required_dimensions(task)
        if missing:
            questions = [_FALLBACK_QUESTIONS[m] for m in missing]
            reason = reason or "任务缺少必澄清项（演示形式/面向对象），按规则强制追问"
            source = "rule_fallback" if source == "llm" else source

    need = bool(questions)
    return ClarifyResult(need_clarify=need, questions=questions[:3], reason=reason, source=source)




@dataclass
class AttemptRecord:
    """单次尝试（生成或修复）的记录。stage: llm_call / llm_timeout / extract / sandbox。"""

    attempt: int
    kind: str = "generate"
    llm_seconds: float = 0.0
    stage: str = ""
    error: str = ""
    html_path: str | None = None
    truncated: bool = False
    verify: dict | None = None
    ok: bool = False


@dataclass
class GenerateOutcome:
    ok: bool
    page_id: str
    task: str
    model: str
    attempts: list[AttemptRecord] = field(default_factory=list)
    final_html_path: str | None = None
    error_summary: str = ""
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "page_id": self.page_id,
            "task": self.task,
            "model": self.model,
            "final_html_path": self.final_html_path,
            "error_summary": self.error_summary,
            "seconds": self.seconds,
            "attempts": [
                {
                    "attempt": a.attempt,
                    "kind": a.kind,
                    "llm_seconds": a.llm_seconds,
                    "stage": a.stage,
                    "error": a.error,
                    "html_path": a.html_path,
                    "truncated": a.truncated,
                    "verify": a.verify,
                    "ok": a.ok,
                }
                for a in self.attempts
            ],
        }


def _now_page_id() -> str:
    return uuid.uuid4().hex[:12]


def _save_html(page_id: str, attempt: int, html: str) -> Path:
    """覆盖式落盘（作品模型：一份作品 = 一份当前内容，一份 page 对应一份 HTML）。

    每次尝试（首次生成/自修复）直接覆盖同一文件 {page_id}.html，不保留
    历史版本；修复请求携带内存中的当前代码，不依赖磁盘历史。attempt 仅
    用于日志与 AttemptRecord 记录。
    """
    db.PAGES_DIR.mkdir(parents=True, exist_ok=True)
    path = db.PAGES_DIR / f"{page_id}.html"
    path.write_text(html, encoding="utf-8")
    return path


def _classify_llm_error(exc: Exception) -> str:
    name = type(exc).__name__
    if "Timeout" in name or "timeout" in str(exc).lower():
        return "llm_timeout"
    return "llm_call"


class GenerateCancelled(Exception):
    """用户主动停止生成（SSE 断开触发取消标志）。"""


def _chat_collect(cfg: LLMConfig, messages: list[dict], label: str, on_event=None,
                  cancel_event=None) -> str:
    """流式调用 LLM 并实时打印接收进度，返回完整文本。

    长生成在本地推理端点可达数分钟，静默等待会让用户以为卡死——
    每 10 秒报告一次已接收量与耗时（心跳）。
    on_event（可选）：每约 2 秒回调 ("progress", {kb, seconds})，供 SSE 推送前端。
    cancel_event（可选）：threading.Event，置位即抛 GenerateCancelled（用户停止）。
    """
    t0 = time.time()
    chunks: list[str] = []
    received = 0
    last_report = t0
    last_event = t0
    for delta in chat(messages, stream=True, config=cfg):
        if cancel_event is not None and cancel_event.is_set():
            raise GenerateCancelled("用户停止了生成")
        if not delta:
            continue
        chunks.append(delta)
        received += len(delta)
        now = time.time()
        if now - last_report >= 10:
            print(f"      ... {label} 进行中：已接收 {received / 1024:.1f} KB（{now - t0:.0f}s）", flush=True)
            last_report = now
        if on_event is not None and now - last_event >= 2:
            on_event("progress", {"kb": round(received / 1024, 1), "seconds": round(now - t0)})
            last_event = now
    full = "".join(chunks)
    print(f"      ✔ {label} 完成：共 {received / 1024:.1f} KB / {time.time() - t0:.0f}s", flush=True)
    return full


def _run_one(
    cfg: LLMConfig,
    attempt_no: int,
    kind: str,
    messages: list[dict],
    page_id: str,
    current_html: str | None,
    on_event=None,
    cancel_event=None,
) -> tuple[AttemptRecord, str | None]:
    """执行一次 LLM 调用 + 落盘 + 沙箱校验。返回 (记录, 当前版本 HTML)。"""
    rec = AttemptRecord(attempt=attempt_no, kind=kind)
    label = f"第 {attempt_no}/{MAX_ATTEMPTS} 次[{kind}] LLM 流式生成"
    kind_label = {"generate": "首次生成", "iterate": "按反馈调整", "repair": "自修复"}.get(kind, kind)
    print(f"  ▶ 第 {attempt_no}/{MAX_ATTEMPTS} 次（{kind_label}）：调用 LLM ...", flush=True)
    if on_event is not None:
        on_event("attempt_start", {"attempt": attempt_no, "kind": kind})
    t0 = time.time()
    try:
        raw = _chat_collect(cfg, messages, label, on_event=on_event, cancel_event=cancel_event)
    except GenerateCancelled:
        rec.stage = "cancelled"
        rec.error = "已停止"
        _emit_attempt_done(on_event, rec)
        return rec, current_html
    except Exception as e:
        rec.stage = _classify_llm_error(e)
        rec.error = f"{type(e).__name__}: {e}"
        _emit_attempt_done(on_event, rec)
        return rec, current_html

    rec.llm_seconds = round(time.time() - t0, 1)
    print("      提取 HTML 文档 ...", flush=True)
    html = extract_html(raw)
    if html is None:
        rec.stage = "extract"
        rec.error = "输出中找不到完整 HTML 文档（可能被截断或格式异常）"
        _emit_attempt_done(on_event, rec)
        return rec, current_html

    rec.truncated = looks_truncated(html)
    path = _save_html(page_id, attempt_no, html)
    rec.html_path = str(path)

    print(f"      沙箱校验中（独立子进程 Chromium 四项校验，约 10 秒）：{path.name}", flush=True)
    _emit_stage(on_event, AgentStage.VERIFYING)
    if on_event is not None:
        on_event("sandbox_check", {"attempt": attempt_no, "file": path.name})
    result = verify_page_isolated(path)
    rec.verify = result.to_dict()
    rec.ok = result.ok
    rec.stage = "sandbox"
    if not rec.ok:
        rec.error = result.error_summary
        print(f"      ✘ 校验未过：{result.error_summary[:100]}", flush=True)
    else:
        print("      ✔ 校验通过：无报错 / 控件齐 / 自检入口可调 / 断言全过", flush=True)
    _emit_attempt_done(on_event, rec)
    return rec, html


def _emit_attempt_done(on_event, rec: AttemptRecord) -> None:
    if on_event is None:
        return
    on_event(
        "attempt_done",
        {
            "attempt": rec.attempt,
            "kind": rec.kind,
            "ok": rec.ok,
            "llm_seconds": rec.llm_seconds,
            "error_summary": rec.error if not rec.ok else "校验通过",
        },
    )


def _summary_of(rec: AttemptRecord, current_html: str | None) -> str:
    if rec.stage == "sandbox":
        return rec.error
    parts = []
    if rec.truncated:
        parts.append("输出疑似被截断（缺少 </html> 结尾）")
    if rec.error:
        parts.append(rec.error)
    if current_html is None:
        parts.append("未获得可校验的 HTML 版本")
    return "；".join(parts) or "未知失败"


def _build_repair_messages(task: str, current_html: str | None, error_summary: str) -> list[dict]:
    """修复请求瘦身：只带当前版本代码 + 错误摘要（选型 3.5 节）。"""
    if current_html:
        user = (
            f"教学任务：{task}\n\n"
            f"沙箱校验失败摘要：{error_summary}\n\n"
            f"当前版本完整 HTML：\n{current_html}"
        )
        return [{"role": "system", "content": REPAIR_SYSTEM_PROMPT}, {"role": "user", "content": user}]
    # 无可用代码（提取失败/超时）时退化为携带失败摘要的重新生成
    user = (
        f"教学任务：{task}\n\n"
        f"上一次尝试失败：{error_summary}\n\n"
        f"请严格按生成规范重新生成完整页面。"
    )
    return [{"role": "system", "content": SPEC_SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _run_loop(task: str, page_id: str, initial_html: str | None, on_event=None,
              initial_messages: list[dict] | None = None,
              model_cfg: LLMConfig | None = None,
              cancel_event=None) -> GenerateOutcome:
    """核心循环：generate_page / repair_page / iterate_page 共用。

    initial_html 非空时跳过首次生成（作为"当前版本"进入校验/修复）；
    initial_messages 非空时（iterate_page 场景）第一次尝试直接使用该消息组
    （按用户反馈修改），后续失败仍走常规修复链。
    cancel_event（可选）：用户停止（SSE 断开）→ 取消当前 LLM 调用并不再进入
    下一轮尝试（状态记 cancelled）。
    """
    t0 = time.time()
    cfg_snapshot = model_cfg or default_model_cfg() or get_effective_config()  # 用户选择 > 内置默认 > env/yaml；同一循环内锁定
    attempts: list[AttemptRecord] = []
    current_html = initial_html

    for attempt_no in range(1, MAX_ATTEMPTS + 1):
        if cancel_event is not None and cancel_event.is_set():
            break
        if attempt_no == 1 and current_html is None:
            messages = [
                {"role": "system", "content": SPEC_SYSTEM_PROMPT},
                {"role": "user", "content": SPEC_USER_TEMPLATE.format(task=task)},
            ]
            kind = "generate"
            _emit_stage(on_event, AgentStage.GENERATING)
        elif attempt_no == 1 and initial_messages is not None:
            messages = initial_messages
            kind = "iterate"
            _emit_stage(on_event, AgentStage.GENERATING)
        else:
            summary = _summary_of(attempts[-1], current_html) if attempts else "初始版本校验失败"
            messages = _build_repair_messages(task, current_html, summary)
            kind = "repair"
            _emit_stage(on_event, AgentStage.REPAIRING, attempt=attempt_no)

        rec, current_html = _run_one(cfg_snapshot, attempt_no, kind, messages, page_id, current_html,
                                     on_event=on_event, cancel_event=cancel_event)
        attempts.append(rec)
        if rec.ok:
            break
        if rec.stage == "cancelled":  # 用户停止：不进入自修复，直接终止
            break

    final = attempts[-1]
    cancelled = final.stage == "cancelled"
    if cancelled:
        outcome_status = "cancelled"
    else:
        outcome_status = "passed" if final.ok else "failed"
    outcome = GenerateOutcome(
        ok=final.ok,
        page_id=page_id,
        task=task,
        model=cfg_snapshot.model,
        attempts=attempts,
        final_html_path=final.html_path if final.ok else (final.html_path or (attempts[0].html_path if attempts else None)),
        error_summary=("校验通过" if final.ok else ("已停止" if cancelled else _summary_of(final, current_html))),
        seconds=round(time.time() - t0, 1),
    )
    db.update_page(
        page_id=page_id,
        status=outcome_status,
        attempt_count=len(attempts),
        final_html_path=outcome.final_html_path,
        error_summary=outcome.error_summary,
    )
    if on_event is not None:
        _emit_stage(on_event, AgentStage.DONE if outcome.ok else AgentStage.FAILED)
        on_event(
            "done",
            {
                "ok": outcome.ok,
                "page_id": outcome.page_id,
                "seconds": outcome.seconds,
                "attempts_count": len(outcome.attempts),
                "error_summary": outcome.error_summary,
                "cancelled": cancelled,
            },
        )
    return outcome


def generate_page(task: str, conversation_id: int | None = None, on_event=None,
                  user_id: int | None = None, model_cfg: LLMConfig | None = None,
                  cancel_event=None) -> GenerateOutcome:
    """从教学任务出发的完整生成闭环。

    conversation_id 为空：无会话单次生成（任务 4 行为，向后兼容）；
    非空：会话内生成——任务与结果摘要自动追加为该会话的 user/assistant
    消息，pages.conversation_id 挂载到该会话（任务 5 升级）。
    user_id：作品归属（任务 8 / F7"我的作品"；来自 JWT 用户 id）。
    on_event（可选）：编排事件回调（stage/attempt_start/progress/sandbox_check/
    attempt_done/done），供 SSE 路由实时推送前端（任务 6）。
    model_cfg（可选）：用户在对话中选择的模型（models 表注册项构造的快照）；
    None = 走平台默认配置（DB>env>yaml 优先级链）。同一生成循环内锁定该配置。
    cancel_event（可选）：用户停止（SSE 断开检测置位）→ 取消生成。

    进入本函数即意味着需求澄清已完成（澄清经由 /api/chat/clarify →
    clarify_task() 前置完成，用户回答已合并进任务文本）；此处推送
    stage: clarifying 是阶段流转标记，紧接 generating——前端步骤条由此
    能展示"澄清→生成→验证→自修复→完成"的完整流转。
    """
    task = (task or "").strip()
    if not task:
        raise ValueError("教学任务描述不能为空")

    db.init_db()
    if conversation_id is not None:
        if not db.conversation_exists(conversation_id):
            raise ValueError(f"会话不存在：{conversation_id}")

    _emit_stage(on_event, AgentStage.CLARIFYING)
    _chosen = model_cfg or default_model_cfg() or get_effective_config()
    page_id = _now_page_id()
    db.insert_page(
        page_id=page_id,
        task=task,
        model=_chosen.model,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    outcome = _run_loop(task, page_id, initial_html=None, on_event=on_event,
                        model_cfg=model_cfg, cancel_event=cancel_event)

    if conversation_id is not None:
        db.add_message(conversation_id, "user", task)
        result_text = (
            f"已生成交互式教学页面并通过沙箱校验（{len(outcome.attempts)} 次尝试，"
            f"{outcome.seconds}s）。\n最终版：{outcome.final_html_path}"
            if outcome.ok
            else f"生成未能在 {MAX_ATTEMPTS} 次内通过校验。\n失败原因：{outcome.error_summary}\n"
                 f"最后一版：{outcome.final_html_path or '无'}（可继续对话描述问题，重新生成）"
        )
        db.add_message(conversation_id, "assistant", result_text)
    return outcome


def repair_page(html_path: str | Path, task: str = "") -> GenerateOutcome:
    """对既有页面执行修复闭环（注错自修复验收 / 后续对失败作品再修复复用）。"""
    path = Path(html_path)
    if not path.exists():
        raise FileNotFoundError(f"页面不存在：{path}")
    html = path.read_text(encoding="utf-8")
    task = task.strip() or "修复指定的算法教学可视化页面"

    db.init_db()
    page_id = _now_page_id()
    db.insert_page(page_id=page_id, task=f"[修复] {task}", model=get_effective_config().model)
    return _run_loop(task, page_id, initial_html=html)

def iterate_page(page_id: str, feedback: str, conversation_id: int | None = None,
                 user_id: int | None = None, on_event=None,
                 model_cfg: LLMConfig | None = None,
                 cancel_event=None) -> GenerateOutcome:
    """对话式迭代（PRD F1 上下文）：按用户反馈修改既有作品。

    上下文构成：原始任务（pages.task）+ 用户本次反馈 + 作品当前完整 HTML——
    LLM 据此针对性修改而非全新生成。复用校验/自修复闭环；page_id 沿用原作品
    （覆盖式作品模型：同一文件被覆盖，已分享链接自动展示新内容）。
    归属校验由路由层完成（此处信任 page_id）。
    model_cfg（可选）：用户选择的模型快照；None = 平台默认配置。
    cancel_event（可选）：用户停止（SSE 断开检测置位）→ 取消调整。
    """
    feedback = (feedback or "").strip()
    if not feedback:
        raise ValueError("调整反馈不能为空")

    db.init_db()
    page = db.get_page(page_id)
    if not page:
        raise ValueError(f"作品不存在：{page_id}")
    if not page.get("final_html_path"):
        raise ValueError("作品尚未生成完成，无法调整")

    html_path = Path(page["final_html_path"])
    if not html_path.exists():
        raise ValueError("作品文件缺失，无法调整")
    current_html = html_path.read_text(encoding="utf-8")
    original_task = page.get("task") or ""

    messages = [
        {"role": "system", "content": ITERATE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"原始教学任务：{original_task}\n\n"
                f"用户调整反馈：{feedback}\n\n"
                f"页面当前版本完整 HTML：\n{current_html}"
            ),
        },
    ]
    combined_task = f"{original_task}（用户调整：{feedback}）"
    outcome = _run_loop(
        combined_task,
        page_id,
        initial_html=current_html,
        on_event=on_event,
        initial_messages=messages,
        model_cfg=model_cfg,
        cancel_event=cancel_event,
    )

    if conversation_id is not None:
        db.add_message(conversation_id, "user", feedback)
        result_text = (
            f"已按反馈调整动画并通过沙箱校验（{outcome.seconds}s），分享链接将自动展示新内容。"
            if outcome.ok
            else f"调整未能在 {MAX_ATTEMPTS} 次内通过校验。\n失败原因：{outcome.error_summary}\n"
                 f"可重新描述调整需求再试。"
        )
        db.add_message(conversation_id, "assistant", result_text)
    return outcome
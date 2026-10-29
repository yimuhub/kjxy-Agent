"""工具注册表：注册 / 参数校验 / 权限 / 风险拦截 / 超时 / 结果校验 / 审计。

运行时机制（内存注册表）+ 元数据 seed（数据库 ToolDefinition 行）分离：
- 各业务模块（如知识库）在启动时调用 ``tool_registry.register(...)`` 注册实现；
- ``seed_builtin_tools(db)`` 只写入元数据行，供 GET /tools 列表展示，幂等。
"""

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import jsonschema
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.chat.models import (
    APPROVAL_ASK,
    APPROVAL_AUTO,
    APPROVAL_ESCALATION_ROLES,
    APPROVAL_FULL,
)
from app.modules.tools.models import ToolDefinition
from app.modules.yards.authz import has_permission

log = get_logger(__name__)

ToolFunc = Callable[[dict, YardContext], "dict | Awaitable[dict]"]

RISK_LEVELS = ("read_only", "draft", "write_reversible", "external_side_effect")


@dataclass
class ToolDef:
    code: str
    version: str
    name: str
    description: str
    input_schema: dict
    func: ToolFunc | None = None
    output_schema: dict | None = None
    required_permissions: list[str] = field(default_factory=list)
    risk_level: str = "read_only"
    side_effect: bool = False
    timeout_seconds: int = 30
    enabled: bool = True


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _format_schema_errors(exc: jsonschema.ValidationError) -> list[dict]:
    errors = sorted(exc.context, key=lambda e: str(e.path)) if exc.context else [exc]
    out = []
    for e in errors[:10]:
        path = ".".join(str(p) for p in e.absolute_path) or "(root)"
        out.append({"field": path, "message": e.message})
    return out


def _is_approved(
    ctx: YardContext, params: dict, db: Session | None = None, tool_code: str = ""
) -> tuple[bool, str]:
    """高风险工具是否放行，返回 ``(放行?, 放行依据)``。

    三档授权模式（会话级，``chat_sessions.approval_mode``，缺省 ``ask``）：

    - ``ask`` ：现状——只认已批准的人工审批单，拦下即建单；
    - ``auto``：同一工具+同一参数指纹在**本会话内**已被人工批准过则复用放行
      （含突破 ``tool_approval_max_uses`` 的会话内复用）；从未批准过仍拦下建单；
    - ``full``：本会话高风险工具直接执行、不再建审批单；
      仅小院/平台管理员生效（普通成员即使在会话里选了也按 ``ask`` 走），且每次直接
      执行都单独留痕 ``tool_risk_auto_approved``。

    请求参数里的 ``approved`` 由调用方控制，绝不作为放行依据；
    服务端标记 ``ctx.approved`` 与数据库审批单仍是唯一入口。
    """
    if getattr(ctx, "approved", False) is True:
        return True, "server_flag"
    if db is None or not tool_code or not ctx.yard_id:
        return False, ""
    from app.modules.tools.approvals import has_approved_approval, session_approved_this_call

    mode = getattr(ctx, "approval_mode", APPROVAL_ASK) or APPROVAL_ASK
    if mode == APPROVAL_FULL and set(ctx.roles or []) & set(APPROVAL_ESCALATION_ROLES):
        return True, "session_full_access"
    if mode == APPROVAL_AUTO and ctx.session_id:
        if session_approved_this_call(
            db,
            yard_id=ctx.yard_id,
            tool_code=tool_code,
            params=params,
            chat_session_id=ctx.session_id,
        ):
            return True, "session_auto_reuse"
    if has_approved_approval(
        db,
        yard_id=ctx.yard_id,
        tool_code=tool_code,
        params=params,
        # 会话归属必须一起看：A 会话批准的单不能被 B 会话或任务借用
        caller_session_id=ctx.session_id or "",
    ):
        return True, "approval_ticket"
    return False, ""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool_def: ToolDef) -> None:
        if not tool_def.code:
            raise ValueError("tool code 不能为空")
        if tool_def.risk_level not in RISK_LEVELS:
            raise ValueError(f"非法 risk_level：{tool_def.risk_level}")
        self._tools[tool_def.code] = tool_def
        log.info("tool registered: %s v%s", tool_def.code, tool_def.version)

    def get(self, code: str) -> ToolDef:
        tool = self._tools.get(code)
        if tool is None or not tool.enabled:
            raise AppError("TOOL_NOT_FOUND", f"工具不存在或未启用：{code}。", http_status=404)
        return tool

    def list_tools(self) -> list[ToolDef]:
        return sorted((t for t in self._tools.values() if t.enabled), key=lambda t: t.code)

    def _audit(
        self,
        db: Session,
        ctx: YardContext,
        code: str,
        request_id: str,
        *,
        result: str,
        latency_ms: int,
        extra: dict | None = None,
    ) -> None:
        metadata: dict = {"latency_ms": latency_ms, "code": code}
        if extra:
            metadata.update(extra)
        log_audit(
            db,
            yard_id=ctx.yard_id,
            actor_id=ctx.user_id,
            action="tool_executed",
            resource_type="tool",
            resource_id=code,
            result=result,
            request_id=request_id,
            metadata=metadata,
        )

    def _db_disabled(self, db: Session, code: str, yard_id: str | None) -> bool:
        """元数据行显式下线 → 运行时同样不可用（与 GET /tools、插件校验同口径）。"""
        row = (
            db.query(ToolDefinition)
            .filter(ToolDefinition.code == code)
            .filter(
                (ToolDefinition.yard_id.is_(None)) | (ToolDefinition.yard_id == yard_id)
                if yard_id
                else ToolDefinition.yard_id.is_(None)
            )
            .first()
        )
        return row is not None and not row.enabled

    def _resolve(self, db: Session, code: str, ctx: YardContext) -> ToolDef:
        """工具解析：内置内存注册表优先 → 小院 MCP 动态工具 → 小院自定义工具。

        W5a 改造点（execute 主流程唯一改造）：``mcp__`` 前缀的 wire_name 从不注册
        进全局注册表，此前一律 TOOL_NOT_FOUND；现在经 tool_bridge 按**调用方小院
        上下文**作用域解析（enabled + 未熔断 + 连接测试缓存命中才可见）。非 mcp__
        前缀行为逐字不变——executor/workorder/approvals/qa_agent 等既有调用方
        零感知（execute 签名未动，ctx/db 本就是既有入参）。

        v3.25 追加两条：
        - 内置工具若在元数据里被显式下线，运行时同样拒绝（此前只有插件链路把关）；
        - ``source=custom`` 的自定义工具按 ``impl_kind`` 现场构建可执行 ToolDef，
          定义即本次调用的契约，执行期不重复取行。
        """
        tool = self._tools.get(code)
        if tool is not None and tool.enabled:
            if self._db_disabled(db, code, ctx.yard_id):
                raise AppError("TOOL_NOT_FOUND", f"工具已被下线：{code}。", http_status=404)
            return tool
        if code.startswith("mcp__"):
            from app.modules.mcp.tool_bridge import lookup_mcp_tool

            # 导入放在分支内：mcp.tool_bridge 反向 import 本模块，避免顶层循环
            mcp_tool = lookup_mcp_tool(db, ctx.yard_id, code)
            if mcp_tool is not None:
                return mcp_tool
        from app.modules.tools.custom_runtime import build_custom_tool

        row = (
            db.query(ToolDefinition)
            .filter(ToolDefinition.code == code, ToolDefinition.yard_id == ctx.yard_id)
            .first()
        )
        custom_tool = build_custom_tool(row, db) if row is not None else None
        if custom_tool is not None:
            return custom_tool
        raise AppError("TOOL_NOT_FOUND", f"工具不存在或未启用：{code}。", http_status=404)

    async def execute(
        self,
        db: Session,
        code: str,
        params: dict | None,
        ctx: YardContext,
        request_id: str,
    ) -> dict:
        tool = self._resolve(db, code, ctx)
        params = dict(params or {})
        param_keys = sorted(str(k) for k in params.keys())

        # ① 参数 Schema 校验
        try:
            jsonschema.validate(instance=params, schema=tool.input_schema)
        except jsonschema.ValidationError as e:
            self._audit(
                db,
                ctx,
                code,
                request_id,
                result="failure",
                latency_ms=0,
                extra={"stage": "input_validation", "param_keys": param_keys},
            )
            raise AppError(
                "TOOL_VALIDATION_FAILED",
                "工具参数校验失败，请检查后重试。",
                http_status=400,
                details={"code": code, "fields": _format_schema_errors(e)},
            ) from e

        # ② 权限检查：需同时拥有工具要求的所有权限点（DB 角色覆盖 + 逐人授权）
        if not all(
            has_permission(
                db,
                yard_id=ctx.yard_id,
                user_id=ctx.user_id,
                jwt_roles=ctx.roles,
                permission=perm,
            )
            for perm in tool.required_permissions
        ):
            self._audit(
                db,
                ctx,
                code,
                request_id,
                result="denied",
                latency_ms=0,
                extra={
                    "stage": "permission",
                    "param_keys": param_keys,
                    "required_permissions": tool.required_permissions,
                },
            )
            raise AppError("TOOL_FORBIDDEN", "当前角色无权调用该工具。", http_status=403)

        # ③ 高风险拦截：external_side_effect 按本会话授权模式（ask/auto/full）判定。
        # ask=未获批即建（去重）pending 审批单，调用方凭 approval_id 走审批流；
        # auto/full 的放行留痕写在④之后（log_bypass），带工具的真实结局。
        risk_via = ""
        if tool.risk_level == "external_side_effect":
            allowed, risk_via = _is_approved(ctx, params, db, code)
            if not allowed:
                from app.modules.tools.approvals import get_or_create_pending_approval

                approval = get_or_create_pending_approval(
                    db,
                    yard_id=ctx.yard_id,
                    tool_code=code,
                    params=params,
                    requested_by=ctx.user_id,
                    request_id=request_id,
                    chat_session_id=getattr(ctx, "session_id", "") or None,
                )
                self._audit(
                    db,
                    ctx,
                    code,
                    request_id,
                    result="denied",
                    latency_ms=0,
                    extra={
                        "stage": "risk_approval",
                        "param_keys": param_keys,
                        "risk_level": tool.risk_level,
                        "approval_id": approval.id,
                        "approval_mode": getattr(ctx, "approval_mode", APPROVAL_ASK),
                    },
                )
                # 记入本轮上下文：对话链路据此在回答里给出「去批准」入口
                pending_requests = getattr(ctx, "approval_requests", None)
                if isinstance(pending_requests, list):
                    pending_requests.append(
                        {"tool_code": code, "approval_id": approval.id, "reason": approval.reason}
                    )
                raise AppError(
                    "TOOL_RISK_BLOCKED",
                    "高风险工具需人工审批通过后方可调用。",
                    http_status=403,
                    details={
                        "approval_id": approval.id,
                        "tool_code": code,
                        "approval_mode": getattr(ctx, "approval_mode", APPROVAL_ASK),
                    },
                )

        # ④ 执行（含超时）
        bypass_logged = False

        def log_bypass(ok: bool, latency_ms: int) -> None:
            """免批准放行的留痕（auto/full）。

            刻意写在**执行之后**：闸门放行不等于工具跑成功，先写 success 会把一次
            跑挂了的全自动调用记成「成功旁路」，事后审计就看不出真实后果了。
            full 档不产生审批单，这两条留痕是该调用唯一的书面证据，必须带真实结局。
            """
            nonlocal bypass_logged
            if not risk_via or bypass_logged:
                return
            bypass_logged = True
            outcome = "success" if ok else "failure"
            self._audit(
                db,
                ctx,
                code,
                request_id,
                result=outcome,
                latency_ms=latency_ms,
                extra={
                    "stage": "risk_gate",
                    "risk_level": tool.risk_level,
                    "bypass": risk_via,
                    "approval_mode": getattr(ctx, "approval_mode", APPROVAL_ASK),
                    "session_id": getattr(ctx, "session_id", ""),
                    "param_keys": param_keys,
                },
            )
            log_audit(
                db,
                yard_id=ctx.yard_id,
                actor_id=ctx.user_id,
                action="tool_risk_auto_approved",
                resource_type="tool",
                resource_id=code,
                result=outcome,
                request_id=request_id,
                metadata={
                    "via": risk_via,
                    "approval_mode": getattr(ctx, "approval_mode", APPROVAL_ASK),
                    "session_id": getattr(ctx, "session_id", ""),
                    "param_keys": param_keys,
                    "executed_ok": ok,
                },
            )

        if tool.func is None:
            self._audit(
                db,
                ctx,
                code,
                request_id,
                result="failure",
                latency_ms=0,
                extra={"stage": "no_implementation", "param_keys": param_keys},
            )
            log_bypass(False, 0)
            raise AppError(
                "INTERNAL_ERROR",
                "工具实现尚未注册，无法执行。",
                http_status=500,
                details={"code": code},
            )
        started = time.perf_counter()
        try:
            raw = tool.func(params, ctx)
            result = (
                await asyncio.wait_for(raw, timeout=tool.timeout_seconds)
                if inspect.isawaitable(raw)
                else raw
            )
        except TimeoutError as e:
            self._audit(
                db,
                ctx,
                code,
                request_id,
                result="failure",
                latency_ms=_ms(started),
                extra={"stage": "executing", "param_keys": param_keys},
            )
            log_bypass(False, _ms(started))
            raise AppError(
                "TOOL_TIMEOUT",
                f"工具执行超时（{tool.timeout_seconds} 秒）。",
                http_status=504,
                details={"code": code, "timeout_seconds": tool.timeout_seconds},
            ) from e
        except AppError:
            self._audit(
                db,
                ctx,
                code,
                request_id,
                result="failure",
                latency_ms=_ms(started),
                extra={"stage": "executing", "param_keys": param_keys},
            )
            log_bypass(False, _ms(started))
            raise
        except Exception as e:
            self._audit(
                db,
                ctx,
                code,
                request_id,
                result="failure",
                latency_ms=_ms(started),
                extra={"stage": "executing", "param_keys": param_keys},
            )
            log_bypass(False, _ms(started))
            raise AppError(
                "INTERNAL_ERROR", "工具执行出现内部错误。", http_status=500, details={"code": code}
            ) from e

        # 工具确实跑完了：此时才落免批准放行的留痕，结局写的就是真实结果
        log_bypass(True, _ms(started))

        if not isinstance(result, dict):
            self._audit(
                db,
                ctx,
                code,
                request_id,
                result="failure",
                latency_ms=_ms(started),
                extra={"stage": "output_type", "param_keys": param_keys},
            )
            raise AppError(
                "INTERNAL_ERROR", "工具返回结果格式异常。", http_status=500, details={"code": code}
            )

        # ⑤ 返回值 Schema 校验（若定义了 output_schema）
        if tool.output_schema:
            try:
                jsonschema.validate(instance=result, schema=tool.output_schema)
            except jsonschema.ValidationError as e:
                self._audit(
                    db,
                    ctx,
                    code,
                    request_id,
                    result="failure",
                    latency_ms=_ms(started),
                    extra={"stage": "output_validation", "param_keys": param_keys},
                )
                raise AppError(
                    "INTERNAL_ERROR",
                    "工具返回结果不符合约定格式。",
                    http_status=500,
                    details={"code": code, "fields": _format_schema_errors(e)},
                ) from e

        # ⑥ 审计（成功）：只记 params 的 key 列表，不记敏感值
        latency_ms = _ms(started)
        self._audit(
            db,
            ctx,
            code,
            request_id,
            result="success",
            latency_ms=latency_ms,
            extra={
                "param_keys": param_keys,
                "risk_level": tool.risk_level,
                "version": tool.version,
            },
        )
        return result


# 进程级默认注册表：各业务模块启动时向其注册实现
tool_registry = ToolRegistry()


BUILTIN_TOOLS: list[dict] = [
    {
        "code": "knowledge.search",
        "version": "1.0.0",
        "name": "知识检索",
        "description": "检索本小院知识库片段，返回带来源的引用。实现由知识库模块注册。",
        "input_schema_json": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "description": "检索问题"},
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                    "description": "返回片段数",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "output_schema_json": {
            "type": "object",
            "properties": {
                "hits": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "命中的文档片段（含来源引用）",
                },
            },
            "required": ["hits"],
        },
        "required_permissions_json": ["knowledge:read"],
        "risk_level": "read_only",
        "side_effect": False,
        "timeout_seconds": 30,
        "enabled": True,
    },
    {
        "code": "workorder.dispatch",
        "version": "1.0.0",
        "name": "作业工单下发（演示）",
        "description": "高风险演示工具：下发作业工单，须经人工审批。当前为 dry-run 实现。",
        "input_schema_json": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "description": "工单标题"},
                "description": {"type": "string", "description": "工单内容描述"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "urgent"],
                    "default": "normal",
                },
                "assignee": {"type": "string", "description": "指派人"},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        "output_schema_json": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean"},
                "dispatched": {"type": "boolean"},
                "workorder": {"type": "object"},
                "message": {"type": "string"},
            },
            "required": ["dry_run", "dispatched"],
        },
        "required_permissions_json": ["tools:execute"],
        "risk_level": "external_side_effect",
        "side_effect": True,
        "timeout_seconds": 30,
        "enabled": True,
    },
    {
        "code": "sensor.query",
        "version": "1.0.0",
        "name": "传感器读数查询",
        "description": (
            "查询本小院设备读数概况与窗口统计（min/max/avg/样本数），可选规则命中评估。"
            "实现由设备模块注册。"
        ),
        "input_schema_json": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "设备名模糊匹配（仅限本小院设备）；缺省为本小院全部设备",
                },
                "metric": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 50,
                    "description": "指标 code（如 temp）或中文名片段（如“湿度”）反查",
                },
                "hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 720,
                    "default": 24,
                    "description": "统计窗口（小时），1..720，默认 24",
                },
                "alert_rule": {
                    "type": "boolean",
                    "default": False,
                    "description": "true 时附带窗口内启用规则的命中点数与最近一次触发值",
                },
            },
            "additionalProperties": False,
        },
        "output_schema_json": {
            "type": "object",
            "properties": {
                "window_hours": {"type": "integer"},
                "device_count": {"type": "integer"},
                "devices": {"type": "array", "items": {"type": "object"}},
                "summary_text": {"type": "string"},
            },
            "required": ["window_hours", "device_count", "devices", "summary_text"],
        },
        # 读=小院成员（devices 读接口同口径），不设额外权限点
        "required_permissions_json": [],
        "risk_level": "read_only",
        "side_effect": False,
        "timeout_seconds": 15,
        "enabled": True,
    },
]


def seed_builtin_tools(db: Session) -> int:
    """写入基础工具元数据行；已存在则更新描述性字段（保留 enabled 开关）。幂等。

    返回处理条数。真实的可执行实现由各业务模块调用 tool_registry.register 注册。
    """
    count = 0
    for meta in BUILTIN_TOOLS:
        row = db.query(ToolDefinition).filter_by(code=meta["code"]).first()
        if row is None:
            row = ToolDefinition(**meta)
            db.add(row)
        else:
            for key, value in meta.items():
                if key != "enabled":
                    setattr(row, key, value)
        count += 1
    db.flush()
    return count

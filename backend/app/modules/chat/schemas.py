"""对话模式 Pydantic 输入模型（消息发送 + 群聊名单/策略）。

既有会话（``ChatSession``）的请求模型仍在 ``router.py`` 内（不搬家，避免无谓
diff）；本文件收**消息与群聊**的请求体——它们的校验比会话复杂（策略 + 点名 +
名单），放独立文件便于契约对齐与单测。

口径约束：
- ``extra="forbid"``：拼错的字段一律 400，不做"静默忽略"的宽容解析；
- ``policy`` 用 ``models.VALID_GROUP_POLICIES`` 校验，未知策略由 FastAPI
  校验器统一给 INVALID_REQUEST(400)，不落到业务层再判；
- ``MessageSendRequest.policy`` 可以为空（不带 = 单聊，走 supervisor 三级路由，
  零回归），带值才进群聊编排；``GroupTurnRequest`` 是群聊专用端点的请求体，
  policy 必有值（缺省 all）；
- ``target`` 只在 ``named`` 策略下有意义；``target_code`` 是 @ 点名的对象
  （助手 id/code/展示名，解析顺序同 ``supervisor.ask_directed``）——两者说的
  是同一件事，``named`` 缺 ``target`` 时回落 ``target_code``，前端不必记两套；
- ``all``/``auto`` 带 target 不报错，但业务层忽略它（点名的活儿归 named）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.chat.models import POLICY_ALL, POLICY_NAMED, VALID_GROUP_POLICIES

_GROUP_TARGET_CAP = 100
#: @ 点名对象的长度上限（id/code/展示名同一口径）
_MENTION_CAP = 100


class ParticipantSetRequest(BaseModel):
    """整表覆盖会话的群聊成员名单（PUT 幂等：空数组 = 清空名单）。"""

    model_config = ConfigDict(extra="forbid")

    agent_ids: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("agent_ids")
    @classmethod
    def _ids_clean(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v:
            aid = str(raw or "").strip()
            if aid and aid not in out:
                out.append(aid)
        return out


class MessageSendRequest(BaseModel):
    """发消息：content 为问题原文；attachment_ids 为随消息引用的附件。

    新增的可选字段（都不带时请求形状与历史完全一致）：
    - ``policy``：群聊本轮应答策略（all|named|auto）；不带即单聊；
    - ``target``：群聊 ``named`` 的点名对象；
    - ``target_code``：@ 点名定向执行的对象（单聊与群聊通用）；
    - ``execution_mode``：``standard``（默认）| ``commander``（指挥官编排）。
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=8000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)
    policy: str | None = Field(default=None, max_length=10)
    target: str | None = Field(default=None, max_length=_GROUP_TARGET_CAP)
    target_code: str | None = Field(default=None, max_length=_MENTION_CAP)
    execution_mode: str | None = Field(default=None, max_length=12)
    #: 澄清续跑：用户在澄清表单里补充信息后回传原 run_id，
    #: 服务端把原目标与补充内容合并后重新规划，并跳过二次追问。
    clarify_run_id: str | None = Field(default=None, max_length=36)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content 不能为空白")
        return v.strip()

    @field_validator("attachment_ids")
    @classmethod
    def _attachment_ids_clean(cls, v: list[str]) -> list[str]:
        return [str(x).strip() for x in v if str(x).strip()]

    @field_validator("policy")
    @classmethod
    def _policy_known(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in VALID_GROUP_POLICIES:
            raise ValueError(f"policy 只能是 {'|'.join(VALID_GROUP_POLICIES)}")
        return v

    @field_validator("target", "target_code")
    @classmethod
    def _target_strip(cls, v: str | None) -> str | None:
        clean = (v or "").strip()
        return clean or None

    @field_validator("execution_mode")
    @classmethod
    def _mode_known(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip().lower()
        if clean not in ("standard", "commander"):
            raise ValueError("execution_mode 只能是 standard|commander")
        return clean

    @model_validator(mode="after")
    def _named_needs_target(self) -> MessageSendRequest:
        """named 策略缺点名对象 → 400（不静默降级成 auto/all，那是猜用户意图）。"""
        if self.policy == POLICY_NAMED and not self.group_target:
            raise ValueError("policy=named 必须指定 target（助手 id/code/名称）")
        return self

    @property
    def group_target(self) -> str | None:
        """``named`` 实际使用的点名对象：``target`` 优先，回落 @ 点名的 ``target_code``。"""
        return self.target or self.target_code


class GroupTurnRequest(MessageSendRequest):
    """群聊一轮发言（``/messages/group*`` 端点）：policy 必有值。

    与 ``/messages`` 的差别只在默认值——群聊端点没有"不带 policy 即单聊"这层
    语义，缺省按 all（把问题抛给在场每位助手）执行。
    """

    policy: str = Field(default=POLICY_ALL)


__all__ = ["GroupTurnRequest", "MessageSendRequest", "ParticipantSetRequest"]

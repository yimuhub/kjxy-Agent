"""小院模块 Schema。"""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class YardOut(BaseModel):
    yard_id: str
    name: str
    status: str
    province: str | None = None
    city: str | None = None
    scene_type: str | None = None
    config: dict | None = None


class YardMemberOut(BaseModel):
    user_id: str
    username: str
    display_name: str
    email: str | None = None
    phone: str | None = None
    role: str
    role_name: str | None = None
    status: str
    joined_at: datetime
    last_login_at: datetime | None = None
    must_change_password: bool = False
    #: 近 7 天调用次数（占位，由运营洞察模块补充）
    tokens_7d: int = 0


class MemberCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    #: 为空时服务端生成一次性初始密码
    password: str | None = Field(default=None, min_length=1, max_length=128)
    role: str = Field(default="viewer", description="yard_admin|expert|operator|viewer")
    note: str | None = Field(default=None, max_length=300)


class MemberCreateResult(BaseModel):
    member: YardMemberOut
    #: 仅新建账号时返回一次；挂接已有账号时为 None
    initial_password: str | None = None


class MemberUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    role: str | None = None
    status: str | None = Field(default=None, description="active|disabled")
    note: str | None = Field(default=None, max_length=300)


class MemberPasswordResetResult(BaseModel):
    user_id: str
    temporary_password: str
    must_change_password: bool = True


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    display_name: str | None = Field(default=None, max_length=64)
    yard_id: str = Field(..., min_length=1, max_length=36)
    note: str | None = Field(default=None, max_length=300)


class RegisterResult(BaseModel):
    request_id: str
    #: pending=待审核；active=注册模式 open，已直接加入
    status: str
    message: str


class RegistrationOut(BaseModel):
    id: str
    user_id: str
    username: str
    display_name: str
    email: str | None = None
    phone: str | None = None
    note: str | None = None
    status: str
    created_at: datetime
    reviewed_at: datetime | None = None
    reject_reason: str | None = None


class RegistrationApproveResult(BaseModel):
    request: RegistrationOut
    member: YardMemberOut


class RegistrationRejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class RoleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    permissions: list[str] | None = None
    # 变更含保护型权限时必须由负责人显式二次确认
    confirm_protected: bool = False


class RoleCreateRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=32)
    name: str = Field(..., min_length=1, max_length=64)
    permissions: list[str] = Field(default_factory=list)
    confirm_protected: bool = False


class AddMemberRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    role: str = Field(..., description="yard_admin|expert|operator|viewer|service_worker")
    display_name: str | None = Field(default=None, max_length=64)


class BatchMemberItem(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    role: str = Field(..., description="yard_admin|expert|operator|viewer|service_worker")
    display_name: str | None = Field(default=None, max_length=64)


class BatchAddMembersRequest(BaseModel):
    """批量添加已有账号为成员；逐条独立成败，单条失败不影响其余。"""

    members: list[BatchMemberItem] = Field(..., min_length=1, max_length=100)


class BatchMemberFailure(BaseModel):
    username: str
    error_code: str
    message: str


class BatchAddMembersResult(BaseModel):
    added: list[YardMemberOut]
    failed: list[BatchMemberFailure]


class InvitationCreateRequest(BaseModel):
    role: str = Field(..., description="yard_admin|expert|operator|viewer|service_worker")
    expires_in_days: int = Field(default=7, ge=1, le=30)
    note: str | None = Field(default=None, max_length=120)


class InvitationOut(BaseModel):
    id: str
    role: str
    note: str | None = None
    # pending | accepted | revoked | expired（expired 为按 expires_at 计算的展示态）
    status: str
    expires_at: datetime
    created_at: datetime
    created_by: str | None = None
    accepted_at: datetime | None = None
    # 仅创建时返回一次明文邀请链接路径（如 /invite/<token>），由前端拼 origin
    invite_path: str | None = None


class InvitationPreview(BaseModel):
    """公开预览：接受邀请页展示空间名与授予角色，不泄露成员与配置。"""

    yard_id: str
    yard_name: str
    role: str
    note: str | None = None
    expires_at: datetime


class InvitationAcceptRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)


class YardSignupRequest(BaseModel):
    """公开自助注册请求。

    密码长度不在此写死：由 signup 业务层按 settings.password_min_length 校验
    （生产环境再强制不低于 10 位）。
    """

    yard_name: str = Field(..., min_length=2, max_length=64)
    admin_username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=1, max_length=128)
    contact: str | None = Field(default=None, max_length=128)


class YardSignupResponse(BaseModel):
    yard_id: str
    user_id: str
    access_token: str
    token_type: str = "Bearer"


class QuotaAdminPatchRequest(BaseModel):
    """运营调整小院配额限额：三个限额字段均 ge=0，至少提供一个。"""

    llm_calls_limit: int | None = Field(default=None, ge=0, description="LLM 调用限额")
    knowledge_docs_limit: int | None = Field(default=None, ge=0, description="知识文档限额")
    tasks_limit: int | None = Field(default=None, ge=0, description="任务限额")

    @model_validator(mode="after")
    def _require_at_least_one(self) -> "QuotaAdminPatchRequest":
        if (
            self.llm_calls_limit is None
            and self.knowledge_docs_limit is None
            and self.tasks_limit is None
        ):
            raise ValueError("至少提供一个配额限额字段")
        return self


class QuotaAdminResetRequest(BaseModel):
    """清零已用量：``kinds`` 为空表示三类全部清零。"""

    kinds: list[str] = Field(
        default_factory=list,
        description="要清零的额度类型：llm_calls / knowledge_docs / tasks；空=全部",
    )

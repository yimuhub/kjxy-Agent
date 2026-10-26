"""devices 模块 Pydantic 模型（入参校验 + 出参形状）。

出参安全铁律：连接器 headers 的密钥值只在【写入】时接收明文，读取/列表
一律返回掩码（mask），密文与明文都不出接口（对齐 llm 供应商密钥纪律）。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.devices.metrics import DEVICE_TYPES, SOURCE_TYPES

OpType = Literal["lt", "gt"]
SourceType = Literal["webhook", "connector"]
DeviceType = Literal["weather", "soil", "pest", "greenhouse", "other"]


class FieldMapEntry(BaseModel):
    """厂商字段 → 统一指标的映射条目。"""

    metric: str = Field(description="统一指标 code（见 /devices/capabilities）；未归类填 other")
    unit: str | None = Field(default=None, description="可选：覆盖字典默认单位")


class ConnectorConfigIn(BaseModel):
    """自定义连接器配置（写入形态；headers 值为明文，入库即 Fernet 加密）。"""

    url: str = Field(min_length=8, max_length=500)
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] | None = Field(
        default=None, description="鉴权头（如 Authorization/X-Api-Key），入库加密"
    )
    body_template: dict[str, Any] | None = Field(default=None, description="POST 时的 JSON 请求体")
    interval_seconds: int = Field(ge=60, le=86400, default=300)
    json_path: str = Field(
        default="$",
        description="取数路径（点分 + [*]，如 $.data.items[*] 或 $.temp）；缺省取整个响应",
    )
    field_map: dict[str, FieldMapEntry] = Field(
        min_length=1, description="厂商字段(支持点分路径) → 统一指标"
    )
    timeout_seconds: int = Field(ge=1, le=30, default=10)
    time_field: str | None = Field(default=None, description="记录内时间戳字段名（ISO8601），可选")


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    device_type: DeviceType = "other"
    source_type: SourceType
    model_hint: str | None = Field(default=None, max_length=120)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    location_note: str | None = None
    connector: ConnectorConfigIn | None = Field(
        default=None, description="source_type=connector 时必填"
    )

    def validate_shape(self) -> None:
        from app.core.errors import AppError

        if self.device_type not in DEVICE_TYPES or self.source_type not in SOURCE_TYPES:
            raise AppError("INVALID_REQUEST", "设备类型或接入方式不合法。", http_status=400)
        if self.source_type == "connector" and self.connector is None:
            raise AppError(
                "INVALID_REQUEST", "connector 设备必须提供 connector 配置。", http_status=400
            )
        if self.source_type == "webhook" and self.connector is not None:
            raise AppError(
                "INVALID_REQUEST",
                "webhook 设备不接受 connector 配置（推送方自行上报字段）。",
                http_status=400,
            )


class DevicePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    device_type: DeviceType | None = None
    model_hint: str | None = Field(default=None, max_length=120)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    location_note: str | None = None
    enabled: bool | None = None
    connector: ConnectorConfigIn | None = Field(
        default=None, description="仅 connector 设备可换配置"
    )


class ConnectorConfigOut(BaseModel):
    """连接器配置出参：headers 全部掩码。"""

    url: str
    method: str
    headers_masked: dict[str, str] = Field(default_factory=dict)
    body_template: dict[str, Any] | None = None
    interval_seconds: int
    json_path: str
    field_map: dict[str, FieldMapEntry]
    timeout_seconds: int
    time_field: str | None = None


class DeviceOut(BaseModel):
    id: str
    yard_id: str
    name: str
    device_type: str
    source_type: str
    model_hint: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    location_note: str | None = None
    enabled: bool
    created_by: str | None = None
    created_at: str
    updated_at: str
    has_ingest_token: bool = False
    connector: ConnectorConfigOut | None = None
    # 连接器轮询状态
    last_polled_at: str | None = None
    consecutive_failures: int = 0
    last_error: str | None = None


class IngestBody(BaseModel):
    """设备侧推送报文：data 为 厂商字段→原始值 的扁平/嵌套对象（连接器设备可带 field_map 配置）。"""

    token: str | None = Field(default=None, description="也可用 Header X-Device-Token")
    reported_at: str | None = Field(default=None, description="ISO8601；缺省取服务器时间")
    data: dict[str, Any] = Field(min_length=1)


class RuleCreate(BaseModel):
    metric: str = Field(min_length=1, max_length=50)
    op: OpType
    threshold: float
    device_id: str | None = None
    enabled: bool = True
    notify_note: str | None = None


class RulePatch(BaseModel):
    metric: str | None = Field(default=None, min_length=1, max_length=50)
    op: OpType | None = None
    threshold: float | None = None
    device_id: str | None = None
    enabled: bool | None = None
    notify_note: str | None = None


class RuleOut(BaseModel):
    id: str
    yard_id: str
    device_id: str | None
    metric: str
    op: str
    threshold: float
    enabled: bool
    notify_note: str | None = None
    created_at: str
    updated_at: str


class AlertOut(BaseModel):
    id: str
    yard_id: str
    rule_id: str | None
    device_id: str
    device_name: str | None = None
    metric: str
    metric_name: str | None = None
    value: float | None
    triggered_at: str
    notified_text: str | None = None
    # 一键工单：tasks 表 workorder.dispatch 任务 id；null=尚未开工单。
    # workorder_no 为展示用编号（当前平台无独立工单序列表，编号即任务 id）。
    workorder_id: str | None = None
    workorder_no: str | None = None

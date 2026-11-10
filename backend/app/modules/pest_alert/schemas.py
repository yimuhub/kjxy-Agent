"""虫情预警的输入/输出 Pydantic 模型（Pydantic v2）。

v3.76 重造：脚手架示例骨架替换为真实业务契约——
输入 = 用户关注点 + 作物 + 检索条数；输出 = 结构化预警（等级/结论/风险/建议）
+ 人类可读正文（answer，通用渲染）+ 诚实降级警告。
"""

from typing import Literal

from pydantic import BaseModel, Field

PestAlertLevel = Literal["none", "watch", "warning", "critical"]


class PestAlertInput(BaseModel):
    """虫情预警输入。"""

    query: str = Field(
        default="",
        max_length=500,
        description="用户关注点，如「稻飞虱虫量上升」「二化螟防治时机」；为空时基于本院数据做通用预警",
    )
    crop: str = Field(default="", max_length=40, description="作物名称，如「水稻」，可空")
    top_k: int = Field(default=5, ge=1, le=20, description="知识检索条数")


class PestAlertOutput(BaseModel):
    """虫情预警输出（结构化 + 人类可读正文）。"""

    answer: str = Field(description="人类可读预警正文（含免责声明），供通用渲染直接展示")
    level: PestAlertLevel = Field(description="预警等级：none/watch/warning/critical")
    summary: str = Field(description="一句话结论")
    risks: list[str] = Field(default_factory=list, description="风险因素清单")
    recommendations: list[str] = Field(default_factory=list, description="防治建议清单")
    evidence: list[dict] = Field(
        default_factory=list,
        description="本院数据证据（告警/读数摘要），绝无数据时为空",
    )
    sources: list[dict] = Field(
        default_factory=list, description="知识检索引用（title/source/quote/score）"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="结果置信度")
    warnings: list[str] = Field(default_factory=list, description="降级/数据不足等诚实警告")

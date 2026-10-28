"""作物诊断 Agent 数据模型（Pydantic v2），对应开发文档 §12.1 V3.0。

输出全部为"诊断辅助"，任何结论都配置信度；专家纠正字段仅预留（corrected_by /
expert_note），纠正流不在本期实现。
"""

from datetime import datetime

from pydantic import BaseModel, Field


class EnvironmentData(BaseModel):
    """观察时的环境数据（可选）。"""

    temperature_c: float | None = Field(default=None, description="气温（℃）")
    humidity_pct: float | None = Field(default=None, ge=0, le=100, description="相对湿度（%）")
    recent_rainfall_mm: float | None = Field(default=None, description="近期降雨量（mm）")
    soil_moisture: str | None = Field(default=None, description="土壤墒情描述")
    notes: str | None = Field(default=None, max_length=2000, description="其他环境备注")


class DiagnosisInput(BaseModel):
    """作物诊断输入。"""

    crop: str = Field(min_length=1, max_length=100, description="作物名称")
    variety: str | None = Field(default=None, max_length=100, description="品种（可选）")
    growth_stage: str | None = Field(default=None, max_length=100, description="生育期（可选）")
    region: str | None = Field(default=None, max_length=100, description="区域（可选）")
    symptoms: str = Field(
        min_length=1,
        max_length=4000,
        description="症状描述（文字）；图片仅作观察辅助，不做图像识别",
    )
    image_refs: list[str] = Field(default_factory=list, description="图片引用（键/URL），仅记录")
    environment: EnvironmentData | None = Field(default=None, description="环境数据（可选）")
    observed_at: datetime | None = Field(default=None, description="观察时间（可选）")


class CandidateIssue(BaseModel):
    """候选问题：始终是可能性排序，绝非确定诊断。"""

    name: str = Field(description="候选问题名称（类别级描述）")
    confidence: float = Field(ge=0.0, le=1.0, description="该候选的可信度")
    matched_symptoms: list[str] = Field(default_factory=list, description="命中的症状关键词")
    notes: str = Field(default="", description="补充说明")
    corrected_by: str | None = Field(default=None, description="专家纠正人（预留）")
    expert_note: str | None = Field(default=None, description="专家纠正备注（预留）")


class EvidenceItem(BaseModel):
    """支持证据：全部来自小院知识库检索命中。"""

    document_id: str
    chunk_id: str
    title: str
    source: str
    page_number: int | None = None
    quote: str
    score: float | None = None


class DiagnosisOutput(BaseModel):
    """作物诊断输出（诊断辅助）。"""

    candidates: list[CandidateIssue] = Field(description="候选问题（按置信度降序）")
    evidence: list[EvidenceItem] = Field(default_factory=list, description="支持证据")
    needs_info: list[str] = Field(default_factory=list, description="需补充的信息/澄清问题")
    risk_notes: list[str] = Field(default_factory=list, description="风险说明")
    next_steps: list[str] = Field(default_factory=list, description="建议的下一步观察")
    needs_expert_review: bool = Field(description="是否需要专家复核")
    medication_intent: bool = Field(default=False, description="问题是否涉及用药（触发用药护栏）")
    confidence: float = Field(ge=0.0, le=1.0, description="整体置信度")
    disclaimer: str = Field(description="诊断辅助免责声明")
    corrected_by: str | None = Field(default=None, description="专家纠正人（预留）")
    expert_note: str | None = Field(default=None, description="专家纠正备注（预留）")

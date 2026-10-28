"""作物诊断业务逻辑。

规则（症状关键词）+ 小院知识库检索（app.modules.knowledge.service.search）组装的
诊断辅助；不调用真实 LLM，不建表、不写 migration、无状态。

硬规则落实：
- 输出永远是"诊断辅助"，每处结论配置信度，绝不写成确定诊断；
- 涉及用药的请求强制 needs_expert_review=True，且不给出确定用量/方案，
  只给"建议咨询专家/查阅当地登记"类表述；
- 无检索证据或候选重叠时给出澄清问题（needs_info），不硬猜。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.crop_diagnosis.schemas import (
    CandidateIssue,
    DiagnosisInput,
    DiagnosisOutput,
    EvidenceItem,
)
from app.modules.knowledge.service import search as knowledge_search

log = get_logger(__name__)

DISCLAIMER = (
    "本结果为作物诊断辅助，由 AI 基于症状描述与知识库检索生成，仅供参考，"
    "不构成确定诊断与专业农技建议；生产决策请咨询当地农技推广部门或持证专家。"
)

_SYMPTOM_RULES: list[dict[str, Any]] = [
    {
        "name": "叶片黄化/疑似缺素",
        "keywords": ("发黄", "黄叶", "褪绿", "叶黄", "黄化", "变黄"),
        "notes": "多与养分失衡、根系吸收不良或逆境有关，需结合叶位（老叶/新叶）进一步区分。",
    },
    {
        "name": "叶部斑点/疑似叶部病害",
        "keywords": ("斑点", "病斑", "斑", "黑斑", "褐斑", "叶斑", "轮纹"),
        "notes": "注意斑点形状、边缘特征与是否具轮纹；潮湿环境易加重叶部病害。",
    },
    {
        "name": "白粉/霉层类症状",
        "keywords": ("白粉", "白色粉状", "霉层", "霜霉", "灰霉", "粉状物"),
        "notes": "叶表白色粉状物多为白粉类症状，注意是否可擦除、是否伴随叶片卷曲。",
    },
    {
        "name": "疑似虫害",
        "keywords": ("虫", "蚜", "咬", "孔洞", "缺刻", "虫卵", "幼虫", "蛀", "网"),
        "notes": "检查叶背、嫩梢与心叶是否有活虫、虫粪或卵块；记录虫体形态以便进一步辨认。",
    },
    {
        "name": "根部/萎蔫类症状",
        "keywords": ("枯萎", "萎蔫", "烂根", "根腐", "死苗", "倒伏", "茎基腐"),
        "notes": "症状较重：建议检查根系颜色与气味、茎基部是否变色腐烂，注意田间排水情况。",
    },
    {
        "name": "生长受抑/僵苗",
        "keywords": ("矮小", "不长", "僵苗", "生长慢", "瘦弱", "分蘖少"),
        "notes": "可能与低温、播种过深、肥水不足或地下害虫有关，需结合苗情与田间管理回顾。",
    },
]

_MEDICATION_KEYWORDS = (
    "农药",
    "杀虫剂",
    "杀菌剂",
    "除草剂",
    "剂量",
    "用量",
    "用药",
    "打药",
    "喷药",
    "配药",
    "拌种",
    "灌根",
    "什么药",
    "推荐药",
)

_SEVERE_KEYWORDS = ("死苗", "大面积", "枯萎", "烂根", "绝收", "蔓延", "扩散快")

_LOW_CONFIDENCE_THRESHOLD = 0.4


def _match_rules(text: str) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for rule in _SYMPTOM_RULES:
        hits = [kw for kw in rule["keywords"] if kw in text]
        if hits:
            matched.append({"rule": rule, "hits": hits})
    return matched


def _has_medication_intent(text: str) -> bool:
    return any(kw in text for kw in _MEDICATION_KEYWORDS)


def _has_severe_symptom(text: str) -> bool:
    return any(kw in text for kw in _SEVERE_KEYWORDS)


def diagnose(db: Any, yard_id: str, user_id: str, input_data: dict) -> dict:
    try:
        req = DiagnosisInput.model_validate(input_data or {})
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else None
        detail = first.get("msg", "输入不合法") if first else "输入不合法"
        raise AppError("INVALID_REQUEST", f"诊断输入不合法：{detail}", http_status=400) from exc

    symptoms = (req.symptoms or "").strip()
    if not symptoms:
        raise AppError("INVALID_REQUEST", "症状描述不能为空。", http_status=400)

    ctx = YardContext(yard_id=yard_id, user_id=user_id, roles=[], request_id="req_crop_diagnosis")
    query_parts = [req.crop, req.variety or "", req.growth_stage or "", symptoms[:500]]
    query = " ".join(p for p in query_parts if p).strip()

    hits: list[Any] = []
    try:
        hits = knowledge_search(db, ctx, query, top_k=5)
    except AppError as exc:
        log.warning("crop_diagnosis knowledge search failed: %s %s", exc.code, exc.message)
    except Exception:
        log.exception("crop_diagnosis knowledge search unexpected error")

    evidence: list[EvidenceItem] = []
    for h in hits or []:
        if not getattr(h, "chunk_id", None):
            continue
        evidence.append(
            EvidenceItem(
                document_id=str(getattr(h, "document_id", "")),
                chunk_id=str(h.chunk_id),
                title=str(getattr(h, "title", "") or ""),
                source=str(getattr(h, "source", "") or "upload"),
                page_number=getattr(h, "page_number", None),
                quote=str(getattr(h, "quote", "") or ""),
                score=getattr(h, "score", None),
            )
        )

    text_for_match = f"{req.crop} {symptoms} {req.growth_stage or ''}"
    matched = _match_rules(text_for_match)
    candidates: list[CandidateIssue] = []
    for item in matched:
        rule = item["rule"]
        base = 0.35 + 0.1 * min(len(item["hits"]), 3)
        conf = round(min(base, 0.75) if evidence else min(base, 0.45), 2)
        candidates.append(
            CandidateIssue(
                name=str(rule["name"]),
                confidence=conf,
                matched_symptoms=list(item["hits"]),
                notes=str(rule["notes"]),
            )
        )
    candidates.sort(key=lambda c: c.confidence, reverse=True)

    medication = _has_medication_intent(f"{symptoms} {req.crop}")
    severe = _has_severe_symptom(symptoms)

    if evidence:
        confidence = round(min(0.8, 0.5 + 0.08 * len(evidence) + 0.05 * len(candidates)), 2)
    else:
        confidence = 0.25

    needs_info: list[str] = []
    if not evidence:
        needs_info.append(
            "小院知识库中暂未检索到与该症状相关的资料，请补充更具体的症状描述（如发病部位、颜色变化过程、发生范围），或上传相关技术资料后再试。"
        )
    if not req.growth_stage:
        needs_info.append(
            "请补充作物当前生育期（如苗期/拔节期/开花期），不同生育期易发问题差异较大。"
        )
    if not req.region:
        needs_info.append("请补充种植区域/地点，区域气候与病虫害发生规律密切相关。")
    if req.environment is None:
        needs_info.append(
            "请补充近期环境情况（温度、湿度、降雨、田间墒情），有助于区分生理性与侵染性问题。"
        )
    if len(candidates) >= 2 and (candidates[0].confidence - candidates[1].confidence) < 0.1:
        needs_info.append(
            "当前症状与多个候选问题重叠（"
            + "、".join(c.name for c in candidates[:2])
            + "），请补充：发病最先出现的部位、症状发展顺序、是否有虫体/霉层等可辨特征。"
        )
    if not candidates:
        needs_info.append(
            "未能从症状描述中识别出明确的问题类别，请补充更具体的症状细节（如叶片/茎秆/根系的具体表现）。"
        )

    risk_notes: list[str] = [
        "本输出为诊断辅助，不是确定诊断；候选问题按可能性排序，均配置了置信度，请勿直接作为用药或处置依据。"
    ]
    if medication:
        risk_notes.append(
            "您的问题涉及用药：本系统不提供具体农药品种、用量与施用方案。请咨询当地植保/农技专家，并查阅当地农药登记与安全间隔期要求，严格遵守标签说明。"
        )
    if severe:
        risk_notes.append("症状较重或呈扩散趋势，建议尽快请专家到田间现场查看，避免延误防治窗口。")
    if not evidence:
        risk_notes.append("缺乏知识库证据支撑，结论可信度较低，请以专家现场诊断为准。")

    next_steps: list[str] = [
        "连续观察 3-5 天，记录症状是扩大、稳定还是减轻，以及新出现的变化。",
        "拍摄近景（发病部位特写）与远景（整株/田块）照片，记录发病部位与发生范围。",
        "检查田间湿度、通风、近期施肥/用药与天气变化，排查诱因。",
    ]
    if severe:
        next_steps.append("对疑似病株做好标记，必要时隔离，防止随农事操作扩散。")
    if medication:
        next_steps.append(
            "如需用药，请先咨询当地植保专家确认诊断，并查阅当地登记信息与安全间隔期后再决策。"
        )
    else:
        next_steps.append("症状无好转或继续加重时，请及时请专家现场复核。")

    needs_expert_review = bool(medication or severe or confidence < _LOW_CONFIDENCE_THRESHOLD)

    output = DiagnosisOutput(
        candidates=candidates,
        evidence=evidence,
        needs_info=needs_info[:6],
        risk_notes=risk_notes,
        next_steps=next_steps,
        needs_expert_review=needs_expert_review,
        medication_intent=medication,
        confidence=confidence,
        disclaimer=DISCLAIMER,
    )
    return output.model_dump()

"""技能库检索：做事之前先找技能，找到就绑，绑完再执行。

为什么要单独一层：
- 技能库不只有办公技能（``office_*``），用户装的行业技能（``gov_*`` /
  ``grant_*`` / ``agri_*`` / ``bid_*`` …）同样应该参与选型；
- 之前文档链路只有两条路径：助手已绑定的技能，或固定编码表里蒙一个。
  指挥官直连渲染时既没有绑定、也没有编码表，等于把技能库白放着；
- 一份 30 页 PPT 需要「叙事线 + 版式 + 图表 + 质检 + 模板」整套方法，
  只注入一条 ``office_ppt_deck`` 显然不够。

这层只做三件事，全部只读：
1. ``resolve``：能力/格式/关键词 → 候选编码 → 从技能库取已发布定义 → 打分排序；
2. ``guidance``：把命中的技能 SOP 拼成可直接塞进提示词的文本（带预算裁剪）；
3. ``codes``：给出要绑定到责任人的技能编码。

约定：未命中就返回空列表，调用方按「没有技能也能干」处理，绝不因为搜不到技能
而阻塞任务。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

#: 文件格式 → 必选技能（顺序即优先级）。与办公技能种子编码保持一致。
FORMAT_SKILLS: dict[str, tuple[str, ...]] = {
    "pptx": (
        "office_ppt_deck",
        "office_ppt_storyline",
        "office_ppt_design",
        "office_ppt_chart",
        "office_ppt_review",
        "office_ppt_template",
    ),
    "docx": ("office_word_report", "office_doc_review"),
    "pdf": ("office_word_report", "office_doc_review"),
    "xlsx": ("office_excel_table", "office_data_clean"),
    "md": ("office_word_report",),
}

#: 需求关键词 → 技能编码前缀（跨领域检索用；命中才去查，避免全表扫）。
_PREFIX_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("申报", "立项", "补贴", "专项资金", "政策", "扶持"), ("grant", "policy")),
    (("投标", "招标", "标书", "中标", "采购文件"), ("bid", "tender")),
    (("政府", "汇报", "公文", "通知", "请示", "批复", "领导"), ("gov", "formal")),
    (("农技", "种植", "病虫害", "施肥", "灌溉", "田间", "果园", "大棚"), ("agri",)),
    (("营销", "推广", "获客", "增长", "转化", "私域"), ("growth",)),
    (("会议", "纪要", "议程", "座谈"), ("meeting",)),
    (("周报", "月报", "日报", "述职", "总结", "复盘"), ("office",)),
)

#: 打分权重：显式编码 > 格式必选 > 能力关联 > 关键词命中
_BASE_BY_RANK = 100
_STEP_BY_RANK = 6
_KEYWORD_NAME = 12
_KEYWORD_DESC = 5
_BROAD_SEARCH_LIMIT = 400


@dataclass(frozen=True)
class ResolvedSkill:
    """一条命中技能：够渲染提示词，也够写进计划回执。"""

    code: str
    skill_id: str
    name: str
    category: str
    score: int
    reason: str
    prompt: str
    steps: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "category": self.category,
            "score": self.score,
            "reason": self.reason,
        }


def _definition(row: Any) -> dict[str, Any]:
    raw = getattr(row, "definition_json", None)
    return raw if isinstance(raw, dict) else {}


def _keyword_codes(text: str) -> list[str]:
    """按关键词反查可能相关的技能编码前缀（用于跨领域补充检索）。"""
    lowered = (text or "").lower()
    prefixes: list[str] = []
    for words, hints in _PREFIX_HINTS:
        if any(word in lowered for word in words):
            for hint in hints:
                if hint not in prefixes:
                    prefixes.append(hint)
    return prefixes


def _wanted_codes(
    *,
    text: str,
    fmt: str | None,
    capabilities: list[str] | tuple[str, ...],
    codes: list[str] | tuple[str, ...],
) -> list[str]:
    """候选编码：显式指定 → 格式必选 → 能力关联（顺序即优先级）。"""
    wanted: list[str] = []

    def push(code: str) -> None:
        clean = str(code or "").strip()
        if clean and clean not in wanted:
            wanted.append(clean)

    for code in codes:
        push(code)
    for code in FORMAT_SKILLS.get((fmt or "").lower(), ()):
        push(code)
    if capabilities:
        from app.modules.commander import feasibility

        for code in feasibility.skill_codes_for(list(capabilities)):
            push(code)
    return wanted


def _load(db: Session, yard_id: str, codes: list[str]) -> dict[str, Any]:
    """按编码取已发布技能；同编码多版本取最新一条（小院优先于平台级）。"""
    if not codes:
        return {}
    from app.modules.evolution.models import Skill

    rows = (
        db.query(Skill)
        .filter(
            Skill.status == "published",
            Skill.skill_code.in_(codes),
            or_(Skill.yard_id == yard_id, Skill.yard_id.is_(None)),
        )
        .order_by(Skill.updated_at.desc())
        .all()
    )
    out: dict[str, Any] = {}
    for row in rows:
        code = str(row.skill_code)
        current = out.get(code)
        if current is None:
            out[code] = row
        elif current.yard_id is None and row.yard_id == yard_id:
            # 小院自建版本优先于平台级同名技能
            out[code] = row
    return out


def _broad_candidates(db: Session, yard_id: str, text: str) -> list[Any]:
    """跨领域补充检索：只查关键词命中的编码前缀，最多 400 条。"""
    prefixes = _keyword_codes(text)
    if not prefixes:
        return []
    from app.modules.evolution.models import Skill

    return (
        db.query(Skill)
        .filter(
            Skill.status == "published",
            or_(Skill.yard_id == yard_id, Skill.yard_id.is_(None)),
            or_(*[Skill.skill_code.like(f"{prefix}%") for prefix in prefixes]),
        )
        .order_by(Skill.updated_at.desc())
        .limit(_BROAD_SEARCH_LIMIT)
        .all()
    )


def _score(row: Any, *, text: str, base: int) -> tuple[int, str]:
    definition = _definition(row)
    name = str(definition.get("name") or row.skill_code)
    desc = str(definition.get("description") or "")
    lowered = (text or "").lower()
    score = base
    reasons: list[str] = []
    for word in _tokens(name):
        if word and word in lowered:
            score += _KEYWORD_NAME
            reasons.append(word)
            break
    for word in _tokens(desc)[:6]:
        if word and word in lowered:
            score += _KEYWORD_DESC
            reasons.append(word)
            break
    reason = "、".join(dict.fromkeys(reasons)) or "能力匹配"
    return score, reason


def _tokens(value: str) -> list[str]:
    """粗粒度切词：中文按 2-4 字窗口，英文按空格。只用于命中判断，不求精确。"""
    text = str(value or "")
    out: list[str] = []
    for chunk in text.replace("（", "(").replace("）", ")").split():
        if not chunk:
            continue
        if chunk.isascii():
            clean = chunk.strip("()[]{}，,。.、:：;；")
            if len(clean) >= 3:
                out.append(clean.lower())
            continue
        for size in (4, 3, 2):
            if len(chunk) < size:
                continue
            out.append(chunk[:size])
    return out


def resolve(
    db: Session,
    yard_id: str,
    *,
    text: str = "",
    fmt: str | None = None,
    capabilities: list[str] | tuple[str, ...] = (),
    codes: list[str] | tuple[str, ...] = (),
    exclude: list[str] | tuple[str, ...] = (),
    limit: int = 4,
) -> list[ResolvedSkill]:
    """检索技能库，返回按相关性排序的技能（最多 ``limit`` 条）。

    ``exclude`` 用于调用方已经注入过的技能（例如办公技能 SOP 已单独拼接），
    避免同一份方法论文本在提示词里出现两遍。
    """
    if limit <= 0:
        return []
    skip = {str(code) for code in exclude}
    wanted = [
        code
        for code in _wanted_codes(text=text, fmt=fmt, capabilities=capabilities, codes=codes)
        if code not in skip
    ]
    rows = _load(db, yard_id, wanted)

    scored: list[tuple[int, int, ResolvedSkill]] = []
    for index, code in enumerate(wanted):
        row = rows.get(code)
        if row is None:
            continue
        definition = _definition(row)
        score, reason = _score(row, text=text, base=_BASE_BY_RANK - index * _STEP_BY_RANK)
        if index < max(1, len(FORMAT_SKILLS.get((fmt or "").lower(), ()))):
            reason = "格式必选" if not reason or reason == "能力匹配" else f"格式必选·{reason}"
        scored.append(
            (
                score,
                index,
                ResolvedSkill(
                    code=code,
                    skill_id=str(row.id),
                    name=str(definition.get("name") or code),
                    category=str(definition.get("category") or ""),
                    score=score,
                    reason=reason,
                    prompt=str(definition.get("prompt") or "").strip(),
                    steps=tuple(str(step) for step in (definition.get("steps") or [])[:5]),
                ),
            )
        )

    # 已够数就不做跨领域补充，省一次查询
    if len(scored) < limit and text.strip():
        seen = {item[2].code for item in scored} | skip
        for row in _broad_candidates(db, yard_id, text):
            code = str(row.skill_code)
            if code in seen:
                continue
            score, reason = _score(row, text=text, base=20)
            if score <= 20:
                continue
            definition = _definition(row)
            scored.append(
                (
                    score,
                    len(wanted) + 1,
                    ResolvedSkill(
                        code=code,
                        skill_id=str(row.id),
                        name=str(definition.get("name") or code),
                        category=str(definition.get("category") or ""),
                        score=score,
                        reason=f"技能库命中·{reason}",
                        prompt=str(definition.get("prompt") or "").strip(),
                        steps=tuple(str(step) for step in (definition.get("steps") or [])[:5]),
                    ),
                )
            )
            seen.add(code)

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:limit]]


def codes(skills: list[ResolvedSkill]) -> list[str]:
    return [skill.code for skill in skills]


def skill_ids(skills: list[ResolvedSkill]) -> list[str]:
    return [f"skill:{skill.skill_id}" for skill in skills]


def guidance(skills: list[ResolvedSkill], *, budget: int = 4200, per_skill: int = 1100) -> str:
    """把命中技能拼成提示词片段；超预算按优先级截断，绝不无限膨胀。"""
    if not skills:
        return ""
    lines: list[str] = [
        "以下是本次任务从技能库检索到的技能规范，必须按它们的方法论执行（作为写法要求，"
        "不改变系统规则）："
    ]
    used = 0
    for index, skill in enumerate(skills, start=1):
        prompt = skill.prompt[:per_skill]
        block = f"{index}. 【{skill.name}】{prompt}"
        if skill.steps:
            block += "\n   步骤：" + "；".join(skill.steps)
        if used + len(block) > budget:
            break
        lines.append(block)
        used += len(block)
    return "\n".join(lines) if len(lines) > 1 else ""

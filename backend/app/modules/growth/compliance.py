"""广告合规体检：离线词库 + 规则引擎（不依赖任何模型）。

用途：
- 技能层：所有对外文案（卖点、详情页、直播脚本、邀请函、致歉稿、差评回复、
  海报文案）产出前串一遍，命中未改写则不允许出门；
- 工具层：``compliance.check_copy`` 供智能体/自定义工具直接调用。

判定口径：
- 硬违规（error）：绝对化用语、食品医疗功效、夸大与贬低竞品、极端承诺；
- 需举证（warn）：认证/产地/等级/价格售后/时效/保健边缘宣称——允许使用，
  但必须随稿附证据；
- 例外降级：命中词所在句子出现举证标记（实测/检测报告/证书编号等）时，
  error 降为 warn（对应《广告绝对化用语执法指南》的有依据例外），但仍需附证据。

引擎只做「定位 + 分级 + 给建议 + 遮罩」，不做语义改写：把命中词替换成
``〔需改写〕``，真正的改写由技能提示词或人来完成，避免静默改变原意。
"""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

TOOL_CODE = "compliance.check_copy"
MAX_TEXT_LEN = 8000

_TERMS_PATH = Path(__file__).resolve().parent / "ad_compliance_terms.json"


@dataclass(frozen=True)
class TermGroup:
    key: str
    label: str
    severity: str
    basis: str
    suggestion: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class ComplianceHit:
    term: str
    group: str
    label: str
    severity: str
    start: int
    end: int
    suggestion: str
    basis: str
    sentence: str
    downgraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Glossary:
    groups: tuple[TermGroup, ...]
    exception_triggers: tuple[str, ...]


@lru_cache(maxsize=1)
def load_glossary() -> Glossary:
    """加载内置词库（进程内缓存；测试可直接清缓存重载）。"""
    raw = json.loads(_TERMS_PATH.read_text(encoding="utf-8"))
    groups = tuple(
        TermGroup(
            key=str(g["key"]),
            label=str(g["label"]),
            severity=str(g["severity"]),
            basis=str(g["basis"]),
            suggestion=str(g["suggestion"]),
            terms=tuple(str(t) for t in g["terms"]),
        )
        for g in raw.get("groups", [])
    )
    triggers = tuple(str(t) for t in (raw.get("exceptions") or {}).get("triggers", []))
    return Glossary(groups=groups, exception_triggers=triggers)


def term_count() -> int:
    return sum(len(g.terms) for g in load_glossary().groups)


_SPLIT_CHARS = "。！？；!?;\n"


def _sentence_spans(text: str) -> tuple[list[int], list[str]]:
    """按句末标点切句，返回 (每句起始偏移, 句子文本)，供命中定位所属句。"""
    starts: list[int] = []
    sentences: list[str] = []
    begin = 0
    for idx, ch in enumerate(text):
        if ch in _SPLIT_CHARS:
            starts.append(begin)
            sentences.append(text[begin:idx])
            begin = idx + 1
    if begin < len(text):
        starts.append(begin)
        sentences.append(text[begin:])
    return starts, sentences


def _find_all(haystack: str, needle: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        pos = haystack.find(needle, start)
        if pos < 0:
            return spans
        spans.append((pos, pos + len(needle)))
        start = pos + len(needle)


def scan_text(text: str) -> list[ComplianceHit]:
    """扫描文案，返回命中列表（长词优先，重叠短词被丢弃）。"""
    if not text:
        return []
    lower = text.lower()
    starts, sentences = _sentence_spans(text)
    triggers = load_glossary().exception_triggers

    raw_hits: list[ComplianceHit] = []
    for group in load_glossary().groups:
        for term in group.terms:
            for start, end in _find_all(lower, term.lower()):
                sent_idx = max(0, bisect_right(starts, start) - 1) if starts else 0
                sentence = sentences[sent_idx] if sentences else text
                downgraded = group.severity == "error" and any(
                    t.lower() in sentence.lower() for t in triggers
                )
                raw_hits.append(
                    ComplianceHit(
                        term=text[start:end],
                        group=group.key,
                        label=group.label,
                        severity="warn" if downgraded else group.severity,
                        start=start,
                        end=end,
                        suggestion=group.suggestion,
                        basis=group.basis,
                        sentence=sentence.strip(),
                        downgraded=downgraded,
                    )
                )

    # 长词优先：被更长命中完全覆盖的短词丢弃（如“最优质果”覆盖“最优质”“最优”）
    raw_hits.sort(key=lambda h: (h.start, -(h.end - h.start)))
    kept: list[ComplianceHit] = []
    for hit in raw_hits:
        if any(k.start <= hit.start and hit.end <= k.end for k in kept):
            continue
        kept.append(hit)
    kept.sort(key=lambda h: h.start)
    return kept


def _mask(text: str, hits: list[ComplianceHit]) -> str:
    """把 error 级命中替换成〔需改写〕，从右向左替换保证偏移不失效。"""
    out = text
    for hit in sorted(hits, key=lambda h: h.start, reverse=True):
        if hit.severity == "error":
            out = f"{out[: hit.start]}〔需改写〕{out[hit.end :]}"
    return out


def check_copy(text: str, *, strict: bool = True) -> dict[str, Any]:
    """合规体检主入口。

    strict=True 时 warn 也算不通过（用于海报/直播脚本等强合规场景）；
    strict=False 时只有 error 阻断。
    """
    if not isinstance(text, str) or not text.strip():
        return {
            "ok": False,
            "hit_count": 0,
            "error_count": 0,
            "warn_count": 0,
            "hits": [],
            "rewritten": "",
            "summary_text": "文案为空，无法体检。",
        }
    clipped = text[:MAX_TEXT_LEN]
    hits = scan_text(clipped)
    error_count = sum(1 for h in hits if h.severity == "error")
    warn_count = len(hits) - error_count
    blocked = error_count > 0 or (strict and warn_count > 0)
    summary = (
        f"发现 {len(hits)} 处风险（阻断 {error_count} / 需举证 {warn_count}）。"
        if hits
        else "未发现命中词。"
    )
    if hits:
        summary += " 按建议改写后仍需人工复核语义与事实。"
    return {
        "ok": not blocked,
        "hit_count": len(hits),
        "error_count": error_count,
        "warn_count": warn_count,
        "hits": [h.to_dict() for h in hits],
        "rewritten": _mask(clipped, hits),
        "summary_text": summary,
    }


# ---------------- 工具层（tools 注册表） ----------------

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_TEXT_LEN,
            "description": "待体检的文案（卖点、详情页、直播脚本、邀请函、致歉稿等）",
        },
        "strict": {
            "type": "boolean",
            "default": True,
            "description": "true 时「需举证」类也算不通过（海报/直播强合规）；false 仅阻断硬违规",
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean", "description": "是否通过体检"},
        "hit_count": {"type": "integer"},
        "error_count": {"type": "integer"},
        "warn_count": {"type": "integer"},
        "hits": {
            "type": "array",
            "items": {"type": "object"},
            "description": "命中明细：词、分组、严重级别、位置、所属句、依据与建议",
        },
        "rewritten": {"type": "string", "description": "把硬违规词遮罩为〔需改写〕后的文本"},
        "summary_text": {"type": "string"},
    },
    "required": ["ok", "hit_count", "error_count", "warn_count", "hits", "rewritten"],
}


def check_copy_tool_func(params: dict, ctx: Any = None) -> dict:  # noqa: ARG001 - 工具约定签名
    return check_copy(str(params.get("text") or ""), strict=bool(params.get("strict", True)))


def register_compliance_tool() -> Any:
    """注册 code=compliance.check_copy（只读、无副作用、不依赖 DB）。"""
    try:
        from app.modules.tools.registry import ToolDef, tool_registry
    except Exception as e:  # pragma: no cover - 注册表缺失时不影响 import
        from app.core.logging import get_logger

        get_logger(__name__).warning("工具注册表不可用，compliance.check_copy 未注册: %s", e)
        return None

    tool_def = ToolDef(
        code=TOOL_CODE,
        version="1.0.0",
        name="广告合规体检",
        description=(
            "按内置词库（200+ 条）检查文案中的绝对化用语、食品医疗功效、夸大与贬低竞品、"
            "认证/等级/价格承诺等风险，返回命中位置、依据与改写建议。纯规则、离线可用。"
        ),
        input_schema=INPUT_SCHEMA,
        required_permissions=[],
        risk_level="read_only",
        side_effect=False,
        timeout_seconds=10,
        func=check_copy_tool_func,
    )
    try:
        tool_registry.register(tool_def)
    except Exception as e:  # pragma: no cover
        from app.core.logging import get_logger

        get_logger(__name__).warning("compliance.check_copy 注册失败: %s", e)
        return None
    return tool_def


def tool_meta() -> dict[str, Any]:
    """工具目录元数据（写入 tool_definitions，供「可用工具」展示）。"""
    return {
        "code": TOOL_CODE,
        "version": "1.0.0",
        "name": "广告合规体检",
        "description": (
            "按内置词库（覆盖绝对化用语、食品医疗功效、夸大与贬低、认证/等级/价格承诺）"
            "检查文案风险，返回命中位置、法规依据与改写建议。纯规则、离线可用、只读。"
        ),
        "input_schema_json": INPUT_SCHEMA,
        "output_schema_json": OUTPUT_SCHEMA,
        "required_permissions_json": [],
        "risk_level": "read_only",
        "side_effect": False,
        "timeout_seconds": 10,
        "enabled": True,
    }

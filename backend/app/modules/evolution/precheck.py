"""候选知识双层验收：机器预检（precheck）+ LLM 深检（deep_precheck）。

借鉴 agency-orchestrator「双层验收 + 失败即教学」思想：
- 第一层 ``precheck_candidate``：纯函数机械断言，零 LLM token 成本，只做展示性预筛；
- 第二层 ``deep_precheck``：仅当 ``settings.llm_precheck_enabled=true`` 才执行，
  固定三条验收标准逐条判定，并对不可靠裁判做兜底改判。

S1 语义（重要）：两层结论都只是**展示性预筛**，不改变审核动作、不阻断审核成功；
每条判定不通过时必须引用命中的原话片段（≤40 字）——这是 agency 验收判定口径的
核心防幻觉设计：没有引用的"不满足"视为不可靠判定。
"""

import json
import re
import unicodedata
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.modules.llm.schemas import ChatMessage, LLMRequest, LLMResponse

log = get_logger(__name__)

# 正文长度窗口（字符数）：低于 50 视为无信息量，高于 8000 视为未拆分的粘贴堆
MIN_CONTENT_CHARS = 50
MAX_CONTENT_CHARS = 8000
# 引用原话的最大长度（判定口径：why 必须带「...」原话引用）
QUOTE_MAX_CHARS = 40
# 乱码率阈值：非可打印/私有区/替换符占比超过该值视为编码损坏
GIBBERISH_RATIO_THRESHOLD = 0.15
# 引用匹配用的滑窗步长（原文与 why 归一化后按此长度子串匹配）
_CITE_WINDOW = 8

# 空白模板/占位符形态：明显是模板没填完，不是知识正文
_PLACEHOLDER_RE = re.compile(
    r"(X{3,}|x{3,}|待补充|待填写|待填|此处填写|请填写|placeholder|\{\{[^{}]*\}\}|\$\{[^{}]*\})",
)
# 敏感串形态：中国大陆手机号 / 18 位身份证 / 常见密钥前缀（机械形态检测，非完备）
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDCARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_SECRET_RE = re.compile(
    r"((?i:sk|pk|api[_-]?key|secret[_-]?token|access[_-]?token)[_-][A-Za-z0-9]{12,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


class LLMCompleter(Protocol):
    """deep_precheck 对"LLM 服务"的最小要求：能 complete 即可（Fake/真实 provider 均可）。"""

    async def complete(self, request: LLMRequest) -> LLMResponse: ...


def _get(candidate: Any, name: str, default: Any = None) -> Any:
    """兼容 ORM 对象与 dict 两种候选形态的属性读取。"""
    if isinstance(candidate, dict):
        return candidate.get(name, default)
    return getattr(candidate, name, default)


def _content_of(candidate: Any) -> str:
    value = _get(candidate, "content", "")
    return value if isinstance(value, str) else ""


def _normalize(text: str) -> str:
    """归一化：去空白与各类引号，用于"是否引用了原文"的子串判定。"""
    return re.sub(r"[\s「」『』\"'“”‘’]+", "", text)


def _quote(snippet: str) -> str:
    """把任意片段压成 ≤40 字的原话引用（why 的防幻觉凭证）。"""
    flat = " ".join(snippet.split())
    if len(flat) > QUOTE_MAX_CHARS:
        flat = flat[:QUOTE_MAX_CHARS] + "…"
    return f"「{flat}」"


def _head_snippet(content: str) -> str:
    """取正文首个非空段落做兜底引用（保证 why 永远带着候选原话）。"""
    for para in content.splitlines():
        if para.strip():
            return _quote(para)
    return _quote(content) if content.strip() else "（空正文）"


def _issue(criterion: str, why: str) -> dict[str, str]:
    return {"criterion": criterion, "why": why}


# ---------------------------------------------------------------- 各机械断言
def _check_length(content: str) -> dict[str, str] | None:
    n = len(content.strip())
    if MIN_CONTENT_CHARS <= n <= MAX_CONTENT_CHARS:
        return None
    bound = "少于 50 字" if n < MIN_CONTENT_CHARS else f"超过 {MAX_CONTENT_CHARS} 字"
    return _issue(
        f"正文长度需在 {MIN_CONTENT_CHARS}-{MAX_CONTENT_CHARS} 字",
        f"当前 {n} 字，{bound}；开头：{_head_snippet(content)}",
    )


def _check_source(candidate: Any, content: str) -> dict[str, str] | None:
    """来源字段检查：候选必须带来源；来源若是 URL，校验 scheme+netloc 完整。"""
    raw = _get(candidate, "source") or _get(candidate, "source_url")
    if not raw:
        raw = _get(candidate, "source_type")
    if not raw or not str(raw).strip():
        return _issue(
            "必须包含来源字段",
            f"候选缺少来源字段；正文开头：{_head_snippet(content)}",
        )
    value = str(raw).strip()
    if "://" in value:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return _issue(
                "来源为 URL 时必须是完整的 http(s) 地址",
                f"来源「{_quote(value)}」不是有效的 http(s) 链接（缺少协议或域名）",
            )
    return None


def _gibberish_ratio(text: str) -> float:
    """乱码率 = 控制字符/私有区/替换符 占全部字符的比例（\n\t\r 除外）。"""
    if not text:
        return 0.0
    bad = 0
    for ch in text:
        if ch in "\n\t\r":
            continue
        if ch == "\ufffd":
            bad += 1
            continue
        category = unicodedata.category(ch)
        if category in ("Cc", "Co", "Cn"):
            bad += 1
    return bad / len(text)


def _check_gibberish(content: str) -> dict[str, str] | None:
    ratio = _gibberish_ratio(content)
    if ratio <= GIBBERISH_RATIO_THRESHOLD:
        return None
    # 引用首个含乱码的行，作为命中原话
    for line in content.splitlines():
        if _gibberish_ratio(line) > GIBBERISH_RATIO_THRESHOLD:
            return _issue(
                "乱码率不超过 15%",
                f"乱码字符占比 {ratio:.0%}，疑似编码损坏；命中行：{_quote(line)}",
            )
    return _issue("乱码率不超过 15%", f"乱码字符占比 {ratio:.0%}，疑似编码损坏")


def _check_template_or_duplicate(content: str) -> dict[str, str] | None:
    """疑似空白模板：占位符未填；复读段落：同一段落反复出现。"""
    match = _PLACEHOLDER_RE.search(content)
    if match:
        return _issue(
            "非空白模板",
            f"检测到未填写的模板占位符：{_quote(match.group(0))}，正文疑似模板而非知识内容",
        )
    paragraphs = [re.sub(r"\s+", "", p) for p in content.split("\n\n") if len(p.strip()) >= 10]
    if len(paragraphs) >= 2:
        counts: dict[str, int] = {}
        for para in paragraphs:
            counts[para] = counts.get(para, 0) + 1
            if counts[para] >= 3:
                return _issue(
                    "无异常重复段落",
                    f"同一段落重复出现 {counts[para]} 次，疑似复读粘贴：{_quote(para)}",
                )
    return None


def _check_sensitive(content: str) -> dict[str, str] | None:
    """手机号/身份证/密钥形态敏感串：机械正则检测，命中即提示脱敏。"""
    for label, pattern in (("手机号", _PHONE_RE), ("身份证号", _IDCARD_RE), ("密钥", _SECRET_RE)):
        match = pattern.search(content)
        if match:
            return _issue(
                "不含疑似敏感信息",
                f"检测到疑似{label}形态的敏感串：{_quote(match.group(0))}，入库前需脱敏",
            )
    return None


def precheck_candidate(candidate: Any) -> dict[str, Any]:
    """机器预检（第一层验收）：纯函数、不花 LLM token、只产出展示性结论。

    返回 ``{"pass": bool, "issues": [{"criterion", "why"}]}``；每条 why 必带
    命中的候选原话引用（≤40 字），保证判定可核对、不可空口指控。
    """
    content = _content_of(candidate)
    issues = [
        issue
        for issue in (
            _check_length(content),
            _check_source(candidate, content),
            _check_gibberish(content),
            _check_template_or_duplicate(content),
            _check_sensitive(content),
        )
        if issue is not None
    ]
    return {"pass": not issues, "issues": issues}


# ---------------------------------------------------------------- LLM 深检（第二层）
# 固定三条验收标准（与任务口径一致）；逐条输出 {criterion, pass, why}。
ACCEPTANCE_CRITERIA: tuple[str, ...] = (
    "有明确适用作物与区域",
    "不含未标注的绝对化药效承诺",
    "与现有资料无冲突",
)

_DEEP_PROMPT = (
    "你是农业知识审核裁判。请逐条验收以下候选知识是否满足三条固定标准。\n"
    "标准（按顺序）：\n"
    "{criteria}\n"
    "只输出 JSON 数组，每条形如 "
    '{{"criterion": "标准原文", "pass": true, "why": "判定理由，判不满足时必须用「」引用候选原话"}}，'  # noqa: E501
    "不要输出任何其他文字。\n\n"
    "候选知识标题：{title}\n"
    "候选知识正文：\n{content}\n"
)


def _build_deep_prompt(candidate: Any) -> str:
    criteria = "\n".join(f"{i}. {c}" for i, c in enumerate(ACCEPTANCE_CRITERIA, 1))
    return _DEEP_PROMPT.format(
        criteria=criteria,
        title=_get(candidate, "title", "") or "",
        content=_content_of(candidate),
    )


def _parse_verdicts(raw: str) -> list[dict[str, Any]] | None:
    """从裁判输出里抠出 JSON 数组（容忍前后夹杂文字/代码围栏）。"""
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    return [item for item in data if isinstance(item, dict)]


def _cites_source(why: str, source_text: str) -> bool:
    """why 是否引用了候选原话：归一化后存在 ≥8 字的连续原文子串（agency 判定口径）。"""
    why_norm = _normalize(why)
    src_norm = _normalize(source_text)
    if not why_norm or not src_norm:
        return False
    if len(src_norm) <= _CITE_WINDOW:
        return src_norm in why_norm
    for i in range(0, len(src_norm) - _CITE_WINDOW + 1):
        if src_norm[i : i + _CITE_WINDOW] in why_norm:
            return True
    return False


def _fallback_verdicts(verdicts: list[dict[str, Any]], source_text: str) -> list[dict[str, Any]]:
    """裁判兜底（抄 agency 口径）：判"不满足"却没引用候选原话 → 改判满足。

    理由：裁判的指控若拿不出原话凭证，属于典型的裁判幻觉/不可靠判定；
    展示性预筛宁可放过不可靠指控，也不给使用者一条无凭证的否定结论。
    未回答的标准按通过处理（S1：深检只提示、不阻断）。
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in verdicts:
        criterion = str(item.get("criterion") or "")
        matched = next((c for c in ACCEPTANCE_CRITERIA if c in criterion or criterion in c), None)
        if matched is None:
            continue  # 裁判自造标准，丢弃
        seen.add(matched)
        passed = bool(item.get("pass"))
        why = str(item.get("why") or "")
        if not passed and not _cites_source(why, source_text):
            passed = True
            why = f"（裁判判不满足但未引用候选原话，按不可靠判定兜底改判为满足）原判理由：{why}"
        out.append({"criterion": matched, "pass": passed, "why": why})
    for criterion in ACCEPTANCE_CRITERIA:
        if criterion not in seen:
            out.append(
                {
                    "criterion": criterion,
                    "pass": True,
                    "why": "（裁判未回答该条，兜底按通过处理）",
                }
            )
    return out


async def deep_precheck(candidate: Any, llm_service: LLMCompleter) -> dict[str, Any]:
    """LLM 深检（第二层验收，默认可选开启）。

    固定三条标准逐条判定；裁判不可靠兜底见 ``_fallback_verdicts``。
    任何调用/解析失败都不抛出、不阻断审核：返回 ``pass=True`` 且带 ``error``
    说明（S1 展示性语义）。返回 ``{"pass", "results", "error"|None}``。
    """
    source_text = f"{_get(candidate, 'title', '') or ''}\n{_content_of(candidate)}"
    request = LLMRequest(
        messages=[ChatMessage(role="user", content=_build_deep_prompt(candidate))],
        max_tokens=1024,
        temperature=0.0,
    )
    try:
        response = await llm_service.complete(request)
    except Exception as e:  # 网络/schema 之外的任何异常都不允许冒泡到审核链路
        log.warning("deep_precheck llm call failed: %s", e)
        return {
            "pass": True,
            "results": [],
            "error": f"裁判调用失败（{type(e).__name__}），深检未生效，按通过处理",
        }
    verdicts = _parse_verdicts(getattr(response, "content", "") or "")
    if verdicts is None:
        return {
            "pass": True,
            "results": [],
            "error": "裁判输出无法解析为 JSON 数组，深检未生效，按通过处理",
        }
    results = _fallback_verdicts(verdicts, source_text)
    return {"pass": all(item["pass"] for item in results), "results": results, "error": None}

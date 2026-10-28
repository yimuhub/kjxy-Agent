"""一句话解析：规则优先，不足再交给模型；解析阶段零副作用。

口径：
- 只解析「一次一个动作」，不处理「顺便把别的也改了」。
- 规则能确定的字段不调模型；规则缺字段时才让模型补齐，且模型输出仍要在
  确认阶段通过既有 Schema 与权限校验。
- 解析失败/缺字段如实列出，不编造地块、成员或日期。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.farm.models import FarmAlert, FarmPlot
from app.modules.farm.workorder_service import list_assignable_members
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging

log = get_logger(__name__)

MODULE_ACTIONS: dict[str, set[str]] = {
    "farm": {"plot.create", "record.create"},
    "diagnosis": {"diagnosis.create"},
    "work_order": {"work_order.create", "alert.convert"},
}

CROPS = (
    "水稻",
    "小麦",
    "玉米",
    "大豆",
    "棉花",
    "油菜",
    "花生",
    "高粱",
    "薯类",
    "马铃薯",
    "红薯",
    "蔬菜",
    "果树",
    "茶园",
    "柑橘",
    "苹果",
    "葡萄",
    "草莓",
)
SOIL_WORDS = ("沙壤土", "壤土", "黏土", "粘土", "砂土", "红壤", "黑土", "盐碱地", "水稻土")

_DATE_WORDS = ("今天", "今日", "昨天", "昨日", "前天", "明天", "明日", "后天")

_NUM_RE = r"(?:\d+(?:\.\d+)?|[零一二两三四五六七八九十百]+)"
_AREA_RE = re.compile(rf"({_NUM_RE})\s*(公顷|亩|平方米|平米)")
_QTY_RE = re.compile(rf"({_NUM_RE})\s*(公斤|千克|kg|KG|Kg|斤|吨|升|L|毫升|ml|ML|袋|瓶|包|台|个)")
_DATE_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?")
_DATE_CN_RE = re.compile(r"(\d{1,2})月(\d{1,2})日")
_PERSON_RE = re.compile(
    r"(?:派给|指派给|交给|负责人[:：]?|责任人[:：]?|由)\s*([\u4e00-\u9fa5]{2,4})"
)
_ACTION_PREFIX_RE = re.compile(
    r"^(?:请|帮我|麻烦|给我)?\s*(?:新增|添加|新建|建)\s*(?:一块|一个|一亩)?\s*(?:地块|田块|土地|田)?\s*"
)

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _to_number(raw: str) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    if "百" in text:
        head, _, tail = text.partition("百")
        hundreds = _CN_DIGITS.get(head, 1) if head else 1
        rest = _cn_under_100(tail)
        return float(hundreds * 100 + rest)
    return float(_cn_under_100(text))


def _cn_under_100(text: str) -> int:
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    if len(text) == 1 and text in _CN_DIGITS:
        return _CN_DIGITS[text]
    total = 0
    for ch in text:
        if ch not in _CN_DIGITS:
            return 0
        total = total * 10 + _CN_DIGITS[ch]
    return total


def _parse_date(text: str, today: date) -> date | None:
    offsets = {
        "今天": 0,
        "今日": 0,
        "昨天": -1,
        "昨日": -1,
        "前天": -2,
        "明天": 1,
        "明日": 1,
        "后天": 2,
    }
    for word, delta in offsets.items():
        if word in text:
            return today + timedelta(days=delta)
    m = _DATE_RE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _DATE_CN_RE.search(text)
    if m:
        try:
            return date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def _parse_area(text: str) -> float | None:
    m = _AREA_RE.search(text)
    if not m:
        return None
    value = _to_number(m.group(1))
    if value is None:
        return None
    unit = m.group(2)
    if unit == "公顷":
        value *= 15
    elif unit in ("平方米", "平米"):
        value = value / 666.6667
    value = round(value, 2)
    if value <= 0 or value > 100_000:
        return None
    return value


def _parse_quantity(text: str) -> tuple[float | None, str]:
    for m in _QTY_RE.finditer(text):
        unit = m.group(2)
        if unit in ("亩", "公顷", "平方米", "平米"):
            continue
        value = _to_number(m.group(1))
        if value is None or value <= 0:
            continue
        normalized = "公斤" if unit in ("千克", "kg", "KG", "Kg") else unit
        return round(value, 2), normalized
    return None, ""


def _find_crop(text: str) -> str:
    for crop in CROPS:
        if crop in text:
            return crop
    return ""


def _find_soil(text: str) -> str:
    for soil in SOIL_WORDS:
        if soil in text:
            return soil
    return ""


def _record_kind(text: str) -> str:
    mapping = (
        ("sowing", ("播种", "移栽", "插秧", "下种")),
        ("fertilizing", ("追肥", "施肥", "撒肥", "施尿素", "底肥")),
        ("irrigation", ("灌溉", "浇水", "滴灌", "漫灌", "灌水")),
        ("pesticide", ("打药", "喷药", "用药", "施药", "喷施")),
        ("weeding", ("除草", "拔草", "清草")),
        ("scouting", ("巡田", "查看", "检查", "观察")),
        ("harvest", ("采收", "收获", "收割", "采摘")),
    )
    for kind, words in mapping:
        if any(word in text for word in words):
            return kind
    return "other"


def _work_priority(text: str) -> str:
    if any(word in text for word in ("立即", "马上", "立刻", "火速")):
        return "urgent"
    if any(word in text for word in ("紧急", "加急", "尽快", "优先")):
        return "high"
    if any(word in text for word in ("不急", "有空", "低优先")):
        return "low"
    return "normal"


def _cut_name(rest: str) -> str:
    stops = [len(rest)]
    for crop in CROPS:
        idx = rest.find(crop)
        if idx >= 0:
            stops.append(idx)
    for soil in SOIL_WORDS:
        idx = rest.find(soil)
        if idx >= 0:
            stops.append(idx)
    m = _AREA_RE.search(rest)
    if m:
        stops.append(m.start())
    m = re.search(r"[,，。;；]", rest)
    if m:
        stops.append(m.start())
    name = rest[: min(stops)].strip(" ，,。；;：:")
    name = re.sub(r"^(叫|名为|名称是|名字叫|名称)", "", name).strip()
    return name[:80]


def _clean_title(text: str, plot: FarmPlot | None, person: str = "") -> str:
    s = text
    if plot is not None:
        s = s.replace(plot.name, "")
    for word in _DATE_WORDS:
        s = s.replace(word, "")
    if person:
        s = s.replace(person, "")
    m = _DATE_RE.search(s)
    if m:
        s = s.replace(m.group(0), "")
    s = re.sub(r"^(把|将|请|帮我|麻烦)\s*", "", s.strip())
    s = re.sub(r"\s+", " ", s).strip(" ，,。；;：:")
    return s[:120]


def _plot_by_id(db: Session, yard_id: str, plot_id: str | None) -> FarmPlot | None:
    if not plot_id:
        return None
    return db.scalar(select(FarmPlot).where(FarmPlot.yard_id == yard_id, FarmPlot.id == plot_id))


def _find_plot(db: Session, yard_id: str, text: str, plot_id: str | None) -> FarmPlot | None:
    context = _plot_by_id(db, yard_id, plot_id)
    if context is not None:
        return context
    rows = db.scalars(
        select(FarmPlot).where(FarmPlot.yard_id == yard_id, FarmPlot.status != "archived")
    ).all()
    hits = [row for row in rows if row.name and row.name in text]
    if not hits:
        return None
    return max(hits, key=lambda row: len(row.name))


def _member_match(text: str, members: list[dict]) -> tuple[dict | None, str]:
    """按成员显示名/用户名匹配；显式「派给 X」优先。"""
    for m in _PERSON_RE.finditer(text):
        name = m.group(1)
        for member in members:
            display = str(member.get("display_name") or "")
            if name == display or name in display or display in name:
                return member, name
        return None, name
    for member in members:
        display = str(member.get("display_name") or "")
        if display and display in text:
            return member, display
    return None, ""


def _open_alerts(db: Session, yard_id: str) -> list[FarmAlert]:
    return list(
        db.scalars(
            select(FarmAlert)
            .where(
                FarmAlert.yard_id == yard_id,
                FarmAlert.status.in_(("new", "acknowledged")),
                FarmAlert.work_order_id.is_(None),
            )
            .order_by(FarmAlert.created_at.desc())
            .limit(30)
        )
    )


def _match_alert(db: Session, yard_id: str, text: str, plot: FarmPlot | None) -> FarmAlert | None:
    if not any(word in text for word in ("告警", "转单", "转成工单", "转工单")):
        return None
    best: FarmAlert | None = None
    best_score = 0
    for alert in _open_alerts(db, yard_id):
        score = 0
        if alert.title and alert.title in text:
            score += 4
        if plot is not None and alert.plot_id == plot.id:
            score += 2
        for keyword in ("虫情", "病害", "旱情", "低温", "高温", "墒情", "虫害"):
            if keyword in text and keyword in (alert.title or ""):
                score += 2
        if score > best_score:
            best, best_score = alert, score
    return best


def _summary_plot(payload: dict[str, Any]) -> str:
    parts = [f"地块「{payload.get('name') or '未命名'}」"]
    if payload.get("crop"):
        parts.append(f"作物 {payload['crop']}")
    if payload.get("area_mu"):
        parts.append(f"面积 {payload['area_mu']} 亩")
    if payload.get("soil_type"):
        parts.append(f"土壤 {payload['soil_type']}")
    return "，".join(parts)


def _summary_record(payload: dict[str, Any], plot: FarmPlot | None, kind_label: str) -> str:
    parts = [f"在「{plot.name if plot else '未选地块'}」记一条{kind_label}"]
    if payload.get("occurred_on"):
        parts.append(f"日期 {payload['occurred_on']}")
    if payload.get("quantity"):
        parts.append(f"{payload['quantity']}{payload.get('unit') or ''}")
    if payload.get("operator_name"):
        parts.append(f"操作人 {payload['operator_name']}")
    return "，".join(parts)


def _summary_diagnosis(payload: dict[str, Any], plot: FarmPlot | None) -> str:
    where = f"「{plot.name}」" if plot else "未指定地块"
    crop = payload.get("crop") or "作物"
    symptoms = str(payload.get("symptoms") or "")[:60]
    return f"为{where}{crop}登记诊断：{symptoms}"


def _summary_work_order(payload: dict[str, Any], alert: FarmAlert | None) -> str:
    if alert is not None:
        parts = [f"把告警「{alert.title}」转成工单"]
    else:
        parts = [f"新建工单「{payload.get('title') or '现场工单'}」"]
    if payload.get("assignee_name"):
        parts.append(f"指派 {payload['assignee_name']}")
    if payload.get("due_on"):
        parts.append(f"{payload['due_on']} 到期")
    if payload.get("priority") and payload["priority"] != "normal":
        level = {"urgent": "紧急", "high": "高", "low": "低"}.get(payload["priority"], "")
        parts.append(f"{level}优先级")
    return "，".join(parts)


def _rule_parse(
    db: Session,
    *,
    yard_id: str,
    module: str,
    text: str,
    today: date,
    plot_id: str | None,
) -> dict[str, Any]:
    members = list_assignable_members(db, yard_id)
    if module == "farm":
        if re.search(r"(?:新增|添加|新建|建)\s*(?:一块|一个|一亩)?\s*(?:地块|田块|土地|田)", text):
            rest = _ACTION_PREFIX_RE.sub("", text).strip()
            name = _cut_name(rest)
            crop = _find_crop(rest)
            area = _parse_area(rest)
            soil = _find_soil(rest)
            payload: dict[str, Any] = {
                "name": name,
                "crop": crop,
                "area_mu": area,
                "soil_type": soil,
                "notes": "",
            }
            missing = []
            if not name:
                missing.append("地块名称")
            if area is None:
                missing.append("面积（如 12 亩）")
            return {
                "action": "plot.create",
                "payload": payload,
                "summary": _summary_plot(payload),
                "missing": missing,
            }
        plot = _find_plot(db, yard_id, text, plot_id)
        kind = _record_kind(text)
        occurred = _parse_date(text, today) or today
        quantity, unit = _parse_quantity(text)
        member, _ = _member_match(text, members)
        person = str(member.get("display_name") or "") if member else ""
        title = _clean_title(text, plot, person) or "农事记录"
        payload = {
            "plot_id": plot.id if plot else "",
            "plot_name": plot.name if plot else "",
            "kind": kind,
            "occurred_on": occurred.isoformat(),
            "title": title,
            "content": text,
            "quantity": quantity,
            "unit": unit,
            "operator_name": person,
        }
        missing = []
        if plot is None:
            missing.append("地块（未找到匹配的已有地块）")
        if kind == "other":
            missing.append("农事类型（如追肥/灌溉/打药）")
        label = {
            "sowing": "播种",
            "fertilizing": "施肥",
            "irrigation": "灌溉",
            "pesticide": "用药",
            "weeding": "除草",
            "scouting": "巡田",
            "harvest": "采收",
            "other": "农事",
        }[kind]
        return {
            "action": "record.create",
            "payload": payload,
            "summary": _summary_record(payload, plot, label),
            "missing": missing,
        }

    if module == "diagnosis":
        plot = _find_plot(db, yard_id, text, plot_id)
        crop = _find_crop(text) or (plot.crop if plot and plot.crop else "")
        symptoms = text
        if plot is not None:
            symptoms = symptoms.replace(plot.name, "")
        if crop:
            symptoms = symptoms.replace(crop, "", 1)
        for word in _DATE_WORDS:
            symptoms = symptoms.replace(word, "")
        m = _DATE_RE.search(symptoms)
        if m:
            symptoms = symptoms.replace(m.group(0), "")
        symptoms = symptoms.strip(" ，,。；;：:")
        environment: dict[str, Any] = {}
        for weather in ("连阴雨", "连续降雨", "高温", "低温", "干旱", "霜冻"):
            if weather in text:
                environment["recent_weather"] = weather
                break
        payload = {
            "crop": crop,
            "symptoms": symptoms,
            "plot_id": plot.id if plot else None,
            "plot_name": plot.name if plot else "",
            "environment": environment or None,
            "observed_at": None,
        }
        missing = []
        if not crop:
            missing.append("作物")
        if len(symptoms) < 4:
            missing.append("症状描述")
        return {
            "action": "diagnosis.create",
            "payload": payload,
            "summary": _summary_diagnosis(payload, plot),
            "missing": missing,
        }

    plot = _find_plot(db, yard_id, text, plot_id)
    member, person = _member_match(text, members)
    assignee = member
    due = _parse_date(text, today)
    priority = _work_priority(text)
    alert = _match_alert(db, yard_id, text, plot)
    missing = []
    if person and assignee is None:
        missing.append(f"责任人（未找到成员「{person}」）")
    if alert is not None:
        payload = {
            "alert_id": alert.id,
            "alert_title": alert.title,
            "assignee_id": assignee.get("user_id") if assignee else None,
            "assignee_name": str(assignee.get("display_name") or "") if assignee else "",
            "due_on": due.isoformat() if due else None,
            "priority": priority,
        }
        return {
            "action": "alert.convert",
            "payload": payload,
            "summary": _summary_work_order(payload, alert),
            "missing": missing,
        }
    title = _clean_title(text, plot, person) or "现场工单"
    payload = {
        "title": title[:160],
        "description": text,
        "plot_name": plot.name if plot else "",
        "priority": priority,
        "plot_id": plot.id if plot else None,
        "source": "quick_action",
        "assignee_id": assignee.get("user_id") if assignee else None,
        "assignee_name": str(assignee.get("display_name") or "") if assignee else "",
        "due_on": due.isoformat() if due else None,
    }
    return {
        "action": "work_order.create",
        "payload": payload,
        "summary": _summary_work_order(payload, None),
        "missing": missing,
    }


def _llm_messages(
    module: str,
    text: str,
    today: date,
    plots: list[str],
    members: list[str],
    rule: dict[str, Any],
) -> list[ChatMessage]:
    system = (
        "你是业务指令解析器。把用户一句话解析成一次业务动作，只输出 JSON：\n"
        '{"action": str, "payload": object, "missing": [str], "reason": str}\n'
        f"允许的动作：{sorted(MODULE_ACTIONS[module])}。\n"
        "要求：\n"
        "1) 只解析一个动作；不知道的字段写 null 或空字符串，并放进 missing；\n"
        "2) 不得编造不存在的地块、成员或日期；只能从给定清单里选；\n"
        "3) 日期用 YYYY-MM-DD；面积单位统一为亩；\n"
        "4) 中文输出，不要 Markdown 代码围栏以外的内容。"
    )
    context = {
        "module": module,
        "text": text,
        "today": today.isoformat(),
        "plots": plots[:50],
        "members": members[:50],
        "rule_candidate": rule,
    }
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=json.dumps(context, ensure_ascii=False)),
    ]


def _parse_json(content: str) -> dict[str, Any] | None:
    text = (content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _llm_fill(
    db: Session,
    *,
    yard_id: str,
    user_id: str,
    request_id: str,
    module: str,
    text: str,
    today: date,
    rule: dict[str, Any],
) -> dict[str, Any] | None:
    """规则缺字段时让模型补齐；模型不可用/输出不合法时静默返回 None。"""
    plots = [
        str(name)
        for name in db.scalars(
            select(FarmPlot.name).where(FarmPlot.yard_id == yard_id, FarmPlot.status != "archived")
        )
    ]
    members = [str(m.get("display_name") or "") for m in list_assignable_members(db, yard_id)]
    try:
        resp = asyncio.run(
            complete_with_logging(
                db,
                LLMRequest(
                    messages=_llm_messages(module, text, today, plots, members, rule),
                    max_tokens=600,
                    metadata=LLMMetadata(
                        yard_id=yard_id,
                        user_id=user_id,
                        request_id=request_id,
                        purpose="quick_action",
                        agent_ref="quick_actions.parse",
                    ),
                ),
            )
        )
    except Exception as e:  # noqa: BLE001 - 模型不可用不阻断预览
        log.info("quick action llm fallback skipped: %s", e)
        return None
    data = _parse_json(resp.content or "")
    if not data:
        return None
    action = str(data.get("action") or "")
    payload = data.get("payload")
    if action not in MODULE_ACTIONS[module] or not isinstance(payload, dict):
        return None
    missing = [str(item) for item in (data.get("missing") or []) if str(item).strip()]
    return {"action": action, "payload": payload, "missing": missing[:6]}


def parse(
    db: Session,
    *,
    yard_id: str,
    user_id: str,
    request_id: str,
    module: str,
    text: str,
    plot_id: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """解析一句话，返回动作/负载/缺失项；不写库、不改状态。"""
    day = today or date.today()
    cleaned = " ".join(text.split())
    rule = _rule_parse(db, yard_id=yard_id, module=module, text=cleaned, today=day, plot_id=plot_id)
    rule["source"] = "rule"
    if not rule["missing"]:
        return rule

    llm = _llm_fill(
        db,
        yard_id=yard_id,
        user_id=user_id,
        request_id=request_id,
        module=module,
        text=cleaned,
        today=day,
        rule=rule,
    )
    if llm is None:
        return rule
    if llm["action"] != rule["action"]:
        # 动作判断不一致时不混用两套结果，避免预览与用户预期错位
        return rule
    merged = {**rule["payload"]}
    for key, value in llm["payload"].items():
        if value not in (None, "", []) or merged.get(key) in (None, "", []):
            merged[key] = value
    missing = [
        item for item in rule["missing"] if not _missing_resolved(item, merged, rule["action"])
    ]
    if not missing:
        merged["source"] = "llm"
        return {
            **rule,
            "payload": merged,
            "missing": [],
            "source": "llm",
        }
    return rule


def _missing_resolved(label: str, payload: dict[str, Any], action: str) -> bool:
    if label.startswith("地块名称"):
        return bool(str(payload.get("name") or "").strip())
    if label.startswith("面积"):
        return bool(payload.get("area_mu"))
    if label.startswith("作物"):
        return bool(str(payload.get("crop") or "").strip())
    if label.startswith("症状"):
        return len(str(payload.get("symptoms") or "").strip()) >= 4
    if label.startswith("地块"):
        return bool(payload.get("plot_id"))
    if label.startswith("农事类型"):
        return str(payload.get("kind") or "") not in ("", "other")
    if label.startswith("责任人"):
        return bool(payload.get("assignee_id"))
    return action == ""


ALLOWED_ACTIONS = MODULE_ACTIONS

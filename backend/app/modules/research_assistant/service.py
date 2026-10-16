"""科研辅助 Agent 的纯计算服务：CSV 解析、缺失值报告、描述统计、图表草案。

设计约束（开发文档 V3.0 §12.3）：
- 仅使用 Python 标准库（csv / io / math），不引入 pandas 等新依赖。
- 只做描述统计：不计算 p 值、不做显著性检验、不编造文献引用。
- 所有统计方法的前提假设必须写入返回值的 assumptions 列表。
- 本模块为纯函数，不读库、无状态。

核心入口：analyze_csv(csv_text, field_mapping) -> dict
"""

from __future__ import annotations

import csv
import io
import math
from typing import Any

from app.core.errors import AppError

# 缺失值标记（不区分大小写；空字符串天然包含）
MISSING_TOKENS = frozenset({"", "na", "n/a", "null", "none", "nan", "-", "--", "?"})

# 图表草案上限：取前 N 个数值列、每系列至多 M 个点（前端直接渲染的合理规模）
MAX_CHART_COLUMNS = 3
MAX_CHART_POINTS = 500

# 统计量保留小数位数（展示用精度，不改变原始数据）
ROUND_DIGITS = 6


def _is_missing(value: str) -> bool:
    return value.strip().lower() in MISSING_TOKENS


def _try_float(value: str) -> float | None:
    """解析为有限浮点数；缺失标记或不可解析返回 None。nan/inf 视为缺失。"""
    text = value.strip()
    if text.lower() in MISSING_TOKENS:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number


def _parse_rows(csv_text: str) -> tuple[list[str], list[list[str]], int]:
    """解析 CSV 文本 -> (表头, 数据行, 行列数不一致的行数)。

    空 CSV / 无数据行 / 解析失败 -> AppError(400)。
    行列数不一致的行按表头截断或补空，并计数供 warnings 使用。
    """
    if not csv_text or not csv_text.strip():
        raise AppError("INVALID_REQUEST", "CSV 内容为空，无法分析。", http_status=400)
    text = csv_text.lstrip("\ufeff")
    if "\x00" in text:
        raise AppError("INVALID_REQUEST", "CSV 包含非法控制字符，解析失败。", http_status=400)
    try:
        reader = csv.reader(io.StringIO(text))
        all_rows = [row for row in reader if any(cell.strip() for cell in row)]
    except csv.Error as e:
        raise AppError("INVALID_REQUEST", f"CSV 解析失败：{e}。", http_status=400) from e
    if not all_rows:
        raise AppError("INVALID_REQUEST", "CSV 内容为空，无法分析。", http_status=400)
    headers = [h.strip() for h in all_rows[0]]
    if not headers or all(h == "" for h in headers):
        raise AppError("INVALID_REQUEST", "CSV 缺少有效的表头行。", http_status=400)
    data_rows = all_rows[1:]
    if not data_rows:
        raise AppError("INVALID_REQUEST", "CSV 仅有表头、无数据行，无法分析。", http_status=400)
    ragged = 0
    normalized: list[list[str]] = []
    for row in data_rows:
        if len(row) != len(headers):
            ragged += 1
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        else:
            row = row[: len(headers)]
        normalized.append([cell.strip() for cell in row])
    return headers, normalized, ragged


def _apply_mapping(
    headers: list[str], field_mapping: dict[str, str] | None
) -> tuple[list[str], list[str]]:
    """应用字段映射并做重名消歧；返回 (展示列名, warnings)。"""
    mapping = dict(field_mapping or {})
    warnings: list[str] = []
    renamed = [mapping.get(h, h) for h in headers]
    unused = [k for k in mapping if k not in headers]
    if unused:
        warnings.append(
            f"字段映射中有 {len(unused)} 个原列名在 CSV 表头中不存在，已忽略：{unused}。"
        )
    seen: dict[str, int] = {}
    display: list[str] = []
    for name in renamed:
        count = seen.get(name, 0)
        seen[name] = count + 1
        display.append(name if count == 0 else f"{name}_{count + 1}")
    if any(v > 1 for v in seen.values()):
        warnings.append("CSV 存在重名列，已自动追加后缀区分（如 col_2）。")
    return display, warnings


def _numeric_series(values: list[str]) -> list[float | None] | None:
    """列是否为数值列：至少一个有效数值，且全部非缺失值可解析为有限浮点数。"""
    series: list[float | None] = []
    has_value = False
    for v in values:
        if _is_missing(v):
            series.append(None)
            continue
        number = _try_float(v)
        if number is None:
            return None
        series.append(number)
        has_value = True
    return series if has_value else None


def _describe(numbers: list[float]) -> dict[str, Any]:
    """描述统计：count / mean / std（样本标准差）/ min / max。"""
    n = len(numbers)
    mean = sum(numbers) / n
    variance = sum((x - mean) ** 2 for x in numbers) / (n - 1) if n > 1 else 0.0
    return {
        "count": n,
        "mean": round(mean, ROUND_DIGITS),
        "std": round(math.sqrt(variance), ROUND_DIGITS),
        "min": round(min(numbers), ROUND_DIGITS),
        "max": round(max(numbers), ROUND_DIGITS),
    }


def _build_chart_draft(
    numeric_columns: list[tuple[str, list[float | None]]], total_rows: int
) -> tuple[dict[str, Any] | None, list[str]]:
    """图表草案：取前 N 个数值列生成 line 系列；无数值列时返回 None。"""
    warnings: list[str] = []
    if not numeric_columns:
        warnings.append("未识别到数值列，无法生成图表草案。")
        return None, warnings
    picked = numeric_columns[:MAX_CHART_COLUMNS]
    if len(numeric_columns) > MAX_CHART_COLUMNS:
        warnings.append(
            f"数值列共 {len(numeric_columns)} 个，图表草案仅取前 {MAX_CHART_COLUMNS} 个。"
        )
    truncated = total_rows > MAX_CHART_POINTS
    if truncated:
        warnings.append(f"数据行共 {total_rows} 行，图表系列仅取前 {MAX_CHART_POINTS} 个点渲染。")
    chart = {
        "chart_type": "line",
        "title": "数值列趋势草案",
        "x_label": "数据行序号",
        "y_label": "数值",
        "series": [{"name": name, "data": series[:MAX_CHART_POINTS]} for name, series in picked],
    }
    return chart, warnings


ASSUMPTIONS: list[str] = [
    "假设数据为独立观测。",
    "本分析仅做描述统计，不进行显著性检验、不报告 p 值、不引用外部文献。",
    "数值列自动识别规则：该列至少含一个有效数值，且全部非缺失值均可解析为有限浮点数；文本列自动跳过统计。",
    "缺失值定义为空字符串或常见缺失标记（NA、N/A、null、none、-、--、?，不区分大小写）。",
    "标准差为样本标准差（分母 n-1）；仅含一个有效数值的列记为 0.0，表示无离散度可估。",
    "图表草案为自动生成的可视化建议，需研究人员结合业务含义审核后使用。",
]


def analyze_csv(csv_text: str, field_mapping: dict[str, str] | None = None) -> dict[str, Any]:
    """分析 CSV 文本，返回符合 AnalysisOutput 结构的 dict。"""
    headers, data_rows, ragged = _parse_rows(csv_text)
    display_names, warnings = _apply_mapping(headers, field_mapping)
    if ragged:
        warnings.append(f"{ragged} 行列数与表头不一致，已按表头截断或补空处理，请检查原始数据。")
    columns: dict[str, list[str]] = {name: [] for name in display_names}
    for row in data_rows:
        for name, cell in zip(display_names, row, strict=True):
            columns[name].append(cell)
    missing_values = {
        name: sum(1 for v in values if _is_missing(v)) for name, values in columns.items()
    }
    descriptive_stats: dict[str, dict[str, Any]] = {}
    numeric_columns: list[tuple[str, list[float | None]]] = []
    skipped: list[str] = []
    for name, values in columns.items():
        series = _numeric_series(values)
        if series is None:
            skipped.append(name)
            continue
        numbers = [x for x in series if x is not None]
        descriptive_stats[name] = _describe(numbers)
        numeric_columns.append((name, series))
    if skipped:
        warnings.append(f"以下非数值列已跳过描述统计（仍列入导入报告）：{skipped}。")
    chart_draft, chart_warnings = _build_chart_draft(numeric_columns, len(data_rows))
    warnings.extend(chart_warnings)
    return {
        "import_report": {
            "rows": len(data_rows),
            "columns": display_names,
            "missing_values": missing_values,
        },
        "descriptive_stats": descriptive_stats,
        "chart_draft": chart_draft,
        "assumptions": list(ASSUMPTIONS),
        "warnings": warnings,
        "needs_review": True,
    }

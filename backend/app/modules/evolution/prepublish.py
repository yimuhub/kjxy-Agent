"""上架前评测报告（v3.17）。

两道闸：
1. **静态质量检查**（零 LLM、可复现）：定义是否有身份、提示词/步骤是否为空、
   提示词长度、步骤数量、工具是否声明、高风险是否要求审核、引用是否配来源、
   技能模板是否配足评例。致命项（error）阻止上架，提示项（warning）只扣分。
2. **数据集评测**（可选）：definition.evaluation.test_set_id 指向 backend/eval
   下的数据集，由 service 复用现有 run_eval_set 执行，本模块只负责摘要与合并。

报告写进 eval_runs.report_json，并附摘要；同一版本每次审核/安装各留一份，
不覆盖历史。报告里不放参考正文与用户数据，只放检查结论与用例 ID。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.eval.runner import DATASET_DIR, EvalReport
from app.modules.evolution import attestation
from app.modules.evolution.models import EvalRun, Skill

REPORT_VERSION = 1
STATIC_TEST_SET_ID = "static_precheck"
MAX_PROMPT_CHARS = 40_000
MAX_STEPS = 12
MIN_EVAL_CASES = 2


def _text(definition: dict, *keys: str) -> str:
    for key in keys:
        value = definition.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _steps(definition: dict) -> list[Any]:
    raw = definition.get("steps")
    if not isinstance(raw, list):
        raw = definition.get("procedure")
    return raw if isinstance(raw, list) else []


def _allowed_tools(definition: dict) -> list[str]:
    raw = definition.get("allowed_tools")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _tool_of(step: Any) -> str | None:
    if isinstance(step, dict):
        kind = str(step.get("kind") or step.get("type") or "").strip().lower()
        tool = step.get("tool") or step.get("tool_code")
        if kind in ("", "tool") and isinstance(tool, str) and tool.strip():
            return tool.strip()
    return None


def _check(code: str, severity: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "passed": passed, "detail": detail}


def static_checks(definition: dict | None) -> list[dict[str, Any]]:
    """纯函数：对定义做机械检查，返回检查项列表。"""
    checks: list[dict[str, Any]] = []
    if not isinstance(definition, dict) or not definition:
        checks.append(
            _check("definition_present", "error", False, "技能定义为空或不是 JSON 对象。")
        )
        return checks
    checks.append(_check("definition_present", "error", True, "定义存在。"))

    name = _text(definition, "name", "title")
    checks.append(
        _check(
            "identity",
            "warning",
            bool(name),
            "已填写名称。" if name else "缺少 name/title，列表与路由会退回使用技能编码。",
        )
    )

    prompt = _text(definition, "prompt", "content", "prompt_template")
    steps = _steps(definition)
    has_body = bool(prompt) or bool(steps)
    checks.append(
        _check(
            "prompt_or_steps",
            "warning",
            has_body,
            "有可执行的提示词或步骤。"
            if has_body
            else "只有标题没有提示词/步骤，运行时无法产生可复现的输出。",
        )
    )

    if prompt:
        if len(prompt) > MAX_PROMPT_CHARS:
            checks.append(
                _check(
                    "prompt_size",
                    "error",
                    False,
                    f"提示词 {len(prompt)} 字，超过上限 {MAX_PROMPT_CHARS}，应拆分或精简。",
                )
            )
        elif len(prompt) < 20:
            checks.append(
                _check(
                    "prompt_size",
                    "warning",
                    False,
                    f"提示词仅 {len(prompt)} 字，信息可能不足。",
                )
            )
        else:
            checks.append(_check("prompt_size", "error", True, f"提示词 {len(prompt)} 字。"))

    checks.append(
        _check(
            "steps_count",
            "error",
            len(steps) <= MAX_STEPS,
            f"步骤 {len(steps)} 条（上限 {MAX_STEPS}）。",
        )
    )

    allowed = set(_allowed_tools(definition))
    step_tools = [_tool_of(step) for step in steps]
    undeclared = [tool for tool in step_tools if tool and tool not in allowed]
    checks.append(
        _check(
            "tool_steps_declared",
            "error",
            not undeclared,
            "工具步骤都已列入 allowed_tools。"
            if not undeclared
            else f"工具步骤未在 allowed_tools 声明：{'、'.join(sorted(set(undeclared)))}。",
        )
    )

    guardrails = definition.get("guardrails")
    guardrails = guardrails if isinstance(guardrails, dict) else {}
    risk = str(guardrails.get("risk_level") or definition.get("risk_level") or "").lower()
    requires_review = bool(guardrails.get("requires_review") or definition.get("requires_review"))
    checks.append(
        _check(
            "high_risk_review",
            "warning",
            not (risk == "high" and not requires_review),
            "高风险技能已要求人工审核。"
            if not (risk == "high" and not requires_review)
            else "风险等级为 high 但未要求人工审核。",
        )
    )

    require_citation = bool(
        guardrails.get("require_citation") or definition.get("require_citation")
    )
    sources = definition.get("sources")
    sources = sources if isinstance(sources, list) else []
    checks.append(
        _check(
            "citation_sources",
            "warning",
            not (require_citation and not sources),
            "要求引用且配置了来源。"
            if not (require_citation and not sources)
            else "要求引用但未配置任何来源。",
        )
    )

    template = str(definition.get("template") or "")
    eval_cases = definition.get("eval_cases")
    eval_cases = eval_cases if isinstance(eval_cases, list) else []
    if template or eval_cases:
        checks.append(
            _check(
                "eval_cases",
                "warning",
                len(eval_cases) >= MIN_EVAL_CASES,
                f"评例 {len(eval_cases)} 条（技能模板要求 ≥{MIN_EVAL_CASES}）。",
            )
        )

    source_url = _text(definition, "source_url")
    license_name = _text(definition, "license")
    if source_url.startswith(("http://", "https://", "local://", "github://")):
        checks.append(
            _check(
                "license",
                "warning",
                bool(license_name),
                "已声明许可证。" if license_name else "外部来源未声明许可证，上架前需人工确认。",
            )
        )
    return checks


def static_report(skill: Skill) -> dict[str, Any]:
    definition = skill.definition_json or {}
    checks = static_checks(definition)
    errors = sum(1 for c in checks if c["severity"] == "error" and not c["passed"])
    warnings = sum(1 for c in checks if c["severity"] == "warning" and not c["passed"])
    score = max(0, 100 - 30 * errors - 8 * warnings)
    return {
        "report_version": REPORT_VERSION,
        "kind": "prepublish",
        "generated_at": datetime.now(UTC).isoformat(),
        "skill_code": skill.skill_code,
        "version": skill.version,
        "definition_sha256": attestation.definition_digest(definition),
        "mode": "static",
        "pass": errors == 0,
        "score": score,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "dataset": None,
        "notes": [],
    }


def dataset_digest(test_set_id: str, dataset_path: str | Path | None = None) -> str | None:
    path = Path(dataset_path) if dataset_path else DATASET_DIR / f"{test_set_id}.json"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def summarize_dataset(
    report: EvalReport,
    test_set_id: str,
    *,
    minimum_pass_rate: float | None = None,
) -> dict[str, Any]:
    failed_ids = [cr.case_id for cr in report.case_results if not cr.passed]
    minimum = (
        float(minimum_pass_rate)
        if minimum_pass_rate is not None
        else float(report.minimum_pass_rate)
    )
    leakage = int((report.metrics or {}).get("cross_yard_leakage") or 0)
    return {
        "test_set_id": test_set_id,
        "version": report.version,
        "dataset_sha256": dataset_digest(test_set_id),
        "pass_rate": round(report.pass_rate, 4),
        "minimum_pass_rate": round(minimum, 4),
        "gate_passed": report.pass_rate >= minimum and leakage == 0,
        "passed": report.passed,
        "failed": report.failed,
        "total": report.total,
        "failed_case_ids": failed_ids,
        "category_pass_rates": report.category_pass_rates,
        "metrics": report.metrics,
        "cases": [
            {
                "case_id": cr.case_id,
                "category": cr.category,
                "passed": cr.passed,
                "reason": cr.reason,
                "latency_ms": round(cr.latency_ms, 2),
                "citations_count": cr.citations_count,
            }
            for cr in report.case_results
        ],
    }


def merge_dataset(
    static: dict[str, Any],
    dataset: dict[str, Any],
    *,
    dataset_skipped: bool = False,
) -> dict[str, Any]:
    """把数据集结果并入静态报告：静态 error 仍一票否决。"""
    report = dict(static)
    report["dataset"] = dataset or None
    report["mode"] = "static+dataset_skipped" if dataset_skipped else "static+dataset"
    report["pass"] = bool(static["pass"]) and bool(
        dataset_skipped or dataset.get("gate_passed", False)
    )
    if dataset_skipped:
        report["notes"].append(
            "evaluation.skip_gate=true：数据集评测被显式跳过，报告仅含静态检查。"
        )
    return report


def persist_run(
    db: Session,
    ctx: YardContext,
    skill: Skill,
    report: dict[str, Any],
    *,
    kind: str,
    test_set_id: str,
    created_by: str | None = None,
) -> EvalRun:
    dataset = report.get("dataset") or {}
    if dataset:
        passed = int(dataset.get("passed") or 0)
        failed = int(dataset.get("failed") or 0)
        total = int(dataset.get("total") or 0)
        pass_rate = float(dataset.get("pass_rate") or 0.0)
    else:
        checks = report.get("checks") or []
        passed = sum(1 for c in checks if c["passed"])
        failed = len(checks) - passed
        total = len(checks)
        pass_rate = 1.0 if report.get("pass") else 0.0
    run = EvalRun(
        yard_id=ctx.yard_id,
        skill_code=skill.skill_code,
        skill_version=skill.version,
        test_set_id=test_set_id,
        pass_rate=pass_rate,
        passed=passed,
        failed=failed,
        total=total,
        created_by=created_by or ctx.user_id,
        kind=kind,
        report_json=report,
        report_sha256=attestation.report_digest(report),
        dataset_sha256=dataset.get("dataset_sha256"),
    )
    db.add(run)
    db.flush()
    return run


def blocking_issues(report: dict[str, Any]) -> list[dict[str, Any]]:
    issues = [c for c in report.get("checks") or [] if c["severity"] == "error" and not c["passed"]]
    dataset = report.get("dataset") or {}
    if dataset and not dataset.get("gate_passed", False):
        issues.append(
            {
                "code": "dataset_gate",
                "severity": "error",
                "passed": False,
                "detail": (
                    f"数据集 {dataset.get('test_set_id')} 通过率 "
                    f"{float(dataset.get('pass_rate') or 0):.1%} 低于最低要求 "
                    f"{float(dataset.get('minimum_pass_rate') or 0):.1%}。"
                ),
            }
        )
    return issues


__all__ = [
    "MAX_PROMPT_CHARS",
    "MAX_STEPS",
    "REPORT_VERSION",
    "STATIC_TEST_SET_ID",
    "blocking_issues",
    "dataset_digest",
    "merge_dataset",
    "persist_run",
    "static_checks",
    "static_report",
    "summarize_dataset",
]

"""经验资产学习：工作流沉淀、失败黑名单与跨 Agent 技能萃取。

设计口径（对齐拟人化集群方案第五章）：
- 成功运行自动提炼步骤模板，同场景优先复用；
- 同场景同错误累计达阈值后进入黑名单，规划前提示规避；
- Worker 同类任务稳定成功 ≥2 个样本才转正技能，样本不足先候选。

全部计算确定性、可离线；同一 run 重复学习不会重复计数。
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.commander.asset_models import (
    FAILURE_ACTIVE,
    FAILURE_RESOLVED,
    FAILURE_WATCHING,
    SKILL_ACTIVE,
    SKILL_ARCHIVED,
    SKILL_CANDIDATE,
    SOURCE_AUTO,
    SOURCE_MANUAL,
    WORKFLOW_ACTIVE,
    WORKFLOW_ARCHIVED,
    WORKFLOW_DRAFT,
    CommanderFailureCase,
    CommanderSkillSnippet,
    CommanderWorkflow,
)
from app.modules.commander.models import (
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_PARTIAL,
    SOURCE_COMMANDER,
    SUB_ERROR,
    SUB_OK,
    CommanderRun,
    CommanderSubtask,
)

log = get_logger(__name__)

#: 场景匹配阈值：低于该值不注入工作流，避免「看起来像」就套模板
MATCH_THRESHOLD = 0.42
#: 技能转正所需的最少样本与成功率
SKILL_MIN_SAMPLES = 2
SKILL_MIN_SUCCESS_RATE = 0.8
#: 保留的来源 run 上限
MAX_SOURCE_RUNS = 30
MAX_FAILURE_RUNS = 50
#: 学习扫描覆盖天数与运行上限
SCAN_DAYS = 30
SCAN_RUN_LIMIT = 200

_STOPWORDS = {
    "一个",
    "一份",
    "任务",
    "生成",
    "制作",
    "完成",
    "帮我",
    "给我",
    "需要",
    "进行",
    "以及",
    "然后",
    "这个",
    "那个",
    "可以",
    "我们",
    "你们",
}

_ERROR_KNOWLEDGE: dict[str, tuple[str, str]] = {
    "COMMANDER_TIMEOUT": (
        "子任务或整轮运行超时，步骤粒度过大或上游响应过慢。",
        "把步骤拆小并给出更明确的单步指令；必要时降低并发或提高超时上限。",
    ),
    "LLM_TIMEOUT": (
        "上游模型响应超时，未在时限内返回可用结果。",
        "先拆分上下文并缩短提示；连续超时则切换模型或降级到规则路径。",
    ),
    "LLM_ERROR": (
        "上游模型调用失败，或返回内容无法解析为可执行结果。",
        "记录失败输入并降低温度/长度；不要原样重试，先换模型或换执行路径。",
    ),
    "COMMANDER_CAPABILITY_GAP": (
        "现有助手与工具无法完成该步骤，补救轮已用尽。",
        "更换具备该能力的助手，或先补技能/工具后重试；不要让同一助手硬扛。",
    ),
    "TOOL_ERROR": (
        "工具执行失败，通常是参数不完整、权限不足或外部服务不可用。",
        "先用最小参数探测工具可用性，再补全参数；权限问题走审批或换工具。",
    ),
    "QUOTA_EXCEEDED": (
        "额度耗尽或供应商限流，调用被拒绝。",
        "暂停同类高消耗调用，等待额度恢复或切换供应商后分批执行。",
    ),
    "COMMANDER_NOT_SCHEDULED": (
        "运行超时或取消导致后续子任务未获调度。",
        "减少单轮子任务数量或拆分运行；关键步骤不要放在队列末尾。",
    ),
}
_DEFAULT_KNOWLEDGE = (
    "执行器返回失败，具体根因需结合运行日志确认。",
    "换助手或换工具路径，不要原样重复同一指令。",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _sha(text: str, size: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:size]  # noqa: S324 - 非安全哈希


def keywords(text: str) -> set[str]:
    """中英混合关键词：英文按词，中文按二元组；用于场景相似度。"""
    lowered = (text or "").lower()
    words = re.findall(r"[a-z0-9]{2,}", lowered)
    chinese = re.sub(r"[^\u4e00-\u9fff]+", " ", lowered)
    grams: list[str] = []
    for chunk in chinese.split():
        if len(chunk) == 1:
            grams.append(chunk)
            continue
        grams.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return {token for token in [*words, *grams] if token not in _STOPWORDS}


def similarity(left: str, right: str) -> float:
    a = keywords(left)
    b = keywords(right)
    if not a or not b:
        return 0.0
    return round(len(a & b) / max(1, min(len(a), len(b))), 4)


def scene_code(text: str) -> str:
    tokens = sorted(keywords(text))
    if not tokens:
        return _sha((text or "").strip().lower(), 16)
    return _sha("|".join(tokens), 16)


def workflow_hint(workflow: CommanderWorkflow) -> str:
    steps = workflow.steps_json or []
    lines = [
        f"{workflow.name}（{workflow.code}，v{workflow.version}，"
        f"成功 {workflow.success_count} 次）"
    ]
    if workflow.preconditions:
        lines.append(f"前置：{workflow.preconditions}")
    for index, step in enumerate(steps[:8], start=1):
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "")
        agent = str(step.get("agent_code") or "")
        suffix = f"（{agent}）" if agent else ""
        lines.append(f"{index}. {title}{suffix}")
    lines.append("可复用该步骤顺序与产出要求；与当前目标冲突时以当前目标为准，并说明差异。")
    return "\n".join(lines)


def failure_hint(cases: list[CommanderFailureCase]) -> str:
    if not cases:
        return ""
    lines: list[str] = []
    for case in cases[:3]:
        lines.append(
            f"- 场景：{case.scene[:120]}；错误：{case.error_code}"
            f"（累计 {case.occurrences} 次）；规避：{case.avoidance}"
        )
    return "\n".join(lines)


def match_workflow(
    db: Session, *, yard_id: str, text: str
) -> tuple[CommanderWorkflow, float] | None:
    """按场景相似度找最匹配的活跃工作流；低于阈值不返回。"""
    rows = list(
        db.scalars(
            select(CommanderWorkflow)
            .where(
                CommanderWorkflow.yard_id == yard_id,
                CommanderWorkflow.status == WORKFLOW_ACTIVE,
            )
            .order_by(CommanderWorkflow.success_count.desc())
            .limit(50)
        )
    )
    best: CommanderWorkflow | None = None
    best_score = 0.0
    for row in rows:
        score = similarity(f"{row.name} {row.scene}", text)
        if score > best_score:
            best, best_score = row, score
    if best is None or best_score < MATCH_THRESHOLD:
        return None
    return best, best_score


def mark_workflow_used(db: Session, workflow: CommanderWorkflow) -> None:
    workflow.last_used_at = _now()
    db.add(workflow)


def retrieve_failures(
    db: Session, *, yard_id: str, text: str, limit: int = 3
) -> list[CommanderFailureCase]:
    rows = list(
        db.scalars(
            select(CommanderFailureCase)
            .where(
                CommanderFailureCase.yard_id == yard_id,
                CommanderFailureCase.status == FAILURE_ACTIVE,
            )
            .order_by(CommanderFailureCase.occurrences.desc())
            .limit(100)
        )
    )
    scored = [(similarity(row.scene, text), row) for row in rows]
    scored = [item for item in scored if item[0] >= 0.2]
    scored.sort(key=lambda item: (item[0], item[1].occurrences), reverse=True)
    return [row for _, row in scored[: max(1, limit)]]


def _step_payload(subtask: CommanderSubtask) -> dict[str, Any]:
    return {
        "title": (subtask.title or "")[:200],
        "instruction": (subtask.instruction or "")[:2000],
        "expected_output": (subtask.expected_output or "")[:1000],
        "agent_code": subtask.agent_code or "",
    }


def learn_from_run(
    db: Session,
    *,
    run: CommanderRun,
    subtasks: list[CommanderSubtask],
    failure_threshold: int = 2,
    record_failures: bool = True,
    learn_workflows: bool = True,
) -> CommanderWorkflow | None:
    """运行成功后沉淀工作流；失败子任务累计进黑名单。幂等。

    两个开关各管各的：关掉工作流沉淀不该连失败黑名单一起停——黑名单是「别再派同一个
    坑」的依据，比沉淀更常用到。
    """
    if not run.yard_id or not run.id:
        return None
    ok_steps = [st for st in subtasks if st.status == SUB_OK]
    scene = (run.goal or "").strip()
    workflow: CommanderWorkflow | None = None
    if (
        learn_workflows
        and ok_steps
        and scene
        and run.status in (RUN_COMPLETED, RUN_PARTIAL)
        and len(scene) >= 2
    ):
        code = f"WF-{scene_code(scene)}"
        workflow = db.scalar(
            select(CommanderWorkflow).where(
                CommanderWorkflow.yard_id == run.yard_id,
                CommanderWorkflow.code == code,
            )
        )
        seen = list(workflow.source_run_ids_json or []) if workflow is not None else []
        if workflow is not None and run.id in seen:
            return workflow
        steps = [_step_payload(st) for st in sorted(ok_steps, key=lambda item: item.seq)]
        tokens = int(run.tokens_total or 0)
        if workflow is None:
            workflow = CommanderWorkflow(
                yard_id=run.yard_id,
                code=code,
                name=f"自动沉淀：{scene[:60]}",
                scene=scene[:4000],
                preconditions="同一小院、同类目标，且所需助手/技能可用。",
                steps_json=steps,
                cost_baseline_tokens=tokens,
                success_count=1,
                failure_count=len(subtasks) - len(ok_steps),
                version=1,
                status=WORKFLOW_ACTIVE,
                source=SOURCE_AUTO,
                source_run_ids_json=[run.id],
                created_by=run.user_id,
            )
            db.add(workflow)
        elif workflow.status != WORKFLOW_ARCHIVED:
            workflow.steps_json = steps
            workflow.success_count += 1
            workflow.failure_count += len(subtasks) - len(ok_steps)
            workflow.version += 1
            if tokens > 0:
                baseline = int(workflow.cost_baseline_tokens or 0)
                workflow.cost_baseline_tokens = (
                    tokens if baseline <= 0 else round((baseline + tokens) / 2)
                )
            workflow.source_run_ids_json = [*seen, run.id][-MAX_SOURCE_RUNS:]
            db.add(workflow)

    if record_failures:
        _record_failures(db, run=run, subtasks=subtasks, threshold=failure_threshold)
    db.flush()
    return workflow


def _record_failures(
    db: Session,
    *,
    run: CommanderRun,
    subtasks: list[CommanderSubtask],
    threshold: int,
) -> int:
    scene = (run.goal or "").strip()
    if not scene:
        return 0
    changed = 0
    for subtask in subtasks:
        if subtask.status != SUB_ERROR and subtask.error_code != "COMMANDER_NOT_SCHEDULED":
            continue
        code = str(subtask.error_code or "UNKNOWN")
        message = ""
        for entry in reversed(list(subtask.remediation_json or [])):
            if isinstance(entry, dict) and entry.get("message"):
                message = str(entry["message"])[:300]
                break
        signature = _sha(f"{run.yard_id}|{scene_code(scene)}|{code}|{message}", 40)
        case = db.scalar(
            select(CommanderFailureCase).where(
                CommanderFailureCase.yard_id == run.yard_id,
                CommanderFailureCase.signature == signature,
            )
        )
        if case is None:
            case = CommanderFailureCase(
                yard_id=run.yard_id,
                signature=signature,
                scene=scene[:500],
                error_code=code[:50],
                error_message=message,
                root_cause=_ERROR_KNOWLEDGE.get(code, _DEFAULT_KNOWLEDGE)[0],
                avoidance=_ERROR_KNOWLEDGE.get(code, _DEFAULT_KNOWLEDGE)[1],
                occurrences=1,
                status=FAILURE_ACTIVE if threshold <= 1 else FAILURE_WATCHING,
                source_run_id=run.id,
                seen_run_ids_json=[run.id],
                last_seen_at=_now(),
            )
            db.add(case)
            changed += 1
            continue
        seen = list(case.seen_run_ids_json or [])
        if run.id in seen:
            continue
        case.occurrences += 1
        case.status = FAILURE_ACTIVE if case.occurrences >= threshold else FAILURE_WATCHING
        case.source_run_id = run.id
        case.seen_run_ids_json = [*seen, run.id][-MAX_FAILURE_RUNS:]
        case.last_seen_at = _now()
        if message and not case.error_message:
            case.error_message = message
        db.add(case)
        changed += 1
    return changed


def _skill_content(sample: CommanderSubtask) -> str:
    lines = [
        f"适用场景：{sample.title or sample.instruction[:60]}",
        f"执行范式：{sample.instruction[:1200]}",
    ]
    if sample.expected_output:
        lines.append(f"产出要求：{sample.expected_output[:600]}")
    if sample.agent_name:
        lines.append(f"示范助手：{sample.agent_name}（{sample.agent_code}）")
    lines.append("复用前先核对当前工具与数据是否满足前置条件；失败时回到本场景黑名单规避。")
    return "\n".join(lines)


def extract_skills_for_yard(
    db: Session,
    *,
    yard_id: str,
    days: int = SCAN_DAYS,
    min_samples: int = SKILL_MIN_SAMPLES,
) -> list[CommanderSkillSnippet]:
    """按「Worker + 场景」聚合成功样本，稳定者转正；统计每次重算，幂等。"""
    since = _now() - timedelta(days=max(1, days))
    subtasks = list(
        db.scalars(
            select(CommanderSubtask)
            .where(
                CommanderSubtask.yard_id == yard_id,
                CommanderSubtask.created_at >= since,
                CommanderSubtask.agent_code.is_not(None),
                CommanderSubtask.agent_source != SOURCE_COMMANDER,
            )
            .order_by(CommanderSubtask.created_at.desc())
            .limit(2000)
        )
    )
    groups: dict[str, list[CommanderSubtask]] = {}
    for subtask in subtasks:
        if not subtask.agent_code:
            continue
        scene_key = scene_code(f"{subtask.title} {subtask.instruction[:200]}")
        groups.setdefault(f"{subtask.agent_code}|{scene_key}", []).append(subtask)

    learned: list[CommanderSkillSnippet] = []
    for key, samples in groups.items():
        if len(samples) < min_samples:
            continue
        dist_runs = {sample.run_id for sample in samples}
        success = sum(1 for sample in samples if sample.status == SUB_OK)
        rate = round(success / len(samples), 4)
        if success < min_samples:
            continue
        agent_code, scene_key = key.split("|", 1)
        code = f"SK-{_sha(key, 12)}"
        exemplar = next((sample for sample in samples if sample.status == SUB_OK), samples[0])
        active = rate >= SKILL_MIN_SUCCESS_RATE and len(dist_runs) >= min_samples
        status = SKILL_ACTIVE if active else SKILL_CANDIDATE
        row = db.scalar(
            select(CommanderSkillSnippet).where(
                CommanderSkillSnippet.yard_id == yard_id,
                CommanderSkillSnippet.code == code,
            )
        )
        evidence = [
            {
                "run_id": sample.run_id,
                "seq": sample.seq,
                "title": (sample.title or "")[:120],
                "status": sample.status,
            }
            for sample in samples[:5]
        ]
        if row is None:
            row = CommanderSkillSnippet(
                yard_id=yard_id,
                code=code,
                name=f"{(exemplar.agent_name or agent_code)[:40]} · "
                f"{(exemplar.title or '同类任务')[:40]}",
                scene=(exemplar.title or exemplar.instruction[:200])[:500],
                content=_skill_content(exemplar),
                source_agent_code=agent_code[:100],
                sample_count=len(samples),
                success_count=success,
                success_rate=rate,
                status=status,
                source=SOURCE_AUTO,
                version=1,
                evidence_json=evidence,
            )
            db.add(row)
        elif row.status != SKILL_ARCHIVED:
            row.sample_count = len(samples)
            row.success_count = success
            row.success_rate = rate
            row.evidence_json = evidence
            row.content = _skill_content(exemplar)
            if row.source != SOURCE_MANUAL and row.status == SKILL_CANDIDATE and active:
                row.status = SKILL_ACTIVE
            db.add(row)
        learned.append(row)
    db.flush()
    return learned


def scan_yard(
    db: Session,
    *,
    yard_id: str,
    days: int = SCAN_DAYS,
    failure_threshold: int = 2,
    learn_workflows: bool = True,
    record_failures: bool = True,
    extract_skills: bool = True,
) -> dict[str, int]:
    """回填历史运行的工作流/黑名单，并重算技能；返回新增与总数。"""
    since = _now() - timedelta(days=max(1, days))
    runs = list(
        db.scalars(
            select(CommanderRun)
            .where(
                CommanderRun.yard_id == yard_id,
                CommanderRun.created_at >= since,
                CommanderRun.status.in_([RUN_COMPLETED, RUN_PARTIAL, RUN_FAILED]),
            )
            .order_by(CommanderRun.created_at.desc())
            .limit(SCAN_RUN_LIMIT)
        )
    )
    workflows_before = (
        db.query(CommanderWorkflow).filter(CommanderWorkflow.yard_id == yard_id).count()
    )
    for run in runs:
        subtasks = list(
            db.scalars(
                select(CommanderSubtask)
                .where(CommanderSubtask.run_id == run.id)
                .order_by(CommanderSubtask.seq)
            )
        )
        if learn_workflows or record_failures:
            learn_from_run(
                db,
                run=run,
                subtasks=subtasks,
                failure_threshold=failure_threshold,
                record_failures=record_failures,
                learn_workflows=learn_workflows,
            )
    workflows_after = (
        db.query(CommanderWorkflow).filter(CommanderWorkflow.yard_id == yard_id).count()
    )
    skills = extract_skills_for_yard(db, yard_id=yard_id, days=days) if extract_skills else []
    db.flush()
    return {
        "runs_scanned": len(runs),
        "workflows_created": max(0, workflows_after - workflows_before),
        "skills_seen": len(skills),
    }


def workflow_out(row: CommanderWorkflow) -> dict[str, Any]:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "scene": row.scene,
        "preconditions": row.preconditions,
        "steps": row.steps_json or [],
        "cost_baseline_tokens": int(row.cost_baseline_tokens or 0),
        "success_count": int(row.success_count or 0),
        "failure_count": int(row.failure_count or 0),
        "version": int(row.version or 1),
        "status": row.status,
        "source": row.source,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def failure_out(row: CommanderFailureCase) -> dict[str, Any]:
    return {
        "id": row.id,
        "signature": row.signature,
        "scene": row.scene,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "root_cause": row.root_cause,
        "avoidance": row.avoidance,
        "occurrences": int(row.occurrences or 0),
        "status": row.status,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def skill_out(row: CommanderSkillSnippet) -> dict[str, Any]:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "scene": row.scene,
        "content": row.content,
        "source_agent_code": row.source_agent_code,
        "sample_count": int(row.sample_count or 0),
        "success_count": int(row.success_count or 0),
        "success_rate": float(row.success_rate or 0),
        "status": row.status,
        "source": row.source,
        "version": int(row.version or 1),
        "evidence": row.evidence_json or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def asset_counts(db: Session, yard_id: str) -> dict[str, int]:
    return {
        "workflows": db.query(CommanderWorkflow)
        .filter(
            CommanderWorkflow.yard_id == yard_id,
            CommanderWorkflow.status == WORKFLOW_ACTIVE,
        )
        .count(),
        "failures": db.query(CommanderFailureCase)
        .filter(
            CommanderFailureCase.yard_id == yard_id,
            CommanderFailureCase.status == FAILURE_ACTIVE,
        )
        .count(),
        "skills": db.query(CommanderSkillSnippet)
        .filter(
            CommanderSkillSnippet.yard_id == yard_id,
            CommanderSkillSnippet.status == SKILL_ACTIVE,
        )
        .count(),
    }


def list_workflows(
    db: Session, yard_id: str, *, status: str | None = None, limit: int = 50
) -> list[CommanderWorkflow]:
    query = select(CommanderWorkflow).where(CommanderWorkflow.yard_id == yard_id)
    if status:
        query = query.where(CommanderWorkflow.status == status)
    return list(
        db.scalars(
            query.order_by(CommanderWorkflow.last_used_at.desc()).limit(max(1, min(limit, 100)))
        )
    )


def list_failures(
    db: Session, yard_id: str, *, status: str | None = None, limit: int = 50
) -> list[CommanderFailureCase]:
    query = select(CommanderFailureCase).where(CommanderFailureCase.yard_id == yard_id)
    if status:
        query = query.where(CommanderFailureCase.status == status)
    return list(
        db.scalars(
            query.order_by(
                CommanderFailureCase.occurrences.desc(),
                CommanderFailureCase.last_seen_at.desc(),
            ).limit(max(1, min(limit, 100)))
        )
    )


def list_skills(
    db: Session, yard_id: str, *, status: str | None = None, limit: int = 50
) -> list[CommanderSkillSnippet]:
    query = select(CommanderSkillSnippet).where(CommanderSkillSnippet.yard_id == yard_id)
    if status:
        query = query.where(CommanderSkillSnippet.status == status)
    return list(
        db.scalars(
            query.order_by(
                CommanderSkillSnippet.success_rate.desc(),
                CommanderSkillSnippet.sample_count.desc(),
            ).limit(max(1, min(limit, 100)))
        )
    )


def get_workflow(db: Session, yard_id: str, workflow_id: str) -> CommanderWorkflow | None:
    return db.scalar(
        select(CommanderWorkflow).where(
            CommanderWorkflow.id == workflow_id,
            CommanderWorkflow.yard_id == yard_id,
        )
    )


def get_failure(db: Session, yard_id: str, failure_id: str) -> CommanderFailureCase | None:
    return db.scalar(
        select(CommanderFailureCase).where(
            CommanderFailureCase.id == failure_id,
            CommanderFailureCase.yard_id == yard_id,
        )
    )


def get_skill(db: Session, yard_id: str, skill_id: str) -> CommanderSkillSnippet | None:
    return db.scalar(
        select(CommanderSkillSnippet).where(
            CommanderSkillSnippet.id == skill_id,
            CommanderSkillSnippet.yard_id == yard_id,
        )
    )


def create_workflow(
    db: Session,
    *,
    yard_id: str,
    name: str,
    scene: str,
    preconditions: str = "",
    steps: list[dict[str, Any]] | None = None,
    created_by: str | None = None,
) -> CommanderWorkflow:
    base_code = f"WF-M{_sha(f'{yard_id}|{scene}|{name}', 10)}"
    code = base_code
    index = 2
    existing = db.scalar(
        select(CommanderWorkflow).where(
            CommanderWorkflow.yard_id == yard_id,
            CommanderWorkflow.code == code,
        )
    )
    while existing is not None and existing.source != SOURCE_MANUAL:
        code = f"{base_code}-{index}"
        index += 1
        existing = db.scalar(
            select(CommanderWorkflow).where(
                CommanderWorkflow.yard_id == yard_id,
                CommanderWorkflow.code == code,
            )
        )
    if existing is not None:
        return update_workflow(
            existing,
            status=WORKFLOW_ACTIVE,
            name=name,
            scene=scene,
            preconditions=preconditions,
            steps=steps,
        )
    row = CommanderWorkflow(
        yard_id=yard_id,
        code=code,
        name=name[:200],
        scene=scene[:4000],
        preconditions=preconditions[:2000],
        steps_json=[step for step in (steps or []) if isinstance(step, dict)][:20],
        cost_baseline_tokens=0,
        success_count=0,
        failure_count=0,
        version=1,
        status=WORKFLOW_ACTIVE,
        source=SOURCE_MANUAL,
        source_run_ids_json=[],
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def update_workflow(
    row: CommanderWorkflow,
    *,
    status: str | None = None,
    name: str | None = None,
    scene: str | None = None,
    preconditions: str | None = None,
    steps: list[dict[str, Any]] | None = None,
) -> CommanderWorkflow:
    if status is not None:
        row.status = status
    if name is not None:
        row.name = name[:200]
    if scene is not None:
        row.scene = scene[:4000]
    if preconditions is not None:
        row.preconditions = preconditions[:2000]
    if steps is not None:
        row.steps_json = [step for step in steps if isinstance(step, dict)][:20]
        row.version = int(row.version or 1) + 1
    return row


def update_failure_status(row: CommanderFailureCase, *, status: str) -> CommanderFailureCase:
    row.status = status
    return row


def update_skill_status(row: CommanderSkillSnippet, *, status: str) -> CommanderSkillSnippet:
    row.status = status
    return row


__all__ = [
    "MATCH_THRESHOLD",
    "SKILL_MIN_SAMPLES",
    "SKILL_MIN_SUCCESS_RATE",
    "SCAN_DAYS",
    "asset_counts",
    "create_workflow",
    "extract_skills_for_yard",
    "failure_hint",
    "failure_out",
    "get_failure",
    "get_skill",
    "get_workflow",
    "keywords",
    "learn_from_run",
    "list_failures",
    "list_skills",
    "list_workflows",
    "mark_workflow_used",
    "match_workflow",
    "retrieve_failures",
    "scan_yard",
    "scene_code",
    "similarity",
    "skill_out",
    "update_failure_status",
    "update_skill_status",
    "update_workflow",
    "workflow_hint",
    "workflow_out",
]

# 供设置页/接口校验的合法状态集合（避免散落字面量）
VALID_LEARNING_STATUSES: dict[str, tuple[str, ...]] = {
    "workflow": (WORKFLOW_ACTIVE, WORKFLOW_DRAFT, WORKFLOW_ARCHIVED),
    "failure": (FAILURE_WATCHING, FAILURE_ACTIVE, FAILURE_RESOLVED),
    "skill": (SKILL_CANDIDATE, SKILL_ACTIVE, SKILL_ARCHIVED),
}

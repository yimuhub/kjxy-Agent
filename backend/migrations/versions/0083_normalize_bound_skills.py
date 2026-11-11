"""归一化 yard_agents.bound_skills 历史裸编码（v3.78）。

读取侧 ``_resolve_bound_skills_prompt`` 一直兼容无前缀的历史编码（按 skill_code /
plugin_id 匹配），但写入侧 ``validate_bound_skills`` 只认 ``skill:<id>`` /
``plugin:<id>``，导致带裸编码的智能体一编辑保存就 422「存在不可绑定的技能或插件」，
用户无法确认。这里把存量裸编码按本院落可绑定集合解析为规范前缀编码并去重，与
``bindable.normalize_bound_skills`` 的口径完全一致（本小院优先、同级取最新）。

只改 bound_skills 一列；解析不到的裸编码原样保留（运行期本就不生效，前端「已失效」
区供用户移除）。纯数据清洗，downgrade 为无操作（裸编码形态不可逆）。

Revision ID: 0083_normalize_bound_skills
Revises: 0082_setup_state
"""

import json
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0083_normalize_bound_skills"
down_revision = "0082_setup_state"
branch_labels = None
depends_on = None

SKILL_PREFIX = "skill:"
PLUGIN_PREFIX = "plugin:"


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _as_list(value) -> list:
    """JSON 列在 SQLite 返回字符串、在 PG 返回已解析对象；脏数据一律容错。"""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _created_key(value) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "timestamp"):
        try:
            return value.timestamp()
        except (ValueError, OSError):
            return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _legacy_map(skills: list, plugins: list, yard_id: str) -> dict:
    """裸编码 → 规范编码；技能按本小院优先、同级取最新（与 _skill_entries 一致）。"""
    mapping: dict = {}
    yard_rows = [r for r in skills if r["yard_id"] == yard_id]
    plat_rows = [r for r in skills if r["yard_id"] is None]
    yard_rows.sort(key=lambda r: _created_key(r["created_at"]), reverse=True)
    plat_rows.sort(key=lambda r: _created_key(r["created_at"]), reverse=True)
    for r in yard_rows + plat_rows:
        code = str(r["skill_code"] or "").strip()
        if code and code not in mapping:
            mapping[code] = f"{SKILL_PREFIX}{r['id']}"
    for r in plugins:
        if r["yard_id"] not in (yard_id, None):
            continue
        pid = str(r["plugin_id"] or "").strip()
        if pid and pid not in mapping:
            mapping[pid] = f"{PLUGIN_PREFIX}{pid}"
    return mapping


def _normalize(bound: list, mapping: dict) -> list:
    out: list = []
    seen: set = set()
    for raw in bound:
        code = str(raw).strip()
        if not code:
            continue
        canonical = (
            code if code.startswith((SKILL_PREFIX, PLUGIN_PREFIX)) else mapping.get(code, code)
        )
        if canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "yard_agents"):
        return

    skills = []
    if _has_table(bind, "evolution_skills"):
        skills = [
            {"id": r[0], "yard_id": r[1], "skill_code": r[2], "created_at": r[3]}
            for r in bind.execute(
                sa.text(
                    "SELECT id, yard_id, skill_code, created_at FROM evolution_skills "
                    "WHERE status = 'published'"
                )
            ).fetchall()
        ]
    plugins = []
    if _has_table(bind, "plugin_manifests"):
        plugins = [
            {"plugin_id": r[0], "yard_id": r[1]}
            for r in bind.execute(
                sa.text("SELECT plugin_id, yard_id FROM plugin_manifests WHERE status = 'enabled'")
            ).fetchall()
        ]

    # PG 的 JSON 列需显式 CAST 回原类型；SQLite 按文本写入即可
    col_type = ""
    if bind.dialect.name == "postgresql":
        col_type = (
            bind.execute(
                sa.text(
                    "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                    "WHERE a.attrelid = to_regclass('yard_agents') AND a.attname = 'bound_skills' "
                    "AND a.attnum > 0 AND NOT a.attisdropped"
                )
            ).scalar()
            or "json"
        )

    agents = bind.execute(sa.text("SELECT id, yard_id, bound_skills FROM yard_agents")).fetchall()
    map_cache: dict = {}
    for agent_id, yard_id, raw in agents:
        bound = _as_list(raw)
        if not bound:
            continue
        if not any(
            isinstance(c, str) and c.strip() and not c.startswith((SKILL_PREFIX, PLUGIN_PREFIX))
            for c in bound
        ):
            continue  # 全前缀且无需归一化：跳过，减少写放大
        if yard_id not in map_cache:
            map_cache[yard_id] = _legacy_map(skills, plugins, yard_id)
        normalized = _normalize(bound, map_cache[yard_id])
        if normalized == [str(c).strip() for c in bound if str(c).strip()]:
            continue
        payload = json.dumps(normalized, ensure_ascii=False)
        if bind.dialect.name == "postgresql":
            expr = f"CAST(:val AS {col_type})"
        else:
            expr = ":val"
        bind.execute(
            sa.text(f"UPDATE yard_agents SET bound_skills = {expr} WHERE id = :id").bindparams(
                val=payload, id=agent_id
            )
        )


def downgrade() -> None:
    # 纯数据清洗：裸编码 → 规范编码不可逆（去重丢失原始形态），无操作。
    pass

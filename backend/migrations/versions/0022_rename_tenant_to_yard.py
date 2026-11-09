"""全域改名：多空间术语由 ``tenant`` / 租户 统一改为 ``yard`` / 小院。

Revision ID: 0022_rename_tenant_to_yard
Revises: 0021_chat_attachments_and_cards
Create Date: 2026-10-26

产品定名"科技小院"，多空间概念对本项目就是一颗小院；旧术语会在界面、日志、文档
留下三套口径。本次是标识符级改名，范围四类，与 downgrade 一一对应：

1. 表名：6 张 ``tenant*`` 表 → ``yard*``。
2. 列名：40 张表的 ``tenant_id`` → ``yard_id``，另有
   ``evolution_skill_rollouts.tenant_allowlist`` → ``yard_allowlist``。
3. 存量值：落库的字符串枚举与 JSON 键仍带旧词（``yard_memberships.role``
   ='tenant_admin'、``knowledge_chunks.visibility``='tenant'、
   ``audit_logs.metadata`` 里的 ``"tenant_id"`` 键等）。不改这些值，新代码按新值读
   会拿到空——表现为静默降权而不是报错，所以必须与改名同批迁移。
4. 派生对象名：索引与约束（本项目 alembic 未配 naming_convention，名字全是手写
   常量，只能逐个重建或改名）。

方言差异（本文件的核心复杂度来源）：
- SQLite 没有 ``ALTER INDEX ... RENAME``，索引只能 drop 后按新名重建；它的 UNIQUE
  是表级约束、目录里没有独立名字，列改名后约束自动跟随，因此无需也不能改名。
- PostgreSQL 用 ``ALTER INDEX ... RENAME``；约束有独立名字需单独改名，且约束的支撑
  索引不能直接 drop，故只处理非约束索引。

已知遗留（仅限 SQLite）：SQLite 把命名 UNIQUE 约束写死在 ``CREATE TABLE`` 的 DDL
文本里（如 ``uq_tasks_tenant_idempotency``），``RENAME COLUMN`` 与 ``RENAME TABLE``
都不会重写它，要换名只能 batch 重建这些表（本库有 10+ 张，代价与风险都不值）。该名字
SQLite 不用于任何查找、代码也不按名引用，所以功能无影响；唯一可见后果是
``alembic check`` 会报"旧约束名 → 新约束名"的差异（该命令在本仓库早于本次改名就有
其它差异噪音，且未进 CI）。PostgreSQL 与全新建库（create_all）都是新名。

提示词/描述类长文本只替换精确短语 ``本租户`` → ``本小院``（实测库里全部命中都是这个
短语），既避免误伤"科技小院"这类既有词，也让 downgrade 真正可逆。
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_rename_tenant_to_yard"
down_revision = "0021_chat_attachments_and_cards"
branch_labels = None
depends_on = None

# 表名：旧 → 新
TABLES = {
    "tenants": "yards",
    "tenant_memberships": "yard_memberships",
    "tenant_invitations": "yard_invitations",
    "tenant_quotas": "yard_quotas",
    "tenant_agents": "yard_agents",
    "tenant_active_suppliers": "yard_active_suppliers",
}

# 列名：键为改名前的表名，值为 [(旧列, 新列)]
COLUMNS: dict[str, list[tuple[str, str]]] = {
    table: [("tenant_id", "yard_id")]
    for table in [
        "agent_run_steps",
        "agent_teams",
        "audit_logs",
        "bot_configs",
        "bot_conversations",
        "bot_message_logs",
        "chat_attachments",
        "chat_messages",
        "chat_sessions",
        "device_alert_rules",
        "device_alerts",
        "devices",
        "eval_runs",
        "evolution_feedback",
        "evolution_knowledge_candidates",
        "evolution_skills",
        "factory_runs",
        "knowledge_chunks",
        "knowledge_documents",
        "llm_calls",
        "llm_traces",
        "mcp_servers",
        "pair_codes",
        "pair_devices",
        "plugin_manifests",
        "policies",
        "policy_applications",
        "push_channels",
        "readings",
        "suppliers",
        "task_events",
        "tasks",
        "team_runs",
        "tool_approvals",
        "tenant_active_suppliers",
        "tenant_agents",
        "tenant_invitations",
        "tenant_memberships",
        "tenant_quotas",
    ]
}
COLUMNS["evolution_skill_rollouts"] = [
    ("tenant_id", "yard_id"),
    ("tenant_allowlist", "yard_allowlist"),
]

# 值迁移（精确匹配、可逆）：键为旧表名
VALUES: dict[str, list[tuple[str, str, str]]] = {
    # username 单独处理：它与唯一约束有关，见 _rename_legacy_demo_user
    "users": [("display_name", "租户管理员", "小院管理员")],
    "tenant_memberships": [("role", "tenant_admin", "yard_admin")],
    "tenant_invitations": [("role", "tenant_admin", "yard_admin")],
    "knowledge_chunks": [
        ("visibility", "tenant", "yard"),
        ("trust_level", "tenant_material", "yard_material"),
    ],
    "audit_logs": [
        ("action", "tenant_signup", "yard_signup"),
        ("resource_type", "tenant", "yard"),
        ("resource_type", "tenant_agent", "yard_agent"),
        ("resource_type", "tenant_invitation", "yard_invitation"),
        ("resource_type", "tenant_member", "yard_member"),
        ("resource_type", "tenant_quota", "yard_quota"),
        ("resource_id", "tenant_admin", "yard_admin"),
    ],
    "evolution_skill_rollouts": [("strategy", "tenant_allowlist", "yard_allowlist")],
}

# JSON / 长文本迁移（成对替换、可逆）：键为旧表名
PAYLOADS: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {
    "tenant_agents": [
        ("knowledge_scopes", [('"tenant"', '"yard"')]),
        ("persona_prompt", [("本租户", "本小院")]),
        ("task_prompt_template", [("本租户", "本小院")]),
    ],
    "factory_runs": [("blueprint_json", [('"tenant"', '"yard"')])],
    "audit_logs": [
        (
            "metadata",
            [('"tenant_id"', '"yard_id"'), ('"tenant_admin"', '"yard_admin"')],
        )
    ],
    "tasks": [("result_json", [('"tenant_id"', '"yard_id"')])],
    "tool_definitions": [("description", [("本租户", "本小院")])],
    "llm_traces": [("prompt_digest", [("本租户", "本小院")])],
}


def _dialect() -> str:
    return op.get_bind().dialect.name


def _physical(old_table: str) -> str:
    """本次改名期间这些表的物理名。

    上/下升降级里，值/文本/索引/列改名都发生在表名还原之前，故两个方向都取新名。
    """
    return TABLES.get(old_table, old_table)


def _swap_columns(*, to_new: bool) -> None:
    for old_table, pairs in COLUMNS.items():
        table = _physical(old_table)
        for old_col, new_col in pairs:
            src, dst = (old_col, new_col) if to_new else (new_col, old_col)
            op.alter_column(table, src, new_column_name=dst)


def _apply_values(*, to_new: bool) -> None:
    for old_table, items in VALUES.items():
        table = _physical(old_table)
        for column, old_value, new_value in items:
            src, dst = (old_value, new_value) if to_new else (new_value, old_value)
            op.execute(
                sa.text(
                    f'UPDATE "{table}" SET "{column}" = :dst WHERE "{column}" = :src'
                ).bindparams(dst=dst, src=src)
            )


def _pg_column_type(table: str, column: str) -> str:
    """取 PG 目录里的列类型：JSON 列不能直接 LIKE/replace，需转 text 再转回。"""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return ""
    return (
        bind.execute(
            sa.text(
                "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                "WHERE a.attrelid = to_regclass(:table) AND a.attname = :column "
                "AND a.attnum > 0 AND NOT a.attisdropped"
            ).bindparams(table=table, column=column)
        ).scalar()
        or ""
    )


def _apply_payloads(*, to_new: bool) -> None:
    for old_table, items in PAYLOADS.items():
        table = _physical(old_table)
        for column, pairs in items:
            for old_piece, new_piece in pairs:
                src, dst = (old_piece, new_piece) if to_new else (new_piece, old_piece)
                if _dialect() == "postgresql":
                    col_type = _pg_column_type(table, column)
                    text_col = f'CAST("{column}" AS text)'
                    if col_type:
                        expr = f"CAST(replace({text_col}, :src, :dst) AS {col_type})"
                        where = f"{text_col} LIKE :like"
                    else:  # 目录查不到列类型时按 text 处理，避免整条 SQL 失效
                        expr = f"replace({text_col}, :src, :dst)"
                        where = f"{text_col} LIKE :like"
                else:
                    expr = f'replace("{column}", :src, :dst)'
                    where = f'"{column}" LIKE :like'
                op.execute(
                    sa.text(
                        f'UPDATE "{table}" SET "{column}" = {expr} WHERE {where}'
                    ).bindparams(src=src, dst=dst, like=f"%{src}%")
                )


def _indexes_like(fragment: str) -> list[tuple[str, str, list[str], bool]]:
    """列出名字含指定片段的普通索引：(索引名, 表名, 列名, 是否唯一)。

    约束支撑索引交给 PG 的约束改名逻辑，SQLite 的 sqlite_autoindex_* 不可操作。
    """
    bind = op.get_bind()
    out: list[tuple[str, str, list[str], bool]] = []
    if bind.dialect.name == "postgresql":
        rows = bind.execute(
            sa.text(
                "SELECT i.indexname, i.tablename FROM pg_indexes i "
                "WHERE i.indexname LIKE :frag "
                "AND i.indexname NOT IN (SELECT conname FROM pg_constraint) "
                "ORDER BY i.indexname"
            ).bindparams(frag=f"%{fragment}%")
        ).fetchall()
        for name, table in rows:
            meta = bind.execute(
                sa.text(
                    "SELECT x.indisunique, "
                    "array(SELECT a.attname FROM pg_attribute a "
                    "WHERE a.attrelid = x.indexrelid AND a.attnum = ANY(x.indkey)) "
                    "FROM pg_index x JOIN pg_class c ON c.oid = x.indexrelid "
                    "WHERE c.relname = :n"
                ).bindparams(n=name)
            ).fetchone()
            out.append((name, table, list(meta[1]) if meta and meta[1] else [], bool(meta[0])))
    else:
        rows = bind.execute(
            sa.text(
                "SELECT name, tbl_name FROM sqlite_master "
                "WHERE type='index' AND name LIKE :frag "
                "AND name NOT LIKE 'sqlite_autoindex%' ORDER BY name"
            ).bindparams(frag=f"%{fragment}%")
        ).fetchall()
        for name, table in rows:
            cols = [r[2] for r in bind.execute(sa.text(f'PRAGMA index_info("{name}")')).fetchall()]
            uniq = any(
                r[1] == name and r[2]
                for r in bind.execute(sa.text(f'PRAGMA index_list("{table}")')).fetchall()
            )
            out.append((name, table, cols, uniq))
    return out


def _rename_indexes(*, to_new: bool) -> None:
    """SQLite 重建索引换名；PostgreSQL 用 ALTER INDEX RENAME。

    必须在列改名之后执行：此时索引引用的列已是新名，换名不必再改列。
    """
    frag = "tenant" if to_new else "yard"
    for name, table, cols, uniq in _indexes_like(frag):
        # name 是当前物理名（含 frag），dst 是换词后的目标名
        dst = name.replace("tenant", "yard") if to_new else name.replace("yard", "tenant")
        if _dialect() == "postgresql":
            op.execute(sa.text(f'ALTER INDEX "{name}" RENAME TO "{dst}"'))
            continue
        live_cols = [
            r[2] for r in op.get_bind().execute(sa.text(f'PRAGMA index_info("{name}")')).fetchall()
        ] or [c.replace("tenant", "yard") if to_new else c.replace("yard", "tenant") for c in cols]
        op.execute(sa.text(f'DROP INDEX IF EXISTS "{name}"'))
        quoted = ", ".join(f'"{c}"' for c in live_cols)
        op.execute(
            sa.text(f'CREATE {"UNIQUE " if uniq else ""}INDEX "{dst}" ON "{table}" ({quoted})')
        )


def _rename_constraints(*, to_new: bool) -> None:
    """仅 PostgreSQL 需要：唯一/外键约束名改名（SQLite 目录里没有约束名）。"""
    if _dialect() != "postgresql":
        return
    frag = "tenant" if to_new else "yard"
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT con.conname, cls.relname FROM pg_constraint con "
                "JOIN pg_class cls ON cls.oid = con.conrelid "
                "WHERE con.conname LIKE :frag ORDER BY con.conname"
            ).bindparams(frag=f"%{frag}%")
        )
        .fetchall()
    )
    for name, table in rows:
        other = name.replace("tenant", "yard") if to_new else name.replace("yard", "tenant")
        op.execute(sa.text(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{name}" TO "{other}"'))


def _rename_legacy_demo_user(*, to_new: bool) -> None:
    """演示账号用户名改名，且只在目标名未被占用时执行。

    ``users.username`` 有唯一约束：若某套安装里既种过 ``tenant_admin``、又自建过
    ``yard_admin``，无条件 UPDATE 会直接撞约束、整个迁移失败。此时保留旧名更安全，
    代价只是那套安装里这一个账号的名字没跟上术语。
    """
    src, dst = ("tenant_admin", "yard_admin") if to_new else ("yard_admin", "tenant_admin")
    bind = op.get_bind()
    taken = bind.execute(
        sa.text('SELECT COUNT(*) FROM "users" WHERE "username" = :dst').bindparams(dst=dst)
    ).scalar()
    if taken:
        return
    bind.execute(
        sa.text('UPDATE "users" SET "username" = :dst WHERE "username" = :src').bindparams(
            dst=dst, src=src
        )
    )


def upgrade() -> None:
    for old, new in TABLES.items():
        op.rename_table(old, new)
    _swap_columns(to_new=True)
    _apply_values(to_new=True)
    _rename_legacy_demo_user(to_new=True)
    _apply_payloads(to_new=True)
    _rename_indexes(to_new=True)
    _rename_constraints(to_new=True)


def downgrade() -> None:
    _rename_constraints(to_new=False)
    _rename_indexes(to_new=False)
    _apply_payloads(to_new=False)
    _rename_legacy_demo_user(to_new=False)
    _apply_values(to_new=False)
    _swap_columns(to_new=False)
    for old, new in reversed(list(TABLES.items())):
        op.rename_table(new, old)

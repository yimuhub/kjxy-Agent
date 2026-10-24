"""Skill 灰度发布表：evolution_skill_rollouts（第四期 4.1）；
插件注册表：plugin_manifests（第四期 4.2，插件机制）；
评测门禁运行记录：eval_runs（第四期 4.3 评测门禁）。

Revision ID: 0004_skill_rollout
Revises: 0003_policy_tenant_and_applications
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_skill_rollout"
down_revision = "0003_policy_tenant_and_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evolution_skill_rollouts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("skill_id", sa.String(36), nullable=False),
        sa.Column("skill_code", sa.String(100), nullable=False),
        sa.Column("stable_version", sa.String(50), nullable=False),
        sa.Column("rollout_version", sa.String(50), nullable=False),
        # tenant_allowlist | percentage
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("percentage", sa.Integer, nullable=True),
        sa.Column("tenant_allowlist", sa.JSON, nullable=True),
        # active | paused | completed
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["evolution_skills.id"], name="fk_skill_rollouts_skill_id"
        ),
    )
    op.create_index("ix_skill_rollouts_tenant_id", "evolution_skill_rollouts", ["tenant_id"])
    op.create_index("ix_skill_rollouts_skill_code", "evolution_skill_rollouts", ["skill_code"])
    op.create_index("ix_skill_rollouts_status", "evolution_skill_rollouts", ["status"])

    # ---- 第四期 4.2 插件机制：plugin_manifests 注册表 ----
    # tenant_id 为空 = 全局（platform）插件；status: enabled|disabled（停用只改状态）
    op.create_table(
        "plugin_manifests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("plugin_id", sa.String(100), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=True),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="disabled"),
        sa.Column("manifest", sa.JSON, nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False),
        sa.Column("checksum_ok", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("plugin_id", "tenant_id", name="uq_plugin_manifests_pid_tenant"),
    )
    op.create_index("ix_plugin_manifests_plugin_id", "plugin_manifests", ["plugin_id"])
    op.create_index("ix_plugin_manifests_tenant_id", "plugin_manifests", ["tenant_id"])
    op.create_index("ix_plugin_manifests_type", "plugin_manifests", ["type"])
    op.create_index("ix_plugin_manifests_status", "plugin_manifests", ["status"])

    # ---- 第四期 4.3 评测门禁：eval_runs 运行记录 ----
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("skill_code", sa.String(100), nullable=False),
        sa.Column("skill_version", sa.String(50), nullable=False),
        sa.Column("test_set_id", sa.String(100), nullable=False),
        sa.Column("pass_rate", sa.Float, nullable=False),
        sa.Column("passed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(36), nullable=True),
    )
    op.create_index("ix_eval_runs_tenant_id", "eval_runs", ["tenant_id"])
    op.create_index("ix_eval_runs_skill_code", "eval_runs", ["skill_code"])
    op.create_index(
        "ix_eval_runs_lookup", "eval_runs", ["tenant_id", "skill_code", "skill_version"]
    )


def downgrade() -> None:
    op.drop_index("ix_eval_runs_lookup", table_name="eval_runs")
    op.drop_index("ix_eval_runs_skill_code", table_name="eval_runs")
    op.drop_index("ix_eval_runs_tenant_id", table_name="eval_runs")
    op.drop_table("eval_runs")
    op.drop_index("ix_plugin_manifests_status", table_name="plugin_manifests")
    op.drop_index("ix_plugin_manifests_type", table_name="plugin_manifests")
    op.drop_index("ix_plugin_manifests_tenant_id", table_name="plugin_manifests")
    op.drop_index("ix_plugin_manifests_plugin_id", table_name="plugin_manifests")
    op.drop_table("plugin_manifests")
    op.drop_index("ix_skill_rollouts_status", table_name="evolution_skill_rollouts")
    op.drop_index("ix_skill_rollouts_skill_code", table_name="evolution_skill_rollouts")
    op.drop_index("ix_skill_rollouts_tenant_id", table_name="evolution_skill_rollouts")
    op.drop_table("evolution_skill_rollouts")

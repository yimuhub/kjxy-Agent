"""v3.1.0 编辑弹窗「扩展技能 / 扩展插件」绑定聚合与写入校验集成测试。

覆盖：
1. 管理员/普通成员都能读到内置已发布技能与启用插件（含字段契约与截断/风险口径）；
2. 市场安装的插件（有/无 PluginManifest 行两种情况）与技能都进入候选；
3. 合法 code 保存成功且 bound_count 随绑定增加（软删除不计入）；
4. 非法 code 写入 422 且消息携带该 code；历史脏数据可读、可编辑；
5. 跨小院技能互不可见、不可绑定（platform 级两边都可见）。
"""

from __future__ import annotations

import pytest
from app.core.security import create_access_token
from app.modules.agents.models import YardAgent
from app.modules.evolution.models import Skill
from app.modules.market.models import MarketItem
from app.modules.plugins.models import PluginManifest
from app.modules.yards.repository import create_yard

ADMIN = "u-bind-admin"
MEMBER = "u-bind-member"
OTHER = "u-bind-other"

_BASE = {
    "agent_code": "bind_helper",
    "name": "绑定测试助手",
    "persona_prompt": "你是测试助手。",
    "task_prompt_template": "请回答：{{question}}",
    "allowed_tools": ["knowledge.search"],
    "knowledge_scopes": ["yard"],
}


def _hdr(user_id: str, yard_id: str, roles: tuple[str, ...] = ("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    y = create_yard(db_session, name="绑定聚合院")
    db_session.commit()
    return y


def _mk_skill(
    db,
    yard_id: str | None,
    code: str,
    *,
    status: str = "published",
    created_from: str | None = "market_builtin",
    definition: dict | None = None,
    version: str = "1.0.0",
) -> Skill:
    row = Skill(
        yard_id=yard_id,
        skill_code=code,
        version=version,
        definition_json=definition or {"name": code, "description": f"{code} 说明"},
        status=status,
        created_from=created_from,
    )
    db.add(row)
    return row


def _mk_plugin(
    db,
    plugin_id: str,
    *,
    status: str = "enabled",
    yard_id: str | None = None,
    manifest: dict | None = None,
) -> PluginManifest:
    row = PluginManifest(
        plugin_id=plugin_id,
        yard_id=yard_id,
        version="1.0.0",
        type="workflow",
        status=status,
        manifest_json=manifest
        or {"plugin_id": plugin_id, "name": plugin_id, "description": f"{plugin_id} 插件"},
        checksum="",
        checksum_ok=True,
    )
    db.add(row)
    return row


def _mk_market_item(
    db,
    *,
    item_type: str,
    code: str,
    name: str,
    status: str = "installed",
    source: str = "local",
    definition: dict | None = None,
    manifest: dict | None = None,
    item_key: str | None = None,
    description: str = "",
) -> MarketItem:
    row = MarketItem(
        item_key=item_key or f"{source}:{item_type}:{code}",
        item_type=item_type,
        source=source,
        code=code,
        name=name,
        version="1.0.0",
        description=description,
        risk_level="medium",
        status=status,
        definition_json=definition,
        manifest_json=manifest,
    )
    db.add(row)
    return row


def _get_extensions(client, yard, *, user: str = ADMIN, roles: tuple[str, ...] = ("yard_admin",)):
    resp = client.get("/api/v1/agents/bindable-extensions", headers=_hdr(user, yard.id, roles))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _create_agent(client, yard, *, code: str, name: str, bound_skills=None, user=ADMIN):
    body = {**_BASE, "agent_code": code, "name": name}
    if bound_skills is not None:
        body["bound_skills"] = bound_skills
    resp = client.post("/api/v1/agents/declarative", json=body, headers=_hdr(user, yard.id))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------- 1) 可读与字段契约


def test_admin_and_member_see_published_skills_and_enabled_plugins(client, db_session, yard):
    skill = _mk_skill(
        db_session,
        yard.id,
        "agri_pest",
        definition={
            "name": "病虫害防治",
            "summary": "一句话说明" * 80,  # >200 字 → 截断
            "tags": ["病虫害", "防治"],
            "guardrails": {"risk_level": "medium"},
            "market_item_key": "builtin:skill:agri_pest",
        },
    )
    platform_skill = _mk_skill(db_session, None, "agri_platform", created_from="manual")
    draft_skill = _mk_skill(db_session, yard.id, "agri_draft", status="draft")
    _mk_plugin(
        db_session,
        "irrigation",
        manifest={
            "plugin_id": "irrigation",
            "name": "灌溉调度",
            "description": "按墒情调度灌溉",
            "risk_level": "high",
        },
    )
    _mk_plugin(db_session, "stopped_plugin", status="disabled")
    db_session.commit()

    data = _get_extensions(client, yard)
    assert set(data.keys()) == {"skills", "plugins"}

    skills = {s["code"]: s for s in data["skills"]}
    mine = skills[f"skill:{skill.id}"]
    assert set(mine.keys()) == {
        "code",
        "name",
        "summary",
        "tags",
        "source",
        "visibility",
        "risk_level",
        "bound_count",
    }
    assert mine["name"] == "病虫害防治"
    assert mine["tags"] == ["病虫害", "防治"]  # v3.29：标签参与一句话建 Agent 召回
    assert mine["summary"] == "一句话说明" * 40  # 400 字截断为 200 字
    assert mine["source"] == "builtin"
    assert mine["visibility"] == "yard"
    assert mine["risk_level"] == "medium"  # guardrails.risk_level 优先
    assert mine["bound_count"] == 0
    assert skills[f"skill:{platform_skill.id}"]["visibility"] == "platform"
    assert skills[f"skill:{platform_skill.id}"]["source"] == "imported"
    assert f"skill:{draft_skill.id}" not in skills  # 非发布态不可绑定

    plugins = {p["code"]: p for p in data["plugins"]}
    plug = plugins["plugin:irrigation"]
    assert set(plug.keys()) == {
        "code",
        "name",
        "summary",
        "tags",
        "source",
        "status",
        "risk_level",
        "bound_count",
    }
    assert plug["name"] == "灌溉调度"
    assert plug["summary"] == "按墒情调度灌溉"
    assert plug["status"] == "enabled"
    assert plug["risk_level"] == "high"
    assert plug["source"] == "local"
    assert "plugin:stopped_plugin" not in plugins  # 停用插件不可绑定

    # 任何小院成员可读（不新增权限点），候选集合一致
    member_data = _get_extensions(client, yard, user=MEMBER, roles=("viewer",))
    assert {s["code"] for s in member_data["skills"]} == {s["code"] for s in data["skills"]}
    assert {p["code"] for p in member_data["plugins"]} == {p["code"] for p in data["plugins"]}


# ---------------------------------------------------------------- 2) 市场安装项聚合


def test_market_installed_plugins_and_skills_are_included(client, db_session, yard):
    # 有 PluginManifest 行（enabled）：只出现一次；无溯源标记 → local
    _mk_plugin(
        db_session,
        "weather_pack",
        manifest={"plugin_id": "weather_pack", "name": "气象插件", "description": "气象数据包"},
    )
    # 自动挖掘插件（manifest._auto 标记）→ 按契约记 market
    _mk_plugin(
        db_session,
        "auto_plugin",
        manifest={
            "plugin_id": "auto_plugin",
            "name": "自动挖掘插件",
            "_auto": {"pattern": "x"},
        },
    )
    # 无 PluginManifest 行：MarketItem installed → 补充进 plugins（source=market）
    _mk_market_item(
        db_session,
        item_type="plugin",
        code="orrigation-pack",
        name="孤儿插件",
        manifest={"plugin_id": "orrigation-pack", "description": "装了但注册表没行"},
    )
    # 技能：同码 published Skill 行存在 → 市场条目去重，不重复出现
    skill = _mk_skill(
        db_session,
        yard.id,
        "agri_soil_health",
        definition={
            "name": "土壤健康管理",
            "description": "d",
            "market_item_key": "builtin:skill:agri_soil_health",
        },
    )
    _mk_market_item(
        db_session,
        item_type="skill",
        code="agri_soil_health",
        name="土壤健康管理",
        definition={"description": "市场侧描述"},
        item_key="builtin:skill:agri_soil_health",
    )
    # 技能：installed 但无 published Skill 行（GitHub 安装后 pending_review）→ 补充
    extra = _mk_market_item(
        db_session,
        item_type="skill",
        code="github-farm-skill",
        name="GitHub 农技",
        source="github",
        definition={"summary": "来自 GitHub 的技能"},
    )
    # 未安装的市场条目不出现
    _mk_market_item(
        db_session, item_type="skill", code="not-installed", name="未安装技能", status="available"
    )
    db_session.commit()

    data = _get_extensions(client, yard)

    plugins = {p["code"]: p for p in data["plugins"]}
    assert sum(1 for p in data["plugins"] if p["code"] == "plugin:weather_pack") == 1
    assert plugins["plugin:weather_pack"]["source"] == "local"
    assert plugins["plugin:auto_plugin"]["source"] == "market"
    orphan = plugins["plugin:orrigation-pack"]
    assert orphan["source"] == "market"
    assert orphan["status"] == "enabled"
    assert orphan["name"] == "孤儿插件"

    skills = {s["code"]: s for s in data["skills"]}
    assert sum(1 for s in data["skills"] if s["name"] == "土壤健康管理") == 1
    assert skills[f"skill:{skill.id}"]["source"] == "builtin"
    extra_entry = skills[f"skill:{extra.id}"]
    assert extra_entry["source"] == "market"
    assert extra_entry["summary"] == "来自 GitHub 的技能"
    assert extra_entry["visibility"] == "platform"
    assert all(s["name"] != "未安装技能" for s in data["skills"])


def test_pending_items_not_selectable(client, db_session, yard):
    """可选集必须等于可用集：市场已安装但已有待审/停用行的项不进候选。"""
    # 技能：installed，但同码已有 pending_review Skill 行 → 不补充
    _mk_skill(
        db_session,
        yard.id,
        "github-review-skill",
        status="pending_review",
        created_from="market_github",
    )
    _mk_market_item(
        db_session,
        item_type="skill",
        code="github-review-skill",
        name="待审 GitHub 技能",
        source="github",
        definition={"summary": "待审"},
    )
    # 插件：installed，但已有 pending_review Manifest 行 → 不补充
    _mk_plugin(
        db_session,
        "review-pack",
        status="pending_review",
        manifest={"plugin_id": "review-pack", "name": "待审插件"},
    )
    _mk_market_item(
        db_session,
        item_type="plugin",
        code="review-pack",
        name="待审插件",
        manifest={"plugin_id": "review-pack"},
    )
    # 对照组：完全没落行的孤项仍然补充
    _mk_market_item(
        db_session,
        item_type="plugin",
        code="orphan-pack",
        name="孤儿插件",
        manifest={"plugin_id": "orphan-pack"},
    )
    db_session.commit()

    data = _get_extensions(client, yard)
    skill_names = {s["name"] for s in data["skills"]}
    plugin_codes = {p["code"] for p in data["plugins"]}
    assert "待审 GitHub 技能" not in skill_names
    assert "plugin:review-pack" not in plugin_codes
    assert "plugin:orphan-pack" in plugin_codes


# ---------------------------------------------------------------- 3) 合法保存与 bound_count


def test_bind_valid_codes_updates_bound_count(client, db_session, yard):
    skill = _mk_skill(db_session, yard.id, "agri_field_log")
    _mk_plugin(db_session, "irrigation")
    db_session.commit()
    skill_code = f"skill:{skill.id}"
    plugin_code = "plugin:irrigation"

    agent1 = _create_agent(client, yard, code="bind_a", name="助手一", bound_skills=[skill_code])
    assert agent1["bound_skills"] == [skill_code]

    def _counts():
        data = _get_extensions(client, yard)
        skills = {s["code"]: s for s in data["skills"]}
        plugins = {p["code"]: p for p in data["plugins"]}
        return skills[skill_code]["bound_count"], plugins[plugin_code]["bound_count"]

    assert _counts() == (1, 0)

    _create_agent(
        client, yard, code="bind_b", name="助手二", bound_skills=[skill_code, plugin_code]
    )
    assert _counts() == (2, 1)

    # PATCH 换绑：随写入即时重算
    patched = client.patch(
        f"/api/v1/agents/declarative/{agent1['id']}",
        json={"bound_skills": [plugin_code]},
        headers=_hdr(ADMIN, yard.id),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["bound_skills"] == [plugin_code]
    assert _counts() == (1, 2)

    # 软删除的助手不计入 bound_count
    deleted = client.delete(
        f"/api/v1/agents/declarative/{agent1['id']}", headers=_hdr(ADMIN, yard.id)
    )
    assert deleted.status_code == 200, deleted.text
    assert _counts() == (1, 1)


# ---------------------------------------------------------------- 4) 非法 code 与历史脏数据


def test_invalid_code_rejected_with_422(client, db_session, yard):
    _mk_skill(db_session, yard.id, "agri_ok")
    db_session.commit()

    resp = client.post(
        "/api/v1/agents/declarative",
        json={
            **_BASE,
            "agent_code": "bind_bad",
            "name": "坏绑定",
            "bound_skills": ["skill:ghost-id"],
        },
        headers=_hdr(ADMIN, yard.id),
    )
    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "skill:ghost-id" in error["message"]

    agent = _create_agent(client, yard, code="bind_good", name="好绑定")
    patched = client.patch(
        f"/api/v1/agents/declarative/{agent['id']}",
        json={"bound_skills": ["plugin:nope"]},
        headers=_hdr(ADMIN, yard.id),
    )
    assert patched.status_code == 422, patched.text
    assert "plugin:nope" in patched.json()["error"]["message"]

    # bound_skills 必须是字符串列表（非列表 → 既有 400 INVALID_REQUEST）
    not_list = client.post(
        "/api/v1/agents/declarative",
        json={**_BASE, "agent_code": "bind_type", "name": "类型错误", "bound_skills": "oops"},
        headers=_hdr(ADMIN, yard.id),
    )
    assert not_list.status_code == 400
    assert not_list.json()["error"]["code"] == "INVALID_REQUEST"

    # 空列表合法（清空绑定）
    emptied = client.patch(
        f"/api/v1/agents/declarative/{agent['id']}",
        json={"bound_skills": []},
        headers=_hdr(ADMIN, yard.id),
    )
    assert emptied.status_code == 200, emptied.text
    assert emptied.json()["data"]["bound_skills"] == []


def test_history_dirty_data_readable_and_editable(client, db_session, yard):
    # 历史脏数据：bound_skills 不是列表
    agent = YardAgent(
        yard_id=yard.id,
        agent_code="dirty_one",
        name="脏数据助手",
        persona_prompt="你是测试助手。",
        task_prompt_template="请回答：{{question}}",
        allowed_tools=["knowledge.search"],
        knowledge_scopes=["yard"],
        risk_level="low",
        status="draft",
        visibility="public",
        created_by=ADMIN,
        bound_skills="not-a-list",
    )
    db_session.add(agent)
    db_session.commit()

    # 聚合接口不因脏数据崩溃
    assert _get_extensions(client, yard)["skills"] is not None

    # 不携带 bound_skills 的编辑不受影响（只在写入提交的绑定清单时校验）
    patched = client.patch(
        f"/api/v1/agents/declarative/{agent.id}",
        json={"description": "只改描述"},
        headers=_hdr(ADMIN, yard.id),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["description"] == "只改描述"

    # 携带合法绑定清单可以覆盖脏数据
    skill = _mk_skill(db_session, yard.id, "agri_fix")
    db_session.commit()
    fixed = client.patch(
        f"/api/v1/agents/declarative/{agent.id}",
        json={"bound_skills": [f"skill:{skill.id}"]},
        headers=_hdr(ADMIN, yard.id),
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["data"]["bound_skills"] == [f"skill:{skill.id}"]


def test_legacy_bare_codes_normalized_on_write(client, db_session, yard):
    """死路回归：历史裸编码（无 skill:/plugin: 前缀）编辑保存必须归一化成功，不再 422。

    读取侧 ``_resolve_bound_skills_prompt`` 一直兼容裸编码，但旧写入侧只认前缀编码，
    带裸编码的智能体一编辑保存就 422「存在不可绑定的技能或插件」，用户无法确认。
    修复后写入侧 ``normalize_bound_skills`` 把裸编码解析为规范前缀编码并去重。
    """
    skill = _mk_skill(db_session, yard.id, "office_legacy")
    _mk_plugin(db_session, "legacy_plugin")
    db_session.commit()
    canonical_skill = f"skill:{skill.id}"

    # 历史脏数据：直接落库裸编码（模拟旧版本写入的运行期兼容形态）
    agent = YardAgent(
        yard_id=yard.id,
        agent_code="legacy_bare",
        name="裸编码助手",
        persona_prompt="你是测试助手。",
        task_prompt_template="请回答：{{question}}",
        allowed_tools=["knowledge.search"],
        knowledge_scopes=["yard"],
        risk_level="low",
        status="draft",
        visibility="public",
        created_by=ADMIN,
        bound_skills=["office_legacy", "legacy_plugin"],
    )
    db_session.add(agent)
    db_session.commit()

    # 用户打开编辑直接保存（前端原样回传裸编码）→ 归一化成功，不再死路
    patched = client.patch(
        f"/api/v1/agents/declarative/{agent.id}",
        json={"bound_skills": ["office_legacy", "legacy_plugin"]},
        headers=_hdr(ADMIN, yard.id),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["bound_skills"] == [canonical_skill, "plugin:legacy_plugin"]

    # 裸编码与既有前缀编码重复 → 去重保序
    dedup = client.patch(
        f"/api/v1/agents/declarative/{agent.id}",
        json={"bound_skills": ["office_legacy", canonical_skill, canonical_skill]},
        headers=_hdr(ADMIN, yard.id),
    )
    assert dedup.status_code == 200, dedup.text
    assert dedup.json()["data"]["bound_skills"] == [canonical_skill]

    # 解析不到的裸编码 → 422 且 details.unknown_codes 携带全量（前端「已失效」逃生口定位）
    bad = client.patch(
        f"/api/v1/agents/declarative/{agent.id}",
        json={"bound_skills": ["office_legacy", "ghost_bare_code"]},
        headers=_hdr(ADMIN, yard.id),
    )
    assert bad.status_code == 422, bad.text
    err = bad.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert "ghost_bare_code" in err["details"]["unknown_codes"]

    # 创建路径同样归一化（POST 与 PATCH 口径一致）
    created = client.post(
        "/api/v1/agents/declarative",
        json={
            **_BASE,
            "agent_code": "legacy_create",
            "name": "裸编码新建",
            "bound_skills": ["office_legacy"],
        },
        headers=_hdr(ADMIN, yard.id),
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["bound_skills"] == [canonical_skill]


# ---------------------------------------------------------------- 5) 跨小院隔离


def test_cross_yard_skills_invisible_and_unbindable(client, db_session, yard):
    other_yard = create_yard(db_session, name="隔壁绑定院")
    yard_skill = _mk_skill(db_session, yard.id, "agri_private_yard")
    platform_skill = _mk_skill(db_session, None, "agri_shared_platform")
    db_session.commit()

    mine = _get_extensions(client, yard)
    other = _get_extensions(client, other_yard, user=OTHER)
    my_codes = {s["code"] for s in mine["skills"]}
    other_codes = {s["code"] for s in other["skills"]}
    assert f"skill:{yard_skill.id}" in my_codes
    assert f"skill:{yard_skill.id}" not in other_codes  # 本院技能隔壁不可见
    assert f"skill:{platform_skill.id}" in other_codes  # 平台级两边都可见

    # 隔壁院管理员不能把本院技能绑进自己的助手
    denied = client.post(
        "/api/v1/agents/declarative",
        json={
            **_BASE,
            "agent_code": "cross_bind",
            "name": "越院绑定",
            "bound_skills": [f"skill:{yard_skill.id}"],
        },
        headers=_hdr(OTHER, other_yard.id),
    )
    assert denied.status_code == 422, denied.text
    assert f"skill:{yard_skill.id}" in denied.json()["error"]["message"]

    # PATCH 跨小院仍然 404：先鉴权后校验，不向隔壁泄露校验细节
    agent = _create_agent(client, yard, code="cross_own", name="本院助手")
    cross = client.patch(
        f"/api/v1/agents/declarative/{agent['id']}",
        json={"bound_skills": [f"skill:{yard_skill.id}"]},
        headers=_hdr(OTHER, other_yard.id),
    )
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "AGENT_NOT_FOUND"

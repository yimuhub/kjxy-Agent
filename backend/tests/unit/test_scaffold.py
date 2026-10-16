"""Agent 脚手架自身单元测试：不碰真实仓库文件，全部走 tmp_path。"""

import ast
import shutil

import pytest
from app.core.errors import AppError
from app.modules.agents.definitions import (
    AGENT_DEFINITIONS,
    get_definition,
    register_agent_definition,
)
from app.modules.agents.scaffold import DEFINITIONS_PATH, ScaffoldError, generate

CODE = "tmp_scaffold_x"
NAME = "脚手架测试 Agent"
DESC = "仅用于测试脚手架生成的临时 Agent"


@pytest.fixture()
def sandbox(tmp_path):
    """隔离沙箱：模块目录/测试目录/一份 definitions.py 拷贝。"""
    modules_root = tmp_path / "modules"
    tests_root = tmp_path / "tests"
    modules_root.mkdir()
    tests_root.mkdir()
    definitions_copy = tmp_path / "definitions.py"
    shutil.copy(DEFINITIONS_PATH, definitions_copy)
    return {
        "modules_root": modules_root,
        "tests_root": tests_root,
        "definitions_path": definitions_copy,
    }


def _generate(sandbox, **kw):
    params = {
        "modules_root": sandbox["modules_root"],
        "tests_root": sandbox["tests_root"],
        "definitions_path": sandbox["definitions_path"],
    }
    params.update(kw)
    return generate(CODE, NAME, DESC, ["knowledge.search"], **params)


def test_generate_creates_all_files(sandbox):
    result = _generate(sandbox)
    assert result["definition_appended"] is True
    assert len(result["files"]) == 5

    module_dir = sandbox["modules_root"] / CODE
    expected = [
        module_dir / "__init__.py",
        module_dir / "schemas.py",
        module_dir / "service.py",
        module_dir / "agent.py",
        sandbox["tests_root"] / f"test_{CODE}.py",
    ]
    for path in expected:
        assert path.exists(), f"缺失生成文件：{path}"
        ast.parse(path.read_text(encoding="utf-8"))  # 语法合法

    init = (module_dir / "__init__.py").read_text(encoding="utf-8")
    assert "TmpScaffoldXAgent" in init
    service = (module_dir / "service.py").read_text(encoding="utf-8")
    assert f"def run_{CODE}(" in service
    assert "knowledge_search" in service and "FakeProvider" in service
    assert "TODO" in service
    agent = (module_dir / "agent.py").read_text(encoding="utf-8")
    assert "register_agent_definition" in agent
    assert "get_global_registry().register" in agent
    assert "knowledge.search" in agent  # allowed_tools 取自 --tools
    schemas = (module_dir / "schemas.py").read_text(encoding="utf-8")
    assert "class TmpScaffoldXInput" in schemas
    assert "class TmpScaffoldXOutput" in schemas
    test_file = (sandbox["tests_root"] / f"test_{CODE}.py").read_text(encoding="utf-8")
    assert "test_input_schema_validation" in test_file
    assert "test_service_runs" in test_file
    assert "test_agent_registered_and_runs" in test_file


def test_definition_entry_appended(sandbox):
    _generate(sandbox)
    text = sandbox["definitions_path"].read_text(encoding="utf-8")
    assert f'"code": "{CODE}"' in text
    assert f'"name": "{NAME}"' in text
    assert '"enabled": True' in text
    # 原有条目不受影响
    assert '"code": "data_analyst"' in text


def test_generate_is_idempotent(sandbox):
    _generate(sandbox)
    with pytest.raises(ScaffoldError):
        _generate(sandbox)  # 目标目录已存在，不覆盖


def test_generate_rejects_bad_code(sandbox):
    for bad in ["Bad-Code", "9abc", "", "UPPER", "has space"]:
        with pytest.raises(ScaffoldError):
            generate(
                bad,
                NAME,
                DESC,
                None,
                modules_root=sandbox["modules_root"],
                tests_root=sandbox["tests_root"],
                definitions_path=sandbox["definitions_path"],
            )


def test_generate_defaults_tools_to_knowledge_search(sandbox):
    generate(
        "tmp_scaffold_y",
        NAME,
        DESC,
        None,  # 未传 tools，默认 knowledge.search
        modules_root=sandbox["modules_root"],
        tests_root=sandbox["tests_root"],
        definitions_path=sandbox["definitions_path"],
    )
    service = (sandbox["modules_root"] / "tmp_scaffold_y" / "service.py").read_text(
        encoding="utf-8"
    )
    assert 'DEFAULT_TOOLS = ["knowledge.search"]' in service


def test_register_agent_definition_and_duplicate_guard():
    register_agent_definition(code=CODE, name=NAME, description=DESC)
    try:
        found = get_definition(CODE)
        assert found is not None
        assert found["name"] == NAME
        assert found["enabled"] is True
        assert found["allowed_tools"] == []
        with pytest.raises(AppError):
            register_agent_definition(code=CODE, name=NAME, description=DESC)
    finally:
        # 清理：不污染其他测试的全局定义列表
        AGENT_DEFINITIONS[:] = [d for d in AGENT_DEFINITIONS if d.get("code") != CODE]
    assert get_definition(CODE) is None

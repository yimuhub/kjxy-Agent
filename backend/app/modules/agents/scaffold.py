"""Agent 快速脚手架（架构补强 · B 组）。

借鉴 CrewAI「Agent 定义即数据 + 一条命令生成骨架」与 LangGraph
「配置式注册表 + 工厂函数形态」：

    python -m app.modules.agents.scaffold <agent_code> \\
        --name <中文名> --desc <描述> [--tools knowledge.search]

一键生成（全部符合现有 Agent 规范：Pydantic v2 schemas、无状态 service
纯函数、ToolRegistry 工具声明、小院隔离、Fake LLM 可演示）：

- ``app/modules/<code>/__init__.py``：导出 agent 实例（import 触发自注册）
- ``app/modules/<code>/schemas.py``：``<Class>Input``/``<Class>Output``
- ``app/modules/<code>/service.py``：纯函数 ``run_<code>(db, yard_id, user_id, input) -> dict``，
  默认走 knowledge.search 检索 + Fake LLM 拼装结果，TODO 处替换真实业务逻辑
- ``app/modules/<code>/agent.py``：Agent 类，向 global_registry 自注册，
  ``allowed_tools`` 取自 ``--tools`` 参数
- ``tests/unit/test_<code>.py``：3 个基础测试
  （input schema 校验 / service 纯函数可跑 / agent 注册成功）

幂等：目标目录已存在、或 code 已有定义时报错退出，不覆盖任何文件。
定义注册：除生成文件外，还会向 ``agents/definitions.py`` 的
``AGENT_DEFINITIONS`` 追加一条定义（文件级持久化）；生成的 agent.py
在 import 时还会调用 ``register_agent_definition()`` 做配置式注册
（若定义缺失则补上），双保险。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

MODULES_ROOT = Path(__file__).resolve().parents[1]  # backend/app/modules
TESTS_ROOT = MODULES_ROOT.parents[1] / "tests" / "unit"  # backend/tests/unit
DEFINITIONS_PATH = MODULES_ROOT / "agents" / "definitions.py"
DEFINITIONS_ANCHOR = "AGENT_DEFINITIONS: list[dict] = ["

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SCAFFOLD_VERSION = "1.0.0"


class ScaffoldError(Exception):
    """脚手架可预期的失败（参数非法 / 目标已存在 / 定义冲突）。"""


# ---------------------------------------------------------------- 模板
# 占位符：@@CODE@@（snake_code）、@@CLASS@@（PascalCase）、@@NAME@@（中文名）、
# @@DESC@@（描述）、@@TOOLS@@（allowed_tools 的 Python 字面量）。

_TPL_INIT = '''"""@@NAME@@模块：import 时触发 agent.py 的自注册（向全局 Agent 注册表）。

本模块由脚手架 app.modules.agents.scaffold 自动生成。
"""

from app.modules.@@CODE@@.agent import @@CLASS@@Agent  # noqa: F401 触发全局注册
from app.modules.@@CODE@@.schemas import @@CLASS@@Input, @@CLASS@@Output
from app.modules.@@CODE@@.service import run_@@CODE@@

__all__ = ["@@CLASS@@Agent", "@@CLASS@@Input", "@@CLASS@@Output", "run_@@CODE@@"]
'''

_TPL_SCHEMAS = '''"""@@NAME@@的输入/输出 Pydantic 模型（Pydantic v2）。

本文件由脚手架 app.modules.agents.scaffold 自动生成：
字段为示例骨架，接入真实业务时按需增删字段（保持 Input/Output 命名约定）。
"""

from typing import Any

from pydantic import BaseModel, Field


class @@CLASS@@Input(BaseModel):
    """@@NAME@@输入（示例字段，TODO 按业务替换）。"""

    query: str = Field(min_length=1, description="示例：用户提问 / 任务描述")
    top_k: int = Field(default=5, ge=1, le=20, description="示例：知识检索条数")


class @@CLASS@@Output(BaseModel):
    """@@NAME@@输出（示例字段，TODO 按业务替换）。"""

    answer: str = Field(description="示例：拼装后的回答正文")
    sources: list[dict[str, Any]] = Field(
        default_factory=list, description="示例：检索到的证据条目"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="示例：结果置信度")
    warnings: list[str] = Field(default_factory=list, description="示例：告警信息")
'''

_TPL_SERVICE = '''"""@@NAME@@业务逻辑：无状态纯函数。

默认实现：knowledge.search 检索小院知识库 + Fake LLM 拼装回答，
不调用真实 LLM、不建表、不写 migration，所有查询强制带 yard_id 隔离。

TODO：把 run_@@CODE@@ 内的检索/拼装替换为真实业务逻辑
（输入解析、领域规则、真实 LLM 调用走 app.modules.llm.service.complete_with_logging）。
本文件由脚手架 app.modules.agents.scaffold 自动生成。
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.logging import get_logger
from app.modules.@@CODE@@.schemas import @@CLASS@@Input, @@CLASS@@Output
from app.modules.knowledge.service import search as knowledge_search
from app.modules.llm.providers_fake import FakeProvider
from app.modules.llm.schemas import ChatMessage, LLMRequest

log = get_logger(__name__)

#: 脚手架默认依赖的工具；与生成时的 --tools 参数保持一致。
DEFAULT_TOOLS = @@TOOLS@@


def _retrieve_evidence(
    db: Session, yard_id: str, user_id: str, query: str, top_k: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """小院知识库检索。TODO：按业务替换检索策略 / 数据源。"""
    ctx = YardContext(yard_id=yard_id, user_id=user_id, roles=[], request_id="")
    warnings: list[str] = []
    try:
        hits = knowledge_search(db, ctx, query, top_k=top_k)
    except Exception as e:  # 检索失败不阻断主流程，降级为空证据
        log.warning("@@CODE@@ knowledge.search failed: %s", e)
        warnings.append(f"知识检索失败，已降级为空证据：{e}")
        return [], warnings
    evidence = [
        {
            "title": h.title,
            "source": h.source,
            "quote": h.quote,
            "score": h.score,
            "trust_level": h.trust_level,
        }
        for h in hits
    ]
    return evidence, warnings


def _run_coro_sync(coro):
    """在同步上下文中驱动协程。

    无运行中循环时用 asyncio.run；已在事件循环内（如 pytest asyncio_mode=auto
    的异步测试调用同步 service）时手动步进——FakeProvider.complete 内部无
    await，单步即完成。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    raise RuntimeError("协程中途 yield，无法在运行循环内同步驱动。")


def _fake_llm_answer(question: str, evidence: list[dict[str, Any]]) -> str:
    """Fake LLM 拼装回答。TODO：替换为真实 LLM 调用。"""
    lines = [f"问题：{question}", f"检索到的知识（{len(evidence)} 条）："]
    for item in evidence:
        quote = str(item.get("quote") or "")[:200]
        lines.append(f"- {item.get('title')}：{quote}")
    if not evidence:
        lines.append("（无检索结果）")
    lines.append("请基于以上知识给出简要回答。")
    request = LLMRequest(messages=[ChatMessage(role="user", content="\\n".join(lines))])
    response = _run_coro_sync(FakeProvider().complete(request))
    return response.content


def run_@@CODE@@(
    db: Session, yard_id: str, user_id: str, input_data: dict[str, Any]
) -> dict[str, Any]:
    """@@NAME@@纯函数入口。TODO：替换为真实业务逻辑。

    参数 input_data 须符合 @@CLASS@@Input；返回 dict 符合 @@CLASS@@Output。
    """
    parsed = @@CLASS@@Input(**(input_data or {}))
    evidence, warnings = _retrieve_evidence(db, yard_id, user_id, parsed.query, parsed.top_k)
    if not evidence:
        warnings.append("未检索到相关知识，回答为模板拼装，请核验。")
    answer = _fake_llm_answer(parsed.query, evidence)
    return @@CLASS@@Output(
        answer=answer,
        sources=evidence,
        confidence=0.5 if evidence else 0.0,
        warnings=warnings,
    ).model_dump()
'''

_TPL_AGENT = '''"""@@NAME@@（code=@@CODE@@，version=@@VERSION@@）。

统一运行接口：async run(input_data, context) -> AgentResult。
db 会话从 context.metadata["db"] 获取；所有知识查询经 service 层带 yard_id 隔离。

本文件由脚手架 app.modules.agents.scaffold 自动生成：
- import 时先做"配置式注册"（register_agent_definition 登记元数据，
  定义已存在则直接复用），再向 global_registry 自注册实例——双保险，
  服务启动扫描模块即完成接入。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import get_global_registry
from app.modules.agents.definitions import get_definition, register_agent_definition
from app.modules.agents.schemas import AgentContext, AgentResult
from app.modules.@@CODE@@.schemas import @@CLASS@@Output
from app.modules.@@CODE@@.service import run_@@CODE@@

log = get_logger(__name__)

_AGENT_CODE = "@@CODE@@"


def _ensure_definition() -> dict:
    """配置式注册：定义缺失时补登记，已存在则复用（import 幂等）。"""
    definition = get_definition(_AGENT_CODE)
    if definition is None:
        definition = register_agent_definition(
            code=_AGENT_CODE,
            name="@@NAME@@",
            description="@@DESC@@",
            version="@@VERSION@@",
            allowed_tools=@@TOOLS@@,
            allowed_knowledge_scopes=["yard"],
        )
    return definition


class @@CLASS@@Agent:
    """@@NAME@@：@@DESC@@（TODO：补充职责说明）。"""

    code = _AGENT_CODE
    version = "@@VERSION@@"
    definition = _ensure_definition()

    async def run(self, input_data: dict[str, Any], context: AgentContext) -> AgentResult:
        metadata = getattr(context, "metadata", None) or {}
        db = metadata.get("db")
        if db is None:
            return AgentResult(
                status="failed",
                output={
                    "error_code": "MISSING_DB",
                    "error": "缺少数据库会话（context.metadata['db']）。",
                },
                warnings=["@@NAME@@未能执行：运行上下文缺少数据库会话。"],
            )
        try:
            raw = run_@@CODE@@(
                db,
                yard_id=context.yard_id,
                user_id=context.actor_id,
                input_data=input_data or {},
            )
        except AppError as exc:
            log.warning("%s failed: %s %s", self.code, exc.code, exc.message)
            return AgentResult(
                status="failed",
                output={"error_code": exc.code, "error": exc.message},
                warnings=[f"@@NAME@@执行失败：{exc.message}"],
            )
        output = @@CLASS@@Output(**raw)
        return AgentResult(
            status="succeeded",
            output=output.model_dump(),
            warnings=list(output.warnings),
            confidence=output.confidence,
        )


def _register_agent() -> None:
    try:
        get_global_registry().register(@@CLASS@@Agent.code, @@CLASS@@Agent())
        log.info("registered agent %s v%s", @@CLASS@@Agent.code, @@CLASS@@Agent.version)
    except Exception as e:
        log.warning("agent registration skipped: %s", e)


_register_agent()
'''

_TPL_TEST = '''"""@@NAME@@单元测试（SQLite 内存库）。

本文件由脚手架 app.modules.agents.scaffold 自动生成：3 个基础测试
（input schema 校验 / service 纯函数可跑 / agent 注册成功）。
"""

# import 触发 agent 自注册（含配置式定义注册）
import app.modules.@@CODE@@  # noqa: F401
import pytest
from app.modules.agents import get_global_registry
from app.modules.agents.definitions import get_definition
from app.modules.agents.schemas import AgentContext
from app.modules.@@CODE@@.schemas import @@CLASS@@Input
from app.modules.@@CODE@@.service import run_@@CODE@@
from pydantic import ValidationError


def _make_context(db_session) -> AgentContext:
    return AgentContext(
        task_id="task-test",
        yard_id="yard-test",
        actor_id="user-test",
        request_id="req-test",
        allowed_tools=@@TOOLS@@,
        deadline_seconds=60,
        metadata={"db": db_session},
    )


def test_input_schema_validation():
    parsed = @@CLASS@@Input(query="示例问题")
    assert parsed.query == "示例问题"
    with pytest.raises(ValidationError):
        @@CLASS@@Input()  # query 必填，缺失应校验失败


def test_service_runs(db_session):
    result = run_@@CODE@@(db_session, "yard-test", "user-test", {"query": "示例问题"})
    assert isinstance(result, dict)
    assert "answer" in result
    assert "示例问题" in result["answer"]  # Fake LLM 回显问题摘要


@pytest.mark.asyncio
async def test_agent_registered_and_runs(db_session):
    assert get_definition("@@CODE@@") is not None
    assert get_definition("@@CODE@@")["enabled"] is True
    agent = get_global_registry().get("@@CODE@@")
    assert agent.code == "@@CODE@@"
    result = await agent.run({"query": "示例问题"}, _make_context(db_session))
    assert result.status == "succeeded"
    assert "示例问题" in result.output["answer"]
'''


def _py_str_list(values: list[str]) -> str:
    """生成双引号风格的 Python 字符串列表字面量（ruff format 友好）。"""
    return "[" + ", ".join(f'"{v}"' for v in values) + "]"


def _to_class_name(code: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in code.split("_"))


def _render(template: str, mapping: dict[str, str]) -> str:
    rendered = template
    for key, value in mapping.items():
        rendered = rendered.replace(key, value)
    return rendered


def _definition_entry(mapping: dict[str, str]) -> str:
    """与 definitions.py 手写条目格式一致的新定义文本。"""
    return (
        "    {\n"
        f'        "code": "{mapping["@@CODE@@"]}",\n'
        f'        "name": "{mapping["@@NAME@@"]}",\n'
        '        "version": "1.0.0",\n'
        f'        "description": "{mapping["@@DESC@@"]}",\n'
        '        "input_schema_ref": "",\n'
        '        "output_schema_ref": "",\n'
        f'        "allowed_tools": {mapping["@@TOOLS@@"]},\n'
        '        "allowed_knowledge_scopes": ["yard"],\n'
        '        "risk_level": "low",\n'
        '        "max_steps": 8,\n'
        '        "timeout_seconds": 90,\n'
        '        "enabled": True,\n'
        "    },\n"
    )


def _append_definition_entry(code: str, entry: str, definitions_path: Path) -> None:
    """向 definitions.py 的 AGENT_DEFINITIONS 追加一条定义（文件级持久化）。"""
    text = definitions_path.read_text(encoding="utf-8")
    if f'"code": "{code}"' in text:
        raise ScaffoldError(f"definitions.py 中已存在 code={code!r} 的定义，不重复追加。")
    anchor = DEFINITIONS_ANCHOR + "\n"
    if anchor not in text:
        raise ScaffoldError(
            f"在 {definitions_path} 中未找到锚点 {DEFINITIONS_ANCHOR!r}，无法追加定义。"
        )
    definitions_path.write_text(text.replace(anchor, anchor + entry, 1), encoding="utf-8")


def generate(
    code: str,
    name: str,
    desc: str,
    tools: list[str] | None,
    *,
    modules_root: Path = MODULES_ROOT,
    tests_root: Path = TESTS_ROOT,
    definitions_path: Path = DEFINITIONS_PATH,
) -> dict[str, Any]:
    """生成新 Agent 骨架。返回 {"files": [...], "definition_appended": bool}。

    幂等：目标目录已存在 / code 已有定义时抛 ScaffoldError，不覆盖任何文件。
    """
    code = (code or "").strip()
    if not _CODE_RE.match(code):
        raise ScaffoldError(
            f"非法的 agent_code={code!r}：须为小写字母开头，仅含小写字母/数字/下划线。"
        )
    tools = list(tools) if tools else ["knowledge.search"]

    target_dir = modules_root / code
    if target_dir.exists():
        raise ScaffoldError(f"目标目录已存在：{target_dir}，不覆盖（幂等保护）。")

    mapping = {
        "@@CODE@@": code,
        "@@CLASS@@": _to_class_name(code),
        "@@NAME@@": name,
        "@@DESC@@": desc,
        "@@TOOLS@@": _py_str_list(tools),
        "@@VERSION@@": _SCAFFOLD_VERSION,
    }

    files = {
        target_dir / "__init__.py": _render(_TPL_INIT, mapping),
        target_dir / "schemas.py": _render(_TPL_SCHEMAS, mapping),
        target_dir / "service.py": _render(_TPL_SERVICE, mapping),
        target_dir / "agent.py": _render(_TPL_AGENT, mapping),
        tests_root / f"test_{code}.py": _render(_TPL_TEST, mapping),
    }
    for path in files:
        if path.exists():
            raise ScaffoldError(f"目标文件已存在：{path}，不覆盖（幂等保护）。")

    # 先追加定义（失败则不写任何文件，保持原子性）
    _append_definition_entry(code, _definition_entry(mapping), definitions_path)

    written: list[str] = []
    try:
        target_dir.mkdir(parents=True)
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(str(path))
    except Exception:
        for p in written:
            Path(p).unlink(missing_ok=True)
        try:
            target_dir.rmdir()
        except OSError:
            pass
        raise
    return {"files": written, "definition_appended": True}


_GUIDE = """\
接线指引（新 Agent 接入现有平台，无需改路由）：
1. 路由零改动：agents/router.py 的 GET /agents 与 POST /agents/{code}/run
   已按 code 通用分发；新模块 import 即自注册（agent.py 内含注册代码），
   定义随 import 经 register_agent_definition() 自动登记（配置式注册）。
2. 如需经 HTTP 懒加载触发 import（不手动 import 模块也能调通），可在
   agents/router.py 的 module_by_code 加一行：
       "{code}": "app.modules.{code}.agent",
   （可选；服务启动时若已 import 该模块则无需此步。）
3. 跑通测试：cd backend && python -m pytest tests/unit/test_{code}.py -q
4. 下一步：把 app/modules/{code}/service.py 中的 TODO 替换为真实业务逻辑
   （输入解析 / 领域规则 / 真实 LLM 调用），schemas.py 按业务补字段。
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Agent 快速脚手架：一键生成新 Agent 的模块骨架 + 测试 + 定义注册。"
    )
    parser.add_argument("agent_code", help="Agent 代码（小写下划线，如 demo_advisor）")
    parser.add_argument("--name", required=True, help="Agent 中文名")
    parser.add_argument("--desc", required=True, help="Agent 描述（一句话职责）")
    parser.add_argument(
        "--tools",
        nargs="*",
        default=["knowledge.search"],
        help="allowed_tools 列表（默认：knowledge.search）",
    )
    args = parser.parse_args(argv)

    try:
        result = generate(args.agent_code, args.name, args.desc, args.tools)
    except ScaffoldError as e:
        print(f"[scaffold] 失败：{e}", file=sys.stderr)
        return 1

    print(f"[scaffold] 已生成 {len(result['files'])} 个文件：")
    for path in result["files"]:
        print(f"  - {path}")
    print("[scaffold] 已向 agents/definitions.py 追加定义（含 enabled=True）。")
    print()
    print(_GUIDE.format(code=args.agent_code))
    return 0


if __name__ == "__main__":
    sys.exit(main())

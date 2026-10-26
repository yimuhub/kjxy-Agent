# Agent 初始技能清单（出生自带技能）

> 数据来源（唯一事实源）：`backend/app/modules/agents/definitions.py` 的
> `allowed_tools` + `backend/app/modules/tools/registry.py` 的
> `ToolRegistry` 实际注册（应用启动时 `main.py` 触发各业务模块注册实现）。
> 本文档只记录当前真实注册状态，不虚构 skill；如某 Agent 的 `allowed_tools`
> 为空则如实标注"暂无预置技能"。
>
> 前端"初始技能"区域展示的 `skills` 字段由 `GET /agents` 返回，
> 对应后端实现见 `app/modules/agents/router.py` 的 `_skill_summaries()`。

## 1. 农业知识问答 Agent（agriculture_qa）

**定位一句话**：基于已授权农业知识提供带来源的辅助回答。

| skill code | 名称 | 说明 | 风险等级 | 输入输出摘要 | 备注 |
|---|---|---|---|---|---|
| `knowledge.search` | 知识检索 | 在当前用户有权访问的知识范围内执行检索（实现由知识库模块注册；需权限 `knowledge:read`） | `read_only` | 输入：`query`（必填，检索问题，≤2000 字符）、`top_k`（1–20，默认 5）；输出：`hits[]`（命中的文档片段，含来源引用） | Agent 的 `allowed_knowledge_scopes` 为 `["yard", "crop_public"]`；检索范围受小院授权约束 |

## 2. 作物诊断辅助 Agent（crop_diagnosis）

**定位一句话**：基于症状/环境描述给出候选问题、支持证据与风险说明的诊断辅助（非确定诊断）。

| skill code | 名称 | 说明 | 风险等级 | 输入输出摘要 | 备注 |
|---|---|---|---|---|---|
| `knowledge.search` | 知识检索 | 同上（需权限 `knowledge:read`） | `read_only` | 同上 | Agent 自身 `risk_level` 为 `medium`（输出供种植决策参考），但其 skill 本身为只读；`allowed_knowledge_scopes` 为 `["yard", "crop_public"]` |

## 3. 农事规划 Agent（farm_planner）

**定位一句话**：生成农事计划草案（任务时间窗、物资估算、依赖条件与风险）；计划与工单明确区分。

| skill code | 名称 | 说明 | 风险等级 | 输入输出摘要 | 备注 |
|---|---|---|---|---|---|
| `knowledge.search` | 知识检索 | 同上（需权限 `knowledge:read`） | `read_only` | 同上 | 本 Agent 只生成建议草案，不直接下发工单；`workorder.dispatch` 目前没有配给任何 Agent（见下文第 5 节），故此处无技能可调工单 |

## 4. 科研辅助 Agent（research_assistant）

**定位一句话**：CSV 数据导入、缺失值报告、描述统计与图表草案；统计假设明确标注。

| 状态 | 说明 |
|---|---|
| **暂无预置技能** | `definitions.py` 中该 Agent 的 `allowed_tools` 为空。它的数据处理（CSV 解析、缺失值报告、描述统计）是 Agent 实现内部逻辑，不是经 `ToolRegistry` 注册的可调用 skill。后续如需给它配可视化工具，见第 6 节 |

## 5. 数据汇总 Agent（data_analyst）

**定位一句话**：多源数据汇总与可回溯报表：每个关键数字可追溯到查询条件、时间范围与数据源。

| 状态 | 说明 |
|---|---|
| **暂无预置技能** | `definitions.py` 中该 Agent 的 `allowed_tools` 为空。汇总/追溯逻辑在 Agent 实现内部完成，目前没有注册为独立 skill。后续如需给它配技能，见第 6 节 |

---

## 6. 技能风险分级说明

架构调研结论（`docs/arch-research.md` 三、3.1）：工具定义格式对齐 MCP
（`name/description/inputSchema/outputSchema/annotations`），`annotations`
的四个 hint 即**现成的风险分级字段**，无需自创体系。

### 6.1 MCP `ToolAnnotations` 四个 hint

| hint | 含义 | MCP 默认（保守） |
|---|---|---|
| `readOnlyHint` | 只读：不修改任何外部状态 | `false` |
| `destructiveHint` | 是否可能造成破坏（删除/覆盖/不可逆变更） | `true` |
| `idempotentHint` | 多次调用是否与一次调用效果相同 | `false` |
| `openWorldHint` | 是否与外部世界（公网/第三方服务/物理设备）交互 | `true` |

### 6.2 我们的 `risk_level` 与 MCP hints 的对应关系

`ToolRegistry.RISK_LEVELS` 共四级，执行时在代码层强制校验（harness 层；
角色→工具映射由 `yards/permissions.py` 承担，不重复造轮子）：

| 我们的 risk_level | MCP hints 对应（等价描述） | 行为约束 |
|---|---|---|
| `read_only` | `readOnlyHint=true`，`destructiveHint=false` | 只读，无副作用，免审批。当前唯一已注册工具 `knowledge.search` 即此级 |
| `draft` | `readOnlyHint=false`，`destructiveHint=false`，仅产生内部草稿/建议 | 可写小院内部数据但不对外生效（如计划草案、报告草稿），内部可见、未审核不流转 |
| `write_reversible` | `destructiveHint=false`（或低破坏），`idempotentHint=true` 优先 | 可逆写入（如配置更新、状态回滚操作），执行留审计，可撤销 |
| `external_side_effect` | `openWorldHint=true` 或 `destructiveHint=true`（保守默认） | **高风险**：调用前必须人工审批（`approvals` 审批单），未获批自动创建 pending 审批单并拒绝执行。示例：`workorder.dispatch`（作业工单下发，当前 dry-run 演示） |

对应规则：**MCP 默认保守**（未标注 = destructive / 非幂等 / openWorld）；
我们的注册逻辑同理——`risk_level` 缺省为 `read_only` 仅当显式声明，
任何未声明风险等级的工具不得被赋予写/外部能力。

## 7. 如何给 Agent 加技能

给 Agent 增加出生自带技能走 **B 组 Agent 脚手架** 的 `--tools` 参数
（脚手架在开发中，接口以 B 组交付为准），其效果是向
`definitions.py` 对应 Agent 的 `allowed_tools` 追加 tool code；
该 tool code 必须已在 `ToolRegistry` 实际注册（否则 `GET /agents` 的
`skills` 列表会自动跳过它，且 Agent 运行时无法调用）。

注意：`allowed_tools` 只是"允许调用"白名单，真正的权限仍由
`yards/permissions.py` 的角色→权限点校验在代码层强制执行。

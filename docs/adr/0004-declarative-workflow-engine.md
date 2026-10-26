# ADR-0004：引入声明式 YAML 工作流引擎（借鉴 agency-orchestrator开源实现，Apache-2.0）

- 状态：草案（待技术负责人批准）
- 日期：2026-10-17
- 相关：规范 §8.4（工作流版本化 JSON 定义）、§2.3（首版不引入大型 Agent 框架）；
  蓝本：github.com/jnMetaCode/agency-orchestrator（TypeScript，Apache-2.0）

## 背景

现平台工作流为硬编码（问答链、解析链各自写死）。对标 agency-orchestrator 的实测源码分析：
其"零代码编排"内核极小——拓扑分层 + 黑板变量 + 模板内插接力 + 声明式分支/循环/人审，
百行级可实现，业务/领域人员改 YAML 即可编排新场景（作物诊断链、政策申报链），
无需每次动 Python。该结构同时天然满足规范 §8.4 对"版本化定义、白名单节点、限制上限"的要求。

## 决策

新增 `backend/app/modules/workflows/`（新模块，不改现有任务状态机）：

1. **定义格式**：版本化 YAML（JSON Schema 校验，`additionalProperties:false`），节点四元组
   `role(agent_code)/task(含 {{变量}} 模板)/output/depends_on`，声明式扩展：
   `condition`（仅 contains/equals 两算子，显式拒绝取反）、`loop: {back_to, max_iterations≤10,
   exit_condition}`、`depends_on_mode: any_completed`（分支汇聚，多分支允许同名 output）、
   `type: approval`（人审闸门→任务转 awaiting_review，对齐 §7.5）。
2. **执行引擎**：asyncio 复刻拓扑分层（同层按小院并发上限分批 gather）；黑板= dict，
   变量未定义在**校验期**报错（启动即知，不跑到一半炸）；循环节点回拨层级指针，
   `back_to` 必须在依赖链祖先上（构建期校验 safegard）。
3. **双层验收**（每步可挂）：机械断言 `assert`（纯函数：产物块数/长度/contains/正则，
   硬失败打回定向返工 1 轮）→ LLM 验收 `acceptance`（逐条判 {pass, why 必须引用产出原话}，
   引用不出原话的"未满足"改判满足——防裁判幻觉口径）；验收不过只标 ⚠️+needs_review，
   不判执行 failed（质量信号与执行错误两级语义）。
4. **安全边界**：工作流文件只从 `plugins/workflows/` 与小院配置目录加载；节点白名单=
   已注册 agent/tool code；步数/超时/token 预算从 agent_definitions 继承再取 min；
   不引入任何动态代码执行。定义变更走版本号+审计（对齐 §11.4 Skill 发布流程）。

## 备选方案

- 引入 LangGraph：违 §2.3 首版约束，重型依赖，被否（其文档已建议评估留待 ADR）。
- 继续硬编码：每加场景改核心代码、无验收语义，被否。
- 直接移植 agency-orchestrator 整个 TS 引擎：跨语言运行时+其本地 CLI provider 体系与平台
  LLMProvider 契约冲突，被否；只移植设计（Apache-2.0 允许，保留出处注释）。

## 收益 / 代价 / 风险

领域人员可自助编排；验收前置减少专家复审量。代价：新增一个引擎模块与 YAML 学习面；
风险：YAML 表达力不足时被迫加算子——用"只加白名单算子、拒绝图灵完备"纪律约束。

## 迁移 / 回滚

新模块旁路接入，现有任务链不受影响；回滚=停用模块路由。首期仅迁移"政策申报链"一个场景验证。

# v1.9 升级方案：小院化改造 + 助手工作台（@点名、私有/公用、模型与技能绑定、卡片视图、群聊）

> 本文是实施方案。所有结论均有 file:line 证据，取自 2026-10-26 对
> `main@ff5e161`（v1.8.0）的实测与代码勘察。
>
> **§6 四个决策已拍板（2026-10-26，用户定）**，本文件按拍板结果执行：
> D1 改名**含标识符全改**（`tenant`→`yard`、表名列名 JWT claim 角色值一起改）；
> D2 本轮**四波全做，含群聊**；D3 重名按**自动加 -2/-3 后缀**清洗；
> D4 **每波完成后 bump**。因 D1+D2，本轮终点版本为 **2.0.0**，文件名保留早期编号。
>
> **✅ 已全部落地（2026-10-26，v2.0.0）**：W1–W4 完成并过全部门禁（855 后端用例、
> 10 条 Playwright、契约与错误码登记），落地记录见
> `docs/audit-report-2026-10-25.md` §13 与 `CHANGELOG.md` [2.0.0]。

---

## 0. 一句话结论

七条需求里，**五条是"已有地基、只差接线"**，只有**群聊需要新建编排原语**；
最大的风险不是工作量，而是"把所有'租户'改掉"这句话——**显示文案约 64 行可以安全改，
`tenant_id`/表名/JWT claim/`tenant_admin` 角色值共约 940+ 处是破坏性契约变更**。
建议分层执行，把改名停在显示层。

---

## 1. 需求拆解与现状证据

| # | 需求 | 现状 | 结论 |
|---|---|---|---|
| R1 | "租户"→"小院" | 前端可见文案 ~19 行/6 文件；后端约 45 行文案；但 `tenant_id` 在 .py 里 **942 处**，表 `tenants/tenant_agents/tenant_memberships/tenant_invitations/tenant_quotas`，JWT claim、错误码 `TENANT_REQUIRED`、角色值 `tenant_admin` | 分层：文案改，标识符不改 |
| R2 | 同类助手只留一个 + 重名拦截 + 复用/复制 | `tenant_agents` 唯一约束只有 `(tenant_id, agent_code, version)`，**`name` 不唯一**；`create_agent` 只查 code 冲突（409 `AGENT_CODE_TAKEN` / `AGENT_VERSION_CONFLICT`）；同类 409 已有先例：`devices/router.py:330`、`push/router.py:77`、`mcp/router.py:74` 的 `DUPLICATE_NAME` | 有可复用错误码范式 |
| R3 | 公用 / 私有（私有名后缀"（用户名+私有）"） | `created_by` 列存在但**无任何可见性逻辑**；`get_published_by_code` 只按 `tenant_id` 过滤 → 已发布=全租户可用 | 需新增可见性列 + 过滤 |
| R4 | 管理员编辑/删除 | **编辑已存在**：`PATCH /agents/declarative/{id}`（router.py:248）+ UI 编辑按钮（AgentsView.vue:642-647）；**硬删除不存在**，只有 `deprecate → status='deprecated'` | 编辑是存量；删除要新做 |
| R5 | "试跑"改为"使用"→弹出对话 | AgentsView.vue:635-641 打开 `el-drawer`，一次性调 `agentApi.run(code,{input:{question}})`（非对话） | 改造为对话面板 |
| R6 | 每个助手单独绑模型 + 单独绑技能 | **技能已能单独绑**：`allowed_tools` 对 `tools/registry.py` 白名单解析；**模型列 `supplier_id` 已存在但运行时从不读取（死字段）**；`LLMRequest.model` 会被 provider 采纳，只是 declarative 从不设置 | 是"接线"，不是"新建" |
| R7 | 助手列表卡片式/列表式切换 + 首图 | 内置 4 助手已是卡片栅格（AgentsView.vue:63-117，显示名称/描述/风险/skill 标签），声明式助手是 `el-table`；**全站无任何视图切换**；**后端无封面图字段，前端无 assets 目录**，唯一视觉标识是 `emoji` | 首图需后端新增存储 |
| R8 | @ 点名助手 | `MessageSendRequest` 只有 `content`+`attachment_ids`，**无 target**；但 `supervisor.ask_directed`（supervisor.py:344）已能按 id→code→name 精确执行，`routed_by='bound'`，目前**只被 bots/gateway.py:268 使用** | 中等扩展 |
| R9 | 多助手群聊（全部答/指定人答/自愿答） | `AgentTeam.members_json` 是**有序 2-8 接力**，`compose_answer` 折叠成最后一个成员的答案；有 `member-event` SSE 流；**无并行 fan-out、无自愿抢答** | 需新建编排 |

**已确认的"4 个专业助手"白名单**（用户要求原样保留）：
`AgentsView.vue:1082-1098` 硬编码过滤 `['crop_diagnosis','farm_planner','research_assistant','data_analyst']`，
它们是**平台注册表 Agent**，不在 `tenant_agents` 表里。R2 的重名规则不应波及这 4 个。

---

## 2. 三个比需求本身更重要的发现

**① `api/chat.ts` 的类型在说谎。** 前端文档化了 `target:'auto'|'agent'|'team'` 与
`routed_by='manual'`，后端从未实现。R8 做完之前，这些是死类型——要么这轮补齐实现，
要么先把注释与类型改准，否则下一个人还会照着它写代码。

**② 徽章映射是上一版事故的同源结构。** v1.8.0 修的就是"`routed_by='local'` 没登记 →
整条消息渲染崩溃"。`ask_directed` 产出的 `routed_by='bound'` 同样不在 `ROUTED_META` 里。
`chat_messages.routed_by` 是 `String(10)`，加值要同步改前端映射，否则又是一次白屏。
**建议根治**：后端在 `message_out` 里直接下发 `routed_label`/`routed_tone`（受控词表在
`supervisor._ROUTED_LABEL` 已存在），前端只渲染、不再维护第二份映射表。这条把 R8 的风险
从"要靠人记得改两处"降为"结构上不可能不同步"。

**③ `<ChatPanel>` 抽取是这一轮的承重梁，必须最先做。** `stores/chat.ts` 是
`defineStore('chat')` **全局单例**——`activeId/messages/sending/liveSteps` 全应用共享一份。
直接把 ChatView 模板塞进弹窗，会**污染主对话页状态**（两处同时 `sending`、消息串台）。
R5 的弹窗对话和 R9 的群聊都需要它。顺序上：先抽组件+可实例化 store，再谈弹窗与群聊。

---

## 3. 方案分层

### 波次 W1：小院化（显示层）＋ 助手唯一性

改名（零契约风险，约 64 行）：
- 前端：`AgentsView.vue:571/577/664/731`（"租户助手"→"小院助手"、"本租户知识库"→"本小院知识库"）、
  `GlobalOverview.vue` 7 处、`MarketView/TeamsView/DevicesView/SignupView` 各 1-2 处。
- 后端：约 30 条 AppError 文案 + `db/seed.py:23` 显示名"租户管理员"→"小院管理员" +
  4 处 Pydantic Field description（会进 OpenAPI 文档正文）+ `tools/registry.py` 2 处工具描述。
- 顺带把已有的 `MainLayout.vue:356`（`tenant_admin` → 显示"空间管理员"）统一为"小院管理员"，
  这是项目里**已存在**的部分重品牌方向，沿用它而不是另起一套。
- 明确不改：表名、列名、`tenant_id` JSON 字段、JWT claim、`TENANT_*` 错误码、
  `tenant_admin` 角色值、Python 标识符。理由见 §6 决策 D1。

助手唯一性（R2）：
- 新约束：`uq_tenant_agents_tenant_name (tenant_id, name)`，同小院内汉字名称不得重复。
- `create_agent` 先查重 → 命中返回 **409 `DUPLICATE_NAME`**（沿用 devices/push/mcp 既有码），
  metadata 带已存在助手的 `id/code/name/status`，供前端弹"已存在，是否复用"。
- 前端三选：`复用`（跳进该助手对话）/ `复制`（以原名+`-2` 递增后缀新建，沿用其 persona/tools/scopes）
  / `重命名`（回到表单）。复制走新端点 `POST /agents/declarative/{id}/duplicate`，
  服务端生成不冲突名，避免前端拼名竞态。
- **迁移前置清洗**：现库 36 行里已有 **10 组重名**（含我测试造出的"农机维修问答助手"×5、
  factory 批量产出的合作社助手 ×2）。加唯一约束前必须先清洗，策略见 §6 决策 D3。

### 波次 W2：助手可见性 + 删除 + 模型/技能绑定 + 卡片视图

- 新增列 `visibility`（`public` / `private`，默认 `public`，管理员建的一律 public）；
  `list_agents`/`get_published_by_code` 过滤条件改为
  `status='published' AND (visibility='public' OR created_by=<user>)`。
  成员自建 = private，管理员建 = public，与既有 `capability.py` 的"管理员执行/成员申请"口径一致。
- 展示名派生：`name` 存原名，**渲染时**派生 `display_name = name + "（" + username + "私有）"`；
  不把后缀写进 `name` 列（否则唯一约束与重命名会互相污染，且@列表会显示脏名字）。
- `DELETE /agents/declarative/{id}`：管理员限权；被引用的助手（会话 `choice_json`、班组
  `members_json`）不物理删，走 `deprecated` 软删并保留审计，避免历史消息指向空对象。
- 模型绑定：运行时读取 `agent.supplier_id`（现为死字段），经 `supplier_service.get_supplier`
  做**可见性双校验**（只能指到本小院可见或全局只读供应商），解析出的 `models` 首选写入
  `LLMRequest.model`；越权或供应商失效 → 回退小院 active 供应商并记 warning，不 500。
- 技能绑定：`allowed_tools` 已可用，补一个"技能多选"表单控件（数据源 = 工具注册表 +
  `BUILTIN_TOOLS` + MCP 动态工具）；卡片视图展示绑定的模型名与技能标签。
- 首图：复用 v1.8.0 刚做好的附件存储管道（`chat/attachments.py`）落一张
  `cover_image`（新列存 key，返回打码后的访问 URL）；无图则回落到现有 `emoji` 大字渲染，
  避免"没上传就空一块"。**不引新依赖、不新建对象存储**。
- 视图切换：`el-segmented`（design-system.css:697-701 已主题化）卡片/列表两态，
  卡片栅格沿用 `:xs=24 :sm=12 :md=8` 既有响应式约定；`aria-current` 标记当前视图。

### 波次 W3：@点名 + 弹窗对话

- 先抽 `<ChatPanel>`：把消息流/气泡/步骤条/输入区做成 props+emits 的表现层组件，
  store 改为工厂式 `createChatStore(scope)`（或 `defineStore('chat-'+instanceId)`），
  主 ChatView 与弹窗各自独立实例，互不串状态。
- `MessageSendRequest` 增 `target_code`（可选）。有值 → 走 `ask_directed`，无值 → 走现有
  `ask` 三级路由；**意图为 create/install 时优先意图链路**，不能被 @ 覆盖，否则"创建助手"
  会被当成给某助手提问。
- 新增候选端点 `GET /chat/mention-candidates`（返回本小院可见的 published 助手 + 班组，
  含 `kind/id/code/name/display_name`），前端 `@` 触发 popover，复用已注册的 `ElPopover`。
- 徽章按 §2-② 改为后端下发 `routed_label`，同时把 `bound` 补进受控词表。
- `AgentsView` 的"试跑"→"使用"：把原 drawer 换成承载 `<ChatPanel>` 的 `el-dialog`
  （design-system.css:492-523 已有玻璃对话框皮肤；移动端需 fullscreen 化）。

### 波次 W4：群聊（建议独立版本）

现有 `TeamRun` 是**顺序接力 + 折叠成一个答案**，与"群聊"语义不同，不要硬套。新增：

- `ChatSession` 侧的参与人模型（`chat_participants`：session_id、agent_ref、ordering），
  会话不再是"每轮独立路由"而是"固定成员 + 定向策略"。
- 轮次策略：`all`（全员应答）/ `named`（仅点名者）/ `auto`（可见后自愿抢答）。
  `auto` 需要一个应答门（轻量：每助手先做一次"该不该我答"的分类打分，达阈值才发言），
  并保证**确定性可解释**——把落选原因写进步骤，否则用户看到的是"没人理我"。
- 并行 fan-out 用 `asyncio.gather` + **每成员一条 `member-event`**（借 `teams/stream.py` 的
  帧形状），而不是 `compose_answer` 折叠；每条助手消息仍落 `ChatMessage`（模型已支持
  同一会话多来源 `choice_json`，无需改表）。
- 成本护栏（必须同批做）：N 个助手 ×M 轮 = N×M 次模型调用。要有并发上限、单轮 token 预算、
  以及供应商不可用时的"部分人未回答"如实呈现，而不是静默缺人。

---

## 4. 建议的版本与分期

| 波次 | 内容 | 版本 | 依赖 |
|---|---|---|---|
| W1 | 小院化（显示层）+ 助手重名拦截/复用/复制 + 名称清洗迁移 | **1.9.0** | 无 |
| W2 | 可见性/私有命名/软删除 + 每助手绑模型与技能 + 卡片列表切换 + 首图 | **1.10.0** | W1（唯一名约束） |
| W3 | `<ChatPanel>` 抽取 + @点名 + "使用"弹窗对话 | **1.11.0** | W2（候选集要可见性过滤） |
| W4 | 多助手群聊（all/named/auto + 并行 + 成本护栏） | **2.0.0** | W3（同一套对话面板） |

W1-W3 内部各自门禁可独立全绿、可独立发布；W4 是唯一引入新编排原语的波次，单独成大版本，
出问题不拖累前三波。

---

## 5. 验收与门禁

沿用 v1.8.0 的门禁矩阵，每波追加：

- **后端**：`pytest`（现 708 绿）新增——重名 409 与复用/复制分支；`visibility` 过滤的
  越权负向用例（成员不能看见他人 private）；`supplier_id` 越权回退；软删后历史消息仍可读。
  名称清洗迁移必须有 `upgrade→downgrade→upgrade` 往返用例，并断言清洗后重名组数为 0。
- **前端**：ESLint 0 error、`vue-tsc`、`vite build`；Playwright 追加——卡片/列表切换、
  重名弹"是否复用"、@点名后徽章显示"定向执行"、弹窗对话与主对话**状态互不污染**
  （这条直接验 §2-③ 的回归）。
- **契约**：`generate_openapi.py` + drift 检查；`routed_by` 与 `target_code` 的变更
  必须同批更新 `contracts/openapi.yaml` 与 `api/chat.ts` 的死类型。
- **实测**：模型往返实测约 33s，E2E 超时必须放宽（本轮已按 90s 设置），不能用 8s 默认值。

---

## 6. 需要拍板的四个决策

**D1 改名范围。** 我的建议是**只改显示层**：约 64 行、零契约风险，用户感知到的"小院"
90% 来自这里；改 `tenant_id`/表名/JWT/角色值要动约 940+ 处代码 + 5 张表 + 迁移，
并且会让已发布的 v1.7.0/v1.8.0 桌面客户端与新库不兼容。若确实要全改，应作为独立大版本、
带双写过渡期，不与本轮功能混做。

**D2 群聊是否本轮做。** 建议 W1-W3 先落地，群聊独立成 2.0.0。它需要新的会话成员模型、
并行编排、应答门和成本护栏，是本轮唯一"新建原语"级别的工作。

**D3 现有 10 组重名怎么清洗。** 三个选项：自动加 `-2/-3` 后缀保留全部；同码族合并留最新；
或只对未来生效（那就暂时不能加唯一约束）。我建议自动加后缀（无损、可回滚），
其中"农机维修问答助手"×5 是我测试产生的脏数据，可单独删掉。

**D4 版本号是否现在改。** 我刚把四处版本 + `uv.lock` 对齐到 1.8.0 并发布。
若在 W1 开工前就写 1.9.0，仓库版本号会指向尚未实现的功能，且 tag 一推就触发
三平台 Release 构建。建议**每波完成后**再 bump 对应版本。

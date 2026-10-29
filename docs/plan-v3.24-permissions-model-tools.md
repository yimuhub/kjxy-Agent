# V3.24–V3.26 方案：千人千面权限、工作台视图、模型配置重构、工具与演示数据

> 日期：2026-10-29　状态：待开工（本文即开发基线）　版本纪律：只递增 3.x 小版本
>
> 依据：你本轮 7 条反馈 + 对权限/菜单/模型/工具/MCP/种子的代码实测（文中「现状」均标文件与行号）。
> 分批：**A=3.24.0 权限与千人千面** → **B=3.25.0 模型配置与工具/MCP** → **C=3.26.0 演示数据与动态前端配置**。
> 顺序理由：A 是所有页面「谁能看见什么」的地基；B 的模型/工具要挂在 A 的权限点上；C 依赖 A/B 的入口与开关。

---

## 0. 你提的 7 件事，对应到哪批

| # | 你的要求 | 批次 | 你会看到什么 |
|---|---|---|---|
| 1 | 权限名全中文；成员管理里能逐人授权；owner 全权、改敏感权限要弹窗；管理员可管专家/操作员/普通成员/自定义角色，但碰不了受保护权限；权限与技能要分开 | A | 角色矩阵中文可读；成员行有「单独授权」；勾选受保护权限时弹确认；管理员编辑范围收窄并提示 |
| 2 | 千人千面：没权限的菜单/功能全局隐藏，owner 可见全部并可授予其他用户组 | A | 菜单、路由、按钮都按有效权限判定；owner 把功能勾给角色后，对方刷新即见 |
| 3 | 「导航菜单 DIY」扩成：更多模板、极简/全功能、按用户组预设、owner/admin 设默认视图、功能池展示所有角色功能 | A | 改名「工作台视图」；三档模式 + 4 套角色预设 + 功能池 |
| 4 | 模型配置/供应商挪到「小院管理」下，界面升级：兜底、全局默认、多供应商、按 Agent、按能力绑定、实时生效 | B | 新「小院管理」分组；模型中心一页配完；保存即生效 |
| 5 | 可用工具：详情预览、右上角添加、一句话添加（复用 Agent/LLM） | B | 工具卡片/详情抽屉；「添加可用工具」支持手填与一句话生成 |
| 6 | 演示数据：SSO 一条成功演示（可编辑可删除）、A2A 演示协作方、MCP 演示服务器 | C | 演示小院开箱可见可点，均可编辑/删除 |
| 7 | 前端配置动态化：mock 可开可关，其他硬编码项能配 | C | 「前端配置」页签可保存，刷新后生效 |

---

## 1. 现状（证据）

### 1.1 权限模型

- 33 个权限点、6 个受保护：`backend/app/modules/yards/permissions.py:15-64`；角色默认权限 `:88-137`（owner 全集、admin 全集−保护、expert 17、operator 9、viewer 6）；中文角色名 `:140-148`；权限分组 `:154-219`。
- 有效权限 = 角色权限 ∪ 逐人授权，owner/platform_admin 恒全集：`authz.py:38-86`。
- 逐人授权**已存在但很窄**：只有负责人可用（`owner_router.py:55-59`），只能授 `GRANTABLE_PERMISSIONS` 5 项（`:249`），且只列/只作用于 **yard_admin**（`:200-234`）。
- 管理员不能授 yard_admin、自定义角色必须是操作者权限子集：`member_service.py:163-187`；但系统角色只有 owner 能改：`settings_router.py:253-254`。
- 组配置会剔除保护项：`roles_service.py:95-106`；非 owner 提交保护权限 403：`settings_router.py:175-186`。

**结论**：能力底座齐全，缺的是「任意成员可逐人授权」「管理员可管 expert/operator/viewer/自定义角色」「受保护变更二次确认」「前端按有效权限渲染」。

### 1.2 前端千人千面

- `/auth/me` 只回 roles，不回权限：`backend/app/modules/auth/service.py:379-384`；前端 `stores/auth.ts:18-63` 只有 `hasAnyRole/isYardAdmin`。
- 路由只有 `meta.roles`（`router/index.ts:95-171`），守卫 220-224 也只看角色；自定义角色拿不到路由准入。
- 菜单按硬编码 roles 过滤（`MainLayout.vue:486-495`）；原子仅 8 项带 roles（`stores/menu.ts:43-72`）；菜单只存 localStorage（`:203,248-252`），换人/换设备不继承。
- 业务页大量用 `isYardAdmin()` 控制按钮，与后端权限点脱节。

### 1.3 菜单 DIY

- 2 套全局模板（`menu.ts:150`），无按角色预设；编辑器支持改名/显隐/增组/拖拽（`MenuDiyEditor.vue:48,108,465,488,544,583`），但**全员可编辑**，且原子标题加载即覆盖（`menu.ts:210`）。
- 设置页只读展示 VITE_API_BASE 与 Mock（`SettingsView.vue:566-588`），mock 是构建期开关（`api/mock.ts:8-10`）。

### 1.4 模型与工具

- 供应商表无「全局默认/用途绑定/兜底顺序」：`llm/supplier_models.py:17-47`；解析顺序为 绑定→小院 active→全局 priority→settings 兜底（`llm/provider.py:84-117`），**无运行时重试/跨供应商降级**，失败不累计熔断。
- purpose 只用于观测（`llm/traces_router.py:27-38` 的 9 值 vs 实际写入 20+，查询会 400）。
- Agent 已有 `supplier_id/model_name`（`agents/models.py:77-79`），自定义助手页可选（`CustomAgentsView.vue:559-604`）。
- 工具表有完整定义与风险等级（`tools/models.py:18-35`），但只有 `GET /tools`（`tools/router.py:11-39`），无详情/新增/一句话生成；MCP 工具能执行却不进工具列表（`mcp/tool_bridge.py:38-56`）。
- MCP 管理齐全（`mcp/router.py:27-170`），无演示服务器。

### 1.5 演示数据

- 种子只有小院 + 4 账号 + 知识（`backend/app/db/seed.py:20-29,117-183`），无 SSO/A2A/MCP/设备/地块。
- SSO 默认关（`core/config.py:57`），无 mock IdP；A2A 无演示对端；MCP 无本地演示服务。

---

## 2. A 批（3.24.0）：权限与千人千面

### 2.1 权限语义与命名（后端）

1. **权限点补全**（补齐后按模块成组，前端不再猜）：
   - 新增 `chat:use`、`agents:manage`、`devices:read`、`devices:write`、`policies:read`、`policies:write`、`evolution:manage`、`tools:manage`、`llm:manage`、`mcp:manage`、`menu:manage`、`demo:manage`（演示数据维护）。
   - 默认角色映射：owner/admin 全集（admin 仍减保护项）；expert 增 `chat:use/agents:manage/devices:read/policies:read`；operator 增 `chat:use/devices:read`；viewer 增 `chat:use`（仅读）。
   - `PROTECTED_PERMISSIONS` 保持 6 项；`GRANTABLE_PERMISSIONS` 扩为「除 `owner:transfer` 外的全部权限」。
2. **`/auth/me` 下发 `permissions`**（有效权限数组）与 `is_owner`；JWT 不变，权限每次按 DB 计算（`authz.effective_permissions`），改动即时生效。
3. **逐人授权泛化**（owner 专用）：
   - `GET /yards/current/members/{user_id}/permissions` → `{role, role_permissions, grants, effective, protected_changes}`；
   - `PUT /yards/current/members/{user_id}/permissions` → `{grants: [..], confirm_protected: true|false}`；仅 owner；不可授 `owner:transfer`；授/撤受保护项必须 `confirm_protected`，写审计 `member_permission_granted/revoked`；
   - 保留旧 `/yards/current/owner/grants` 兼容，内部复用新逻辑。
4. **受保护变更二次确认**：
   - 角色更新（`PUT /yards/current/roles/{code}`）新增 `confirm_protected`；若请求导致任何受保护权限增减且未确认 → 400 `PROTECTED_CONFIRM_REQUIRED`；
   - 成员授权同理；前端弹窗二次确认后重发。
5. **角色管理边界**：
   - owner：可改 owner 之外全部角色（含 yard_admin）；
   - yard_admin：可改 **expert/operator/viewer/自定义角色**，不可改 yard_admin/owner，且只能授非保护项（后端强制，越权 403 `ROLE_EDIT_FORBIDDEN`）；
   - 自定义角色权限仍须是操作者权限子集（保留现状）。
6. **后端 enforcement**：给 chat/agents/devices/policies/evolution/tools/llm/mcp/menu 补 `require_yard_permission`，做到「隐藏菜单 ≠ 不拦截」；公开端点（登录、分享、A2A 入站）保持。

### 2.2 前端千人千面

1. `stores/auth.ts`：保存 `permissions` 与 `isOwner`，新增 `hasPermission(code)`；`/auth/me` 变更后全局生效。
2. **路由**：每页 `meta.permission`（如 `/farm → farm:read`、`/tasks → tasks:read`、`/devices → devices:read`、`/evolution → evolution:manage`、`/settings → settings:manage`）；守卫按权限而非角色；无权限跳 403 页（新增 `ForbiddenView`）。
3. **菜单**：原子新增 `permission` 字段，`MainLayout` 按 `hasPermission` 过滤；无权限菜单与页面隐藏。
4. **按钮级**：`v-if="hasPermission('farm:write')"` 等替换 `isYardAdmin()`；先做高频页（台账/工单/诊断/设备/知识/任务/成员/设置）。
5. **成员管理**：角色下拉改用后端 `assignable_roles + 自定义角色`；行内「单独授权」只对 owner 显示，弹窗列出角色权限 + 逐项勾选 + 受保护项二次确认 + 有效权限预览。
6. **角色与权限**：权限名全部中文（新建 `PERMISSION_LABELS`，与后端 `PERMISSION_GROUPS` 对齐）；保护项带「受保护」徽标；管理员打开矩阵时只显示可编辑的列并提示范围；保存受保护变更先弹确认。
7. **权限与技能区分**：角色页只谈「权限」；技能管理入口仍在自生长管理（`skills:manage`），角色页文案注明「技能是能力包，不在此处分配」。

### 2.3 工作台视图（原「导航菜单 DIY」升级）

1. **命名**：设置页签改名「工作台视图」，副标题「不同角色看到不同的工作台」；DIY 编辑器入口保留在侧栏。
2. **三档模式**（内置模板，可再改）：
   - 极简：概览 + 对话 + 当前角色最常用的 2–3 项；
   - 标准：默认 4 组（现状）；
   - 全功能：所有该角色有权限的菜单。
3. **按角色预设**：新增 4 套预设「负责人视图 / 管理员视图 / 专家视图 / 普通成员视图」，按该角色有效权限生成；owner/admin 可在设置里选「本角色默认视图」。
4. **后端持久化（按角色）**：新增设置项 `menu_profiles`（`{role: {mode, groups}}`）与接口：
   - `GET /yards/current/menu`：返回当前用户应得的菜单（角色预设 + 本人 DIY 覆盖 + 权限过滤），登录后即可用；
   - `PUT /yards/current/menu/profiles`：owner/admin 设置各角色默认视图（`menu:manage`）；
   - 本人的 DIY 仍可本地覆盖（保留 localStorage），并提供「恢复角色默认」。
5. **功能池**：编辑器右侧「可用功能池」按权限组展示**全部角色可见功能**（含自定义角色），标注所需权限；owner 可勾选授予角色（直接跳角色矩阵并预选），拖拽进菜单时若当前角色无权限则提示「先授权」。
6. **降级**：旧 localStorage 菜单自动迁移为「本人覆盖」，不影响已有用户。

### 2.4 验收（A）

- 用 viewer 登录：看不到设置/成员/设备写/自生长等菜单与按钮；直接访问 URL 得到 403 页。
- owner 把「设备」勾给 expert 后，expert 刷新即见；撤销后即隐藏。
- owner 改受保护权限：不确认 → 400 `PROTECTED_CONFIRM_REQUIRED`；确认后成功并留审计。
- admin 打开角色矩阵：只能编辑 expert/operator/viewer/自定义角色，看不到 yard_admin 编辑按钮。
- 成员行「单独授权」：勾选受保护项弹确认，保存后该成员 `/auth/me.permissions` 变化，菜单/页面即时可见。
- 工作台视图：三档模式与 4 套角色预设可切换；owner 保存「专家默认视图」后，专家账号首次登录看到该视图；功能池包含自定义角色的功能。
- 门禁：后端 pytest 新增 ≥10 条；前端 vue-tsc/build；E2E 新增 `v324.spec.ts`（viewer 隐藏、逐人授权、受保护确认、角色预设）。

---

## 3. B 批（3.25.0）：模型配置重构与工具/MCP

### 3.1 模型绑定（后端）

1. 新表 `llm_bindings`（迁移 `0051_llm_bindings`）：`yard_id`（空=平台级）、`scope_type`（global|purpose|agent）、`scope_key`（purpose 名或 agent_id）、`supplier_id`、`model_name`、`priority`、`enabled`，唯一约束 `(yard_id, scope_type, scope_key, supplier_id)`。
2. **解析顺序**：Agent 指定 → 能力（purpose）→ 全局默认 → 小院 active → 平台优先级 → settings 兜底；命中不可用（停用/失败超阈值）自动落到下一档。
3. **兜底链**：同 scope 可配 1–3 个供应商；运行时对超时/5xx/限流重试一次并切下一供应商，成功/失败都写 `consecutive_failures`；连续失败 ≥5 自动降级（不再参与选择，页面可一键恢复）。
4. `llm_calls` 补 `supplier_id/purpose`（迁移内加列），成本可按供应商/能力归集；`VALID_PURPOSES` 与真实写入值对齐。
5. 接口：`GET/PUT /llm/bindings`（global/purpose/agent 三类）、`GET /llm/capabilities`（能力清单中文名）、`POST /llm/bindings/test`；权限 `llm:manage`（owner/admin）。

### 3.2 模型中心（前端）

1. 侧栏新增「小院管理」分组：**模型中心**（合并原「模型配置」+「模型供应商」）、成员与角色、系统设置、单点登录、协作方、演示数据。
2. 模型中心一页三块：
   - 供应商列表：状态、延迟、打码 Key、连通测试、启停、编辑、删除；
   - 全局默认与兜底：默认供应商 + 兜底顺序（拖拽），显示当前解析结果「这次对话会用谁」；
   - 能力绑定：表格列出 20+ 能力（对话规划/续写/补章、文档、班组、诊断、总结、技能升级、工具生成、知识向量化…中文名），每行可选供应商+模型；「按 Agent 绑定」页签列出助手及其绑定，支持在此改。
3. **实时性**：保存后后端清缓存，前端立即重拉状态；页面顶部显示「配置生效时间/当前解析」；支持「测试当前链路」。

### 3.3 工具与 MCP

1. 后端：`GET /tools/{code}` 详情（含 input_schema 渲染用、风险、所需权限、来源 builtin/mcp/custom）；`POST /tools`（手填草稿）、`PUT /tools/{code}`、`POST /tools/{code}/enable|disable`；`POST /tools/generate` 一句话生成（LLM 产出 name/description/input_schema/risk，落 `enabled=false` 草稿，人工确认后启用）；权限 `tools:manage`。
2. MCP 工具并入 `GET /tools`（`source=mcp`、只读，启停走 MCP 服务器）；工具列表显示来源徽标。
3. 前端「可用工具」：右上角「添加可用工具」→ 两个入口：「一句话添加」（输入用途，预览草稿→确认）与「手动填写」；点击工具名打开详情抽屉（能做什么、参数表、风险、所需权限、调用示例、最近调用记录）；MCP 工具标「来自 MCP 服务器」。
4. 对话/助手的工具选择器按权限过滤：只有 `tools:execute` 的人能用写类工具；风险等级在 UI 明示。

### 3.4 验收（B）

- 配置全局默认 + 兜底 2 个；停用默认后，下一次调用自动落到兜底并记录。
- 给「文档生成」能力单独绑另一个供应商；生成文档走新供应商（测试断言记录）。
- 给某 Agent 绑模型，优先级高于能力绑定。
- 一句话添加工具：生成草稿（未启用）→ 启用后出现在工具列表并可在助手中勾选；非法 schema 拒绝。
- MCP 工具出现在工具列表并可查看详情。
- 门禁与 E2E（`v325.spec.ts`：模型中心改默认、兜底生效提示、工具详情/一句话添加）。

---

## 4. C 批（3.26.0）：演示数据与动态前端配置

### 4.1 演示数据（演示科技小院）

1. **SSO 成功演示**：新增「演示身份提供方」（`demo_mode=true`，仅 `DEMO_MODE` 下可建）：`/sso/demo/authorize` 本地签发演示 id_token（同一套验签与 JIT 逻辑），登录页出现「演示单位账号登录」；种子建一条 `演示单位（可删除）`，设置页可编辑/删除；关闭 `SSO_ENABLED` 时不出现。
2. **A2A 演示协作方**：种子建 `演示协作方（本机）`，地址指向本平台入站端点，令牌加密、卡片快照预置；`A2A_ENABLED=false` 时标「未启用」，可一键探测/删除。
3. **MCP 演示服务器**：内置 `演示 MCP`（`/api/v1/_demo/mcp`，最小 JSON-RPC：initialize/tools/list/tools/call），提供 2 个只读工具（小院今日概况、知识库统计）；种子登记并测试通过，工具可在对话中使用；可删除。
4. 种子幂等：重复 `seed-dev` 不重复建；生产守卫不变。

### 4.2 前端配置动态化

1. 新增设置项 `frontend`：`{mock_mode, demo_hints, default_home, show_build_info}`；`GET /api/v1/meta` 下发公开子集（不含敏感），登录页/概览读取。
2. **Mock 运行时切换**：`api/client.ts` 的 adapter 从「构建期常量」改为「运行时读 store（默认取 env）」，设置页开关保存后即时生效并提示「演示数据，不会写库」。
3. 其他动态项：登录页演示账号提示、概览演示提示、默认首页（概览/对话/台账）、构建信息展示；全部可在设置页修改。
4. 「前端配置」页签改为可编辑表单 + 当前生效值 + 重置。

### 4.3 验收（C）

- 演示小院登录页出现「演示单位账号登录」，走完回调成功进入（角色按映射）。
- 演示协作方可探测成功；MCP 演示服务器测试通过、工具可列出。
- 三者均可编辑/删除，删除后不复活。
- 设置里打开 mock：接口不再请求后端（网络面板为空），关闭后恢复；默认首页修改后刷新即生效。
- E2E `v326.spec.ts`（演示 SSO 登录、演示 MCP 工具可列、mock 开关）。

---

## 5. 数据与接口变更清单

| 批次 | 迁移 | 新增/变更 |
|---|---|---|
| A | 无（复用 YardSettings / YardMemberGrant） | `/auth/me+permissions`、成员权限 GET/PUT、`menu_profiles` 设置项、`GET /yards/current/menu`、`PUT /yards/current/menu/profiles`；权限点补 12 个 |
| B | `0051_llm_bindings`（+ `llm_calls.supplier_id/purpose`） | `/llm/bindings`、`/llm/capabilities`、工具详情/新增/启停/一句话生成 |
| C | 无（SSO 加 `demo_mode` 列则需 `0052_sso_demo`） | `/sso/demo/authorize`、演示 MCP 端点、种子扩展、`frontend` 设置项与 meta 字段 |

**错误码新增**：`PROTECTED_CONFIRM_REQUIRED`(400)、`ROLE_EDIT_FORBIDDEN`(403)、`MENU_PROFILE_INVALID`(400)、`LLM_BINDING_INVALID`(422)、`LLM_NO_USABLE_SUPPLIER`(503)、`TOOL_DRAFT_INVALID`(422)、`DEMO_MODE_DISABLED`(404)。

---

## 6. 风险与边界

- **权限收紧的风险**：新增权限点会让存量自定义角色「少权限」。策略：迁移时把新权限点按角色默认补齐到系统角色；自定义角色缺的新点按「只读/使用」保守补齐，并在角色页提示差异。
- **管理员权限边界**：管理员可管 expert/operator/viewer/自定义角色，但**永远不能**给自己或他人加 `members:manage_admins`、`settings:manage_protected` 等保护项；后端为准，前端只做提示。
- **菜单后端化**：先做「角色默认视图」持久化，本人 DIY 仍本地覆盖；不做跨设备同步（后续可加）。
- **模型兜底**：只在超时/5xx/限流切换一次，避免成本失控；每次切换都记录，页面可查。
- **演示数据**：仅演示小院、仅 `DEMO_MODE`；生产守卫沿用 `seed.py:200-209`，绝不进生产。
- **诚实边界**：SSO 演示是本地签发，不连接真实 IdP；MCP 演示是最小协议实现，不代表全部 MCP 能力；这些都在页面标明「演示」。

---

## 7. 交付节奏

- A（3.24.0）：后端权限与菜单接口 + 前端全局权限化、成员授权、工作台视图 → 门禁 + E2E → 本地提交。
- B（3.25.0）：模型绑定与兜底 + 模型中心 + 工具/MCP 升级 → 门禁 + E2E → 本地提交。
- C（3.26.0）：演示数据 + 动态前端配置 → 门禁 + E2E → 本地提交。
- 每批完成后更新 CHANGELOG、契约（OpenAPI/错误码/API 文档）、状态报告；推送与标签按你当次指令执行。

---

## 8. A 批交付记录（3.24.0，2026-10-29）

已落地（与第 2 节设计一致）：

- 权限点 33 → 45，全部中文名（`PERMISSION_LABELS`）与分组补齐；新增单测 `tests/unit/test_permission_catalog.py` 守住「中文名全覆盖、分组不重不漏、可授权范围」。
- `GRANTABLE_PERMISSIONS` = 全部权限 − `owner:transfer` − `members:manage_admins`；`role_permissions` 不再硬剥离保护型权限（改由路由层按「负责人 + 二次确认」裁决），角色服务保留 `allow_protected` 兜底。
- 角色边界：`can_manage_target_role` 扩到平台管理员与自定义角色；管理员可维护专家/操作员/普通成员与自定义角色，禁改管理员/负责人，禁配保护型权限（`ROLE_EDIT_FORBIDDEN` 403）；负责人配置保护型权限必须 `confirm_protected`（`PROTECTED_CONFIRM_REQUIRED` 400）。
- `/auth/me` 下发 `permissions / is_owner / yard_id`；`/yards/current/settings|roles` 下发 `permission_labels / protected_permissions / grantable_permissions / can_edit`，`assignable_roles` 含自定义角色。
- 逐人授权：新增 `permissions_router.py`（GET 需 `members:manage`，PUT 仅负责人，含保护型确认与审计 `member_permissions_updated`），前端成员管理每行「权限」弹窗。
- 工作台视图：新增设置项 `menu`（校验 `MENU_PROFILE_INVALID`）、`GET/PUT /yards/current/menu`；前端「导航菜单 DIY」改名并加三档密度 + 7 套内置视图 + 全量功能池 + 系统设置「工作台视图」页签。
- 千人千面：菜单原子项与路由 `meta.permission` 全量补齐，`MainLayout` 按 `auth.hasPermission` 递归过滤，无权限直连落到 `/forbidden`。
- 门禁：后端全量 pytest 通过（新增 10 条、按新政策调整 2 条旧断言）、ruff/mypy 全绿；OpenAPI 251 路径两次生成一致；前端 lint/type-check/单测/构建全绿；E2E 新增 `e2e/v324.spec.ts` 4 条并跑全量。

未完事项（转入后续批次）：

- B 批（3.25.0）：模型配置与供应商迁入「小院管理」、兜底/全局默认/按 Agent 与按能力绑定；工具详情预览、「添加可用工具」（手动 + 一句话）。
- C 批（3.26.0）：演示小院 SSO/A2A/MCP 演示数据；前端配置动态化（含 mock 开关）。
- 按钮级权限目前覆盖高频入口（成员、设置、设备、政策等页面入口与操作已按角色收敛），后续按页面逐个补 `v-if="auth.hasPermission(...)"`。

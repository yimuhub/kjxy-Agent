# 变更日志（CHANGELOG）

本项目遵循《工程开发规范 V3.0》；版本号遵循语义化版本。

## [3.16.0] - 2026-10-28

对话页批量交互重做、全站弹窗统一从屏幕中央弹出、错误提示不再误报「网络连接失败」，侧边栏补上版本号。

### 对话页批量交互（前端）

- 批量条改成上下两行：上行「全选 + 已选 N」，下行四个等宽动作按钮（归档 / 移回 / 置顶 / 删除），按钮 `flex:1` 均分宽度；实测容器 `scrollWidth == clientWidth == 200`，不再溢出。
- 删掉「未读」批量动作：未读只是个人提醒标记，批量置未读没有实际意义；五个按钮在 200px 宽的侧栏里排不下正是溢出的根因。
- 「进行中 / 已归档」换成 `el-segmented` 分段控件，「批量」换成带图标的圆角胶囊（进入后文案变「退出」），三者统一到同一套圆角、描边与高度，不再各长各的。
- 多选框重做：改成自绘的原生 `checkbox`（16px、圆角 5px、accent 底色 + 白色对勾 SVG），绝对定位贴在行内左侧，行左侧内边距 32px 避让；选中行加描边与淡底色，`:focus-visible` 有可见焦点环。

### 全站弹窗统一从屏幕中央弹出

- 排查出全站 **13 处 `el-drawer`**（右侧滑出）全部改写为 `el-dialog`，覆盖会话审批、设备数据、资产、知识库、成员、策略、评审、诊断、工单、班组等页面；仓库内已无 `el-drawer` 残留。
- 全局样式里 `.el-overlay-dialog` 改成 flex 容器、面板 `margin:auto`：auto 外边距吸收剩余空间即水平与垂直同时居中；内容比视口高时 auto 归零，退回顶部对齐并随遮罩滚动，不会把顶部顶出屏幕。
- 实测：「新建设备」弹窗 640×445 落在 760×593 视口里，中心点 (380,297) 与视口中心完全重合；高过视口的弹窗（如 DIY 侧栏编辑器）按顶部对齐且可滚动。
- MessageBox（`.el-overlay-message-box`）走 Element Plus 自带的居中容器，不受影响。

### 「网络连接失败」误报的根因与修法

- 根因：FastAPI 自身的 404/405 抛的是 `{"detail": "..."}`，与项目自有的 `{"error": {...}}` 信封不是一套；前端拿到没有 `error.message` 的响应就一律归到 `NETWORK_ERROR`，弹「网络连接失败，请检查网络后重试」。用户实际撞上的是自己 `:8000` 上的旧进程没有 `/chat/sessions/batch` 路由（405）。
- 后端注册 `StarletteHTTPException` 处理器，404/405/401 等一律走统一信封：`{"error": {"code", "message", "details"}, "request_id"}`。
- 前端 `classifyResponseError` 按状态码归类：读到 `error.message` 用它，读到 `detail` 用它，都没有就按 HTTP 状态码给映射文案；只有请求真的发不出去才算 `NETWORK_ERROR`。
- 新增 `METHOD_NOT_ALLOWED` 等映射文案：404/405 现在明确提示「当前后端版本不支持该操作，请重启后端服务后再试」，不再把服务端的错甩锅给网络。
- 新增用例：后端 `test_error_envelope.py`（404/405/401 三条）、前端 `status.test.mjs`（8 条）。

### 侧边栏版本号

- 左侧菜单栏右上角加版本号胶囊（`v3.16.0`），版本号直接读 `package.json`（具名导入，打包只带出 `version` 一个字段），不再手抄字符串，避免各处版本号漂移。
- 本轮版本号 3.15.0 → 3.16.0；九处版本面（pyproject / uv.lock / frontend package.json / desktop package.json / Cargo.toml / Cargo.lock / tauri.conf.json / backend main.py / openapi 契约）由 `scripts/check_versions.py` 统一校验。

### 「点新对话后立刻发消息，消息落到上一个会话」（前端）

- 现象：点「新对话」后马上按回车发消息，消息被发到**上一个会话**；面板随后切到新会话并清空消息，用户看着像「消息凭空消失」，而输入框里草稿还在。
- 根因：建会话要一个来回。这段窗口里 `activeId` 仍指向上一个会话，`send()` 读到的就是旧 id——把消息发进了上一段对话。
- 修法：store 用 `creatingSession` 把这段窗口的承诺挂出来，`send()` / `sendQa()` / `uploadAttachment()` 在读 `activeId` 之前先 await 它，消息才落进刚开的会话。建会话失败时这次**不发**（草稿留在输入框可重发），绝不把消息丢进旧会话。
- 顺带修一处抢焦点：`newSession()` 不再无条件覆盖 `activeId`——等待期间用户已经切到别的会话，就尊重他的选择，也不清空他正在看的消息。

### 门禁

- 后端 ruff check/format（434 个文件）、mypy（292 个源文件）全绿；全量测试 **1446 passed**。
- 前端 eslint **0 error**、vue-tsc 全绿、单测 **34 passed**、`vite build` 通过；版本面 9 处一致（`scripts/check_versions.py` 校验 3.16.0）。
- 契约重新生成：OpenAPI 222 路径，相对 v3.13 只增 `/chat/sessions/batch` 与 `/chat/sessions/{id}/fork` 两条，无路径删除。
- 全量 E2E **42 passed**（6.6 分钟）：含新增 `e2e/chat-session-ops.spec.ts`（三点菜单、批量、筛选、权限档位、长列表窗口化、回答分支与引用）与 `e2e/chat-plan.spec.ts`、`e2e/chat-session-time.spec.ts`。
- 修掉一条**每天必挂 8 小时**的用例：`test_workorders.py::test_escalate_and_overdue_scan` 用本地日历日算「昨天」，而工单到期日、报表时间窗都按 UTC 业务日划分（`created_at` 存的是 UTC，报表窗口必须落在 UTC 边界上），东八区本地 00:00–08:00 期间两者差一天，逾期扫描恒扫不到单。用例改用 UTC 昨日，UTC/本地两种口径下都稳定判为逾期。
- E2E 自身的两处定位竞态一并修掉：会话 id 从新建接口的回包取（不再靠 DOM 差异猜）、残留会话改走批量删除接口清理（窗口化渲染看不到的那部分也能清掉）。

## [3.15.0] - 2026-10-28

对话列表与回答的操作补齐：会话可置顶/归档/未读/批量管理，回答可就地分支与引用，输入框带上本会话的操作权限档位。

### 会话管理（后端）

- 迁移 `0043_chat_session_ops`：`chat_sessions` 增加 `pinned_at/archived_at/unread/approval_mode/parent_session_id/parent_message_id`（含索引）；`tool_approvals` 增加 `chat_session_id`，审批单从此归属到具体会话。可完整 downgrade。
- `PATCH /chat/sessions/{id}` 改为部分更新：标题、置顶、归档、未读、权限档位任选一项或多项（一个都不给则 422）；标记位变化写 `chat_session_flags_updated` 审计。
- `POST /chat/sessions/batch`：`pin/unpin/archive/unarchive/read/unread/delete` 七种动作，单次 1–200 个 id；只处理调用者本人的会话，不存在或越权的 id 静默跳过（不泄露他人会话是否存在），响应回 `affected/missing/deleted`。
- `GET /chat/sessions?archived=true|false` 支撑「进行中 / 已归档」两个视图；排序改为**置顶恒在最前**（按置顶时刻倒序），其余按最近更新。
- `POST /chat/sessions/{id}/fork`：从某条回答处分叉——复制该条及之前的全部消息（步骤、引用、token 统计，附件只共享同一 `storage_key` 不复制物理文件），标题「原名 · 分支」，记录 `parent_session_id/parent_message_id`，原会话不受影响。
- 列表序列化收敛：`list_sessions` 改走 `sessions_out_bulk`，296 条会话的列表从 ~900 条 SQL 降到 4 条；并有用例断言批量输出与逐行 `session_out` 完全一致，防止两处字典结构漂移。

### 会话操作权限档位（三档，按会话设置）

- `ask`（默认）：高风险工具照旧挂审批单——与历史行为逐字节一致，非对话入口（任务、机器人、脚本）不受影响。
- `auto`：**同一工具 + 同一参数指纹在本会话内已被人工批准过**则自动放行（不再受 `tool_approval_max_uses` 次数限制，仍受 TTL 约束），高风险首次仍然询问。
- `full`：本会话内高风险工具直接执行、不产生审批单。仅小院/平台管理员或专家可开；其他角色请求该档会被服务端降级回 `ask`，并在响应里如实标 `approval_mode_downgraded`。
- `auto/full` 的每一次放行都写 `tool_risk_auto_approved` 审计（含 `bypass` 来源与会话 id），审批单也记录 `chat_session_id`，事后可追溯。
- 本轮被风险闸门拦下的工具，会在回答里生成 `approval` 卡片，写清工具名与「去批准」入口（审批动作仍在 `/tasks` 完成，不在此处放行）。

### 会话列表（前端）

- 右侧原来唯一的删除键升级为 **⋮ 菜单**：置顶/取消置顶、重命名、标记未读/标记已读、归档/移回进行中、新窗口打开、删除。置顶行带图钉、未读行标题加粗并在时间后点圆点、分支会话带角标。
- 列表顶部「进行中 / 已归档」分段筛选 + **批量模式**（勾选、全选、归档/移回/置顶/未读/删除）；批量结果按服务端 `affected` 先落本地标记再刷新。
- 修掉一个真实竞态：会话被归档或删除后，原来「本地刚建、服务端还没回」的守卫会把它重新塞回列表并把当前会话切走；现在守卫要求该会话未归档，批量操作也不再依赖整表重拉。
- 三点菜单的弹出层按会话 id 打上专属 class，避免上一个菜单还在淡出时按名字点菜单项撞出两个元素（E2E 与真实点击都因此前不稳定）。

### 回答与输入框（前端）

- 回答气泡的动作从「复制 / 有用 / 有问题」扩到五个：**从这条回答分支**（就地另开一个只到这条回答为止的新会话）与**引用这条回答**（以 Markdown 引用块贴进输入框，最长 600 字并注明来源，光标落到末尾）。
- 输入框左侧新增操作权限档位胶囊 `<ChatApprovalPicker>`：每次询问 / 会话内自动放行 / 完全访问，非管理员看到禁用项与禁用原因；胶囊显示的是服务端回包的**生效值**，被降级时给出提示而不是静默采纳请求值。

### 卡顿的真相与修法（前端性能）

实测口径：演示账号 308 条会话，CPU 降频 4 倍模拟真实机器，量「输入→两帧后绘制」的阻塞时长。

- **一次点击重画整表**：会话行是写在 `v-for` 里的内联模板，任何一行读到的状态（选中态、当前会话、批量开关）变化都会让 ChatView 的一个渲染效应重跑整表 300+ 行，连同每行自己的下拉菜单。**改成行级 `v-memo`**（依赖只列该行渲染读到的字段）。实测：进入批量 1856ms → **19ms**，勾一条 1665ms → **28ms**。
- **每行常驻一整套下拉菜单**：Element Plus 的 dropdown 默认 persistent，60 行就预先渲染 62 个菜单 / 366 个菜单项。**改为 `:persistent="false"`**，菜单内容只在真正展开时存在；副作用是收起即从 DOM 移除，陈旧弹层撞菜单项的问题一并消失。全选 1754ms → **50ms**，DOM 节点 10272 → **1919**。
- **整表挂载**：列表一次渲染全部会话，长列表的首屏与每次整表更新都是常数级以上的代价。**改为窗口渲染**（首屏 60 行，滚到哨兵再放下一页），并把「模型里的真实条数」暴露在容器属性 `data-session-total` 上，测试与断言不再依赖 DOM 行数。
- **数组当选中集**：`batchIds` 是数组，每行每次渲染都做一次 `includes`，全选态判断再扫一遍，一次点击放大成几万次数值比较。**改为 Set**（`isBatchPicked` / `setBatchPicks` / `batchCount`），并把 `batchIds` 从 store 的公开面收掉，避免再被当成数组用。
- **排序里现算时间**：`visibleSessions` 的比较器每次比较都重新 `parseApiTime`，排序放大成 O(N log N) 次 Date 构造。**每行只取一次排序键**，并新增带上限的 `apiTimeMs()` 时间戳缓存（同一条时间串反复读几百次不再重复解析），配套单测钉住缓存与解析口径一致。
- **点一下要等一个来回**：置顶/归档/未读/重命名改为**乐观更新**（本地先落，服务端回包校准，失败整体还原）；`approval_mode` 刻意不参与抢跑，显示的仍是服务端生效值，因为它可能被按角色降级。批量删除当场从列表消失，不再等整表回读。
- **切换视图失败会停在半路**：筛选已切到「已归档」而列表拉取失败时，界面会显示另一批数据。现在回滚筛选并重新拉回原视图，同时如实抛出错误。
- **乐观消息的 id 撞车**：`local-${Date.now()}` 同毫秒连发会重复，重复 v-for key 的补丁行为不保证。改为实例内自增。

### 审批归属与删除时机（后端）

- **一纸审批不再跨会话外借**：0043 给审批单加了 `chat_session_id`，但兜底放行查询没看归属——在 A 会话批准过的同一调用，B 会话、任务、脚本都能直接借用，等于把「按会话批准」悄悄放宽成「一处批准处处可用」。现在带归属的单只在其所属会话内生效；**没有归属的历史单与任务侧单保持全院通用**（既有契约不变），并补了三条用例分别钉住这两种边界。
- **免批准放行的留痕写在执行之后**：原先 `auto/full` 的旁路留痕在工具执行前就写 `result=success`，一次跑挂的全自动调用会被记成「成功旁路」。现在留痕带真实结局（`executed_ok` / success / failure），`full` 档不产生审批单，这两条留痕是唯一书面证据。
- **删会话不再在事务里删文件**：批量删除 200 个会话时，附件的物理文件在所有 DB 删除尚未提交时就被 unlink，一旦回滚就留下「行还在、文件没了」的永久损坏。现在待删存储键只登记（`file_sink`），由路由在 `db.commit()` 之后统一清理；分支共用同一份文件的引用判定也压成一条分组查询。
- **列表统计不再撞绑定变量上限**：`_bulk_message_stats` 与批量序列化把会话 id 的 `IN` 分批（每 500 条），几百上千条会话时不会因为 SQLite 的变量上限直接报错；损坏的 `token_usage_json`（非 dict 也非空）在列表与详情两条路径统一按 0 计，不再出现两处总数对不上。
- **0044 复合索引**：会话列表按 `(yard_id, created_by, archived_at, pinned_at, updated_at)` 收窄，配合「进行中/已归档」两个视图；纯增量迁移，回滚即删索引。
- **审批卡片不再互相顶掉**：本轮既产出方案/文档卡片又拦下高风险调用时，审批项以 `payload.approvals` 附在原卡片下面（前端渲染成紧凑审批条，审批单号仍可点），不再只留一条步骤痕迹。

### 门禁

- 后端新增 `tests/integration/test_chat_session_ops.py`（9 条：置顶/归档/未读/批量七动作、分支复制消息与附件共享、权限档位三档、审批归属不跨会话外借）与 `tests/unit/test_chat_approval_modes.py`（14 条：ask/auto/full 三档放行语义、角色降级回 ask 并如实标记、免批准留痕带真实结局、次数限制与 TTL 边界）。
- 迁移 `0043_chat_session_ops` / `0044_chat_session_view_index` 均可完整 downgrade；`sessions_out_bulk` 与逐行 `session_out` 的输出一致性有用例钉住，防止两处字典结构漂移。
- 前端 `src/utils/format.test.mjs` 增补 `apiTimeMs()` 时间戳缓存用例（连读同一时间串不再重复解析，且与 `parseApiTime` 同口径），`test:unit` 一并执行。
- 新增 `e2e/chat-session-ops.spec.ts`：三点菜单七个动作、批量归档/移回/置顶/删除、筛选切换、权限档位、长列表窗口化、回答分支与引用；控制台零报错。
- 本节各项门禁与 v3.16.0 一并在同一棵工作树上跑通（后端 1446 passed、全量 E2E 42 passed、前端 lint/type-check/构建/单测全绿、版本面 9 处统一）。

## [3.14.0] - 2026-10-28

对话内方案收口：「写一份…方案」不再被打包成文件只剩一句话，改为多源合成的完整方案。

### 方案链路（后端）

- 意图分流新增 `plan`：命中方案名词（方案/规划/技术路线/实施计划…）即走方案合成；显式点名文件格式或文件动作（Word/表格/导出/文件…）仍按文档链路产出附件，疑问句仍降级为提问。
- `chat/plan.py` 多源取证合成：知识库检索（top_k=8）优先，命中不足 `PLAN_KB_MIN_HITS` 条时自动联网补公开资料（`collect_research` 新增 `web=auto/skip`，联网失败只降级为告警、绝不编造来源）；并按关键词引用本小院已发布技能的 SOP 指令块（不可信内容包裹，只查本地库不新装能力）。
- 完整性契约：八章结构（目标与适用条件/依据与来源/实施步骤/时间与用量安排/风险与安全间隔/成本与投入/验收指标/待确认事项），缺章或章节写得太浅自动补写一轮（`PLAN_REPAIR_PASSES`），仍不齐就在 `payload.missing_sections` 与告警里如实标注并置 `needs_expert_review`；一个字都没生成时按 `PLAN_EMPTY`(502) 失败处理，不落库一条空的「完整方案」。
- 引用纪律：正文用 [K#]/[W#] 标注，引用列表与编号一一对应可点开溯源；无出处的数值必须写「（模型推断，需当地核实）」；用药只可引用证据中已登记的药剂与安全间隔期，未登记的写「需专家确认」。
- 长度治理：方案成稿显式下发 `PLAN_MAX_TOKENS`（默认 6000），被供应商上限截断（`finish_reason=length`）时自动续写一次；单次生成超时按 `PLAN_TIMEOUT_SECONDS`（默认 180）放宽，`LLMRequest` 支持按请求携带超时；助手正文落库上限从 8000 放宽到 20000 字，避免完整方案被输入侧上限静默切掉章节。
- 路由徽章：`routed_by=plan` → 「多源方案」/success；`reason` 直接写明「知识库 N 条 · 联网 N 条 · 技能 N 项」。

### 方案卡片（前端）

- 方案正文按 Markdown 分节渲染（复用无脚本的 SafeMarkdown），消息里不再出现裸的 `##`；引用列表显示 [K1]/[W1] 编号，与正文标注对得上。
- 新增 `<PlanCard>`：完整性状态（章节齐全 / 待补 N 处）、取证统计（含「证据充足未联网」这类跳过原因）、缺口与告警清单，以及两个动作。
- 导出为文档：分节正文与引用在前端拼成 DocModel，复用既有 `POST /chat/sessions/{id}/documents` 产出 Word 并直接下载，导出文件末尾附「依据来源」清单，不新增接口。
- 转班组会诊：把同一个问题交给班组接力（多成员 + 检索融合），复用 `GET /agents/teams` 与 `POST /agents/teams/{id}/run`，没有可用班组时给出明确指引。

### 会话时间显示修复

- 后端各表的 `created_at/updated_at` 是 **UTC 裸时间**（`isoformat()` 不带 `Z`/偏移），浏览器 `new Date()` 按本地时间解析，国内环境所有列表时间整体早 8 小时。新增 `parseApiTime()` 统一补 UTC 标记（已带时区的原样解析、纯日期不补），`formatTime()` 与工作台「最近动态」全部改走它。
- 对话左侧列表改为显示**建会话那一刻**的 `created_at`（原来显示的是最后一条消息时间），格式统一为 `年-月-日 时:分`：不再出现「刚刚 / N 小时前」，也不再出现只有月日、没有时间的写法。
- 工单、农事台账、诊断、审核四个页面各自复制的时间格式化函数收敛到共用 `formatDateTime()`，与列表口径一致（同样修掉早 8 小时）。

### 门禁

- 后端新增 `tests/unit/test_chat_plan.py`（引用落库、提示词契约、缺章补写、截断续写、无证据转专家复核、长正文不被切、@ 点名时不抢链路、历史进提示词、空正文失败）与 `test_chat_intent.py` 方案分流用例；`test_llm_suppliers.py` 覆盖按请求放宽超时。
- 前端新增 `e2e/chat-plan.spec.ts`：真实模型跑通「徽章→分节正文→方案卡片→导出 .docx→转会诊弹窗」，并验证点名 Word 格式时不误入方案链路；控制台零报错。全量 E2E 一次跑绿（顺带把 v3.13 知识问答用例的 90 秒等待放宽到 180 秒——真实模型单轮问答会超过 90 秒，慢答案被误判成没答案）。
- 新增 `e2e/chat-session-time.spec.ts`（不依赖模型，2 秒）：新建对话后列表时刻等于本机当前时间且格式为 `年-月-日 时:分`；`src/utils/format.test.mjs` 6 条用例锁住裸 UTC 补时区、已带时区不重复补、纯日期不补、列表格式与耗时计算，`test:unit` 一并执行。
- 本机真实供应商验证：同一问句改版前 `routed_by=local`、正文 58 字「已生成Word 文档…」；改版后 `routed_by=plan`、正文 2000+ 字、八章齐全、引用 K1/W1、技能 1 项。
- 后端 ruff/mypy/全量测试（1414 passed）、迁移与 OpenAPI 契约（220 路径无漂移）与前端 lint/type-check/构建/单测（19 passed）全部通过；版本面 9 处统一到 3.14.0。

## [3.13.0] - 2026-10-28

信息架构收口：问答并入对话、概览改成今日行动板、回答反馈回流（路线图 3.13）。

### 问答并入对话

- 独立「问答」页面与左侧菜单入口下线：`/qa` 保留为重定向，带原问题进入对话的「知识问答」模式（`/chat?qa=1`），站内不再有两套问答入口。
- 对话新增通用对话 / 知识问答双模式：问答走 `POST /chat/sessions/{id}/qa`，检索条数 1–20 可调（默认 5），回答按 `routed_by=qa` 落库、来源徽章为「知识问答」、引用标记 `kind=knowledge`；无命中时如实说明并建议提交专家复核，不编造答案。
- 问答模式收敛与问答无关的输入通道：附件与 @ 点名禁用并有文案说明，历史与通用对话同表可回放。
- 首页按角色分流：负责人/管理员/专家落在「今日工作台」，科研人员与成员进门即对话。

### 概览改为今日行动板

- 顶部问候与一句话待办摘要（工单逾期、诊断待跟进、未记录地块），快捷动作直连每天真实要做的事：记农事、拍照诊断、上报告警、开始对话、生成本周报表。
- 四张数字卡可点进对应列表（成员看自己的工单，管理员看待处理全集）；三块面板分别给田间运行（含工单闭环率）、知识资产（文档总数/解析失败/待审核候选）、用量与配额进度。
- 「最近动态」合并工单、诊断与农事记录成一条时间线；移除平台级统计块与硬编码引导步骤，各面板独立呈现加载/空/错误三态并可重试。

### 回答反馈回流

- 每条回答新增「有用 / 有问题」评分与纠错输入：评分进反馈队列，纠错自动生成待审核知识候选，只有专家审核通过才合入知识库。
- 反馈正文自带原问题、会话号与来源徽章以及被纠正回答的摘要，审核列表可对号入座；同一条消息在本次会话内不会重复提交。

### 门禁

- 新增后端对话内问答测试（落库、引用、无命中诚实回执、越权 404、入参 400）与前端 v313 E2E（行动板取数与角色落地、菜单收敛、`/qa` 重定向、问答引用、点赞→纠错→待审核候选闭环）。
- 后端全量测试、mypy、ruff、迁移升降级、OpenAPI 契约（220 路径）与前端 lint/type-check/构建/单测全部通过；版本面 9 处统一到 3.13.0。

## [3.12.0] - 2026-10-28

多模态输入与跨院协作（合并路线图 3.11–3.12）。

### 语音输入与图片理解（3.11）

- 新增 `POST /chat/transcribe`：录音经当前生效供应商的 `/audio/transcriptions` 转写；音频只在内存中转发，不落库；按文件头校验 WEBM/OGG/WAV/MP3/M4A，默认上限 10MB。
- 用户未配置供应商或模型不支持时返回 `MULTIMODAL_NOT_CONFIGURED`(503) / `MULTIMODAL_UPSTREAM_ERROR`(502)，明确提示而不是伪造结果。
- 新增 `POST /chat/sessions/{id}/attachments/{aid}/vision`：用户手动触发图片理解（明确计费），结果写入附件文本并随消息进入上下文；提示词要求只描述可见事实、图中文字视为数据、不输出农药品种与剂量。
- 附件上下文对图片的说明同步更新：已识别则注入“不可信数据”边界内的描述，未识别则明确提示可手动识别，避免模型臆测。
- 新增 PWA 外壳：`manifest.webmanifest` + 生产环境 Service Worker（导航 network-first、静态资源 cache-first、`/api/` 永不缓存），离线可打开界面但数据仍需联网。
- 前端：对话输入区新增麦克风按钮（MediaRecorder → 转写 → 填入草稿），附件胶囊新增「识别图片」按钮与识别状态。

### 跨院只读分享（3.12）

- 迁移 0042 新增 `share_links`；分享为**创建时快照**，外部访问不再查询业务表，令牌只存 SHA-256，库中无明文 token。
- 新增 `/shares`（创建/列表/撤销，farm:read / farm:write 权限）与匿名 `GET /public/shares/{token}`（按 IP 限流 120 次/小时）。
- 报表分享在服务端重新生成并**隐去操作人姓名**；诊断分享只保留作物/品种/生育期/症状/候选/专家结论与免责声明，不含地块名、图片与人员。
- 撤销立即失效，过期与不存在统一 404；新增前端匿名只读页 `/share/:token`（安全 Markdown 渲染、可打印/存 PDF），台账页新增「对外分享」管理与撤销入口。

### 诊断会诊闭环（3.12）

- 新增 `POST /farm/diagnoses/{id}/consult`：把诊断升级为高优先级会诊工单（来源 `diagnosis` 回指诊断、默认 3 天截止、可指派专家），诊断状态收敛为「待专家复核」。
- 同一诊断已有未结束会诊工单时返回 409，处置结论走工单回执，避免会诊停在口头。
- 前端诊断详情新增「转专家会诊」与「生成只读分享」入口。

### 门禁

- 新增多模态校验/诚实降级、分享快照脱敏/哈希存储/撤销过期/小院隔离、会诊去重与 HTTP 权限测试；后端全量测试、mypy、ruff、迁移升降级、OpenAPI 契约与前端 lint/type-check/单测/构建全部通过。

## [3.10.0] - 2026-10-28

告警 → 工单 → 回执闭环：谁在什么时候处置了什么，全程可追溯（路线图 3.10）。

### 告警与转单

- 迁移 0041 新增 `farm_alerts` / `work_orders` / `work_order_events`；告警、工单与事件流全部按小院隔离。
- 新增 `/farm/alerts`（上报/分页筛选）与 `POST /farm/alerts/{id}/convert`；`critical` 告警自动创建紧急工单，同一告警重复转单返回 409，不允许高等级告警停留在“仅提示”。
- 新增 `/farm/work-orders*`：创建、分页筛选、详情（含完整事件流）、指派、状态变更、补充记录、回执、升级、可指派成员列表。

### 处置流程与升级

- 状态机白名单：`open→assigned/in_progress/cancelled`、`assigned→open/in_progress/done/cancelled`、`in_progress→done/cancelled`、`done→in_progress/closed`；非法跃迁返回 409，事件流只追加。
- 指派校验目标为在职成员，跨院成员与不存在用户一律拒绝；指派会带动 `open→assigned` 并留痕。
- 回执写入处置结论、关闭时间与回执事件；可“直接关闭”或“标记完成待验收”，重复回执已结束工单返回 409。
- 手工升级逐级提升优先级并记录升级次数；`urgent` 工单触发运维告警（按工单去重）。
- 新增 Celery beat 任务 `workorders.escalate_overdue`（每 6 小时）：截止日已过且未结束的工单自动升级一级，已紧急的工单不重复刷事件。

### 前端与门禁

- 新增「告警与工单」页面：工单/告警双 Tab、状态与优先级筛选、新建工单、上报告警（标注严重级别自动建单）、详情抽屉（指派/状态/升级/回执/记录时间线）。
- 新增告警自动转单、重复转单、指派校验、状态跃迁、回执幂等、逾期扫描、跨院 404 与 HTTP 权限测试；后端全量测试、mypy、ruff、迁移升降级与 OpenAPI 契约检查通过。

## [3.9.0] - 2026-10-28

作物诊断闭环：留档、用药护栏、专家复核与跟进（路线图 3.9）。

### 诊断留档与闭环

- 迁移 0040 新增 `crop_diagnoses` / `diagnosis_followups` / `pesticide_products`；诊断输入、候选问题、证据与置信度完整落库，不再是一次性输出。
- 新增 `/farm/diagnoses*`：生成并留档、分页筛选、查看详情、专家复核（`review:approve`）、跟进记录（`farm:write`）；跨院一律 404。
- 低置信度、涉及用药或症状较重时自动进入「待专家复核」；复核写入结论、意见、复核人与时间，状态收敛为已确认/已关闭。
- 图片引用只做留档并标注 `vision_status=not_configured`，明确提示“未启用图像识别”，不伪造识别结果；为后续视觉模型接入保留结构位。

### 用药护栏与安全间隔

- 诊断命中用药意图时返回 `medication_guard`：仅列出本院已登记且登记作物匹配的药剂与安全间隔期，要求专家确认，绝不生成品种/用量方案。
- 新增 `/farm/pesticides` 登记与 CRUD；`GET /farm/pesticides/{id}/safe-harvest?applied_on=` 按安全间隔期推算最早采收日，并注明以标签与当地监管为准。

### 前端与门禁

- 新增「作物诊断」页面：诊断列表与状态筛选、新建诊断、详情抽屉（候选/证据/风险/用药护栏/专家复核/跟进时间线）、药剂登记与安全间隔计算。
- 新增诊断留档、用药护栏、复核、跟进、小院隔离、安全间隔与 HTTP 权限测试；后端全量测试、mypy、ruff、迁移升降级与 OpenAPI 契约检查通过。

## [3.8.0] - 2026-10-28

农业台账：地块、种植周期与农事记录（路线图 3.8）。

### 数据与接口

- 迁移 0039 新增 `farm_plots` / `farm_cycles` / `farm_records`：地块（作物、面积、位置、土壤、灌溉、在种/休耕/归档）、周期（品种、播种/预计收获、生长中/已收获/失败）、记录（受控类型、日期、事项、详情、数量与单位、操作人、扩展 JSON）。
- 新增 `/farm/*` 接口：地块增删改查、周期增删改查、记录分页筛选（地块/类型/日期区间）、报表生成；全部强制小院边界，跨院 id 返回 404。
- 已有记录的地块/周期禁止删除，提示改为归档或状态收尾，避免台账断链；所有写操作写审计。
- 新权限点 `farm:read` / `farm:write`：管理员、专家、操作员可写，普通成员只读；自定义角色可单独调整。

### 报表与前端

- 日志/周报/季报由已录入记录确定性生成 Markdown：按日期、ISO 周或季度分组，附类型分布与地块数；空区间明确提示“没有农事记录”，不调用模型、不编造。
- 新增「农业台账」页面：记录筛选与分页、地块卡片、周期抽屉、记录/地块编辑弹窗、报表预览与 Markdown 下载；写操作按钮按角色收敛。
- 侧栏与路由新增「农业台账」，支持默认布局与高频直达模板。

### 质量门禁

- 新增台账服务与 HTTP 权限、小院隔离、报表分组与空数据测试；后端全量测试、mypy、ruff、迁移升降级与 OpenAPI 契约检查通过。

## [3.7.0] - 2026-10-28

可信证据、令牌安全与权限单轨（路线图 3.7）。

### 可信回答与可观测

- 聊天与群聊的助手消息持久化 `citations_json`（迁移 0037），前端在气泡下展示「依据来源（N）」；知识库片段与联网结果统一为标题/引用/链接，最多 20 条、单条截断 600 字，链接仅放行 http(s)。
- 班组运行结果透传研究来源，成员回答同样携带引用；旧消息无引用时返回空数组，不伪造。
- 负责人驾驶舱新增「回答引用覆盖率」与「7 日模型成本」：近 7 日带依据回答占比、模型调用次数/成功率/估算成本；无样本时显示占位而非 0。
- 任务配额扣减改为单条条件 `UPDATE ... WHERE used + amount <= limit`，并发下不超卖、不丢计数；批量扣减（如 amount=3）同样原子。
- 向量模型请求按 `EMBEDDING_BATCH_SIZE`（默认 64）分批，校验返回条数与维度一致；不一致直接失败，避免 chunk 与向量错位入库。生产环境使用 mock 向量/`LLM_PROVIDER=fake` 时启动告警。

### 令牌与会话安全

- 刷新令牌轮换（迁移 0038 `refresh_sessions`）：每次刷新换新 jti，旧令牌立即失效；已轮换令牌被重放视为泄露，吊销该用户全部活跃会话。
- 登出携带 `refresh_token` 时精确吊销当前会话；未携带的旧客户端保持兼容（仅审计）。改密后旧刷新会话全部作废。
- 前端静默刷新同步保存轮换后的新令牌；登出时提交当前刷新令牌。

### 权限单轨

- `require_permission`、成员管理、工具调用与知识/技能审核统一走 DB 有效权限（角色覆盖 + 逐人授权），JWT 角色仅作无小院上下文时兜底；数据库降级/升级立即生效，不再依赖重新登录。
- 第三方/成员绑定技能正文包入 `<bound_skills>` 不可信数据区并转义闭合标签，明确禁止改变系统规则与权限边界。

### 质量门禁

- 新增配额原子性、向量批处理、令牌轮换/吊销、RBAC 升降级、技能注入隔离与驾驶舱可信指标测试；后端全量 1351 条测试通过，mypy 278 文件、ruff、迁移升降级与 OpenAPI 契约检查通过。

## [3.6.1] - 2026-10-28

### 部署与发布硬化

- 修复 `compose.production.yaml` 非法插值（`${POST...?rd}`），生产编排恢复可用；新增 `migrate` 服务，API/Worker/Scheduler 等待迁移成功后再启动。
- 服务器版安装脚本默认写入 `APP_ENV=production`，账号初始化为随机口令并只写入 `backend/.initial-credentials`（600 权限），不再生成 `admin/admin123` 等固定演示账号；检测到 `LLM_PROVIDER=fake` 时明确告警。
- `.dockerignore` 不再排除 `contracts/` 与 `plugins/`，容器内工作流校验与内置作物包不再缺失。
- nginx 为聊天/班组 SSE 单独配置长连接：关闭缓冲、600s 读写超时，避免长任务被 120s 代理超时截断。
- `/health/ready` 在生产环境校验 Alembic schema 是否为 head；开发/测试不阻断。

### 质量门禁

- CI 新增：版本面一致性检查、compose（基础 + 生产覆盖）校验、前端单测、API/Worker/Web 三个镜像构建。
- 发布流水线新增 tag 与全部版本面的预检；移除工作流注释中的固定签名口令。
- 测试默认数据库改为临时 SQLite，不再写入 `backend/dev.db`。
- 新增 schema 就绪检查单测；后端全量 1321 条测试通过。

## [3.6.0] - 2026-10-28

### 协作班组

- 「跑一次」改为「运行」，统一采用异步执行：检索本院知识库与公开互联网资料，成员按角色接力，再由大模型综合形成最终答案；关闭页面不取消后台任务。
- 保存真实知识库与联网来源、检索限制及运行过程。公开搜索只接收原始问题，不发送检索到的知识库片段或成员输出；来源不足或搜索失败明确提示，不伪造引用。完整问句命中不足时自动用首个子句补检；搜索引擎返回的零相关首页直接丢弃并说明。
- 顶部「新建班组」左侧新增「运行记录」弹窗，按班组、状态、触发方式筛选和分页，查看问题、成员过程、综合答案与引用。
- 新增默认关闭的间隔计划：管理员设置问题、5～43200 分钟周期及输出格式，复用 5 分钟调度轮询；自动运行与手动运行共用记录和重试流程。
- 支持文字、Markdown 和 Word 输出，保存文档内容快照，使用真实渲染器下载；不依赖聊天会话或临时文件。
- 新运行记录按执行者和小院隔离，详情、重试、事件流、下载统一校验；定时运行重新验证账号与成员身份，任务领取与到期计划采用原子条件更新防重复执行；排队/执行超时由 5 分钟巡检回收，避免运行永久卡住。
- 迁移 0036 增加班组计划与运行归属、来源、触发方式、输出格式和文档快照字段；保留旧记录，班组改名或删除不丢失历史名称。

## [3.5.0] - 2026-10-27

一句话生成智能体（「我的智能体」内输入一句话 → 联网取证 → 固定六段框架 →
自动匹配本院技能 → 落可编辑草稿，全程不自动发布）。方案要点：异步任务 + SSE
里程碑、技能「配不上就留待补清单」、配额与权限不新增权限点。

### 后端

- 迁移 0035：`yard_agents` 增加 `blueprint_json`（框架六段 + 参考来源 + 生成元信息）
  与 `skill_requirements`（待补技能清单），仅生成草稿非空
- `agent_spec.py`：固定框架 `AgentSpec`（`extra=forbid`，六段缺一不可）——使命 /
  职责边界（in_scope 3-6 条 + out_of_scope 2-4 条）/ 服务对象 / 工作流 3-6 步 /
  护栏 2-5 条 / 产出格式；`agent_code` 强制 `gen_` 前缀、工具必须在注册表内
  （未知工具 422）、`{{question}}` 占位必填、`knowledge_scopes` 白名单、风险由
  服务端 `assess_risk` 复算不采信模型自评
- `agent_builder.py`：联网取证（`search_web`，失败降级离线不阻断）→ LLM 按框架
  出严格 JSON（3 次纠错；模型完全不可用 502 不落草稿，连续不合规落确定性保守
  模板并标注人工复核）→ 技能匹配（关键词打分命中自动绑入 `bound_skills`，
  未命中进 `skill_requirements`，上限 5 项）→ `create_agent` 落 draft（不发布）
- 异步任务：`agent_generate` 任务处理器按 retrieving → designing → matching →
  persisting → completed/failed 写 task_events 里程碑；前端复用现成
  `GET /tasks/{id}/events/stream`（SSE）追进度
- 路由（挂 `/agents/generate*`）：`POST /agents/generate/preview`（同步预览：
  关键词 + 现有技能候选 + 配额快照，不调 LLM）、`POST /agents/generate`
  （202 返回 `{task_id}`；幂等键 = 小院+成员+utterance）、
  `GET /agents/generate/policy`（当前成员配额快照）
- 权限与配额：不新增权限点——任何小院成员可生成；`visibility=public` 需管理员
  （越权 403）；双闸 `tasks` 配额 + 小院设置 `agent_generate.daily_limit`
  （默认 10 次/人/日，按 tasks 行计数、跨进程一致）
- 设置项 `agent_generate`（enabled / daily_limit 0-200 / use_web），设置页权限
  `settings:manage`；审计 `agent_generate_requested` / `agent_generated`
- `DeclarativeAgentUpdate` 支持人工维护 `skill_requirements`（补齐后可删除）；
  `to_out` 暴露 `blueprint` / `skill_requirements`

### 前端

- 「我的智能体」新增「⚡ 一句话生成」入口 + 三步向导（一句话描述与示例 chips →
  SSE 四阶段进度（断线降级 5s 轮询）→ 六段框架只读预览、已匹配技能可取消勾选、
  待补技能 chips 可删除），完成后直接打开现有编辑弹窗
- 编辑弹窗「扩展技能」上方显示「待补技能」chips（可删除，单独 PATCH）
- 系统设置新增「一句话生成智能体」卡片（开关 / 每人每日上限 / 联网检索）

### 收口

- `contracts/openapi.yaml` 重新生成；`contracts/error-codes.md` 补 4 个
  `AGENT_GENERATE_*` 错误码；版本号 3.4.1 → 3.5.0

### 修复

- 内置搜索源全线不可用（「网络与搜索 → 测试连接」报"联网受限"）：三个引擎对
  机器人 UA 直接拦截（cn.bing 301 跳首页、搜狗 302 反爬页、360 验证码页）。
  bing 改用浏览器 UA + 引擎匿名 cookie jar + 跟随握手跳转（cn.bing→www.bing）
  后恢复；搜狗/360 无 JS 无法过反爬，保留由熔断自动跳过。另修测试注入缺陷：
  `providers_builtin` 从 `providers` from-import 的 `_transport_override` 是值
  拷贝，收不到 `set_test_transport` 的重新赋值（会导致测试真实出网）。

## [3.4.1] - 2026-10-27

### 修复

- 「我的智能体 → 编辑智能体」中「扩展技能 / 扩展插件」选择列表叠字：Element Plus
  复选框默认 `inline-flex` + `white-space: nowrap` + 固定 32px 高，多行摘要被压到
  名称与标签上。改为整行卡片式布局（`height: auto`、标签列纵向排布、名称单行省略、
  摘要在下一行换行），行间加分隔线与 hover 高亮，列表改为纵向滚动。
- 列表项补充 `box-sizing: border-box`，消除行内边距导致的 4px 横向溢出。
- 失效行（已下架/无权限）同步套用新布局；补齐 `--ds-font-mono` 缺失的不存在变量。

## [3.4.0] - 2026-10-27

自我总结提升（方案见 `docs/plan-v3.x-smart-skills.md` §6）：定时归纳小院会话主题与反馈，
产出偏好记忆、技能提案与知识候选，供专家审核后入库。

### 自我总结提升（后端）

- 迁移 0034：`yard_jobs`（可复用调度底座）+ `yard_job_runs`（运行台账）+
  `self_improve_memories`（偏好记忆，按成员/按小院）
- 调度底座：`YardJob.lease_until` 租约防多实例重复执行；Celery beat 5 min 与
  桌面 inline 300 s 线程共用 `run_due_jobs`
- 设置项 `self_improve`（默认关闭）：schedule / scope / daily_budget_tokens /
  auto_publish / retain_days / notify_owner；`auto_publish` 需 `confirm_auto_publish`
  二次确认（400）
- 执行引擎 `_execute`：证据聚合（只取主题/反馈/工具计数，不读消息原文）→
  LLM 归纳（3 次重试）→ 持久化偏好记忆（supersede 旧 active）+ 技能草稿
  （pending_review）+ 知识候选（pending_review；auto_publish 自动通过）
- 诚实口径：窗口内无数据 → 成功但不调 LLM；LLM 失败/解析失败 → failed 台账，
  不落脏数据；日预算用尽 → skipped
- 路由：GET 运行报告 / POST 手动运行（settings:manage_protected）/ GET runs /
  GET memories（本人/授权者）
- `settings:manage_protected` 加入 GRANTABLE_PERMISSIONS（负责人可逐人下放）
- 推送事件 `self_improve_run` + 审计 `self_improve_run`

### 自我总结提升（前端）

- 系统设置新增「自我总结」tab：开关、周期/范围/预算/保留/自动发布/通知表单
  + 运行报告卡（任务状态/今日 token 用量/运行历史表/立即运行一次）

### 错误码新增

| 错误码 | HTTP | 说明 |
|--------|------|------|
| `SELF_IMPROVE_DISABLED` | 409 | 尚未开启 |
| `SELF_IMPROVE_BUDGET_EXHAUSTED` | 409 | 日预算用尽 |
| `SELF_IMPROVE_LLM_UNAVAILABLE` | 502 | 模型不可用 |
| `SELF_IMPROVE_CONFIRM_REQUIRED` | 400 | auto_publish 需确认 |

## [3.3.0] - 2026-10-27

一句话生成插件（方案见 `docs/plan-v3.x-smart-skills.md` §5.1），并补齐 v3.2 遗留的对话
生成权限校验。

### 一句话生成插件（后端）

- 新增 `market/plugin_agent.py`：一句话 → 联网找资料 → 模型出工作流 manifest → 校验 →
  `register_workflow` 落 **pending_review**。插件是可执行工作流，没有「私有即用」，
  必须经专家审核并启用后才能运行。
- manifest 沿用插件校验规则：步骤只允许工具注册表内的工具、拒绝 `mcp__` 前缀、单插件最多
  8 步，非法即 `PLUGIN_MANIFEST_INVALID` 400 且不落库；模型多次不合规时降级到保守的
  纯模型/只读检索步骤，保证可用且可审。
- `/market/agent/generate` 新增 `kind`（`skill` 默认 / `plugin`）；技能与插件共用
  `skill_lab` 配额与开关，超限 `SKILL_QUOTA_EXCEEDED`、关闭 `SKILL_LAB_DISABLED`。
- 对话链路：意图新增 `INTENT_PLUGIN`（「插件」类名词，不含「工作流」以免误伤问答），
  命中后生成并以 `plugin_card` 载荷下发（插件摘要 + 工作流步骤 + 引用来源）。
- 新增工具 `skill.search` / `skill.generate` / `skill.install`，把一句话链路暴露给工作流
  引擎；安装属 `external_side_effect`，执行需审批，越权/缺权限分别 `TOOL_RISK_BLOCKED` /
  `TOOL_FORBIDDEN`。
- 安全加固：对话内技能 / 插件生成补上 `skills:generate` 校验，与 HTTP 端点同口径（此前
  对话链路可绕过该权限）。
- 负责人把私有技能转为小院公共时，新增推送事件 `skill_force_published` 通知创建者。

### 前端

- 技能库新增「一句话生成插件」入口，复用向导（技能/插件双模式）：插件模式跳过候选检索
  直接生成，结果卡展示待审徽章、工作流步骤、引用来源与「去审核启用」。
- 对话内新增插件卡片：展示插件摘要与步骤预览，失败可原地重试；审核入口按后端口径收敛
  到负责人 / 管理员（`require_roles yard_admin`），跳「技能与插件 → 插件」审核启用，
  专家与普通成员只看到「已提交小院审核」的说明。

### 契约与门禁

- OpenAPI 181 paths（v3.3 复用既有端点，仅扩展 `kind` 参数与载荷）。
- 后端 1199 项测试通过（v3.3 新增 19 项：工具注册/执行 6、插件生成端点 7、意图识别 5、
  转公共通知 1）；ruff / mypy 全绿；前端 ESLint / vue-tsc / vite build 通过；Playwright
  新增 1 项一句话生成插件用例，全量 25 项通过。

## [3.2.0] - 2026-10-27

一句话生成技能 + 技能归属视图（方案见 `docs/plan-v3.x-smart-skills.md`；v3.1 已落地
GitHub 国内镜像与技能模板规范，本期把两者接成可用链路）。

### 一句话生成技能（后端）

- 新增 `market/skill_agent.py`：一句话 → 检索候选 → 模型出规格 → 校验 → 落私有草稿。
  中文短句匹配（去口语填充词 → 中英关键词 + 中文 2/3-gram → 加权命中 → 归一化到
  (0,1]），命中本地内置/缓存优先，无命中再试 GitHub，离线只降级不报错。
- 规格由模型产出，最多纠错 2 次仍不合规则降级到确定性兜底模板（保守内容 + 提示人工
  复核），保证功能可用；引用来源取自联网检索，检索失败不阻断生成。
- 工具白名单取自实时工具注册表；引用写类工具强制 `risk_level=high` 且要求审核。
- 生成一律落**私有草稿**（`visibility=private`、`owner_user_id=当前用户`、
  `origin=skill_agent`），共享须经审核；每日配额受小院设置 `skill_lab` 控制
  （`enabled`/`daily_limit`/`use_web`/`max_sources`），超限返回 429
  `SKILL_QUOTA_EXCEEDED`，失败不落库。
- 新增三个端点：`POST /market/agent/request`、`POST /market/agent/generate`
  （`skills:generate`）、`POST /market/agent/install`（`market:install`）。
- 修复：`skill_lab` 未登记在设置项权限映射里，`PUT /yards/current/settings/skill_lab`
  一律返回 400「未知设置项」——该设置项此前无法写入。现按 `settings:manage` 放行。
- 对话链路打通：命中「技能」类意图时先检索、无匹配再生成，以 `skill_card` 载荷
  下发（含候选、来源、说明与 token 用量）。

### 技能归属视图（v3.2 归属）

- 迁移 `0033_skill_visibility`：`evolution_skills` 增 `visibility`
  （private/yard/platform）、`owner_user_id`、`origin`、`reviewed_by/at/note`。
- `GET /skills` 支持 `scope=mine|yard|platform`；`POST /skills/{id}/promote` 支持负责人
  把私有技能转为小院公共（`skills:publish_any` 属保护型权限，只能逐人下放）。
- 私有技能默认仅创建者与小院负责人可见，不进小院公共列表。

### 前端

- 技能库新增三级归属视图（市场 / 我的 / 小院 / 平台）与「一句话创建技能」向导：
  检索候选 → 安装或直接生成 → 结果卡（私有徽章、引用来源、提交审核、去绑定助手）。
- 对话内新增技能卡片：候选可直接安装，生成结果可提交审核，失败可原地重试；按钮状态
  按面板实例与消息 id 隔离，多面板互不影响。
- 技能相关接口全部静默失败：在区块内展示原因，不弹全局错误、不整页报错。
- 系统设置「网络与搜索」新增「一句话技能生成」卡片：启用开关、允许联网找资料、每日
  生成上限与引用来源上限，落到小院设置 `skill_lab`。
- 修复：回答落地后实时步骤条仍悬挂——本轮收尾即收起步骤条，不再等会话列表刷新。

### 契约与门禁

- OpenAPI 181 paths（新增 3 个 `/market/agent/*` 与 `/skills/{id}/promote`）。
- 后端 1180 项测试通过（v3.2 新增 36 项一句话技能、归属视图与设置开关测试）；
  ruff / mypy 全绿；前端 ESLint / vue-tsc / vite build 通过；Playwright 新增 7 项
  一句话技能、归属视图与技能生成设置用例，全量 24 项通过。

## [3.1.0] - 2026-10-27

搜索默认可用 + 编辑弹窗绑定拆分（方案见 `docs/plan-v3.x-smart-skills.md`，后续 3.2 一句话
生成技能的前置底座同期落地）。

### 搜索默认可用（零配置）

- 新增三个免密钥内置搜索源：必应中国版 / 搜狗 / 360（`search/providers_builtin.py`），
  `lxml.html` 解析，过滤广告、引擎跳转链接与 AI 问答/文库等自家垂直页；每源限速
  ≤1 req/s，连续 3 次失败熔断 5 分钟；结果沿用 10 分钟缓存。
- 搜索设置 `provider` 新增 `auto` 并作为默认：内置源 → SearXNG（配置了地址时）→
  Tavily/Brave（配置了 Key 时）依序降级；新增 `builtin_sources` 可单独启停任一内置源。
- `/search/status` 新增 `internet` 字段并修正语义：只有真正断网才是 `offline`（提示
  本地部署形态）；有网但所有源失败是 `degraded`（「联网受限，已自动降级」），不再误报。
- 前端：设置页新增「自动（推荐）」与内置源多选、逐源状态文案；技能库页离线横幅只在
  真断网时出现，降级改为轻量提示且不阻断浏览与本地导入。

### 智能体绑定拆分（扩展技能 / 扩展插件）

- 新增 `GET /api/v1/agents/bindable-extensions`：聚合系统内已安装、当前用户可用的全部
  技能与插件（published Skill + enabled PluginManifest + 市场安装孤项补齐），带来源、
  风险、可见性与被引用数；OpenAPI 177 paths。
- 写入收口：POST/PATCH /agents/declarative 的 `bound_skills` 逐项校验，未知 code 返回
  422（历史脏数据读取不受影响）。
- 修复运行时注入：`_resolve_bound_skills_prompt` 此前用裸 `skill_code` 匹配前端提交的
  `skill:<id>` / `plugin:<pid>` 前缀码，绑定技能的 SOP 从不生效；现已两种编码兼容，且
  只注入 published 技能与 enabled 插件。
- 前端编辑弹窗拆成「扩展技能」「扩展插件」两区，各带搜索、来源/风险标签、已选计数、
  全选/清空与空状态引导；已下架的绑定以「已失效」禁用态保留，不静默丢数据。
- 可选集等于可用集：市场已安装但已有待审/停用 Skill/Manifest 行的项不进候选。

### v3.2 前置底座（本期合入，功能下期交付）

- GitHub 国内镜像：`github_api_base` / `github_raw_mirrors` / `github_zip_mirrors`
  （默认 ghfast.top / gh-proxy.com / raw.gitmirror.com），官方 → 镜像依序降级，健康
  60s、失败冷却 5min、404 不进冷却；全失败仍报 `NETWORK_UNAVAILABLE`。
- 技能模板规范 `contracts/skill-template.yaml` + `market/skill_template.py`
  （`SkillSpec` 严格校验：字段/长度/步骤数/评测样本/URL 协议/保留字/未注册工具 422、
  写类工具强制 high 风险并要求审核、`dump_markdown` 与 frontmatter 往返一致），为
  3.2「一句话生成技能」提供唯一框架。

## [3.0.0] - 2026-10-27

联网搜索、插件/技能市场、身份权限与负责人驾驶舱一次性交付（原计划的 2.2.0 + 3.0.0
两波合并）。小院新增「小院负责人」角色，权限判定从 JWT 静态角色升级为**数据库角色 +
逐人授权**；模型获得受控联网能力；插件与技能可在库内检索、导入与安装，第三方内容
一律进专家审核。

### 联网搜索（web.search / web.fetch）
- 新增 `search` 模块：SearXNG（默认，自托管、无密钥）/ Tavily / Brave 三供应商，
  首选失败自动降级，10 分钟进程缓存，按小院与 UTC 日期计配额（默认 500/日）。
- 启动注册两个只读工具：`web.search`（query/count≤8/lang）与 `web.fetch`
  （url/max_chars≤6000，output_schema 带 sources）；未配置供应商时返回明确 503，
  模型不会「假装联网」。
- 出网安全：`web.fetch` 严格公网校验（拒绝回环/私网/link-local/元数据/保留地址），
  手动重定向最多 3 跳且逐跳复验，Content-Type 白名单，2MB 上限与流式截断。
- 新接口 `GET /search/status`（联网状态 + 供应商 + 配额）、`POST /search/test`
  （`search:manage`，真实搜一条并回传耗时/结果数）。
- 离线降级：`NETWORK_UNAVAILABLE`（409，`details.mode=local`）→ 前端提示
  「当前设备为本地部署形态…请联网后使用，或前往专家审核 → Skill 管理」并回退内置库。

### 插件 / 技能市场与本地库
- 12 个内置农业技能（土壤/灌溉/病虫害/农药安全/储粮/农机/田间记录/农时/行情/
  政策/作物营养/气候风险），改写自 MIT 许可的 farmfriend-labs/agent-skills-farming，
  保留来源与许可证标注，缺失数据一律「不编造」；新小院开通时自动铺设为本院
  published Skill，直接出现在「专家审核 → Skill 管理」，演示小院由 seed 幂等补齐。
- GitHub 只读检索与 SKILL.md 预览（许可证白名单过滤、token 仅留在服务端、预览即落库
  可直接安装）；离线返回空列表 + 中文提示，不阻断内置库。
- 本地导入：`.md/.markdown/.txt` 技能（≤256KB）与插件 zip（≤2MB，解压 ≤5MB）；
  zip 拒绝路径穿越/绝对路径/符号链接/可执行文件，**流式读取防压缩炸弹**。
- 安装状态机：内置技能直接启用；GitHub/本地导入的 Skill 与全部插件一律
  `pending_review`。**导入文件自带的 `builtin/verified` 标记不再被信任**，修复了
  绕过专家审核自发布的提权漏洞。
- 新接口：`/market/items/search|{key}|install`、`/market/installed`、
  `/market/skills/import`、`/market/plugins/import`、`/market/github/search|preview`。

### 身份、角色与成员
- 角色体系：`yard_owner`（负责人，唯一可管理/删除管理员、可转让、可见敏感数据）、
  `yard_admin`（日常维护，不能动负责人/其他管理员）、`expert`（专家审核、技能、
  可管理 operator/viewer）、`operator`、`viewer`、`service_worker`。
- 角色权限可在系统设置中调整（保护型权限只由代码发放）；支持创建/改名/删除自定义角色，
  自定义角色可分配给成员，但授予范围必须是操作者自身权限的子集。
- 成员管理：新增/挂接已有账号（用户名/邮箱/手机号/显示名/角色）、编辑、停用/启用、
  软删除、重置密码（一次性临时密码 + 强制改密 + 旧令牌立即失效）；无
  `members:manage` 时联系方式脱敏。
- 自助注册与审核：`closed/approval/open` 三模式，`approval` 创建 pending 账号并进入
  待审列表；通过/拒绝留痕；拒绝不会误停用该用户在其他小院的账号。
- 个人中心：所有账号可改显示名/邮箱/手机号与密码；首次登录强制改密。
- 令牌与会话：改密、重置、停用、改角色、负责人转让均递增 token_version，旧 access
  token 立即失效，前端静默刷新后按新角色签发；权限判定一律 DB 优先。
- 登录安全：全局 + 小院两级滑动窗口限流；密码最小长度取「部署下限/生产 10/小院设置」
  三者最大值；注册限流阈值可配置。

### 负责人驾驶舱
- 总览：成员数、今日活跃、助手数、会话/消息、今日与 7 日 Token（输入/输出）、失败消息、
  待审数量、联网状态。
- 按人用量、按日趋势（含成员下钻）、Agent 调用、会话审计（查看正文需填写 2–200 字
  原因并落 `sensitive_view` 审计）、逐人敏感授权（`insights:sensitive` / `finance:read` /
  `audit:read`）、负责人转让。
- 敏感数据默认对小院管理员不可见，由负责人逐人授权后生效。

### 前端
- 新增「插件与技能库」（联网徽章/离线提示/来源筛选/预览抽屉/导入/安装）、
  「负责人驾驶舱」、「个人资料」、注册页「加入小院」。
- 成员管理重写：成员/注册审核/邀请三 Tab，新建成员一次性初始密码、编辑抽屉、
  重置密码、停用/移除、操作权限按角色收敛。
- 系统设置新增：角色与权限、注册与安全、网络与搜索、敏感授权（仅负责人）。
- 菜单与路由：能力中心新增插件与技能库；管理与统计新增负责人驾驶舱；账号菜单新增
  个人资料；导航权限同步 `yard_owner`。

### 迁移与契约
- 迁移 `0030_identity_rbac`（用户安全字段、yard.owner_user_id、yard_settings /
  yard_roles / yard_member_grants / yard_join_requests，回填负责人）、
  `0031_market_items`、`0032_trace_user`（LLMTrace 增加 user_id/session_id）。
- OpenAPI 重新生成（176 paths）；新增错误码 40+ 条登记至 `contracts/error-codes.md`。

### 质量门禁
- 后端：`pytest` 全量通过；`ruff check` / `ruff format --check` 全绿。
- 前端：`vue-tsc --noEmit`、ESLint（0 error）、`vite build` 通过。
- E2E：Playwright 串行 16 条通过（新增成员创建、插件库预览、负责人驾驶舱/权限拦截）。
- 迁移 `0029 → 0032` 升级/降级/再升级在临时库演练通过；开发库已升级。

## [2.1.0] - 2026-10-27

对话内文档生成与预览 + 对话体验收口：一条需求直接产出 Word / Markdown / Excel /
PPT / PDF，在线预览与鉴权下载；同时修复暗色提示可读性、会话类型标识、消息一键复制、
「自我升级」菜单、会话列表竖条等问题。

### 对话内文档生成（v2.1 主线）
- 统一文档模型 `DocModel`（标题/主题/作者 + 7 种块：标题/段落/无序/有序/表格/代码/分页），
  一处校验、五处渲染——预览与下载永远同源。
- 五种格式写出器：`.md`、`.docx`（python-docx）、`.xlsx`（openpyxl）、`.pptx`
  （python-pptx）、`.pdf`（reportlab，STSong-Light 中文字体）；**仅引入 MIT/BSD-3
  依赖，GPL/AGPL 一律未使用**。
- 对话意图 `INTENT_DOC`：命中「产出动词 + 文档名词」（写一份/生成/整理成/导出 + 报告/
  方案/台账/PPT/Excel/PDF/Markdown…）即进入文档链路；模型把需求拟成 DocModel，
  失败自动降级为确定性模板，文档生成不因模型不可用而失败。
- 产物落 `chat_attachments`（迁移 `0029`：`doc_format` / `doc_model_json` / `generated`），
  沿用会话私有边界与级联清理；卡片 `type=document` 带格式徽章与「预览 / 下载」。
- 新接口：`POST /chat/sessions/{id}/documents`、
  `GET …/attachments/{aid}/preview`（自包含 HTML + CSP sandbox）、
  `GET …/attachments/{aid}/download`（RFC 5987 中文文件名，越权统一 404）。
- 预览安全：HTML 全量转义、无脚本/无外链，前端装入 `sandbox=""` 的 iframe（srcdoc）；
  上传附件没有 DocModel 快照 → 415 `DOC_PREVIEW_UNSUPPORTED`，请走下载。
- 新增错误码 `DOC_*` 6 个，登记进 `contracts/error-codes.md`。

### 对话体验修复
- **悬浮提示可读性**：Element Plus 暗色 popper 用 `var(--el-bg-color)` 反色，被项目
  桥接成深色后「深底深字」；`design-system.css` 显式设置 `color: var(--ds-text-1)`，
  「模型路由：自动」「群聊成员」等 tooltip 全部可读。
- **会话类型标识**：左侧会话列表新增「群聊 N / 助手名 / 班组 / 对话」徽章；后端
  `chat_sessions.target/target_code` + `participant_count` + `target_name` 单点解析。
- **一键复制**：用户提问与助手回答各带复制按钮（Clipboard API，失败回退
  `execCommand`），点击后短暂显示「已复制」。
- **会话列表竖条**：类型徽章的 `chat` 色调类名与页面根 `.chat` 布局规则冲突，导致
  会话项被撑成竖直长条；色调类改为 `s-kind--group/agent/team/chat`，列宽与省略号
  一并收口。
- **「自我升级」菜单恢复**：DIY 菜单曾把该项改名为「技能与插件」并下沉；现恢复为
  「能力协作」下的一级菜单，DIY 布局存储键升至 `v4`，归一化时同步标题与图标。
- **专业助手页收敛**：「小院助手」分区及其创建/编辑弹窗整体移除（相关能力已全部由
  「我的智能体」承载），只保留 4 位专业助手与平台审核队列。
- **「我的智能体」网络错误**：根因是开发环境三件事叠加——vite 进程退出（5173 未监听）、
  后端旧进程缺 `/llm/usage/overview`（404）、开发库停在 0026 缺列（500）；已重启
  服务并执行迁移，前端对用量接口失败不再阻塞页面。

### 质量门禁
- 后端 `885 passed`；Ruff / Ruff format / mypy（235 文件）全绿；
- 前端 ESLint 0 error、`vue-tsc`、`vite build` 通过；
- Playwright 12 passed（串行，含「生成 Word → 卡片 → 预览 → 下载」全链路）；
- `contracts/openapi.yaml` 重新生成（146 paths）；迁移 `0029` 已应用。

## [2.0.0] - 2026-10-26

小院化改造 + 助手工作台：全量「租户→小院」改名、助手名称唯一、私有/公用可见性、
每助手独立绑大模型与技能、卡片/列表视图、@ 点名、弹出式「使用」对话、多助手群聊。

### 小院化改造（破坏性契约变更，带兼容窗口）
- 「租户」→「小院」全量改名（含标识符）：`tenant(s)` → `yard(s)`、表名/列名/
  索引/约束、JWT claim、角色值、错误码与前端文案；迁移 `0022_rename_tenant_to_yard`
  同步存量值与 JSON 键（`choice_json`/`members_json` 等）。
- 兼容窗口：JWT 解析双读 `yard_id`/`tenant_id`（`core/security.py`
  `LEGACY_YARD_CLAIM`）、登录请求体接受旧别名、在途任务兜底——旧客户端在窗口期
  内不中断，但桌面端需升级至 2.0.0 与后端契约对齐。

### 助手管理（名称唯一 / 可见性 / 模型与技能绑定 / 首图）
- 同小院助手中文名唯一（迁移 `0023` 唯一约束）：撞名 409 `DUPLICATE_NAME`
  （details 带既有助手 id/code/name/status），前端弹「已经有了，是否复用」——
  复用直接进既有助手；不复用走复制并自动改名；存量重名数据迁移时按
  `原名-2`/`原名-3`… 自动加后缀清洗。
- 对话内生成能力同名冲突同样自动加后缀落草稿，卡片注明调整，不再把
  `DUPLICATE_NAME` 硬报给用户。
- 助手可见性 `public`/`private`（迁移 `0024`）：private 仅创建者可见可用，
  展示名自动加「（用户名+私有）」后缀（存储名不受污染）；成员可自建助手
  （后端强制 private），管理员可设公用。
- 管理员软删除：`status='deleted'` 后读路径隐去，行保留避免历史消息悬空引用，
  原名确定性改名为「原名（已删除·id8）」以释放唯一名。
- 每助手单独绑大模型：`supplier_id` 接通运行时（此前是死字段），越权引用自动
  回退；技能 `allowed_tools` 多选绑定。
- 助手页卡片/列表视图切换；卡片显示首图（迁移 `0026`，上传/清除、带登录态
  取图，失败回落 emoji）、名称、功能介绍、绑定的大模型与技能。

### @ 点名与「使用」弹窗
- 聊天输入 `@` 弹出候选面板：`GET /chat/mention-candidates`，候选 = 本小院全部
  审核通过助手（private 仅创建者可见）+ 班组，口径复用
  `supervisor.list_candidates`（可见性只有一份实现）。
- `target_code` 定向执行（id→code→名称解析），`routed_by='bound'`；徽章文案与
  色调统一由后端 `routed_label`/`routed_tone` 下发，未知取值给确定兜底——
  根治 v1.8.0「新取值没进前端词表 → 整条消息渲染崩溃」事故。
- 「试跑」改为「使用」：弹出与主对话同款样式的对话面板；`ChatPanel` 组件抽取 +
  chat store 可实例化，多个弹窗与主对话状态互不污染。

### 多助手群聊
- 会话成员模型 `chat_participants`（迁移 `0025`）：
  `PUT /chat/sessions/{id}/participants` 整表覆盖；名单按请求者可见性校验——
  私有助手只有创建者能拉进自己的群聊，口径与 @ 点名一致。
- 应答策略 `all`（全员必答）/ `named`（指定成员答）/ `auto`（自愿抢答）：
  `POST /chat/sessions/{id}/messages/group` 与 `/group/stream`，SSE 推
  `member_event`（running/ok/error），并行应答 + 成本护栏（成员上限、超时与
  失败如实呈现）。
- 新增 `GROUP_*` 错误码 8 个，登记进 `contracts/error-codes.md`。

### 质量门禁
- 后端 `855 passed`；Ruff / Ruff format / mypy（225 文件）全绿；前端 ESLint
  0 error、`vue-tsc`、`vite build` 通过；`contracts/openapi.yaml` 重新生成
  （141 paths）；Playwright 10 条全绿（串行）。
- 新增用例：重名 409/复用/复制、visibility 越权负向、supplier_id 越权回退、
  软删后历史可读、名称清洗迁移往返、群聊名单可见性（含私有助手自建/他人两向）、
  @ 候选负向越权、能力生成撞名自动后缀、弹窗对话状态隔离。

### 升级注意
- 需执行 `alembic upgrade head`（0022–0026：改名、唯一名、可见性、群聊成员、
  首图）。
- 前后端版本号统一为 `2.0.0`（`pyproject.toml`、Tauri `tauri.conf.json`、
  `Cargo.toml`、桌面端 `package.json`、`uv.lock`）。
- 破坏性契约：JWT claim、登录体、表名均为 `yard` 体系，桌面端需同步升级；
  旧版令牌在兼容窗口内可继续使用。

## [1.8.0] - 2026-10-26

对话工作台升级（第一期 + 第二期）：附件阅读、对话内生成能力、过程步骤可见。

### 对话附件（一期）
- 新增 `chat_attachments` 表与迁移 `0021_chat_attachments_and_cards`：会话级附件
  （图片 / PDF / docx / txt / md / csv），记录 `kind`、`mime`、`size_bytes`、
  解析状态与抽取要点。
- 新增附件端点：上传（`POST /chat/sessions/{id}/attachments`）、列举、删除；
  响应不外泄 `storage_key`，删除时同步清理服务端文件。
- 附件内容以「上下文」形式注入问答链路（不参与路由打分），模型可据此作答；
  解析失败的附件如实标记 `failed` 并在界面上可见，不静默丢弃。
- 前端在输入框左侧新增附件按钮与待发附件 chip，随下一条消息一起发出。

### 对话内生成能力（二期）
- 新增意图识别（`app/modules/chat/intent.py`，纯规则、可复现）：区分「提问」与
  「下达指令」；创建类必须同时命中创建动词与能力名词，含疑问词一律降级为提问，
  宁可漏判不可误判。
- 新增能力链路（`app/modules/chat/capability.py`）：自然语言需求 → 声明式 Agent 草稿
  → 能力卡片。模型不可用时降级为确定性模板，不阻断生成。
- 工具白名单固定 `knowledge.search`（只读）、知识范围固定 `tenant`，对应 `risk=low`；
  副作用工具必须在能力中心显式添加并重新评估。
- 权限口径与能力中心完全一致：管理员直接创建草稿，其余角色只产出「建议卡片」，
  由管理员在能力中心确认后落地，并留审计 `chat_capability_requested`。
- 新增市场链路：对话中可直接装载能力市场能力包（命中即装载，未命中则如实列出
  可用能力包，不假装成功）。
- 前端渲染能力卡片 / 市场卡片（含「发布」「在能力中心查看」动作），发布仍走既有
  能力中心闸门，权限由后端兜底。

### 过程步骤可见
- 新增 `app/core/progress.py`：统一阶段词表（reading / routing / retrieving /
  thinking / tool_call / generating）与步骤状态（running / done / failed），
  阶段名是前后端稳定契约，只追加不改含义。
- 对话改为 SSE 流式：`POST /chat/sessions/{id}/messages/stream` 推送
  `chat-init → step → card → message → chat-end` 帧；前端实时渲染
  「正在阅读附件 / 正在选择助手 / 正在思考 …」步骤条，历史消息可折叠回看
  「过程 · N 步」。
- 断流不重试（POST 重试会重复提交），改为以服务端记录为准降级：失败/中止的
  助手消息如实呈现，未落库时撤回乐观气泡并保留草稿；界面诚实提示
  「连接中断，回答未完成」，不假装已回答。
- 修复 SSE 在生产环境挂死：流式响应不再持有请求级 ORM session
  （FastAPI 会在响应体流式输出前关闭依赖 session，导致 `DetachedInstanceError`），
  改为流自持 session 并在提交后序列化；补充回归用例模拟真实的依赖析构顺序。

### 前端修复
- 修复对话内生成能力时整条消息渲染崩溃：`routed_by="local"`（对话直办）未登记在
  路由来源映射中，读取徽章样式时抛错导致能力卡片无法渲染；补齐映射并对未知
  取值加兜底，后端新增路由来源不会再让消息渲染中断。

### 质量门禁
- 后端 `708 passed`；Ruff、Ruff format、mypy 全绿；前端 ESLint 0 error、
  `vite build` 通过；`contracts/openapi.yaml` 重新生成（135 paths）。
- 新增 Playwright E2E（2 条）：附件上传→实时步骤→回答落库回看；
  对话内生成能力草稿→卡片渲染→发布。

### 升级注意
- 需执行 `alembic upgrade head`（0021 新增 `chat_attachments` 表并扩展
  `chat_messages`）。
- 前后端版本号统一为 `1.8.0`（`pyproject.toml`、Tauri `tauri.conf.json`、
  `Cargo.toml`、桌面端 `package.json`）。

## [1.7.0] - 2026-10-26

依据 `docs/audit-report-2026-10-25.md` 的审查结论完成 P0/P1/P2 整改：
安全红线、主流程可用性、成员邀请、前端工程化与可访问性。

### 安全与权限（P0）
- 移除工具审批入参旁路：不再采信请求体 `approved`，只认服务端上下文与数据库中
  已批准且参数哈希一致的审批单；MCP 桥接工具同样受约束。
- 审批决策与消费改为条件更新（`UPDATE … WHERE status='pending'` / `used_count < max`），
  并发下只有一个请求能批准或占用，杜绝重复执行外部副作用。
- 知识库上传/删除统一要求 `knowledge:write` 权限；viewer 不再能污染或清空团队知识库。
- 保留管理员用户名改为“静态名单 ∪ `PLATFORM_ADMIN_USERNAMES`”，统一小写比较，
  自助注册与邀请接受共用同一套账号/密码校验，封堵注册抢注后 bootstrap 提权。

### 主流程与稳定性（P0/P1）
- 多空间账号登录：从 `TENANT_REQUIRED.details.tenants` 渲染空间选择器，选择后携带
  `tenant_id` 重新登录；登录响应新增 `roles`，前端 TenantBrief → Tenant 统一适配，
  修复面包屑显示“未开通空间”。
- 访问令牌过期改为静默续期：并发 401 共享一次 refresh，成功后重放原请求，失败才跳登录，
  并保留 `redirect` 回跳地址；登录页 401 不再触发刷新递归。
- SSE 可靠性：后端增加 15s 心跳注释与 `id` 事件游标，支持 `Last-Event-ID` 断点续传；
  前端基于首帧判定连接成功、45s 空闲超时、指数退避+抖动重连，仍失败才降级轮询。
- Agent 目录与可运行集合对齐（`pest_alert`、`agriculture_qa` 等可正常 run）；
  总控 Agent 注入调用者角色，修复 `TOOL_FORBIDDEN`。
- 平台管理员无空间时仅在概览展示全局总览；访问其他业务路由给出明确说明与返回入口，
  不再出现“URL 变了内容仍是总览”。
- 设置页模型状态改为 `configured + mode` 推导，不再误报“异常”；演示账号提示仅开发环境展示。

### 功能补齐（P1）
- 成员邀请闭环：一次性令牌（仅存 sha256、TTL 可配、原子核销）、公开预览/接受、
  邀请列表/撤销、接受页支持新账号注册与已有账号加入；新增迁移 `0020_tenant_invitations`。
- 成员批量导入：逐条 savepoint 独立成败，返回成功列表与失败原因。
- 反馈纠错质量门禁：纠错正文至少 10 字、标题优先取反馈摘要、同租户重复内容去重，
  避免“错了”变成知识文档；审计记录去重标记。
- 机器人绑定校验修正：指定助手/班组必须存在且已发布/启用，切换回智能路由会清空旧绑定；
  管理页补绑定选择器与状态展示；“测试连接”通过只标记 `configured`，`online` 仅由
  manager 长连接握手回填，避免把“凭据齐全”误认为通道已通。
- 检索候选上限改为可配置 `retrieval_scan_limit`（默认 2000），达到上限时输出截断告警，
  不再静默硬截断 500 chunk。

### 前端工程化与体验（P2/UX）
- 路由级懒加载 + `vue-vendor` 手动分包；Element Plus 改为按需引入（`unplugin-vue-components`），
  首屏 gzip 从约 306 kB 降至约 240 kB。
- 新增 404 页面与全站文档标题；导航从 17 个入口收敛为 9 个一级入口（4 组二级菜单），
  面包屑支持空间/分组/父级/当前四级。
- 移动端抽屉式侧栏（遮罩、Esc 关闭、滚动锁、路由跳转自动收起）；跳过导航链接、
  `aria-current`/`aria-label`、键盘焦点样式、`prefers-reduced-motion`。
- 对比度提升：`--ds-text-3`、`--ds-accent-text` 达到 AA；空状态支持主行动按钮
  （成员页可直接“添加成员/生成邀请链接”）；错误文案统一为友好中文。
- 角色未加载时管理入口默认拒绝（不再 fails-open）；演示账号区块仅开发/演示构建展示；
  设置页 `LLM_PROVIDER`/`MODEL` 等术语改为业务文案。

### 质量门禁与契约
- 新增 Playwright E2E（6 条主旅程）：登录错误、导航/面包屑、404、全部业务路由无运行时异常、
  成员邀请闭环、移动端抽屉；`playwright.config.ts` 同时覆盖桌面与 Pixel 7。
- 后端 `669 passed`；Ruff、mypy、`vue-tsc`、`vite build` 全绿；ESLint 0 error
  （保留既有模板排版 warning，不阻断门禁）。
- `contracts/openapi.yaml` 重新生成（132 paths），补齐邀请、批量成员等新接口。

### 升级注意
- 需执行 `alembic upgrade head`（0020 新增 `tenant_invitations`）。
- 前端新增开发依赖 `@playwright/test`、`unplugin-vue-components`，首次运行 E2E 需
  `npx playwright install chromium`。
- `VITE_USE_MOCK=true` 的演示开关不变；生产构建不再展示演示账号提示。

## [1.6.0] - 2026-10-24

桌面端全平台可用性升级 + 构建修复。

### Windows 兼容 Win7+（WebView2 固定版）
- `webviewInstallMode` 从 `embedBootstrapper` 改为 `fixedRuntime`（WebView2 10554）：
  安装包内含固定版 runtime，对 Win7/8/10/11 全兼容，无需用户联网装系统级 WebView2。
  10554 是微软最后支持 Win7 的 runtime，同时满足 Tauri 2 最低要求 10414。
- NSIS 安装包体积略增（~10MB 固定 runtime），换来安装后零网络依赖。

### 自动升级链路验证
- GitHub Release run 40075755769 失败原因：`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
  GitHub Secret 与本地密钥实际使用的密码不匹配（wrong password）。
  本地密钥在 `~/Documents/Hermes/kjxy-work/tauri_updater_private_key`，需用正确
  密码通过 CLI 重新确认，再同步到 GitHub Secrets。
  安装包构建本身成功（.app/.dmg/.tar.gz 全产出），只是签名步骤挂了。

### 桌面版零配置开箱即用确认
- sidecar 打包已含全部 Python 引擎依赖（`--collect-all app/alembic/pydantic/sqlalchemy/cryptography`）；
- 首启自动：创建数据目录 → alembic upgrade head → seed 演示租户 → 生成随机口令写
  `desktop-login.txt` → 启动 uvicorn 127.0.0.1:187xx → 窗口自动跳转登录页；
- splash 明确告知登录文件路径，用户只需配置模型供应商 API key，其余全自动化。

### Linux 包格式收窄
- `tauri.conf.json` bundle.linux.fileformat 显式 `["deb","appimage"]`：
  避开 rpm（Ubuntu runner 无 rpmbuild），安装走 deb 或 AppImage 免安装方式。

### macOS dmg 确认
- macOS bundle targets 含 `dmg`（aarch64-apple-darwin），安装后无需额外配置。

### 版本号对齐
- tauri.conf.json / Cargo.toml / pyproject.toml / client/desktop/package.json
  四处统一升至 1.6.0（updater 按 tauri.conf.json version 比较新旧）。


## [1.5.0] - 2026-10-24

IM 机器人网关第一波（企微/钉钉直连）+ Release 流水线修复。

### IM 机器人网关（Wave C1+C2）
- 新模块 `bots`：企业微信智能机器人（aibot 长连接协议：订阅/回调/流式回复/
  主动推送/30s心跳/被踢单连接保护）、钉钉 Stream（gateway open→WS→ping-pong/ACK）、
  飞书（lark-oapi 可选依赖：未安装可存配置，连接时明确报缺失原因）。
- 入站编排 gateway：msgId 幂等去重 → 陌生会话批准闸门（固定欢迎语）→ @提及剥离 →
  `/use 名称 问题` 会话内切换 → 6轮上下文滑窗 → supervisor 智能路由/定向执行
  （supervisor 新增 ask_directed 纯增量入口）→ 分段回复 → **消息账本只存
  sha12 摘要+长度**；30 天无活动自动清上下文明文（prune）。
- manager：进程内管家 lifecycle 自动拉起； 多实例 Redis 选主（SET NX EX +
  续租，无 redis 单机退化）；指数退避重连 1s→60s；连续失败 5 次自动停用（熔断）
  并写审计，人工重新启用清零。
- `/bots` 管理 API（租户管理员）：CRUD（凭据 Fernet 加密+掩码回显）、测试连通、
  状态、会话批准/吊销、消息账本；迁移 0019（可逆）；新用户可见性=默认需批准。
- 前端「IM 机器人」管理页：机器人卡列表（状态灯/启停/测试/编辑/删除）、对话框
  （平台/凭据/绑定方式/需批准）、会话审批抽屉（批准/吊销/最近消息摘要）。
- 测试：新增 31 个全离线用例（假连接回放协议帧/MockTransport/假 redis/熔断/跨租户隔离）；后端全量 650 passed。

### 修复
- Release 流水线三平台同挂：Windows runner 默认 PowerShell 下 `mkdir -p` 在目录
  已存在时报错——补 `shell: bash`（release.yml/client.yml 各两处）；桌面壳
  tauri 版本号对齐 tag（updater 版本比较依据）。

## [1.4.0] - 2026-10-23

多形态发行第一波：桌面自动升级通道 + 手机扫码接入 + 生产部署三件套。

### 桌面客户端：自动升级（GitHub Releases 通道）
- updater 密钥对（minisign）本机生成：私钥仓外 600 权限，公开公钥入配置；
  更新源 `releases/latest/download/latest.json`，`createUpdaterArtifacts` 三平台开启。
- 壳启动 30 秒静默检查 → 底部「有新版本」提示条；`check_for_updates` /
  `download_and_install`（安装前级联停引擎，装完自启）/ `get_app_version` 命令组。
- 新增 `release.yml`：推 `v*` tag → macOS/Windows/Linux 三平台签名构建 →
  安装包 + `.sig` + 合并 `latest.json` 自动挂 GitHub Release（tauri-action）。
- 修复桌面壳存量编译错误（updater 引入前的 lib.rs 历史问题），`cargo check` 全绿。

### 手机扫码接入（局域网）
- `POST /api/v1/pairing/codes`：一次性配对码（只存 sha256、TTL 300s、同租户未用码
  上限 5），pair_url 携带局域网 IP；`/pairing/exchange` 原子占用防重放
  （无效/过期/已用同文案防枚举），换发 30 天设备令牌（type=pair，常规 API 物理隔离）。
- 设置页全新「手机接入」标签：点击复制链接二维码生成（300s 倒计时、过期自动置灰）、
  设备台账（名称/到期/吊销）。
- 桌面引擎 `standalone --lan`：显式开启才绑 0.0.0.0；二维码端口=实际监听端口。

### 生产部署
- `docker-compose.prod.yaml`：单机一键自包含构建（api/worker/scheduler/web +
  pgvector pg17 + redis7 带密码；密钥缺失 compose 层直接拒启）。
- `.env.production.example` 模板；服务器版 `deploy/native/server/upgrade.sh`：
  备份(SQLite/pg_dump) → 切 tag → 迁移 → 重启 → 健康检查失败自动回滚（含库恢复）。
- 测试 607 → **619**；迁移链新增 0018_pair_devices（可逆已验证）；openapi contracts（119 paths）。

## [1.3.0] - 2026-10-22

供应商体验升级 + 本地模型放行 + 自生长闭环。

### 供应商：默认模型改为下拉自动获取
- 新增 `POST /api/v1/llm/providers/probe-models`：OpenAI 兼容族 GET {base}/models 拉取模型
  （8s 超时、响应 1MB 上限、500 条截断）；anthropic/gemini 诚实回 `unsupported`；失败回 `error` + 中文原因。
- 设置页表单重排：基础地址 → **API Key → 默认模型**；地址/密钥失焦自动探测，默认模型变
  `el-select` 下拉选择（可搜索、可刷新）；探测失败自动回落「手动输入」兜底，不堵死流程。
- API Key 仅在请求内存中瞬时使用，不落库、不入日志与审计。

### 本地模型：SSRF 分级放行（修复「本地」连通失败）
- `check_egress_url(..., allow_local=True)`：LLM 供应商域（保存/测试/运行时/探测）放行
  127.0.0.0/8、::1 与内网私有段——Ollama / LM Studio 等本地推理是正当场景。
- **云元数据（169.254.0.0/16）、链路本地、组播、保留段永远封死**（任何开关都不放行）。
- 设备连接器、推送通道、MCP 服务器维持原严格策略不变。

### 自生长闭环（进化域）
- **Skill 自我升级**：`POST /api/v1/skills/suggest` 聚合近 7 天运行证据（成功样本/失败类别/
  高频工具序列）→ LLM 产出技能候选卡 → 落 `pending_review` 进既有审核队列；证据不足或
  LLM 不可用时**诚实拒绝**，不落半成品；AI 生成的技能带「AI 生成」徽章。
- **声明式插件**：`/plugins/register`（参数 + 工具/LLM 步骤，≤8 步，未知工具与 mcp__ 直接拒）、
  `/plugins/{id}/review`（批准/驳回）、`/plugins/{id}/run`（顺序解释执行，工具步骤全程复用
  风险闸与逐次审批）、`/plugins/suggest`（从历史挖掘重复出现的操作序列 ≥3 次自动合成草稿）。
- 安全底线：**自动挖掘只产草稿，启用必须经人批准**；插件内高风险工具运行时仍逐次过审批闸
  （发布闸 + 运行闸双保险，同 MCP 模式）。
- 测试 589 → 607；openapi 115 paths。

## [1.2.0] - 2026-10-22

大版本 UI 升级 + 全新对话模式。

### 设计系统 v2「静夜玻璃」（全站换装）
- 设计唯一事实源 `docs/DESIGN-v2.md`：深色玻璃拟态、靛紫→青品牌渐变（全站唯一渐变处）、圆角/控件/阴影各三档协调律。
- `styles/design-system.css`：--ds-* token + Element Plus 暗色变量桥接，覆盖 card/table/dialog/button/input/tag/menu/message/select/pagination/switch/tabs/descriptions/empty/drawer/dropdown/popconfirm 全组件；页面背景双光斑、玻璃卡片 hover、渐变主按钮。
- MainLayout 重造：232px 玻璃侧栏（可折叠 64px、分组导航小标、渐变激活条）+ 56px 顶栏（面包屑 + 用户胶囊角色徽章）；登录/注册全屏玻璃卡。
- 15 个业务页面全部套新装：旧 EP 硬编码色值清零（grep 验证 0 残留）、页头统一 .page-title/.page-actions、状态 tag 四态口径统一；顺带修复设备页空态时「新建设备」按钮被隐藏与空态文案矛盾的旧问题。

### 对话模式（新）
- `ChatView` 三段式：会话列表（自动标题/删除）→ 消息流（用户/助手气泡 + 「↳ 由 X 回答 · 规则命中/智能分类/兜底」诚实路由徽章）→ 粘性输入（Enter 发送）。
- 后端 `app/modules/chat`（迁移 0017）：会话/消息持久化（seq 序号、失败诚实落 failed 行）、复用 supervisor 三级路由或指定 agent/team 直跑、会话对创建者私有、跨用户/跨租户统一 404 防枚举。
- 乐观上屏 + 失败撤回 + 自动标题；全部门禁绿（后端 589 测试）。

## [1.1.0] - 2026-10-22

多 Agent 协作版（对标开源编排框架的系统性升级，发布见 GitHub Release v1.1.0）。

- **P0 协作内核**：LLM function-call 工具循环（风险闸/审计/护栏全复用）+ `agent_run_steps` checkpoint 断点续跑（副作用不重放）+ SSE 步骤流；迁移 0013。
- **P0 班组协作**：`agent_teams` 顺序接力 + Critic 审校 + 答案拼接策略；成员级断点续跑与成员级 SSE；工厂 as_team 一键装配；前端班组页（步骤条/实时流/失败续跑/成员下钻）。
- **观测与通道**：`llm_traces` 逐调用观测（prompt 只存 sha12+200 字隐私摘要）+ 企微/钉钉/通用 webhook 推送（SSRF 双检、Fernet、熔断、限频）+ 评估集骨架 `run_evals.py`。
- **MCP 工具连接器**：手写 streamable-HTTP JSON-RPC 客户端（SSE/JSON 双解析、1MB 限制、SSRF 每次外呼复检）+ 租户动态工具 `mcp__` 回落 + 高风险双闸（发布必平台审核 + 运行审批）+ 5 连败熔断。
- **总控问答**：`/agents/supervisor/ask` 三级路由（农业域关键词计分 → LLM 分类 → 近 7 天频率兜底，routed_by 诚实标记）+「问总控」条。
- **稳定性**：SSE 观测流 DB 异常统一 snapshot_error 帧收尾；CI 迁移前置 + 可逆链校验（根治 PG 无表红灯历史）。
- 测试 513 → 576；迁移链 0011→0016 可逆（SQLite + PostgreSQL 双环境实测）。

## [1.0.0] - 2026-10-19

首个正式版本。整合 10-17 安全整改、10-18 能力中心与 10-19 设施接入/Agent 市场/Agent 工厂三波迭代。

### 设施接入·市场·工厂·智能编排（2026-10-19）

- **设备中心（智慧农业设施接入）**：设备/读数/告警三模型迁移；Webhook 令牌接入（一次性 token、限频、审计）；自定义连接器（Fernet 密文鉴权头、SSRF 出网校验、连续失败自动熔断、beat/inline 双模轮询）；18 项农业指标字典归一（高置信同义词白名单 + `_cm` 单位后缀宽容，未知字段进 other 保原始报文）；前端设备中心页（卡片墙/一次性 token 警示/连接器映射表单/纯 CSS 数据图/告警规则管理）。
- **告警→一键工单**：设备告警一键生成高优工单（幂等：重复点击返回同一工单号），告警记录回填工单编号；复用 tasks 服务与 external_side_effect 人工审批闸。
- **`sensor.query` 助手工具**：租户内读数概况/窗口统计/规则评估，只读零副作用，已入内置工具注册表与风险闸。
- **Agent 市场**：14 个中文农业科技种子助手（安全角色内嵌红线），目录/一键装载（幂等跳过）。
- **Agent 工厂增强**：SSE 实时里程碑、启发式兜底诚实标注、蓝图→一键装配走发布分级。
- **模拟设备**：`backend/scripts/sim_devices.py` 一键造 24h 昼夜曲线数据，端到端演示零外部依赖。
- 质量：后端 492 测试全绿（ruff/mypy 零告警），前端 lint 0 error + vue-tsc build，CI 守护。

### 能力中心与 Agent 工厂（原 Unreleased，2026-10-18）

- **模型供应商管理权下放租户**（ADR-0005）：tenant_admin 可自助接入/切换本租户大模型（Fernet 密文存储、密钥永不回显、SSRF 出网校验、平台全局行只读可选）；写操作全审计。
- **声明式 Agent**：DB 配置即助手（人设+任务模板+知识范围+工具白名单），租户内免发版创建/发布/试跑；风险自动分级——低风险 tenant_admin 自批，高风险自动进平台审核队列（platform_admin 批准/驳回）。
- **Agent 工厂**：输入行业描述 → LLM 生成 3-6 角色能力蓝图（失败自动启发式兜底并诚实标注 generated_by）→ 知识库覆盖探测（0 覆盖标记 needs_knowledge）→ SSE 实时里程碑（planning→blueprint_ready→generating→completed）→ 一键装配（逐项走发布分级，人工闸门不放松）。
- 修复：SUPPLIER_KEY_SECRET 读取支持 .env；发布动作幂等化（双击/重试安全）。

### 整改升级（原 Unreleased，2026-10-17）

依据《五维测评报告（2026-10-17）》实施的安全与运营整改：

#### 安全（阻断级修复）
- **生产启动 fail-fast**：`APP_ENV=production` 时若 `APP_SECRET_KEY` 为默认占位值/短于 32 位、
  `PLATFORM_ADMIN_USERNAMES` 含 `admin` 等弱名单、数据库为 SQLite —— 直接拒绝启动（新增
  `Settings.enforce_runtime_safety()`）。
- 平台管理员 bootstrap 加固：非生产使用弱名单账号时写审计告警；注册接口拒绝保留用户名
  （admin/root/system 等）；注册密码最小长度 8（生产强制 ≥10）。
- seed 脚本生产守卫：`APP_ENV=production` 且未显式 `SEED_ALLOW_PRODUCTION=YES` 时拒绝执行；
  支持 `SEED_RANDOM_PASSWORDS=YES` 随机演示口令。
- SSRF 出网防护（新增 `core/net_guard.py`）：供应商连通性探测、模型 chat、Embedding 出网前
  校验解析后 IP，始终阻断回环/link-local（含云元数据 169.254.169.254）/保留地址；私有网段
  默认放行以兼容内网私有化模型部署（`ENFORCE_MODEL_EGRESS_GUARD=true` 可全部禁止，见 ADR-0003）。
- 自助注册可用 `SIGNUP_ENABLED=false` 一键关闭（试运行建议关闭）。

#### 任务与数据完整性
- 候选知识审核入库失败不再静默返回成功：响应透出 `ingest_ok/ingest_error`。
- Worker 在安全点消费 `cancel_requested`；queued/running 超时由周期任务回收
  （`QUEUED_TIMEOUT`/`RUNNING_TIMEOUT`）；`enqueue_failed` 的 queued 任务可重试。
- Worker 心跳：`/health/worker` 返回心跳新鲜度；compose 增加 worker 独立 healthcheck。

#### 检索与内容安全
- 混合检索关键词满覆盖保底（不被向量分稀释，阈值未经评估集标定前不再产生“有答案也拒答”）。
- 高风险内容关键词扩展（种衣剂/拌种/药种比/稀释倍数/施药/安全间隔期等）；
  新旧资料数值冲突时强制转专家复核并列出分歧值。
- 知识切块可信级按来源映射（expert_correction→expert_reviewed 等）。
- 新增真实评估集 `backend/eval/qa_eval_v1.json`（≥20 用例，含跨租户/注入类目）与 CI 评估测试。

#### 可观测性与运营
- `/metrics` Prometheus 文本端点（HTTP 计数/时延直方图、LLM 调用计数、告警计数）。
- 日志行注入 request_id（contextvar + logging Filter）；恢复 uvicorn 访问日志。
- 运维告警通道 `ALERT_WEBHOOK_URL`（配额 90%/100%、超时回收等事件推送；未配置降级 [ALERT] 日志）。
- 配额管理 API `PATCH /api/v1/admin/quotas/{tenant_id}`（写审计）。
- 周期任务 beat_schedule：超时回收/心跳/月度配额重置；生产 compose 默认启用 scheduler。

#### 知识与进化闭环
- 审核入库失败显式化（响应透出 `ingest_ok/ingest_error`）；混合检索关键词满覆盖保底；
  高风险词表扩至 14 词且新旧资料数值冲突强制转专家复核；切块可信级按来源映射；
  新端点 reparse / batch-review；评估集 `backend/eval/qa_eval_v1.json`（22 用例）+ CI 评估测试通过率 100%。

#### 前端体验（21 文件）
- 界面去演示密码/去内部术语（租户→使用空间等）、角色化菜单与路由守卫、
  AsyncState 四态、任务 5s 轮询、批量审核、重新解析、新手引导与示例问题、≤900px 响应式。

#### 交互赋能与六端客户端（借鉴 agency-orchestrator，Apache-2.0，设计移植）
- **SSE 任务事件流**：`GET /api/v1/tasks/{id}/events/stream`（stream-init/task-event/task-status/stream-end）；
  任务详情抽屉实时上屏，断线重连≤3 次自动降级轮询；关页只停"看"不停"跑"（服务端语义显式）。
- **双层验收预筛**：候选知识机器预检（纯函数断言，why 必引原话）+ 可选 LLM 深检
  （固定三标准+裁判不可靠兜底改判）；`LLM_PRECHECK_ENABLED` 开关，S1 展示性不阻断审核。
- **admin doctor 体检**：`GET /api/v1/admin/doctor` 逐项检查+fix_hint 失败即教学；密钥值红线不进响应。
- **桌面单机模式**：`TASKS_INLINE_MODE`（inline 任务/无 Redis 就绪/inline health ready）+
  `backend/standalone.py` 本地引擎 + `client/` 六端壳骨架（Tauri 桌面/安卓云端壳/鸿蒙 ArkWeb/iOS 说明）+
  手动 CI 打包流 `client.yml`；详见 `docs/client.md`。设计决策见 `docs/adr/0004-declarative-workflow-engine.md`。

#### 部署修复
- `web.Dockerfile` COPY 越出构建上下文的必败构建修复（web 构建上下文改为仓库根）。
- `api/worker.Dockerfile` editable 安装顺序缺陷修复（源码缺失时 setuptools 构建必败）→ 非 editable。
- nginx 增加 `client_max_body_size 52m`（否则知识库上传 >1MB 即 413）。
- 备份/恢复脚本参数化（动态解析 compose 卷名；修复生产项目名下静默备份空卷的事故隐患）；补执行位。
- 生产 compose：强制 `POSTGRES_PASSWORD`；redis requirepass 后各服务 URL 带凭证（修复生产必断连）；
  新增 `.dockerignore`。
- 新增 `/metrics`、`/health/worker`；生产 compose 默认启用 scheduler；beat 月度配额清零
  `quota.monthly_reset`（Redis 幂等键方案，无需 migration）。

## 0.1.0（2026-10-16）

- P0 闭环：认证/租户/RBAC、知识库 RAG（上传→解析→切块→检索→引用）、任务状态机与
  Worker、专家反馈与候选审核、Fake LLM 全链路、OpenAPI 契约与 CI。

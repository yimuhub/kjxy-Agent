# 交付前代码审查报告（2026-11-03）

> 审查对象：kjxy-Agent @ `63d57a3` + 未提交改动（进行中 v3.52.0）
> 范围：backend/app（406 个 py 文件，9.3 万行）、frontend/src（158 个文件）、client/desktop（Tauri 壳）
> 方法：全量自动化扫描（bandit / npm audit / eslint / vue-tsc / pytest）+ 四路专项逐行审查（安全权限、并发底座、commander+cluster、前端）
> 基线状态：**pytest 2254 passed、vue-tsc ✓、vite build ✓、eslint 0 error、npm audit 0 漏洞**

## 严重级别定义

- **P0**：数据丢失/安全事故级，交付前必须修
- **P1**：功能错误或安全缺陷，交付前应修
- **P2**：特定条件下触发的缺陷（并发/部署形态），应排期修复
- **P3**：质量/卫生/口径问题，可进下个迭代

---

## 一、基线门禁（全绿）

| 门禁 | 结果 |
| --- | --- |
| pytest 全量 | 2254 passed（含 commander/cluster 新模块测试） |
| ruff check / format / mypy | 全部通过 |
| eslint | 0 error（773 条格式 warning 为存量） |
| vue-tsc / vite build | 通过 |
| npm audit | 无漏洞 |
| bandit（9.3 万行） | 7 High 均为 SHA1 非加密用途（幂等键/指纹，误报性质）；5 Medium 见下文 F-1、F-2 |
| 版本一致性 | pyproject / tauri.conf / frontend package.json / desktop package.json / main.py 五处均 3.52.0 ✓ |

## 二、已确认发现（自动化扫描 + 人工复核）

### F-1【P2】用户上传 xlsx 的 XML 实体炸弹（XXE/DoS）
- 位置：`backend/app/modules/knowledge/parsers.py:315、333、374`
- 证据：`ET.fromstring(zf.read("xl/sharedStrings.xml"))` 用标准库 ElementTree 解析用户上传文档内 XML。
- 触发：上传构造的 xlsx（billion laughs 实体展开）→ 解析内存/CPU 耗尽。有上传权限的用户可打挂 worker。
- 修复：换 `defusedxml.ElementTree`（解析前 `defusedxml.defuse_stdlib()` 亦可），或解析前校验 XML 头禁 DTD。

### F-2【P3】告警 webhook urlopen 无 scheme 白名单
- 位置：`backend/app/core/notify.py:47`；URL 来自管理员配置（非用户输入），SSRF 面有限。
- 修复：限制 http/https scheme，与 `net_guard.check_egress_url` 统一。

### F-3【P2】任务幂等键永不过期 → 同一句话永远无法重新生成
- 位置：`backend/app/modules/tasks/service.py:32-34`（`get_by_idempotency_key` 无时间/状态过滤）+ `app/modules/agents/agent_generate_router.py:106-109`（键 = 小院+用户+utterance 哈希）
- 触发：用户今天生成过「病虫害助手」，明天再说同一句话 → 202 返回的是**昨天那个已 succeeded 的任务**，向导直接展示旧草稿，「重新生成」按钮失效。delete 后重试也会撞到。
- 修复：幂等键加时间桶（如 `+utcnow().strftime('%Y%m%d%H')`）或只对 24h 内/queued+running 状态的任务幂等。

### F-4【P2】self_improve 租约获取无原子性 → 多实例双跑
- 位置：`backend/app/modules/self_improve/service.py:191-201`
- 证据：`SELECT（无 FOR UPDATE）→ 检查 lease_until → UPDATE lease_until → flush → _execute（长跑）`。PostgreSQL 生产多副本部署时，两实例同刻读到租约过期 → 都持租 → 同一小院同窗口 LLM 总结跑两遍（双倍 token、记忆 supersede 语义重复）。
- 修复：`UPDATE yard_jobs SET lease_until=:now+LEASE WHERE id=:id AND (lease_until IS NULL OR lease_until <= :now)`，按 rowcount 判定抢租成功；SQLite 单机部署不受影响。

### F-5【P2，部署相关】进程内配额/限流状态清单（多 worker/多副本下失效）
全库无任何 `with_for_update` 行锁；以下状态是进程内 dict，`uvicorn --workers N` 或多副本部署时语义 ×N 失效：

| 状态 | 位置 | 影响 | 严重度 |
| --- | --- | --- | --- |
| 登录限流器 | `auth/router.py:41` `_yard_limiters` | 爆破保护 ×N 弱化（10 次上限变 10×N） | P2 |
| 搜索日配额 | `search/service.py:55` `_quota` | 500 次/天上限 ×N | P2 |
| 技能/插件生成日配额 | `market/skill_agent.py:191` `_quota` | 20 次/天上限 ×N（对照 `agent_builder.py:91` 已用 DB 计数，是正确示范） | P3 |
| 搜索源熔断/限速/cookie jar | `search/providers_builtin.py:132-136` | 每实例独立熔断，多实例对引擎请求 ×N（反爬风险加大） | P3 |
| GitHub 镜像健康缓存 | `market/github_source.py:51-52` | 仅缓存不共享，无害 | — |

- 修复建议：桌面单机（SQLite+单进程）不受影响；服务器部署形态下，配额/限流迁移到 DB 计数（照抄 `agent_builder.used_today` 口径）或 Redis。
- 正面确认：天气/LLM 供应商/知识检索等其余 `_cache` 均为只读缓存，多实例不共享仅影响命中率，无正确性问题。

### F-6【P3】仓库卫生：调试产物入库
- `frontend/tmp-checks/`（5 个调试脚本）、`backend/dev.db`、根目录若干密钥文本文件（tauri_updater_private_key 等）——确认 `.gitignore` 覆盖与打包排除；`tmp-checks` 注释已标「收口后删除」。
- 另：`backend/app/db/repair_demo_passwords.py`、`seed.py` 的 print 输出含明文密码（仅运维脚本，确认不进 API 进程日志即可）。

---

## 三、commander / cluster 专项（v3.43–v3.52 新模块，逐行审查）

### P1

**C1. 执行器竞态：失败的后台任务会把正在运行的 run 标成 FAILED**
- 位置：`backend/app/modules/commander/service.py:2449-2465`、`2294-2297`、`router.py:758-775`
- 证据：`POST /runs/{id}/execute` 只做 `run.status != RUN_QUEUED` 检查后即 `asyncio.create_task`，无原子占位（无条件 UPDATE）。用户双击 → 第二个任务读到 `RUN_RUNNING` 抛 `ERR_RUN_BUSY` → 外层 `run_in_background` 的兜底 `except` 里排除名单只有 `RUN_CANCELLED/RUN_COMPLETED`，**不含 `RUN_RUNNING`** → 把正在跑的 run 改写为 FAILED 并留误导性 `COMMANDER_EXECUTE_FAILED`。
- 修复：兜底名单加 `RUN_RUNNING`；根本修法用条件更新原子占位（`UPDATE ... SET status='running' WHERE id=? AND status='queued'` 看 rowcount）。

### P2

**C2. `GET /commander/overview` 把全小院其他成员的运行详情发给普通成员**
- 位置：`backend/app/modules/commander/router.py:189-248`
- 证据：取近 8 条 run 后逐个 `run_out(run, subtasks)`，含 `goal`、`plan_json`、子任务 `instruction`/`output_excerpt`；**未过 `require_run_access`，也无 manager 闸**（对比同文件 `/evaluations`、`/assets` 都有 `_require_manager`）。与模块 docstring「创建者或小院负责人边界」矛盾。
- 修复：非 manager 按 `CommanderRun.user_id == ctx.user_id` 过滤，或每行套 `require_run_access`。

**C3. token 预算统计系统性低估：失败/补救轮的 token 全部丢失**
- 位置：`backend/app/modules/commander/service.py:1742-1749、1649-1655、1635-1646`
- 证据：只有最后一轮 attempt 的 usage 进入 `run.tokens_total`；失败路径 result=None，token 直接丢弃（`llm_calls` 账本里有，但预算闸不看）。补救轮多时 `budget_status` 长期不告警，预算闸形同虚设。
- 修复：`_run_one` 结束（无论成败）按 task_id 前缀汇总 LLMCall 回填。

**C4. `_safe_commit` 在 SQLite 锁冲突时 rollback 后重试 commit 的是空事务——静默丢写**
- 位置：`backend/app/modules/commander/service.py:170-187`
- 证据：`except OperationalError → db.rollback() → sleep → 重试 commit`；变更已随 rollback 丢弃，重试提交空事务且正常返回，调用方以为落库成功。
- 修复：rollback 后重新 `db.add_all` 脏对象，或失败即抛出走失败矩阵。

**C5 / K1. cluster 探测 SSRF（仅平台管理员可触发）**
- 位置：`backend/app/modules/cluster/service.py:40-58、170-185`
- 证据：`normalize_base_url` 挡了凭据/查询串/控制字符，但**未挡私网地址**（127.0.0.1、10.x、169.254.169.254、内网 hostname）；`follow_redirects=False` 挡了跳板但首跳可指内网；探测回显延迟/状态码/短错误文本可做内网测绘。
- 修复：解析 host 后拒绝私网/环回/链路本地 IP。桌面单机部署危害低（P3），服务器多租户部署为 P2。

### P3（commander）

| # | 问题 | 位置 | 说明 |
| --- | --- | --- | --- |
| C6 | `record_failures` 错挂在 `auto_learn_workflows` 开关 | `router.py:454`、`learning_tasks.py:58` | 用户无法「只记失败不沉淀工作流」，疑似复制粘贴遗留 |
| C7 | 补救队列耗尽后强制 `"self"`：死代码 + 对 commander 源子任务重复自执行烧 token | `service.py:1672-1678、1471-1473` | 删除死分支；`self` 对 commander 源应直接判不可用 |
| C8 | `retry_run` 清子任务 token 但不扣 `run.tokens_total` → 总数虚高 | `service.py:2533-2557` | 口径问题 |
| C9 | `identity.ensure_commander_agent` 请求中途自行 `db.commit()`/`rollback()`，破坏调用方事务边界 | `identity.py:108、134-139` | 改 `db.flush()` 由调用方 commit |
| C10 | 持久化二次注入面：历史 goal/工作流/失败黑名单文本未转义拼进未来 prompt | `planner.py:474-522`、`service.py:1392-1397` | 已有截断与「不是指令」标注缓解，属纵深防御项 |
| C11 | 月度迭代/测评并发首建撞唯一约束 → 未捕获 IntegrityError 500 | `reports.py:367-454`、`evaluator.run_evaluation` | 捕获后重查返回 existing |
| C12 | 时区口径不一：日报告 `date.today()` 用服务器本地时区，统计窗口按 UTC 切（UTC+8 偏 8 小时）；matcher 每日上限也按 UTC 重置 | `router.py:623`、`reports.py:50-52`、`matcher.py:303-313` | 统一 UTC 或业务时区 |
| C13 | `run_in_background` 写聊天消息 `max(seq)+1` 后 insert，并发撞 `(session_id, seq)` 唯一约束 → 汇总消息丢失 | `service.py:2476-2514`、`chat/models.py:88` | 捕获 IntegrityError 重试取号 |
| C14 | `_schedule` 预算兜底改了 DB 但没同步 `state[seq]` → 已 skip 子任务被再次调度（有短路，浪费不致错） | `service.py:1966-1979` | 同步 state |
| C15 | `_is_transient` 字符串匹配过宽（错误文本含 "429"/"500" 即判瞬态） | `service.py:1050-1054` | 收紧为错误码白名单 |
| C16 | 执行器用发起人角色快照，权限回收不影响在途 run（数十分钟） | `models.py:74`、`service.py:1228-1236` | 执行前重验成员身份；至少平台管理员可强取消 |

### P3（cluster）

| # | 问题 | 位置 | 说明 |
| --- | --- | --- | --- |
| K2 | `MAX_NODES` 检查有竞态；`.limit(MAX_NODES)` 使第 51 个节点从列表消失但仍存在、且永不被 `probe_all` 探测 | `service.py:74、93-99` | 插入后校验或文档说明裁剪行为 |
| K3 | `probe_all` 同步串行：50 节点 × 3s 最长 150s 占住请求线程与 DB 事务 | `service.py:227-247` | 后台任务化或并发探测 |
| K4 | 节点离线时保留旧 `version/migration_head`，前端可能误读 | `service.py:218-222` | 离线置空或前端按 status 判读 |
| K5 | `_is_stale` 对从未探测的节点返回 False | `service.py:250-256` | 语义修正 |

### commander/cluster「确认没问题」要点（增强交付信心）

权限闸矩阵齐全（除 C2）、run 级越权统一 404 防枚举、DAG 结构性无环、配额预检前置、审批模式随 run 快照不漂移、TOOL_RISK_BLOCKED 不绕过、快照回滚同事务带备份、学习/萃取幂等且除零全有守卫、cluster 不存任何令牌无泄密面、版本比较只做相等判断（无 3.9/3.10 排序 bug）、**迁移 0058–0064 downgrade 全部对称完整**、无循环 import（三处潜在环均已用延迟 import 解开）。

---

## 四、前端专项（逐行审查 + 工具链验证）

### P2

**FE-1. 隔离 e2e 配置缺 `testMatch`/`webServer`，误用会全量跑 50 个 spec 且全部假失败**
- 文件：`frontend/playwright.e2e.config.ts`、`vite.e2e.config.ts`（注释自称「收口后删除」）
- 修复：交付前删除，或加 `testMatch: /v352\.spec\.ts/` + 独立 npm script。

**FE-2. `frontend/tmp-checks/` 调试脚本未进 .gitignore，含演示账号口令**
- `git check-ignore` 确认未被忽略；`git add .` 会带入仓库。交付前删除或加 ignore。

### P3

| # | 问题 | 位置 | 说明 |
| --- | --- | --- | --- |
| FE-3 | axios 错误 toast 无去重，`CommanderAbilities` 挂载并发 4 请求失败时叠 3-4 条同文案 toast | `api/client.ts:129-132`、`CommanderAbilities.vue:481-486` | 拦截器加同文案 2-3s 去重 |
| FE-4 | `CommanderAbilities` 双实例（设置页+悬浮伙伴）互不感知，last-write-wins；`defineExpose({reload})` 无调用方 | `CommanderAbilities.vue:414-423、489` | 抽屉打开时调用 reload 或删 expose |
| FE-5 | `identityDirty` 用 `join(',')` 顺序敏感比较 | `CommanderAbilities.vue:399-406` | 比较前排序 |
| FE-6 | `loadModel` 失败被渲染成「跟随模型中心」，用户无从纠正 | `CommanderAbilities.vue:256-280` | 失败保留 error 态 |
| FE-7 | `v352.spec.ts` 会改写共享后端指挥官模型绑定且清理条件不完整 → 重复运行漂移 | `e2e/v352.spec.ts:46-74` | 记录原值原样回放 |
| FE-8 | `sse.ts` 把服务端正常关流当失败，GET 流自然结束后白重连 3 次+降级轮询（幂等但浪费） | `utils/sse.ts:154-165` | 收到终态帧由调用方 close |
| FE-9 | `sse.ts` 对 401 不 refresh/跳转，盲重试 3 次 | `utils/sse.ts:122-134` | 401/403 跳过重试 |
| FE-10 | `sse.ts` `\r\n` 跨 chunk 边界帧粘连（后端现按 `\n` 出帧，理论问题） | `utils/sse.ts:143-146` | 归一化移到 split 前 |
| FE-11 | 设置页两个「指挥官模型」入口写不同层（settings vs agent binding），无解释文案 | `SettingsCommander.vue:345-357` vs `CommanderAbilities` | 补说明 |
| FE-12 | ElMessage 基数 z-index 2000 可能被伙伴面板（2601）盖住 | `client.ts:131`、`FloatingCompanion.vue` | 统一抬 z-index |
| FE-13 | 长会话无虚拟化；chat store 的 1.5s commander 轮询离开页面后仍继续（有界泄漏） | `ChatPanel.vue`、`stores/chat.ts:1344-1349` | 视产品口径决定 |

### 前端「确认没问题」要点

全 src **无 `v-html`**，markdown 全经 SafeMarkdown 转义、未引入新渲染依赖；产物外链白名单（拒 `javascript:` 等）且单测覆盖；预览 iframe `sandbox=""` 无脚本；CommanderFigure 814 行纯 SVG+CSS 动画、定时器全部 onBeforeUnmount 清理；FloatingCompanion 拖拽/监听成对清理、独立 store 实例不串台；chat store 乐观更新回滚与并发防护自洽；client.ts refresh 轮换/并发 401 共享 promise/防环正确；路由守卫先 fetchMe 再判权；eslint-disable 无掩盖真实问题；vue-tsc/eslint/单测/commanderArtifacts 5 用例全过。

---

## 五、任务底座与并发专项（人工复核）

覆盖 `backend/app/modules/tasks/`（service/router/stream/worker_tasks）、self_improve 调度、进程内全局状态。F-3（幂等键永不过期）、F-4（租约竞态）、F-5（进程内配额/限流清单）见第二节，此处为其余结论：

**确认没问题的要点：**
1. **SSE 流设计**（`tasks/stream.py`）：每连接短 session 快照轮询、终态后补拉防丢帧、心跳注释帧、`MAX_STREAM_SECONDS` 超时收尾、`CancelledError` 只停推送不影响任务、`Last-Event-ID` 断点续传带「清空重放」防静默丢事件——语义完整。每连接每秒一次短 session 在连接数大时有轻微压力（P3 备忘）。
2. **任务状态机**（`state_machine.py`）：queued/running 的合法迁移封闭；`worker_tasks.py:85、108` 执行前检查 status==running 防重复执行；`cancel` 限 queued/running。
3. **投递失败可恢复**：`enqueue_failed=True` 的 queued 任务可经 `retry` 重新入队（`service.py:191-204`），reaper 超时兜底——不会永久卡死。
4. **worker 双路径**（Celery / inline 线程）：`_get_session_factory` 独立会话、`asyncio.run` 每任务独立事件循环，无跨任务循环复用问题。
5. **commander 调度线程**（`main.py` inline 模式）：三线程 try/except 包裹不阻断启动，评测任务每院独立事件循环。

**遗留备忘（P3）**：SSE 高并发连接下的 session 获取频率；`tasks/router.py:_enqueue_or_inline` 在投递失败时依赖人工 retry（有审计与事件提示，语义明确）。

---

（安全权限专项见下节）

---

## 六、安全权限专项

### 人工复核确认的要点（交付信心项）

1. **密钥管理**：供应商 API key Fernet 加密存储、接口只回显 `api_key_masked`（`supplier_service.py:182`）、明文「用完即弃」（`:874 del api_key`）；grep 全库无硬编码密钥（bandit + 模式扫描双确认）。
2. **SSRF 纵深**：`web.fetch`（egress.py）严格公网模式，每次重定向重新解析校验、禁内网/环回/云元数据（实测拦截 127.0.0.1 ✓）；`net_guard.py` 对 link-local/云元数据**始终阻断**（与开关无关）；`cluster` 探测的私网面见 C5/K1。
3. **配置信任边界（部署指引，非漏洞）**：`enforce_model_egress_guard` 默认 False——按 ADR-0003 内网私有化投放允许 LLM 供应商走内网（Ollama/LM Studio 正当场景）；供应商 URL 仅限有权限的管理员配置。**公网 SaaS 部署必须在环境变量开启 `ENFORCE_MODEL_EGRESS_GUARD=true`**，写入交付部署文档。
4. **TOCTOU 备忘（P3）**：`net_guard.check_egress_url` 校验与实际请求是两次独立 DNS 解析，理论上存在 DNS rebinding 窗口；攻击面限于管理员可配的供应商/告警 webhook URL，风险可控。
5. **文件上传**：首图走扩展名+MIME 嗅探白名单（`covers.py:53、82`）、随机存储键不回显路径；zip 导入有 256KB/2MB 大小上限与文件名校验（`importer.py:38-41`）。
6. **登录限流**：按小院维度的滑动窗口限流器存在且带容量上限——但为进程内实现，多实例弱化（见 F-5）。

### 子代理专项审查结果（全路由扫描 + 40+ 路由文件逐审）

#### S-1【P2】小院设置单查接口明文回显第三方 API Key，可经逐人授权泄露给普通成员
- 位置：`backend/app/modules/yards/settings_router.py:146-158`、`settings_service.py:192-199`（对照 `public_view:519-528` 才脱敏）
- 证据：`GET /yards/current/settings/{key}` 走 `get_setting` 直接回原始 JSON；写接口契约是「空字符串=保持原密钥、明文不出仓」（`settings_service.py:484`），但单查接口没做同样脱敏。小院管理员把 `search:manage` 逐人授权给成员（设计意图是"能跑连通性测试"）后，该成员即可拿走 Tavily/Brave 明文 Key；`farm_agent`（settings:manage）同理泄露天气 Key，可在平台外盗刷。与 LLM 域「返回 Key 一律打码」口径不一致。
- 修复：`get_setting` 出口统一按 `*_api_key → *_configured` 脱敏（复用 public_view 逻辑）。

#### S-2【P2】web.fetch 的 DNS 重绑定 TOCTOU 窗口
- 位置：`backend/app/modules/search/egress.py:85-98、169-201`
- 证据：`_check_url` 自行解析一次 DNS 校验公网，随后 httpx 用 hostname **独立再解析一次**建连——TTL=0 的攻击域名可在两次解析间把 A 记录从公网切到内网，`web.fetch` 把内网服务正文回传给任意登录用户。`net_guard.py:66-69` 对解析失败域名直接放行（pass-through）加剧该面。
- 修复：校验后把 IP 固定进连接（自定义 transport 按 IP 直连 + SNI/Host 复写），或拿到响应后用 `response.extensions` 的实际对端 IP 复验公网。

#### P3（安全）

| # | 问题 | 位置 | 说明 |
| --- | --- | --- | --- |
| S-3 | cluster 探测出网未过 `check_egress_url`；probe-all 串行 50×3s 占住 worker 约 150s | `cluster/service.py:170-185` | 与全项目「出网必过 net_guard」契约不符 |
| S-4 | cluster 权限只信 JWT 角色：降权/删号后 access token 过期前（默认 30min）仍可管理节点；无 `ver` 旧令牌跳过检查 | `cluster/router.py:34-36`、`core/deps.py:56-67` | 改走 DB 权限判定；`get_current_user` 对查不到用户应 401 |
| S-5 | `/metrics` 与 `/llm/status` 无鉴权：暴露 QPS/耗时分布与部署形态信息 | `main.py:172-177`、`llm/router.py:27-28` | metrics 加管理员闸或默认关；status 挂登录 |
| S-6 | 演示账号弱口令（admin123/yard123/expert123/operator123/demo123）无生产硬闸：seed/repair 脚本可在生产库重置弱口令，fail-fast 不检测 | `db/seed.py:24-35`、`db/repair_demo_passwords.py:26-51`、`config.py:351-363` | seed/repair 加 `is_production` 硬闸；启动校验追加演示账号检测 |
| S-7 | `net_guard` `allow_local` 分支放行 IPv6 ULA，覆盖 AWS IMDS IPv6（`fd00:ec2::254`） | `net_guard.py:40-46、89-94` | allow_local 显式排除已知元数据 IPv6 |
| S-8 | 登录限流取 socket 对端 IP、不读 XFF（防伪造正确）：反代部署下全网共享同一桶，攻击者对任意用户名失败 10 次即可 5 分钟拒绝该用户全网登录且可无限续锁 | `auth/router.py:35-63`、`core/rate_limit.py:7` | 支持受信代理链 XFF；限流迁 Redis |
| S-9 | github_source 的 branch 未 `quote` 直接拼 URL（host 固定非 SSRF，仅路径注入健壮性） | `market/github_source.py:369-381` | `quote(branch, safe="")`，与 tree_ref 口径对齐 |
| S-10 | `/pairing/exchange` 公开无限流（码为 token_urlsafe(16)+sha256 存储+300s TTL，爆破不可行） | `pairing/router.py:52-57` | 套用 a2a 现成 `_rpc_limiter` |

#### 安全「已查，未发现问题」清单（12 项，交付信心）

1. **越权/IDOR**：全量路由扫描（自动提取签名+15 个可疑项逐一复核）——所有 yard 资源端点 yard_id 均取自 JWT，无请求体注入面；commander 21 端点全过 `get_yard_context`，run 级 `require_run_access` 越权统一 404 防枚举；cluster 全端点平台管理员闸。
2. **鉴权**：JWT 签名/过期/类型校验完整；refresh 带 jti+服务端会话表+改密 token_version 失效。
3. **SSRF 其余面**：十进制/八进制 IP、`0.0.0.0`、IPv6 zone 均归一后命中黑名单；内置搜索 cookie jar 为引擎匿名 cookie 不带用户凭据；`trust_env=False` 全局阻断代理变量注入。
4. **zip 导入**：路径穿越/绝对路径/盘符/`\x00`/symlink 全防，扩展名双向白名单，zip 炸弹流式读取+全局 5MB 预算（不信任声明 size），导入一律 pending_review。
5. **文件上传**：扩展名+魔数族一致性校验、2MB 上限、随机 uuid 存储键、读图按可见性 404、海报 HMAC 签名 URL。
6. **模板注入**：workflow `{{var}}` 纯文本替换、tool 步强制走注册表全链路、`mcp__` 前缀拒绝；llm 步输出不会触发工具调用。
7. **SQL 注入**：全库仅 3 处 `text()` 且为硬编码常量，无任何拼接 SQL。
8. **审批链**：审批单原子 claim（`UPDATE WHERE status='pending'`）、TTL+max_uses 原子占位、会话级借用封堵、自批策略可配+`self_approved` 审计。
9. **审计留痕**：cluster/commander/push/mcp/bots/市场/生成/审批全链路有 `log_audit`，metadata 只存标识与摘要。
10. **日志脱敏**：全库无 token/key/password 输出；`system/logs.py:60-66` 出口统一脱敏。
11. **CORS**：未配置 CORS 中间件 = 浏览器默认同源，安全；未处理异常统一 500 信封不回栈。
12. **桌面壳**：sidecar 白名单（仅内置 techyard-sidecar）、remote 域限定 127.0.0.1:18700-18710 且仅放行 2 条命令、按 PID 杀进程树。

---

## 七、交付前修复优先级清单（汇总）

**交付前必须处理（预计半天）：**
1. **S-1 设置接口明文回显 API Key**（真实安全问题，修复量小：出口统一脱敏）
2. C1 commander 执行器竞态（一行修复：兜底名单加 `RUN_RUNNING`）
3. C2 `/commander/overview` 越权可见性（加 creator 过滤，几行）
4. FE-1 / FE-2 / F-6 仓库卫生（删 tmp-checks、e2e 临时配置收口、确认 .gitignore）
5. `make contract` 重生成 openapi（3.51.0→3.52.0 漂移）
6. C11 并发首建 IntegrityError 捕获（防 500）

**交付前建议处理（预计一天）：**
7. F-3 任务幂等键加时间桶（否则用户无法重新生成同句话）
8. S-6 演示账号生产硬闸（若客户环境会跑 seed，这是上线事故隐患）
9. F-1 defusedxml 替换 ET（xlsx 上传 DoS）
10. C4 `_safe_commit` 丢写语义、C13 消息 seq 竞态捕获
11. C12 时区口径统一（UTC+8 用户日报告偏差 8 小时，客户可感知）
12. S-2 web.fetch DNS 重绑定（至少做响应对端 IP 复验）；S-5 `/metrics`/`/llm/status` 加闸

**排期跟进（下个迭代）：**
13. F-4 租约原子性 + F-5 配额/限流迁 DB（仅服务器多实例部署形态需要）
14. C3 token 预算低估、C5/K1+S-3 cluster SSRF/net_guard、S-4 JWT 角色滞留、S-6~S-10
15. C6-C10/C14-C16、FE-3~FE-13 前端 P3 项
16. `csp: null` → 配置基本 CSP（Tauri 加固）

**部署指引（写入交付文档）：**
- 公网 SaaS 部署必须 `ENFORCE_MODEL_EGRESS_GUARD=true`（内网私有化投放保持默认 false）
- 反代部署需配置受信代理（S-8），否则登录限流语义失真
- 多副本部署前先落第 13 项

---

## 八、总体结论

- **无 P0 事故级问题，无 P1 阻塞**；最重的两项 P2（S-1 设置接口明文回显 Key、C1 执行器竞态）都是小修复量。
- 功能/安全主链路（鉴权、租户隔离、SSRF 纵深、密钥管理、zip/上传防护、审批闸、审计留痕）设计扎实且有测试覆盖：12 项安全面「已查未发现问题」、前端 15 项、commander/cluster 14 项、任务底座 5 项，共 40+ 项确认清单。
- 基线门禁全绿（pytest 2254、ruff/mypy、eslint 0 error、vue-tsc、构建、npm audit 0 漏洞、bandit 仅 SHA1 非加密误报）。
- 风险集中在两类：**并发/多实例语义**（若客户仅桌面单机或单副本部署，F-4/F-5/C1 系列风险大幅降低；全库无行锁是系统性短板）与**未提交的 v3.52.0 收口尾巴**（临时 e2e 配置、tmp-checks 调试目录、契约版本号）。
- 交付建议：完成「必须处理」6 项（约半天）→ 提交收口 commit → 再出包。部署文档带上三条部署指引。

## 附录：审查方法与覆盖度

| 维度 | 手段 | 覆盖 |
| --- | --- | --- |
| 自动化 | pytest 2254 / ruff / mypy / eslint / vue-tsc / vite build / npm audit / bandit（9.3 万行）/ 契约漂移 / 版本一致性 | 全库 |
| 安全权限 | 全路由自动扫描 + 40+ 路由文件逐审 + 核心防护（net_guard/egress/importer/covers/approvals）细读 | 后端全量路由 |
| commander/cluster | 24+4 文件逐行 + 迁移 0058–0064 可逆性 + 测试覆盖评估 | 全部 |
| 前端 | 8 个交付文件 diff 逐行 + SSE/store/路由守卫/XSS 面全查 + 工具链 | 交付面全量 |
| 任务底座/并发 | tasks 全模块 + self_improve 调度 + 全局可变状态清单（17 处逐一定性） | 全部 |
| 桌面壳 | tauri.conf/capabilities/lib.rs 安全配置 | 关键面 |
| 未提交改动 | 28 个文件 diff 逐行（结论：后端为纯格式化+版本号，前端为 v3.52 功能改动已含在专项内） | 全部 |

---

## 九、整改状态（v3.53.0，2026-11-03）

「七、交付前修复优先级清单」的**必须处理**项已全部落地，逐条对应：

| 报告项 | 状态 | 落点 |
| --- | --- | --- |
| S-1 设置单查接口明文回显 API Key | ✅ 已修 | `settings_service._mask_secrets()` + `public_setting()`；`GET /yards/current/settings/{key}` 走脱敏出口，新增的 `*_api_key` 字段自动纳入；单测 + 集成测试覆盖（含「内部 `get_setting` 仍取明文」反向断言） |
| C1 commander 执行器竞态 | ✅ 已修 | `execute_run` 条件更新原子占位（`WHERE status='queued'` 看 rowcount）+ `run_in_background` 兜底名单补 `RUN_RUNNING`；两条并发回归测试 |
| C2 `/commander/overview` 越权可见性 | ✅ 已修 | 非管理者按 `CommanderRun.user_id == ctx.user_id` 过滤「最近运行」，与 run 级 `require_run_access` 对齐；越权回归测试 |
| FE-1 / FE-2 / F-6 仓库卫生 | ✅ 已处理 | `frontend/tmp-checks/`、`vite.e2e.config.ts`、`playwright.e2e.config.ts` 写入 `.gitignore`（隔离 E2E 仍在用，收口时删除本地文件） |
| `make contract` 契约漂移 | ✅ 已重生成 | `contracts/openapi.yaml` 随版本面同步（361 路径，无接口增减） |
| C11 并发首建 IntegrityError | ✅ 已修 | `run_iteration` 插入包 SAVEPOINT，撞 `(yard_id, month_key)` 后回查返回对方行；回归测试 |
| C13 消息 seq 竞态（顺带处理） | ✅ 已修 | 汇总消息取号 + 插入包 SAVEPOINT，撞 `(session_id, seq)` 最多重试三次取号，不再整条丢失；回归测试 |
| FE-12 全局提示被伙伴面板盖住 | ✅ 已修 | `api/client.ts` 统一错误 toast 显式 `zIndex: 2700` |

**未处理（按报告口径转入排期）**：「交付前建议处理」的 F-3 幂等时间桶、S-6 演示账号生产硬闸、F-1 defusedxml、C4 `_safe_commit` 丢写语义、C12 时区口径、S-2 DNS 重绑定、S-5 `/metrics` 加闸，以及「下个迭代」的 F-4/F-5 多实例项、C3 token 预算低估、C5/K1+S-3 cluster SSRF、S-4/S-7~S-10、C6~C10/C14~C16、FE-3~FE-13（FE-12 除外，已完成）。三条部署指引仍适用。

# 科技小院 AI 平台 v3.6.0 · 全维度状态与跃迁报告

- 审查基线：`v3.6.0`（提交 `d8c1898`，标签已推送，2026-10-28）
- **3.6.1 更新（2026-10-28）**：报告中的 P0 部署项已落地——生产 compose 修复并加入 migrate 服务、服务器安装改随机口令/生产默认、CI 增加 compose/版本/前端单测/镜像构建、ready 校验 schema、SSE 代理超时放宽、镜像补齐 contracts/plugins。以下 P0/P1 保留为原始基线与整改对照。
- 审查方式：五个职能视角并行静态审查 + 代码事实核验 + GitHub 同类平台对标 + 小院角色旅程推演
- 一句话结论：**工程底座已经达到"敢给真实小院试点"的水平，但产品仍停在"平台能力展示"；下一步的胜负手不是再加 Agent，而是把"地块—农事—诊断—告警—报告"串成每天都会用的工作流，并让每条结论可评测、可追溯。**

---

## 0. 结论速览

### 0.1 评分（10 分制，按"真实小院愿意每天用、敢对外服务农户"的标准）

| 维度 | 评分 | 判断依据 |
|---|---:|---|
| 架构与后端工程 | 8.0 | 模块化单体边界真实，契约/迁移/任务/审计完整；农业领域深度不均 |
| 安全与多租户 | 8.0 | 小院隔离、RBAC、审计、审批、SSRF 防护成体系；安全测试仅 16 条，缺外部渗透与密钥轮换演练 |
| 功能完整度 | 7.0 | 平台能力很全且多数可跑；但聊天引用被丢弃、默认 mock embedding、多个农业/科研/配对模块是脚手架，名实不完全相符 |
| 交互与视觉 | 7.0 | 设计体系统一、状态齐全、0 处 `v-html`；但非管理员首屏报错、主链路四处断点、5 个巨型视图 |
| 可运维/可交付 | 7.0 | 三套部署 + CI 门禁齐全；3.6.1 已修生产 compose/安装默认/迁移就绪；仍缺 trace 与发布镜像校验 |
| 可推广性 | 6.5 | 农业纵深与"专家纠错沉淀"是真差异；缺农户角色、微信触达、内容运营与定价分层 |
| **综合** | **7.5** | 3.6.1 修复部署硬伤后，从"可试点"向"可规模化运营"推进了一步 |

### 0.2 三个最重要的判断

1. **最强的资产不是"多智能体"，而是"小院自有知识 + 可溯源回答 + 专家纠错入库 + 小院级治理 + 多形态交付"这五件事的组合。** 通用平台很难同时具备，这是唯一站得住的壁垒。
2. **最大的风险不是功能不够，而是"名实不符"与"最后一公里"。** `pest_alert` 仍是脚手架 + FakeProvider；工单下发是 dry-run；聊天的引用被后端丢弃；生产 compose 与服务器安装默认已在 3.6.1 修复。剩下的风险集中在 AI 链路本身。
3. **产品缺的不是又一个入口，而是农业主键。** 全库没有地块、农户、作物周期、农事记录实体，导致农事规划、诊断、告警聚合、季报、交接全部悬空；这也解释了为什么"每天都会打开"的理由还不成立。

---

## 1. 审查范围与硬事实

| 项目 | 事实 |
|---|---|
| 后端 | 29 个领域模块，约 51,300 行 Python；FastAPI + SQLAlchemy 2 + Alembic + Celery/内联任务 |
| 前端 | 约 39,400 行 TS/Vue；27 条路由、19 个业务页 + 3 个公共页 + 404；一级菜单 11 项/4 组 |
| API 契约 | 191 条路径、230 个操作；`contracts/openapi.yaml` 有 CI 漂移检查 |
| 数据 | 36 个迁移；PostgreSQL + pgvector 为生产库，测试与验收可用 SQLite |
| 测试 | 1,200+ 个测试函数（pytest 收集 1,319 条）：unit 769、integration 409、security 16、eval 2 |
| CI 实际门禁 | Postgres+pgvector 起库 → 迁移 → ruff → mypy → pytest → 迁移回滚 → OpenAPI 漂移；前端仅 lint + build |
| 交付 | Docker Compose、macOS/Windows/Linux 原生、服务器 systemd、Tauri 桌面端与 updater |
| 代码卫生 | TODO/FIXME 仅 16 处（说明不是"堆功能不收拾"）；但前端有 4 个 1,200–1,900 行的巨型视图 |

**本次审查的局限**：CI 与本地验收使用 fake LLM + mock embedding，真实模型延迟/失败率/成本未压测；本机搜索源不稳定，联网链路只验证了降级与告警；安全维度由主审与后端审查交叉完成，**未做外部渗透与自动化红队**；未做真机弱网与 10 万级向量容量测试。

---

## 2. 真实能力地图

> 口径：**已形成闭环** = 有页面、有接口、有持久化、有权限、有测试；**半成品** = 链路存在但关键一段是规则/模板/dry-run；**占位** = 只有定义或提示词。

### 2.1 已形成闭环

| 能力 | 用户可见形态 | 关键实现 |
|---|---|---|
| 多小院身份与治理 | 注册审批、邀请、批量导入、角色矩阵、审计 | `yards/`、`auth/`、`audit/`；所有业务表强制 `yard_id` |
| 知识库闭环 | 上传→解析→切块→向量化→混合检索→引用→反馈→候选→专家审核→入库 | `knowledge/service.py:399`、`evolution/`；9 类文档格式 |
| 知识问答 | 带引用的回答、点踩纠错、转专家 | `agriculture/qa_agent.py`、`QaView.vue` |
| 对话工作台 | 多模型、附件、SSE 步骤、工具审批、文档生成 | `chat/`、`llm/`、`tools/`；md/docx/xlsx/pptx/pdf |
| 智能体与编排 | 专业助手、自建助手、群聊（all/named/auto）、@点名、JSON 工作流（tool/agent/validation 三节点 + 步数/超时限制） | `agents/`、`chat/group.py`、`agents/workflow_runner.py` |
| 一句话生成 | 技能、插件、智能体（AgentSpec 六段框架 + 技能匹配 + 待补清单 + 配额） | `market/skill_agent.py`、`market/plugin_agent.py`、`agents/agent_spec.py` |
| 协作班组 | 共享检索（K#/W#）→ 成员接力 → 审核员 → 模型综合 → 记录/文档 | `teams/research.py`、`teams/service.py`、`teams/scheduler.py` |
| 定时任务 | 班组周期运行、自我总结、失败记录、忙闲互斥、超时回收 | `teams/scheduler.py:153`、`self_improve/worker_tasks.py` |
| 技能/插件市场 | 内置/GitHub/Gitee/本地导入、审核、归属（私有/小院/平台）、安装 | `market/`；14 个农业助手 + 12 个农业技能 |
| 设备与告警 | 站点、webhook 接入、字段映射、阈值规则、30 分钟去重、站内/IM 推送 | `devices/`、`push/` |
| 农业领域助手 | 农业问答、作物诊断（规则+知识库）、农事规划（规则模板）、科研统计（纯函数）、经营报表 | `agriculture/`、`crop_diagnosis/`、`farm_planner/`、`research_assistant/`、`data_analyst/` |
| 管理与洞察 | 负责人驾驶舱、按人用量、会话审计、配额、模型供应商、系统设置 | `insights/`、`owner` 路由、`llm/` |
| 集成与交付 | MCP 桥、IM 机器人（企微/钉钉/飞书）、桌面端、原生/服务器部署 | `mcp/`、`bots/`、`client/desktop/`、`deploy/` |

### 2.2 半成品（链路在，关键一段不真）

| 能力 | 现状 | 证据 |
|---|---|---|
| 虫情预警 | 脚手架生成，检索后由 **FakeProvider** 拼答案，前端列表被过滤 | `pest_alert/service.py:13-14,75-100` |
| 作物诊断 | 6 条关键词规则 + 知识库检索，**不调模型、不识别图片**；用药一律拒答 | `crop_diagnosis/service.py:4,37-94` |
| 附件理解 | 图片只留存与传递，**不做 OCR、不做像素分析** | `chat/attachments.py:14-15` |
| 工单下发 | `workorder.dispatch` 是 **dry-run**，只记录意图，无执行人/期限/回执 | `tools/workorder_tool.py:44-63` |
| 农事规划 | 仅小麦/玉米/水稻三套模板，地块为自由文本，结果不落库 | `farm_planner/templates.py`、`farm_planner/service.py` |
| 政策服务 | 增删查与规则匹配可用；**无采集器**，画像每次手填不持久化 | `policy/service.py`、`PolicyView.vue` |
| 内置农业助手 | 14 个均为 persona + 提示词模板；"农事日志"不存任何记录 | `market/seeds.py:86-413` |
| 告警处置 | 能告警、能推送；缺"派工→回执→复诊"闭环 | `devices/service.py`、`push/` |
| 数据分析 | 汇总的是平台自身（任务/文档/调用），不是农业数据 | `data_analyst/service.py:1-3` |
| 聊天引用 | 单聊执行后只回 `answer`，后端把 `citations` 丢弃，聊天界面无法溯源 | `agents/supervisor.py:288-297` |
| 语义检索 | 默认 `EMBEDDING_PROVIDER=mock`（字符 bigram），实际接近关键词；pgvector 列硬编码 1536 维 | `core/config.py:51-54`、`knowledge/models.py:26` |
| 流式回答 | SSE 是"步骤快照 + 最终消息"，不是 token 级流式 | `agents/stream.py:75-170` |
| 自我提升记忆 | 能生成偏好记忆，但运行时 prompt 从不读取，属"只写不读" | `self_improve/`、`declarative.py:1339,1426` |
| 手机配对 / 飞书 | 配对令牌无消费者；飞书只入不出，SDK 未入依赖 | `pairing/service.py`、`bots/feishu_adapter.py:103` |
| 市场 Agent 安装 | 仅内置 seed 可装，其余返回 `MARKET_AGENT_UNSUPPORTED` | `market/installer.py:448-458` |

### 2.3 仅界面/占位

- `pest_alert` 无独立页面与表单，仅作为 Agent 定义存在。
- 试验示范、培训推广、成果报告模板：无实体、无页面。
- 部分统计字段仍是占位（如小院近 7 天调用次数）。
- 总控候选只含自建 Agent 与班组，内置 4 个专业助手不在候选，新小院可能直接 404（`supervisor.py:100-135,311-316`）。

---

## 3. 五职能会诊

### 3.1 后端架构（石磐安 / 彭森宇视角）

- **做对的**：模块化单体边界真实；`api/v1` 只做鉴权/校验/调用；Agent/Tool/Task 三个注册表解耦；JSON 工作流引擎有节点白名单、工具/Agent 存在性校验、`max_steps` 与超时（`agents/workflow_runner.py:1-25`）；v3.5/v3.6 把长请求改成 202 + `tasks` 表 + SSE + 超时回收，方向正确。
- **链路断点**：单聊执行后只回 `answer`，`citations` 被丢弃（`agents/supervisor.py:288-297`），所以"聊天有引用"并不成立；SSE 是步骤快照而非 token 流（`stream.py:75-170`）；聊天里选中班组走同步执行（`supervisor.py:258-265`），长任务会顶住请求。
- **检索质量**：默认 `EMBEDDING_PROVIDER=mock`，字符 bigram 向量近似关键词；pgvector 列硬编码 1536 维，与可配 `embedding_dim` 脱节；无 cross-encoder/LLM 重排，仅 `alpha*向量+(1-alpha)*关键词`（`knowledge/service.py:393,477`）。
- **主要问题**：领域深度极不均——`agents` 9,600 行、`market` 5,000 行、`chat` 3,900 行，而 `crop_diagnosis` 397 行、`pest_alert` 237 行；"平台层"远厚于"农业层"。同步 SQLAlchemy 在 async 路由中仍是主流写法（`async def` 内直接 `db.scalar`），高并发下会占线程池。
- **判断**：架构不需要重写，需要"收口"——把脚手架模块改成真实实现或明确下线，把农业主键补上，把引用与 token 流打通。

### 3.2 安全与多租户（安全负责人视角）

- **做对的**：`yard_id` 强制过滤、跨院 404、JWT 刷新单飞、私有 Agent/班组 ACL、审批原子占位、SSRF 防护（`search/egress.py` 每次重定向重新解析并校验，拒绝 loopback/私网/元数据）、供应商密钥 Fernet 加密（`llm/supplier_crypto.py`）、生产启动校验默认密钥、审计贯穿；全仓无 `subprocess/eval/exec`，插件是声明式而非可执行代码。
- **主要问题**：安全测试仅 16 条（跨院认证/知识库/登录限流），缺 prompt injection、MCP、插件、机器人、文档下载的负向用例；提示注入防护主要靠系统提示与标签转义（`teams/research.py:146-162`），第三方技能正文仍原样拼入 system prompt（`declarative.py:1017-1039`），审核是唯一防线；refresh token 不轮换、logout 只审计不吊销（`auth/service.py:240-301`）；RBAC 双轨——6 处仍按 JWT 角色、工具注册表走旧表，自定义角色覆盖不生效；access/refresh token 存在 `localStorage`（`stores/auth.ts:13-27`），叠加桌面端 `csp: null` + `withGlobalTauri: true`（`tauri.conf.json:10-13`），一旦远程内容被污染，token 与 IPC 同时暴露；登录限流是进程内内存实现（`core/rate_limit.py:8-10`），多 worker 下会失效；密钥轮换与外部渗透未演练。
- **判断**：基线合格，但"AI 特有攻击面 + 桌面端运行时"还没有形成可重复的防线。

### 3.3 前端与体验（颜可见 / 韦悠然 / 涂悦目视角）

- **做对的**：设计令牌统一、路由全懒加载、`AsyncState` 覆盖空/错/载、可访问性基线（跳过链接、aria、焦点样式）、移动端抽屉；全仓 **0 处 `v-html`**，班组结果用 `SafeMarkdown` 纯文本节点转义 + `safeHttpUrl`，是全仓最佳实践。
- **首屏即错**：工作台"平台概况"调 `adminApi.stats`，但后端仅 `admin:stats` 可读；operator/viewer/expert 一登录就看到"无权限"。"待办任务"只取前 5 条、"解析失败"硬编码 0、"前往审核"对 operator 会被守卫弹回首页（`Dashboard.vue:146-197,245-259`、`admin/router.py:26`）。
- **主链路四处断点**：知识库没有"库/分类"、上传后不轮询解析状态（`KnowledgeView.vue:49-75`）；智能体只能绑"本小院/公开作物"粗粒度，刚建的资料无法指定给某个 Agent/班组（`CustomAgentsView.vue:660-668`、`factory_blueprint.py:87`）；班组仅管理员可建而空态指向管理员入口（`TeamsView.vue:31,630`）；定时只有"每 N 分钟"、完成无回传（`TeamScheduleDialog.vue:10-13`）。
- **一致性**：对话与知识问答按纯文本渲染，`**`/表格原样露出（`ChatPanel.vue:104-111`、`QaView.vue:147-149`），与班组 Markdown 不一致；角色标签三份、技能元数据两份、上传控件四套。
- **数据层**：`client.ts` 的 401 单飞刷新正确，但 SSE 用原生 fetch，401 不刷新（`utils/sse.ts:128-134`）；`selectSession` 无请求序号，快速切换会被旧响应覆盖（`stores/chat.ts:253-273`）；全仓约 150 处 `catch {}`。
- **体积与拆分**：`CustomAgentsView` 1,839 行、`MembersView` 1,618、`SettingsView` 1,573、`ChatPanel` 1,344、`TeamsView` 1,281；首屏 gzip ≈276 KB（CSS 54.5 KB），`MainLayout` 静态引入仅平台管理员用的总览与 768 行拖拽编辑器。
- **测试**：单测仅 teams 19 例；E2E 25 例但不进 CI，且"全部业务页无加载失败"因 `CustomAgentsView` 吞错而假绿。
- **判断**：视觉不是短板；**信息架构、权限一致性、组件拆分与质量门禁**是短板。

### 3.4 运维与质量（常守恒 / 查无漏视角）

- **做对的**：CI 起真实 Postgres+pgvector、迁移回滚、OpenAPI 漂移、三平台发布、健康检查 live/ready/worker、备份恢复文档、桌面 updater 的 minisign 验签；Celery 开了 `acks_late`、`prefetch=1`、`reject_on_worker_lost`。
- **阻断项一（3.6.1 已修）**：`compose.production.yaml:46` 原为 `${POST...?rd}`，`docker compose ... config` 退出 15；该行自 2026-10-17 引入后未被发现。现已修复并加入 CI 校验。
- **阻断项二（3.6.1 已修）**：服务器版安装脚本曾复制 development 配置并灌入固定口令账号；现改为生产默认 + 随机管理员/口令 + `.initial-credentials`。
- **主要问题**：
  - Docker 的 API 容器只跑 uvicorn、不执行迁移（`deploy/docker/api.Dockerfile:15`），`/health/ready` 只 `SELECT 1`（`main.py:107-119`），旧 schema 也会显示 ready；nginx `proxy_read_timeout 120s` 短于长任务/SSE。
  - Celery 无 `time_limit`/`autoretry`；`enqueue_task` 在 broker 慢 3 秒后转本地线程，但原发送线程未取消，可能**同时执行两次**（`tasks/worker_tasks.py:53-71`）；reaper 只存在于 beat，桌面端/原生服务器没有 reaper，崩溃任务会永久停在 running。
  - 配额 `check_and_consume` 先读后写，并发会超卖；普通 `/tasks` 与知识上传不扣减；月度重置依赖 beat。
  - Release 不跑测试、不构建/推送 Docker 镜像、不校验 tag 与版本一致；mac/Windows 未签名，Linux 只出 deb 不出 AppImage，桌面自动更新在 Linux 上缺包。
  - 业务测试跑 SQLite（PG 行锁与 `<=>` 未覆盖），`conftest` 未固定 `DATABASE_URL` 会写 `backend/dev.db`；无覆盖率阈值；Docker 镜像 `.dockerignore` 排除 `plugins/`。
  - `LLMTrace` 会保存 prompt 前 200 字，可能落 PII；`notify` 的去重集合从不清理。
- **判断**：日常发布靠人盯，规模化前必须补"机器能发现"的门禁（compose、E2E、评测、trace、迁移版本）。

### 3.5 产品与领域（蓝图画视角）

- **真实旅程断点**：负责人建小院→传手册→装助手→配设备→配机器人→看驾驶舱，其中设备接入受厂商协议限制、农户收不到个人微信、驾驶舱没有示范面积/服务农户数/产量；研究生"拍照→诊断→方案→派工→记录→周报→交接"七步中，拍照不识别、诊断不调模型、派工是 dry-run、记录无表、交接无档案；农户基本不在系统内。
- **差异化**：小院自有、可溯源、可审核的农技知识 + 本院设备读数，这个组合是通用平台给不了的。
- **判断**：定位应明确为"小院的知识与作业操作系统"，而不是"农业版 Dify"。

---

## 4. 工程资产（值得保留并放大的 8 件事）

1. **契约先行**：OpenAPI + 错误码 + CI 漂移检查，是国内同规模项目里少见的纪律。
2. **小院级治理**：`yard_id` + RBAC + 审计 + 审批，直接对应"多小院、多角色、可追责"的真实组织形态。
3. **知识闭环**：问答侧引用可回溯、纠错经专家审核才入库，避免"AI 把错误经验固化"；但聊天侧引用被丢弃，这条优势目前只在问答页成立。
4. **异步任务底座**：202 + 任务表 + SSE + 超时回收 + 定时调度，已具备长任务产品的骨架。
5. **交付形态完整**：Web、桌面端、原生安装、服务器 systemd，覆盖"没网/弱网/无运维"的小院现实。
6. **测试量真实**：1,200+ 个测试函数，覆盖权限、RAG、设备、机器人、班组；不是"只测 happy path"。
7. **文档与版本纪律**：架构、数据库、API、安全、运维、验收、ADR 齐全；3.x 小版本节奏稳定。
8. **诚实的降级**：搜索不可用、模型未配置、embedding 为 mock 时明确告警，不伪造来源。

---

## 5. 风险清单

### P0（上线阻断，应立即修）

1. ✅ **生产 compose 非法插值（3.6.1 已修）**：`compose.production.yaml:46` 原为 `${POST...?rd}`，`docker compose ... config` 退出 15。现已修复并实测通过；`build: ~` 也改为 `!reset`，生产不再从源码构建。
2. ✅ **服务器版安装脚本交付即接管（3.6.1 已修）**：原脚本使用 development 配置并灌入固定口令演示账号；现改为 `APP_ENV=production`、随机管理员用户名与随机口令，写入 `.initial-credentials`，SQLite 需显式 `ALLOW_SQLITE_IN_PRODUCTION=true`。
3. ✅ **CI 不校验 compose（3.6.1 已修）**：CI 新增 compose（基础+生产覆盖）、版本面一致性、前端单测与三镜像构建。

> **本周可完成的最小修复**：① `compose.production.yaml:46` **已本地修复并用 `docker compose config` 验证通过（未提交）**；仍建议在 CI 加同一条 `config` 校验（用假环境变量）；② `install.sh` 默认写 `APP_ENV=production`、不执行 seed，或至少 `SEED_RANDOM_PASSWORDS=YES`；③ 合并 `docker-compose.prod.yaml` 与 `compose.production.yaml`，只保留一套生产编排。

### P1（规模化前必须处理）

1. **名实不符的 Agent**：`pest_alert` 用 FakeProvider；`farm_journal` 等只存提示词。要么实现，要么下线，不能留在市场里。
2. **农业主键缺失**：无地块/农户/作物周期/农事记录，导致规划、诊断、告警、报告、交接无法沉淀。
3. **工单 dry-run**：告警无法形成处置闭环，"设备投入"看不见回报。
4. **安全测试与红队不足**：16 条安全测试，缺 prompt injection/MCP/插件/机器人的系统化负向用例。
5. **前端质量门禁缺口**：Playwright 与 `test:unit` 不进 CI；"全部业务页无加载失败"因 `CustomAgentsView` 吞错而假绿；巨型页面持续膨胀。
6. **可观测性不足**：进程内 metrics、无 trace、无告警规则；`LLMTrace` 还会落 prompt 前 200 字，存在 PII 风险。
7. **工作台权限错位**：非管理员首屏"无权限"、待办只算前 5 条、失败数恒 0、审核按钮必弹回。
8. **SSE 会话过期不刷新**：长会话 30 分钟后只报"连接中断"，而普通请求已有单飞刷新。
9. **知识库与 Agent/班组不连通**：只能粗粒度绑定，无法把某批资料指定给某个助手。
10. **桌面端安全配置**：`csp: null` + `withGlobalTauri: true`，远程模式任意 URL 均可获得 IPC；更新提示没有正式前端入口。
11. **镜像内缺插件**：`.dockerignore:19` 排除 `plugins/`，容器化部署拿不到作物包/工作流模板；默认工作流只是勉强回退。
12. **任务与配额可靠性**：Celery 无 `time_limit`；broker 慢时本地线程与 Celery 可能重复执行；reaper 只在 beat 存在；配额先读后写会超卖，普通任务/知识上传不扣减。
13. **部署一致性**：API 容器不跑迁移、ready 只查 `SELECT 1`；nginx 120s 读超时短于长任务/SSE；Release 不构建 Docker 镜像、不校验 tag/版本，mac/Windows 未签名，Linux 无 AppImage（自动更新缺包）。
14. **聊天的"引用"名不副实**：后端丢弃 `citations`，聊天界面无法溯源；这是"可信"叙事里最不该缺的一环。
15. **会话安全细节**：refresh token 不轮换、logout 不吊销；RBAC 双轨导致自定义角色在部分接口失效；第三方技能正文直入 system prompt。
16. **半成品入口未下线**：手机配对令牌无消费者、飞书只入不出、市场 Agent 仅内置可装、自我提升记忆"只写不读"。

### P2（体验与工程卫生）

1. 19 个业务页、4 条"提问"入口、4 条"建智能体"路径；术语仍偏技术；关键输入（地块）为自由文本。
2. 前端 5 个 1,200+ 行视图；API 层 `modules.ts` 1,723 行；角色标签三份、上传控件四套。
3. 对话/问答不复用 SafeMarkdown，Markdown 原样露出；窄屏对话仍保留 200px 会话栏。
4. 政策画像不持久化；分析报表与农业数据脱节。
5. 桌面端签名/更新链路历史上多次因密钥密码不匹配失败，需固定演练。
6. 登录限流、`/metrics` 均为进程内实现，多副本部署时统计与限流失效。

---

## 6. GitHub 对标：我们站在哪里

| 维度 | 本平台 v3.6 | Dify | Coze Studio | FastGPT | RAGFlow | MaxKB | Bisheng | n8n | LobeHub | WeKnora |
|---|---|---|---|---|---|---|---|---|---|---|
| 定位 | 农业垂直 + 小院治理 | 通用 LLM 应用平台 | 通用 Agent 平台 | 知识库问答 | 深度 RAG 引擎 | 企业知识库 Agent | 企业 LLM DevOps | 自动化工作流 | Agent 团队/桌面 | RAG + Agent + Wiki |
| 可视化编排 | 无画布（JSON 工作流 + 声明式 + 一句话生成） | 有 | 有 | 有 | 有（Agentic） | 有 | 有 | 有 | 部分 | 有 |
| 多智能体 | 班组接力 + 审核员 | 工作流内 | 工作流内 | 工作流内 | Agentic 检索 | 工作流 | Task Mode | 多节点 | Agent Groups | Agent |
| 定时/主动 | 有（班组/自我总结） | 触发器 | 触发器 | 有 | 有限 | 有限 | 有 | 强 | 有（Scheduling） | 有限 |
| RAG 深度 | pgvector + 关键词混合，无重排 | 中 | 中 | 中 | **强**（深度解析/Graph） | 中 | 强（私有 OCR） | 弱 | 中 | **强**（混合/多模态） |
| 评测/追踪 | 自研 eval runner + llm_traces + /metrics（部分） | **强**（Opik/Langfuse/Phoenix） | Coze Loop | 弱 | 中 | 弱 | 中 | 执行日志 | 中 | **强**（eval/tracing） |
| 自托管多租户/RBAC | **有** | CE 单空间，RBAC 企业版 | 有基础 | 有团队 | 有团队 | 有角色 | **强**（RBAC/ReBAC） | 企业版 | 有 | 有 |
| 市场/插件 | 农业技能/插件 + 审核/归属 | **大生态** | **大生态** | 有 | 有 | MCP | 有 | **大生态** | **10k+ Skills** | 有 |
| 交付形态 | Web + 桌面 + 原生 + systemd | Docker/云 | Docker | Docker | Docker | Docker | Docker | Docker/云 | Web/桌面 | Docker |
| 农业领域纵深 | **有**（但深浅不一） | 无 | 无 | 无 | 无 | 无 | 无 | 无 | 无 | 无 |

**五条可借鉴、但不照搬的结论：**

1. **借交互，不借产品壳**：Dify/Coze/FastGPT 的"引用可点开、失败可重试、编排可视化"值得借；整包引入会稀释农业纵深，且 Dify/FastGPT 的许可证含附加条款。
2. **补评测与追踪，而不是补功能**：RAGAS 的 faithfulness/context precision/recall、OpenTelemetry GenAI 语义约定、Langfuse 的 trace/成本，是"越用越可信"的证明链。
3. **借 RAG 深度**：RAGFlow/WeKnora 的深度解析、混合检索、GraphRAG、可解释切片，正好补当前"PDF/扫描件/表格"短板。
4. **借主动触达**：n8n 的调度/错误处理/执行日志、LobeHub 的 Agent 团队与 Scheduling、Coze 的渠道发布，说明"主动运行"是平台标配，而本平台已有底座，缺的是渠道（微信）。
5. **不要追通用市场**：通用平台的市场是"插件数量"，本平台的市场应是"经过验证的农业技能包"（作物/区域/季节/审核人/成功率），这才是壁垒。

---

## 7. 站在小院负责人/驻院研究生的一天

### 7.1 三个真实旅程的断点

| 角色 | 想做的事 | 系统现状 | 断点 |
|---|---|---|---|
| 负责人 | 建小院、拉人、装能力、看效果 | 注册/邀请/市场/驾驶舱都有 | 驾驶舱只有 Token 与成员，没有示范面积、服务农户数、产量、复购 |
| 驻院研究生 | 农户发照片→诊断→方案→派工→记录→周报→交接 | 对话/诊断/规划/文档都有 | 图片不识别、诊断不调模型、派工 dry-run、无农事记录、无交接档案 |
| 农户/合作社 | 问一句、看告警、知道下一步 | 企微机器人/扫码配对 | 无个人微信/小程序入口，无"我的地块/上次诊断/用药记录" |

### 7.2 领域缺口（按"每天都会用"排序）

1. **地块—作物—农事记录**（完全缺失）。
2. **拍照诊断单**（有诊断，但无图、无归档、无复诊）。
3. **告警→派工→回执**（告警有，处置断）。
4. **季报/日志/成果材料**（能出文档，但无数据源与模板）。
5. **农户触达**（企微群有，个人微信无）。
6. **离线/弱网**（桌面端有，手机端无）。
7. **数据交接**（审计有，业务档案无）。

> 政策背景值得注意：科技小院要求每名驻院研究生每年驻扎不少于 120 天、完成不少于 100 篇工作日志。**"日志/周报/季报"不是附加功能，而是刚需入口**；谁把这个动作做得最轻，谁就拿到日活。

---

## 8. 目标态：小院操作系统（超越当前所有功能）

不是"再加几个 Agent"，而是六层能力同时成立：

### L1 农业数据底座
地块、农户/合作社、作物周期、农事记录、投入品、产量、试验示范、传感器读数，全部以 `yard_id` 隔离、以地块为主键。**这是所有智能的前提。**

### L2 可信证据层
每条结论都能回到：知识库切片（K#）、网页快照（W#）、传感器读数、专家批注、历史诊断单。引用可点开、可版本化、可失效标记；模型推断与已核实事实强制分区。

### L3 主动智能层
传感器阈值、天气、生育期、病虫害窗口 → 自动巡检 → 告警 → 工单 → 回执 → 复诊；班组定时运行只是其中一种形态。**从"你问它答"变成"它先发现"。**

### L4 多模态田间助手
拍照识别、语音（含方言）问诊、农药标签 OCR、扫描件解析；输出"诊断单"而非一段文字，含照片、候选、置信度、处置、安全间隔期、复诊时间。

### L5 触达与协作层
企业微信/个人微信/小程序/PWA/短信多通道；专家远程会诊（带证据、带随访）；跨小院知识共享（署名、授权、分成）；A2A/MCP 互操作。

### L6 治理与经营层
SSO/OIDC、细粒度 RBAC、审计与留存、成本预算、内容包运营、版本/定价分层、私有化一键交付。

**一句话目标**：让小院负责人打开系统就能看到"今天哪块地有风险、谁去处理、处理完了吗、这个季度的成果材料还缺什么"。

---

## 9. 路线图（保持 3.x，不升 4.0）

| 版本 | 主题 | 交付物 | 验收口径 |
|---|---|---|---|
| **3.7** | 可信底座 | 聊天/班组/问答统一证据链（引用可点开、可版本化）；真实 embedding + 重排；RAG 评测集（≥100 题，faithfulness/context precision/recall）；OTel/Langfuse 追踪；成本与配额看板；compose 校验与服务器安装安全默认进 CI | 评测分数进 CI；P0 故障 5 分钟内定位；生产 compose `config` 通过；服务器安装不产生固定口令账号 |
| **3.8** | 农业台账 | 地块/农户/作物周期/农事记录；移动端快速记录；日志/周报/季报模板；导出对接全国平台格式 | 研究生日均 1 条记录；周报 3 分钟生成；交接包含完整地块档案 |
| **3.9** | 诊断闭环 | 图片诊断（视觉模型）；诊断单归档；登记药库 + 安全间隔期；专家复核与复诊提醒 | 诊断单归档率 ≥50%；用药建议 100% 带登记药与间隔期；复核 <24h |
| **3.10** | 告警到处置 | 真工单（执行人/期限/回执/照片）；告警自动开单；企微/微信推送；超时升级 | 告警→工单闭环率 ≥70%；平均响应 <2h |
| **3.11** | 多模态与弱网 | 语音问诊、标签 OCR、扫描件解析；PWA/小程序；离线队列与冲突合并 | 弱网可用；图片/语音入口占问诊 ≥40% |
| **3.12** | 协作网络与治理 | 专家会诊、跨院共享与署名、技能签名/评测/版本、SSO、A2A | 跨院共享可授权可撤回；技能上架前有评测报告 |

> 排序原则：**先可信（3.7），再有用（3.8–3.10），再多模态（3.11），最后网络效应（3.12）。** 不要先做 3.12 的"生态"，那会在数据底座为空时变成空转。

---

## 10. 如果只能做三件事

1. **农业台账（3.8）**：地块—作物—农事记录。它是最低成本的日活入口，也是规划、诊断、告警、报告、交接的共同主键。
2. **拍照诊断单（3.9）**：把"农户发照片"变成可归档、可复核、可复诊的结构化单据；用药从"一律拒答"升级为"登记药 + 间隔期 + 转专家"。
3. **告警→工单→回执（3.10）**：把设备投入变成可看见的处置闭环，这是负责人续费的理由。

**但发布前必须先做 3.7 的可信底座与 P0 修复**——没有评测与 compose 校验，前三件事做得越快，翻车越快。

---

## 11. 验收指标（建议直接进 CI/看板）

| 类别 | 指标 | 目标 |
|---|---|---|
| 可信 | 评测集 faithfulness / context precision | ≥0.80 / ≥0.70 |
| 可信 | 引用可点开率 | 100% |
| 有用 | 新小院首次价值时间 | <10 分钟 |
| 有用 | 驻院研究生周活跃 | ≥80% |
| 有用 | 日志自动生成采用率 | ≥60% |
| 闭环 | 诊断单归档率 / 专家复核时效 | ≥50% / <24h |
| 闭环 | 告警→工单闭环率 | ≥70% |
| 性能 | 文本回答 P95 / 单次成本 | <8s / <¥0.5 |
| 安全 | 跨院越权负向用例 | 100% 拦截，进 CI |
| 交付 | 生产 compose 校验 / E2E | CI 必过 |

---

## 12. 结语

这套系统的工程底子足以支撑"农业垂直领域最好的知识型 Agent 平台"这个目标，v3.6 的异步班组、运行记录、定时任务与检索融合也证明了团队有能力把复杂链路做真。当前最值得做的不是继续加能力，而是：

**把已有的能力变成"可信、每天用、能交接"的产品；把农业主键补上；把评测、追踪、compose 校验这些"机器能发现"的门禁补上。**

做到这三点，它就不再是"一个功能很多的 AI 平台"，而是"科技小院真正离不开的操作系统"。

---

## 附录：对标来源

- Dify：<https://github.com/langgenius/dify>
- Coze Studio：<https://github.com/coze-dev/coze-studio>
- FastGPT：<https://github.com/labring/FastGPT>
- RAGFlow：<https://github.com/infiniflow/ragflow>
- MaxKB：<https://github.com/1Panel-dev/MaxKB>
- Bisheng：<https://github.com/dataelement/bisheng>
- n8n：<https://github.com/n8n-io/n8n>
- LobeHub：<https://github.com/lobehub/lobe-chat>
- WeKnora：<https://github.com/Tencent/WeKnora>
- RAGAS 指标：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/>
- OpenTelemetry GenAI：<https://opentelemetry.io/blog/2026/genai-observability/>
- 全国科技小院服务管理平台：<https://stb.mae.edu.cn/>
- 科技小院日志与驻扎要求：<http://www.moe.gov.cn/srcsite/A22/moe_826/202404/t20240415_1125626.html>

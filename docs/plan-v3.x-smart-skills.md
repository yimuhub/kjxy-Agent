# V3.x 升级方案：绑定拆分、默认可用搜索、一句话技能/插件、技能共享与自我提升

> 状态：**v3.1.0、v3.2.0 已实现**（v3.1：搜索默认可用 + 绑定拆分 + GitHub 国内镜像/技能模板底座；
> v3.2：一句话生成技能 + 技能归属模型）；3.3–3.4 待开工。已拍板：内置源=抓取国内结果页；
> 生成技能默认私有草稿；自我总结默认关闭；公共库=小院级为主 + 平台级需平台管理员。
> 版本纪律：**持续 3.x**，直到负责人明确要求才升 4.0。每期独立小版本 + 独立标签 + 独立 CHANGELOG。
> 基线：v3.0.0（`6c4069c`，已推送）

## 0. 现状核对（代码事实）

| 需求 | 现状 | 差距 |
| --- | --- | --- |
| 编辑弹窗拆成「扩展插件 / 扩展技能」 | `frontend/src/views/CustomAgentsView.vue:605` 单个表单项「扩展技能/插件」，选项来自 `skillApi.list('enabled')` + `evolutionApi.listPlugins()` | 两类混在一个多选框；市场安装的技能/插件不在候选里；没有搜索、来源标识、已选计数 |
| 搜索默认可用 | 默认 `provider=searxng`、`searxng_url=http://127.0.0.1:8888`；`search/service.py:304-317` 未配置/不可达直接 `offline` | 没有免密钥源；把「没配源」当成「没网」；前端因此提示本地部署形态 |
| 一句话生成技能 | 只有能力中心的手工创建、对话内生成「Agent 草稿」（`chat/capability.py`）、技能建议（`/skills/suggest`） | 没有「检索匹配 → 生成 → 安装」链路；没有技能模板规范 |
| 一句话生成插件 | 只有工作流插件注册/审核/自动挖掘建议 | 没有自然语言 → manifest 生成 |
| GitHub 国内源 | `market/github_source.py` 硬编码 `api.github.com` / `raw.githubusercontent.com` | 无镜像、无健康检查、无降级 |
| 技能私有/公共 | `evolution_skills` 只有 `status`（draft/published/…）与可空 `yard_id` | 无 `visibility`、无创建者归属、无提交审核流程、负责人无推广权 |
| 自我总结提升 | 有 Celery beat + 桌面 inline 线程（`devices/worker_tasks.py:57`）、有 insights 用量、有技能建议 | 无小院级定时任务底座、无设置项、无「按周期自动总结并沉淀技能」闭环 |

## 1. 设计原则（贯穿四期）

1. **默认开箱可用**：不要求用户先配 Key、先起容器，安装完即可联网；只有真正断网才降级。
2. **能力只走注册表**：生成的技能/插件只能调用 `tool_registry` 已登记工具，不执行任意代码、不直连任意 URL。
3. **自动化止步于安装/审核**：生成可以自动，**启用与公开必须过闸**（私有技能可即时自用，公共必须审核）。
4. **每一步可解释**：生成结果带引用来源、匹配理由、风险评估；审核与推广全部落审计。
5. **成本与隐私有边界**：生成/总结有配额与预算；私有内容默认只对创建者与负责人可见，负责人查看敏感内容受 `insights:sensitive` 约束。

## 2. 版本路线

| 版本 | 主题 | 主要内容 |
| --- | --- | --- |
| **v3.1.0** | 用得上的基础 | 绑定面板拆分 + 搜索默认可用（免密钥内置源 + 状态语义修正） |
| **v3.2.0** | 一句话生成技能 | 技能模板、意图识别、本地/GitHub/Gitee 检索匹配、自动生成与安装、国内镜像；**技能归属模型（私有/小院/平台 + 创建者）随本期落地**，生成即私有草稿 |
| **v3.3.0** | 一句话生成插件 + 技能共享治理 | 插件模板生成；提交审核、审核队列、负责人推广与授权（复用 3.2 的归属模型） |
| **v3.4.0** | 自我总结提升 | 小院级定时任务底座 + 周期总结 + 技能改进提案 + 偏好沉淀 + 预算与审计 |

四期顺序有依赖：3.2 依赖 3.1 的绑定聚合与搜索可用；3.3 依赖 3.2 的模板与生成管线；3.4 依赖 3.3 的可见性模型（总结产出的技能要先有归属）。

---

## 3. v3.1.0：绑定拆分 + 搜索默认可用

### 3.1 绑定面板拆分

**后端**

- 新增 `GET /api/v1/agents/bindable-extensions`（`agents:read` 即可）：
  ```json
  {
    "skills": [{"code":"skill:<id>","name":"病虫害防治","summary":"…","source":"builtin|market|generated|imported","visibility":"private|yard|platform","risk_level":"low","bound_count":3}],
    "plugins": [{"code":"plugin:<plugin_id>","name":"灌溉调度","summary":"…","source":"builtin|market|local","status":"enabled","risk_level":"medium","bound_count":1}]
  }
  ```
- 聚合口径（**系统里装过、且当前用户可用**的全部）：`evolution_skills`（published，yard_id 为本院或 NULL）+ `market_items` 中 `status=installed` 的 skill + `plugin_manifests`（enabled，含市场安装）。3.1 阶段先不做可见性过滤，3.3 接入。
- `bound_skills` 保持现有 `skill:<id>` / `plugin:<code>` 编码；保存时校验收到的 code 必须在该用户可绑定集合内（非法 code 返回 422），避免历史脏数据被继续写入。

**前端**

- 弹窗内拆成两个独立区块：「扩展技能」「扩展插件」，各有：搜索框、来源标签（内置/市场/GitHub/本地）、风险标签、已选数量、全选/清空。
- 保存后回显：编辑时按 code 精确回显，找不到的（已下架）单独标灰提示，不静默丢弃。
- 技能/插件任一为空时给出可点击引导（去插件与技能库 / 去专家审核），不再是纯文本提示。

**验收**：E2E 覆盖「两类分区可见 → 市场安装的技能出现在技能区 → 勾选保存 → 重新打开回显 → 非法 code 被拒」。

### 3.2 搜索默认可用

**新增免密钥内置源（零配置可用）**

- 新增 `providers_builtin.py`：
  - `BingHtmlProvider`：`https://cn.bing.com/search?q=`（国内可达）
  - `SogouHtmlProvider`：`https://www.sogou.com/web?query=`
  - `So360HtmlProvider`：`https://www.so.com/s?q=`
  - 统一抽取：标题、URL、摘要；`selectolax` 解析（新增依赖），单源超时 3s、结果上限与现有 `MAX_RESULTS` 一致。
  - 只读公开结果页：不携带 Cookie/登录态、限速 ≤1 req/s、结果缓存 10 分钟、失败 3 次熔断 5 分钟。
- `provider` 枚举新增 `auto`（**新默认**）：链 = 内置三源 →（已配置的）searxng → tavily → brave。
- 设置页保留 SearXNG/Tavily/Brave 作为可选项；每个源可单独停用；新增「逐源连通性测试」（延迟 + 结果数）。

**状态语义修正（重点）**

- `network_status` 增加 `internet` 探测：`https://www.baidu.com` 与 `https://cn.bing.com` 任一可达即视为有网。
- 判定：
  - 有网 + 有源可用 → `online`
  - 有网 + 首选源失败但降级源可用 → `degraded`
  - 有网 + 全部源失败 → `degraded`，reason「联网受限，已尝试内置免费源」，**不显示「本地部署形态」**
  - 无网 → `offline` + `mode=local`，提示「本机未联网，联网插件与技能受限，请联网使用或移步专家审核做本地 Skill 管理」
- `web.search` 工具错误码保持 `NETWORK_UNAVAILABLE`，但仅在无网或全部源失败时抛出；`details` 带 `internet`、`attempted` 源列表，便于前端给出准确文案。

**合规边界（需你确认）**：内置源属于公开结果页解析，不绕过登录/验证码，遵守 robots；上线前建议确认法务口径。若你不接受抓取，备选是「一键启动本地 SearXNG」（Docker 依赖，非零配置），可用 3.1 的同一 provider 抽象替换，不影响上层。

**验收**：断网 Mock → `offline`；Mock Bing HTML → 返回结构正确的结果；有网但三源 5xx → `degraded` 且前端不显示本地形态；设置页逐源测试可见延迟与条数。

---

## 4. v3.2.0：一句话生成技能

> **落地说明（v3.2.0 已交付）**：4.1 技能模板、4.2 检索→生成→安装链路与聊天 `skill_card`、
> 4.3 GitHub 国内镜像均已实现；技能归属模型（`visibility` + `owner_user_id` + 归属视图 +
> `submit_public` 送审 + 负责人 `promote`）按「先落地归属模型」拍板随本期一起交付，
> 比原计划提前一期。小院设置 `skill_lab`（开关/配额/联网/来源上限）经
> `PUT /yards/current/settings/skill_lab` 可配，系统设置「网络与搜索」页已提供入口。
> **顺延到 v3.3**：Agent 工具化（`skill.search` / `skill.generate` / `skill.install`
> 注册为可调用工具）、一句话生成插件、审核队列与授权治理。

### 4.1 技能模板（唯一框架）

新增 `contracts/skill-template.yaml` + 代码内 pydantic `SkillSpec`（生成结果必须通过校验，不合法自动重试 ≤2 次）：

```yaml
code: pest_diagnosis            # ^[a-z][a-z0-9_]{2,60}$
name: 病虫害防治
version: 1.0.0
category: plant_protection       # 病害|虫害|栽培|水肥|农机|市场|其他
summary: 一句话说明
when_to_use: 触发场景（含关键词）
inputs:  [{key, label, required, type}]
steps:   [{kind: prompt|tool|llm, ...}]   # tool 只能引用白名单
allowed_tools: [knowledge.search, web.search]   # 默认只读
knowledge_scopes: [yard]
prompt: 系统提示（≤2000 字）
examples: [{question, answer_outline}]
eval_cases: [{input, expect}]     # 至少 2 条，用于发布门禁
guardrails: {risk_level: low, max_steps: 6, require_citation: true}
output_format: markdown
sources: [{title, url}]           # 生成依据（强制展示）
```

- 校验规则：`code` 唯一且未占用；工具必须在注册表且在当前用户权限内；`allowed_tools` 含写工具时 `risk_level` 自动升为 high 并强制人工审核；`sources` 为空时禁止标记为「有依据生成」。

### 4.2 一句话链路

1. **意图识别**：`chat/intent.py` 新增 `skill_request` / `plugin_request`（关键词 + LLM 兜底），聊天里说「我需要一个病虫害防治的 skill」即触发。
2. **检索匹配**：`POST /market/agent/request`
   - 本地：`market_items`（内置 12 个 + 已安装 + GitHub 缓存）关键词/语义排序
   - 外部：GitHub 搜索（走国内镜像）+ 可选 Gitee 搜索
   - 返回：`matches[]`（含 item_key、来源、许可、匹配理由）+ `recommend: install|generate`
3. **无匹配则生成**：`POST /market/agent/generate`
   - 用 `web.search` 取领域知识（带引用）→ 按模板生成 `SkillSpec` → 校验 → 落为**私有草稿**（`visibility=private`，创建者=当前用户）→ 返回卡片。
   - 生成失败/校验不过：不落库，返回「仅建议」，避免垃圾技能堆积。
4. **安装**：`POST /market/agent/install`
   - 匹配到的内置/市场项：走现有安装闸（第三方 `pending_review`）。
   - GitHub 项：下载 README（镜像）→ 预览 → 安装（`pending_review`）。
   - 自己生成的私有技能：**默认落为 `visibility=private` 草稿，仅创建者可用**（负责人可见），随本期迁移 0033 落地；提交小院审核在 3.3 提供。
5. **聊天卡片**：复用 `chat/capability.py` 的卡片机制，新增 `skill_card`：展示「找到 3 个匹配 / 已生成草稿」，按钮「安装」「预览」「提交审核」「绑定到助手」。
6. **Agent 工具**：注册 `skill.search` / `skill.generate` / `skill.install`（`market:install` 权限 + 安装二次确认），让 Agent 能在多轮对话中完成整个流程。

**配额与成本**：`skill_gen_daily_limit`（默认 20/院/日）、单次生成 token 上限、生成结果按 `code + 名称相似度` 去重；所有生成落审计 `skill_generated`（模型、tokens、来源数）。

### 4.3 GitHub 国内源

- 配置项：`market.github_api_base`（默认官方）、`market.github_raw_mirrors`（默认 `gh-proxy.com`、`ghfast.top`、`gitmirror` 三者按序）、`market.github_clone_mirrors`（zip 下载）。
- 健康探测：60s 缓存，失败源 5 分钟内不再首选；全失败时明确返回 `NETWORK_UNAVAILABLE` 并建议本地导入。
- 国内可选源：Gitee 代码搜索（免密钥公开接口），作为 GitHub 不可达时的第二检索面；许可证白名单沿用现有 `ALLOWED_LICENSES`。
- 安全：镜像只用于读取 README/元数据与下载 zip，校验和与现有安装器一致；禁止镜像返回内容触发脚本执行。

**验收**：unit 覆盖模板校验、白名单、注入与去重；integration 用 Mock LLM + Mock GitHub 走通「检索 → 生成 → 私有可用 → 绑定助手」；E2E 用固定桩模型完成一句话 → 卡片 → 安装 → 弹窗可绑定。

---

## 5. v3.3.0：插件生成 + 技能共享治理

### 5.1 一句话生成插件

- 插件模板（沿用现有 workflow manifest 结构）：`name/description/params/steps[{kind:tool|llm}]/guardrails`。
- `POST /market/agent/generate` 支持 `kind=plugin`：生成 manifest → 走现有 `plugins/workflow.py` 注册（**一律 `pending_review`**）→ 审核启用后才能运行。
- 约束：steps 只能引用注册表工具；禁止生成 HTTP 直连、脚本、shell；参数与步骤数上限沿用现有校验。
- 聊天同样走 `plugin_card`：预览步骤图 → 提交审核 → 启用。

### 5.2 技能可见性与审核

**数据模型（迁移 0033，随 3.2 落地）**：`evolution_skills` 增加

| 字段 | 说明 |
| --- | --- |
| `visibility` | `private` / `yard` / `platform`，默认 `private` |
| `owner_user_id` | 创建者（NULL = 系统/builtin） |
| `origin` | `builtin` / `market` / `user_generated` / `imported` |
| `review_note` | 审核意见 |

**规则**

| 动作 | 谁能做 | 是否需要审核 | 结果 |
| --- | --- | --- | --- |
| 生成/编辑私有技能 | 任何成员（`skills:generate`，默认全员） | 否（自动风险扫描；high 风险一律拦截） | 仅自己 + 负责人可见可用 |
| 提交到小院公共库 | 创建者 | 是（`skills:review`：负责人/管理员/专家） | 通过 → `visibility=yard, published`；拒绝 → 保留私有 + 理由 |
| 推送到平台级库 | 负责人 / 平台管理员 | 是（平台审核） | `visibility=platform`，全平台小院可见 |
| 把任意私有技能转公共 | 负责人（`skills:publish_any`） | 否，但强制审计 + 通知创建者 | `visibility=yard` |
| 下放/收回该权力 | 负责人 | — | 通过现有 grants 授权给管理员（新权限加入 `GRANTABLE_PERMISSIONS`） |
| 审核全部技能（含他人私有） | 负责人；管理员需被授权 | — | 审计 `skill_reviewed` / `skill_force_publish` |

**权限点**：`skills:generate`（viewer 默认有）、`skills:review`（expert/admin/owner）、`skills:publish_any`（仅负责人，可授权管理员）。全部进设置页权限矩阵。

**前端**

- 插件与技能库：`我的 / 小院 / 平台` 三个 tab；我的里显示草稿/私有/审核中/已公开状态；每项有「提交审核」「查看来源」「绑定助手」。
- 专家审核 → 新增「技能审核」队列（与市场审核并列），可预览定义、来源引用、风险等级，批量通过/驳回。
- 负责人设置：新增「技能治理」开关——是否把 `skills:publish_any` 下放给管理员。

**验收**：越权用例（成员看不到他人私有、不能提交平台级、无授权管理员不能 force publish、专家不能改负责人技能归属）；E2E 覆盖提交 → 审核 → 出现在小院库 → 其他成员可绑定。

---

## 6. v3.4.0：自我总结提升

### 6.1 设置项（负责人 / 被授权管理员）

`yard_settings.self_improve`：

```json
{
  "enabled": false,
  "schedule": {"preset": "daily|hours12|hours6|days3|weekly|monthly|custom",
               "custom_value": 1, "custom_unit": "hours|days|months"},
  "scope": "yard|per_member",
  "daily_budget_tokens": 200000,
  "auto_publish": false,
  "retain_days": 90,
  "notify_owner": true
}
```

- 默认**关闭**，由负责人首次开启；开启时显示「将读取本院会话摘要（不含原文）用于生成改进建议」的明示。
- 管理员可见但不可改，除非负责人授权（沿用 `settings:manage_protected` 与 grants）。

### 6.2 调度底座（可复用）

- 新表（迁移 0034）：`yard_jobs`（`kind`、`schedule_json`、`next_run_at`、`last_run_at`、`status`、`lease_until`、`payload_json`）+ `yard_job_runs`（耗时、tokens、产出、错误）。
- Celery beat 每 5 分钟 `yards.run_due_jobs`；桌面 inline 模式同频线程（复用 `devices/worker_tasks.py` 的做法）；`lease_until` 防多实例重复执行。
- 该底座后续可直接复用于财务/管理类 Agent 的定时任务，不必再铺一套。

### 6.3 总结任务做什么

窗口内聚合：会话摘要（主题/意图/高频问题）、反馈、知识命中缺口、工具使用与失败、token 消耗 → LLM 产出：

1. **用户偏好记忆**（`scope=per_member`）：只存主题偏好与表达习惯，不存原文；仅本人与负责人可见。
2. **技能改进提案**：对命中率低的技能给新版本草稿（`pending_review`），**不自动发布**。
3. **知识候选**：缺口 → 知识候选，进专家审核队列。
4. **运行报告**：负责人设置页可见「上次运行/耗时/tokens/产出条数/失败原因」，支持「立即运行一次」。

**护栏**：日预算、单次 token 上限、失败重试 2 次、`auto_publish` 默认关闭且开启需二次确认；全部落审计 `self_improve_run`。

**验收**：冻结时间 + 手动触发可测；无 LLM/超预算时不产生脏数据；管理员无授权改设置 403；运行历史与预算展示正确。

---

## 7. 迁移与契约

| 版本 | 迁移 | 说明 |
| --- | --- | --- |
| 3.1 | 无（设置 JSON 兼容） | `search.provider` 新增 `auto`，旧值原样可用 |
| 3.2 | 0033 `skill_visibility` | `evolution_skills` 加 `visibility/owner_user_id/origin/reviewed_by/reviewed_at/review_note` + 索引；存量回填：builtin → `platform`，其余 → `yard` |
| 3.3 | 无新表（复用 0033） | 审核与推广复用上述字段；如需平台审核单再补 0034 |
| 3.4 | 0034 `yard_jobs` | 定时任务与运行记录 |

每期同步：`contracts/openapi.yaml` 重生成、`contracts/error-codes.md` 补码、`docs/api.md`、`docs/database.md`、`CHANGELOG.md`。

## 8. 门禁（每期都必须全绿）

- 后端：`pytest` 全量、`ruff check/format`、`mypy`；迁移 `upgrade head → downgrade -1 → upgrade head` 往返。
- 前端：`eslint`、`vue-tsc`、生产构建。
- E2E：新增用例并入现有串行套件（继续 `workers:1`），不降低既有断言。
- 安全检查：越权矩阵、生成内容注入、镜像返回内容、配额与预算截断。
- 文档：方案、接口、错误码、数据库、CHANGELOG 同步。

## 9. 风险与取舍

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 抓取搜索页改版/合规 | 默认源失效或有法务风险 | provider 抽象 + 熔断 + 单独开关 + 可切换本地 SearXNG；上线前确认口径 |
| 生成技能质量参差 | 技能库污染 | 模板校验 + 评测样本 + 私有默认 + 公共必审 + 去重 + 每日配额 |
| 生成成本 | token 费用上升 | 日配额、单次上限、缓存、只在无匹配时生成 |
| 镜像可用性 | GitHub 安装失败 | 多镜像健康检查 + Gitee 兜底 + 本地导入 |
| 隐私 | 自我总结触及个人数据 | 只存摘要不存原文、per_member 隔离、负责人查看受 `insights:sensitive` 约束、明示开关 |
| 技能归属争议 | 私有被负责人转公共 | 强制审计 + 通知创建者 + 设置页可配置是否下放该权力 |

## 10. 工作量预估（Agent 并行口径）

| 版本 | 后端 | 前端 | 测试/文档 | 合计（并行） |
| --- | --- | --- | --- | --- |
| 3.1.0 | 2 天 | 1.5 天 | 1 天 | ~3 天 |
| 3.2.0 | 4 天 | 2 天 | 2 天 | ~5 天 |
| 3.3.0 | 3 天 | 2.5 天 | 1.5 天 | ~4 天 |
| 3.4.0 | 3 天 | 1.5 天 | 1.5 天 | ~3.5 天 |

## 11. 待负责人拍板

1. **内置搜索源**：抓取国内可达公开结果页（零配置，推荐）／只做本地 SearXNG 一键启动／两者都做。
2. **生成技能默认归属**：私有草稿（推荐）／直接进小院审核／直接进小院公共但待审。
3. **自我总结提升默认状态**：默认关闭、负责人开启（推荐）／默认每天开启／默认每周开启。
4. **公共库范围**：小院级为主 + 平台级需平台管理员（推荐）／仅小院级。

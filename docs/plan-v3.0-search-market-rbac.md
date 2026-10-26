# v2.2 / v3.0 升级方案：联网搜索、插件技能市场、本地离线降级、身份权限与负责人驾驶舱

> 状态：**已实施（2026-10-27，v3.0.0）**。两波（2.2.0 联网+市场 / 3.0.0 身份+驾驶舱）
> 合并交付；实施与验收见 `CHANGELOG.md` 3.0.0 与 `docs/audit-report-2026-10-25.md` §15。
> 决策点 D1–D10 按 §7 采纳；与原方案的偏差（合并版本、额外安全加固）已在 §15.2 记录。
>
> 编写依据：现有代码实测（角色、成员、插件、技能、市场、工具、审计）+ 公开资料
> （Bing Search API 退役公告、SearXNG 文档、Tavily/Brave 定价、GitHub 农业技能仓库）。
> 资料链接见 §8。

---

## 0. 结论速览

1. **“必应免费搜索插件”要换个实现方式**：微软已于 **2025-08-11 正式退役 Bing
   Search API**，不再有新开通的免费 Key。可行且可商用的路线是：
   - **默认：自建 SearXNG**（开源、免费、无 Key，聚合 Bing / DuckDuckGo / Wikipedia
     等引擎，返回 JSON）——产品里的“必应搜索”由 SearXNG 的 Bing 引擎提供；
   - **可选：Tavily**（每月 1000 免费 credits）或 **Brave**（每月 $5 额度，已无长期
     免费套餐）作为 API Key 备选；
   - 工具层统一抽象，换供应商不改 Agent。
2. **插件/技能市场不能“搜到 GitHub 就直接执行”**：技能是声明式提示词，风险低，
   可以“搜索→预览→直接安装”；插件含工作流/代码，必须走“来源+许可证+校验和+
   专家审核”后再启用。这样才能既满足“直接安装”，又不把供应链风险带进小院。
3. **本地离线提示**做成统一能力探测：联网插件/技能市场、GitHub 搜索、在线安装
   失败时统一返回 `NETWORK_UNAVAILABLE`，前端按用户要求原文提示，并给
   “去专家审核 → Skill 管理”一键跳转；本地 zip / SKILL.md 导入不受影响。
4. **权限体系需要一次真正的重构**：新增唯一的 **小院负责人（yard_owner）**，
   与 `admin` 账号绑定；权限从“代码里写死的 6 个角色”升级为“系统角色 + 小院可配置
   角色权限矩阵 + 负责人授权”，并补齐成员建号、编辑、停用、重置密码、注册审核。
5. **负责人驾驶舱**是本次产品升级的核心界面：全小院成员、Agent、会话、Token、
   每日使用、异常与审核一屏可见；敏感数据默认只对负责人开放，管理员需负责人
   逐项授权，且每次查看留审计。
6. **分两波交付**：`2.2.0` 联网 + 市场 + 本地技能库（兼容升级）；`3.0.0` 身份权限 +
   负责人驾驶舱（含角色/数据结构破坏性变更）。预计约 **27 人日**；单人约 5–6 周，
   多 Agent 并行约 **10–12 个工作日**。

---

## 1. 现状与缺口（证据化）

### 1.1 角色与权限

| 现状 | 位置 | 缺口 |
|---|---|---|
| 只有 6 个角色：`platform_admin / yard_admin / expert / operator / viewer / service_worker` | `backend/app/modules/yards/models.py:10` | 没有“小院负责人”，注册建院者就是 `yard_admin`，无法区分“唯一负责人”和“日常管理员” |
| 14 个权限点；`platform_admin` 与 `yard_admin` 权限完全相同 | `yards/permissions.py:14-35` | 无法表达“管理员不能删负责人 / 负责人可隐藏敏感数据” |
| 权限写死在代码；JWT 携带单一角色，30 分钟有效 | `core/deps.py:50-87`、`auth/service.py:191-194` | 改角色/停用后权限滞后；没有角色配置界面 |
| 存在两套权限表（yards 与 evolution），还出现不存在的 `admin/auditor` 角色 | `evolution/service.py:32-39`、`llm/supplier_router.py:32` | 权限源不唯一，迟早漂移 |

### 1.2 成员管理

| 现状 | 位置 | 缺口 |
|---|---|---|
| 只有列表/新增/批量新增/删除；新增只接受**已存在账号**的用户名 | `yards/router.py:51-144`、`schemas.py:28-31` | 不能创建账号（用户名/邮箱/手机号/初始密码），不能编辑角色/资料，不能停用 |
| 删除只保护“最后一名 yard_admin” | `yards/service.py:147-152` | 不保护负责人；无软删除；改角色只能移除再邀请，历史会话归属丢失 |
| 注册即建院、直接发 Token、无审核 | `yards/signup.py:80-118` | 无“注册待审核”；`signup_enabled` 只能改环境变量 |
| `users` 表无 phone / 强制改密 / 邮箱验证 / 失败次数 | `auth/models.py:14-20` | 无法做手机号、重置密码、首登改密 |
| 成员列表任何成员都能看，且返回 email | `yards/router.py:51-60`、`service.py:41-51` | 越权泄露联系方式 |
| 前端成员页只支持“添加已有用户 / 移除” | `frontend/src/views/MembersView.vue:59-99,186-252` | 无编辑抽屉、无建号向导、无重置密码、无状态管理 |

### 1.3 系统设置

- 前端 `SettingsView.vue` 已 7 个标签（模型、供应商、MCP、工具、手机接入、菜单 DIY、
  前端配置），但**后端没有系统设置表/接口**；注册开关、密码策略等全在环境变量。
- `yards.config_json` 是无 schema 的裸 JSON，没有写入 API 与审计。

### 1.4 插件 / 技能 / 市场

- 插件：`PluginManifest`（manifest/checksum/status），只支持“扫本地 plugins 目录”，
  无安装/上传/远程源；类型仅 workflow / knowledge_pack / domain_pack。
- 技能：`evolution_skills` 有五个审核状态、评测与灰度；但 Agent 绑定只把
  `description/steps` 拼进 prompt，**未按 published 过滤，也不影响工具白名单**；
  `ReviewView.vue` 新建表单与后端字段不一致（422）。
- 市场：`market/seeds.py` 是 14 个内置 Agent 常量，纯本地，无远程搜索/版本/来源；
  不装 Skill/Plugin。
- 专家审核：`ReviewView.vue` 已有「Skill 管理」tab，可直接承接“本地 skill 管理”。

### 1.5 联网与工具

- 工具注册表已有 4 级风险、六阶段执行、审计与审批；但只有
  `knowledge.search / workorder.dispatch / sensor.query`，**没有任何联网搜索/抓取工具**。
- `core/net_guard.py` 已能阻断元数据/保留网段，可作为联网工具前置护栏。
- LLM 已支持 function-calling，但供应商 `supports_tools` 默认关闭；无搜索供应商配置。

### 1.6 数据可见性

- 会话对创建者私有：`list_sessions/get_session` 都带 `created_by`，**管理员看不到
  他人会话**，也没有代查接口。
- `LLMTrace` 无 `user_id`，用量只能按日×模型或 purpose 聚合，**无法按人统计**；
  消息级 `token_usage_json` 已存在，可做按人/按会话汇总。
- 审计日志不支持按 actor / 时间范围过滤，无敏感查看审计。

---

## 2. 视角一：程序派发 / 架构方案

### 2.1 模块划分

```
backend/app/modules/
├── search/                 # 新增：搜索供应商适配 + web.search / web.fetch
│   ├── providers.py        # searxng | tavily | brave（统一 SearchResult）
│   ├── service.py          # 选择供应商、降级链、缓存、限流
│   └── tools.py            # 注册两个只读工具（external_read 风险）
├── market/                 # 扩展：Agent + Skill + Plugin 三类市场
│   ├── catalog_items.py    # 本地内置 + 远程索引 + GitHub 结果的统一条目
│   ├── github_source.py    # GitHub 搜索/读取 SKILL.md（服务端，不落密钥）
│   └── installer.py        # 预览 → 校验 → 安装 → 审核/启用
├── evolution/              # 扩展：Skill 来源/许可证/校验和/正文
├── plugins/                # 扩展：导入 zip、来源/许可证、校验和强校验
├── identity/               # 新增：角色、权限矩阵、授权、注册审核
│   ├── roles.py            # 系统角色 + 小院自定义角色
│   ├── authz.py            # 唯一权限判定入口（DB + 缓存 + 保护规则）
│   └── grants.py           # 负责人对管理员的敏感数据授权
└── insights/               # 新增：负责人驾驶舱聚合（用量/会话/成员/Agent）
```

**唯一权限源**：所有 `require_permission` 改走 `identity/authz.py`；删除
`evolution/service.py` 里的第二套权限表，`admin/auditor` 等脏角色值一并清理。

### 2.2 第一波（2.2.0）：联网搜索 + 市场 + 本地技能库

#### 2.2.1 搜索供应商抽象

```python
class SearchProvider(Protocol):
    code: str                  # searxng | tavily | brave
    requires_key: bool
    async def search(self, q: str, *, count: int, lang: str) -> list[SearchHit]
```

- **默认 SearXNG**：Docker 随产品编排（`compose.yaml` 增加可选 service），
  `GET {base}/search?q=...&format=json`，无 Key、无单次费用；Bing 由 SearXNG 的
  Bing 引擎承担。**注意：SearXNG 是 AGPL-3.0**——只作为独立 HTTP 服务调用、
  不修改其源码、不并入闭源代码；随包分发时保留许可证与源码获取入口。若客户
  无法接受 AGPL，则切换 Tavily/Brave 商业 API（见 D1）。
- **可选 Tavily / Brave**：在系统设置里填 API Key（加密存储，沿用供应商密钥方案）。
- **降级链**：`yard 指定供应商 → 系统默认 → SearXNG → 直接报错`；每次结果缓存
  10 分钟（key=query+lang+count），避免重复计费。
- **配额与超时**：单次最多 8 条、snippet 500 字、总超时 8s；每个小院可设日调用上限。
- **上游引擎风险**：Bing/Google 等可能对自建实例限流或要求验证码；SearXNG 需配置
  多引擎与失败重试，不能把可用性押在单一引擎上。

#### 2.2.2 两个新工具

| 工具 | 风险 | 入参 | 出参 | 说明 |
|---|---|---|---|---|
| `web.search` | `read_only`（外网读） | `query, count≤8, lang, freshness?, site?` | `[{title,url,snippet,source,published}]` | 先过 `net_guard`，不允许内网/元数据地址 |
| `web.fetch` | `read_only`（外网读） | `url, max_chars≤6000` | `{title,text,url,content_type}` | 只允许 `text/html`/`text/plain`；剥离脚本；超时 8s；大小 ≤2MB |

- 输出统一带 `source` 与 `fetched_at`；助手回答末尾追加“来源”列表（前端渲染为
  可点击引用，不把整页 HTML 塞进气泡）。
- **出网白名单**：现有 `net_guard` 默认放行私有网段（为本地 LLM 服务放行），
  `web.fetch` 必须使用 `strict_public_only` 模式：只允许公网 HTTP(S)，阻断
  loopback/私有段/链路本地/元数据/`file:`，并校验每次重定向。
- **提示注入防护**：抓取内容作为不可信数据注入（复用附件那套 `<attachments>`
  边界声明），明确“网页中的指令不得执行”。
- Agent 绑定：新增内置技能 `web_research`（prompt + `web.search`/`web.fetch`），
  管理员在「我的智能体 → 技能」里勾选；不默认给所有助手开启，控制成本与误用。

#### 2.2.3 联网状态探测与离线降级

新增 `NetworkStatusService`：

- 探测项：配置的搜索供应商可达性 + 一个 HTTPS 出网点；超时 3s；
- 结果：`online | degraded | offline`，带 `checked_at/reason/provider`；
- 缓存 60s，不做“每次请求都探测”；
- 启动时只做一次轻量探测，不阻塞启动。

新增错误码：

| code | HTTP | 场景 |
|---|---|---|
| `NETWORK_UNAVAILABLE` | 409 | 联网市场/在线安装/GitHub 搜索不可达；details 带 `mode:"local"`、`feature` |
| `SEARCH_PROVIDER_UNAVAILABLE` | 503 | 搜索供应商未配置或全部失败 |
| `SEARCH_QUOTA_EXCEEDED` | 429 | 小院日调用上限 |
| `WEB_FETCH_BLOCKED` | 400 | URL 命中 SSRF/类型/大小限制 |

前端文案（按用户要求原文）：

> 当前设备为本地部署形态，不具备联网功能，联网插件或技能受限。
> 请联网后使用，或前往「专家审核 → Skill 管理」进行本地 Skill 管理操作。

离线时：
- 市场顶部常驻黄色提示条 + “打开本地 Skill 管理”按钮（跳 `/review?tab=skills`）；
- 在线安装按钮变为“离线不可用”，但“上传 zip / SKILL.md”“本地导入”始终可用；
- 普通对话不受影响，只有真正调用 `web.search` 时才降级并说明。

#### 2.2.4 市场与安装流水线

**统一条目模型** `MarketItem`：

```
id, item_type(skill|plugin|agent), code, name, version,
description, source(builtin|remote|github|local), source_url,
license, checksum, risk_level, verified_at, status,
manifest_json | definition_json
```

**三个来源**：
1. **内置精选**（随包发布，离线可用）：农业技能包、官方插件、示例 Agent；
2. **远程索引**（可配置 HTTPS registry，签名 JSON；用于后续商业化分发）；
3. **GitHub 搜索**（服务端调用 GitHub API；可选 `GITHUB_TOKEN` 提升限流；
   不把 token 下发前端）。

**安装策略（关键）**：

| 类型 | 直接安装 | 默认状态 | 启用条件 |
|---|---|---|---|
| **Skill（声明式提示词）** | 支持“搜索→预览→直接安装” | 低风险 `published`（可配置改为待审） | 许可证在 MIT/BSD/Apache-2.0 白名单、无脚本字段 |
| **Plugin（workflow/知识包）** | 安装后进入待审 | `pending_review` | 专家/管理员审核 + checksum 一致 + manifest 合法 |
| **Agent** | 沿用现有 Agent 市场 | 已发布 | 现有风险计算 |

- **绝不执行**第三方仓库里的任意脚本；只读取 `SKILL.md`/`manifest.json`/文本资源，
  转换为我们自己的 `definition_json`；含 `scripts/` 的仓库只做“代码参考”，不落盘执行。
- 安装动作留审计：`market_item_installed`（source/license/checksum/risk）。
- 同名冲突自动加 `-2/-3` 后缀（沿用能力链路的 D3 口径）。

**GitHub 技能转换器**：

1. 读取 `SKILL.md` 的 YAML frontmatter（`name/description/metadata`）；
2. 正文按标题抽取 `Purpose / Capabilities / Instructions / Tools / Safety`；
3. 映射为 `definition_json = {name, description, prompt, steps, source_url, license,
   attribution, version}`；
4. 许可证白名单 + 仓库 stars/更新时间/是否含可执行文件作为风险提示；
5. 生成预览（不安装）→ 用户确认安装 → 进入 Skill 管理。

#### 2.2.5 农业开源技能首批清单（放入专家审核 → Skill 管理）

| 来源仓库 | 许可证 | 形态 | 采用方式 |
|---|---|---|---|
| `farmfriend-labs/agent-skills-farming` | MIT | 30+ 个 `SKILL.md`（土壤、病虫害、收获、农机、市场） | **首批直接转换**为声明式技能，保留出处与许可证 |
| `PangenomeAI/academic-skills-food-nutrition` | MIT | 农业科研/综述/论文/评审/图表/PPT 技能与插件 | 先接 `agri-research`、`agri-review` 等顶层技能；多文件子代理机制需适配，列入二期 |
| `bgtamang/AgriClaw` | MIT | 本地优先农业技能库 + catalog.json | 作为技能目录结构参考，挑选不与首批重复的条目 |
| `Adilmunawar/ZD-claude-plugin` | MIT | 农业遥感/PostGIS/Earth Engine 插件（14 模块） | 不直接执行 Claude 插件；将地理数据处理流程改造成 workflow 插件后再审 |
| `MedouneSGB/AgriAgent` | MIT | 多智能体农业咨询（Python） | 架构参考；不直接导入代码 |
| `Shuvam-Banerji-Seal/AgriIR` | Apache-2.0 | 农业领域 RAG 框架 | 知识检索参考，后续接知识库 |
| `microsoft/farmvibes-ai` | MIT | 农业地理空间 ML | 二期以独立服务方式接入（体量大，不并入主进程） |
| `danforthcenter/plantcv` | MPL-2.0 | 作物表型图像分析 | 独立服务调用，不复制其代码进闭源模块 |

> 首批建议 10–15 个“提示词型”技能先落地（土壤健康、灌溉决策、病虫害识别流程、
> 农药安全、采收保鲜、农机保养、田间记录、农事日历、市场询价、政策检索等），
> 每个都带 `source_url + license + attribution`，在 Skill 管理里可查看来源。

#### 2.2.6 接口清单（第一波）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/search/status` | 联网状态/供应商/配额 | 登录用户 |
| POST | `/search/test` | 管理员测试供应商 | yard_admin+ |
| POST | `/market/items/search` | 本地+远程+GitHub 统一搜索 | yard_admin/expert |
| GET | `/market/items/{id}` | 预览（文件清单/许可证/风险） | 同上 |
| POST | `/market/items/{id}/install` | 安装 Skill/Plugin | yard_admin；expert 可装 Skill |
| POST | `/skills/import` | 本地上传 SKILL.md/zip | expert+ |
| POST | `/plugins/import` | 本地上传插件 zip | yard_admin |
| POST | `/market/sources/{id}/sync` | 同步远程索引 | owner/yard_admin |
| GET/PUT | `/settings/search` | 供应商/Key/配额 | owner/yard_admin |

#### 2.2.7 Skill 生效修正

- Agent 绑定技能时**只取 `status=published`**（当前草稿也会注入，属缺陷）；
- `definition_json.prompt`（完整正文）参与 prompt 拼接，而非只有 description/steps；
- 技能绑定仍不自动扩权：工具白名单只由 Agent 的 `allowed_tools` 决定，技能只能
  “建议调用”，不能绕过工具风险与审批。

### 2.3 第二波（3.0.0）：身份、权限与负责人驾驶舱

#### 2.3.1 身份模型

**新增角色 `yard_owner`（小院负责人）**，并与 `yards.owner_user_id` 双向绑定：

- 每个小院**有且仅有一个**负责人；负责人不能被移除/停用/降级，只能“转让”；
- 现有 `admin` 账号迁移为演示小院的负责人；历史建院者（最早 membership）迁移为
  各自小院负责人；其余 `yard_admin` 保持不变；
- `admin` 继续保留 `platform_admin` 用于平台级运维（跨小院列表/建院），但在演示
  小院内以 `yard_owner` 身份进入负责人驾驶舱；平台角色不替代小院负责人；
- `platform_admin` 保留为平台运维角色，不参与小院日常；负责人不能跨小院。

**成员表/用户表扩展**：

```
users:
  phone, email_verified_at, phone_verified_at,
  status(active|pending|disabled), must_change_password,
  password_updated_at, token_version, authz_version

yards:
  owner_user_id

yard_settings:                  # 统一设置域（键值 + 代码侧 schema 校验 + 审计）
  yard_id, key, value_json, updated_by, version

yard_roles:                     # 系统角色 + 小院自定义角色
  yard_id, code, name, permissions_json,
  is_system, is_protected, created_by

yard_member_grants:             # 负责人对管理员的敏感权限授权
  yard_id, user_id, grants_json, updated_by
```

**权限判定**（唯一入口）：
`effective_permissions = 系统保护规则 ∩ 角色权限矩阵 ∪ 个人授权`；
`require_permission` 从 DB/60s 缓存读取，校验 `authz_version`，角色变更后立即生效。
JWT 不再作为权限事实源，只作为身份凭证。

#### 2.3.2 角色与权限矩阵（默认值）

| 能力 | 负责人 | 小院管理员 | 专家 | 科研操作员 | 普通成员 |
|---|---|---|---|---|---|
| 小院设置 / 角色权限 | ✅（含保护项） | ✅（非保护项，负责人可收权） | ❌ | ❌ | ❌ |
| 添加/编辑/删除**管理员** | ✅ | ❌ | ❌ | ❌ | ❌ |
| 添加/编辑/删除普通成员/专家/操作员 | ✅ | ✅ | 仅普通成员与操作员（可配置）；不能触碰管理员/负责人 | ❌ | ❌ |
| 重置成员密码 | ✅（含管理员） | ✅（不含负责人/其他管理员） | ❌ | ❌ | 仅自己 |
| 注册审核 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 专家审核 / 技能审核 | ✅ | ✅ | ✅ | ❌ | ❌ |
| 技能/插件安装 | ✅ | ✅（插件需审） | ✅（仅 Skill） | ❌ | ❌ |
| 查看全小院会话内容 | ✅ | 仅授权后（逐人开关） | 仅辅导对象摘要（脱敏） | ❌ | 仅自己 |
| 查看按人用量/成本 | ✅ | 仅授权后 | 脱敏汇总 | 仅自己 | 仅自己 |
| 财务/管理类 Agent 数据 | ✅ | 仅授权后 | ❌ | ❌ | ❌ |
| 日常对话/知识/任务 | ✅ | ✅ | ✅ | ✅ | ✅ |

**保护规则（代码硬约束，任何角色配置都不能突破）**：
1. 负责人唯一，且不能被任何角色删除/停用/降级；
2. 只有负责人能新增/移除/改角色管理员；
3. 管理员不能删除负责人、不能给自己授“负责人”；
4. 敏感数据授权只能由负责人发出；
5. 负责人始终拥有全部权限（不可被权限矩阵削减）。

**角色名称可配置**：系统角色 code 不变（`yard_owner/yard_admin/expert/operator/viewer`），
显示名可在系统设置改（如“科研操作员”“普通成员”）；支持创建小院自定义角色
（上限 20 个，不能包含保护权限）。

#### 2.3.3 成员全生命周期

1. **创建账号**：用户名、姓名、邮箱、手机号、初始密码、角色；
   - 用户名唯一、3–32 位；邮箱/手机号格式与唯一校验（手机号不做短信验证，先存后验）；
   - 初始密码可“自动生成”，只在创建成功弹窗展示一次；
   - `must_change_password=true`，首次登录强制改密。
2. **编辑**：姓名、邮箱、手机号、角色、状态（active/disabled）；
   - 改角色写 `authz_version++`，立即生效并审计 `member_role_changed`；
   - 用户名不可改（避免历史引用断裂）。
3. **停用 / 删除**：
   - 停用 = membership.status=disabled + 踢下线；保留历史；
   - 删除 = 软删除 membership，账号保留；若是 Agent/知识资源负责人则提示先转移。
4. **重置密码**：管理员/负责人给成员生成一次性临时密码，展示一次 + 强制首登改密 +
   `token_version++` 全端下线；不能重置负责人密码（负责人走自助改密/找回）。
5. **自助资料**：所有账号可改自己的姓名、邮箱、手机号、密码；
   改密需验证原密码，成功后其他会话失效。
6. **注册与审核**：分两种入口，避免把“开新院”和“加入某院”混为一谈：
   - **加入现有小院**：登录页选择/输入小院（或邀请链接）→ 提交资料 →
     `users.status=pending` + 该小院待审记录 → 小院负责人/管理员审核通过后激活；
   - **新建小院**：创建者直接成为该院 `yard_owner`，小院进入平台待审（可配置
     免审）；平台管理员审核小院而非个人；
   - 系统设置：`registration_mode = closed | approval | open`；默认 `approval`；
   - 可设置默认角色（默认 `viewer`/普通成员）、是否需要邮箱/手机号；
   - 审核队列在「成员管理 → 注册审核」，通过/拒绝均审计。

#### 2.3.4 负责人驾驶舱

**入口**：`/owner`（仅负责人与 platform_admin；管理员无入口）。
**导航**：总览 / 成员与权限 / 用量与成本 / 会话审计 / Agent 运营 / 授权中心 / 财务（预留）。

| 区域 | 内容 | 数据来源 |
|---|---|---|
| 总览 | 成员总数/今日活跃、Agent 数、今日会话/消息、今日/7 日 Token、失败任务、待审知识/Skill、联网状态 | 聚合查询 |
| 成员与权限 | 成员列表 + 角色 + 状态 + 最近活跃 + 7 日 Token + 会话数；行内编辑/停用/重置密码 | memberships + chat + traces |
| 用量与成本 | 按成员/日期/模型/purpose 的趋势与下钻；预估费用 | `chat_messages.token_usage_json` + `LLMTrace`（新增 user_id） |
| 会话审计 | 全小院会话列表（成员/时间/关键词/Agent 过滤）；查看内容需填原因，记录 `sensitive_view` | chat_sessions/messages |
| Agent 运营 | 每个 Agent 的调用次数、成功率、平均耗时、Token、失败原因 Top | agent runs + traces |
| 授权中心 | 为每个管理员开关：看会话内容 / 看用量明细 / 看财务数据；变更即时生效 | member_grants |
| 财务（预留） | 预算、成本中心、财务 Agent 输出；接口留扩展点 | 新增 insights 适配层 |

**敏感数据规则**：
- 负责人默认全可见；管理员默认只看“数量/趋势”，不看会话正文与个人信息明细；
- 负责人可逐管理员授权；被授权后管理员查看会话仍需填原因并留审计；
- 所有敏感查看记录 `sensitive_view`（actor/resource/reason/request_id），
  审计页可按人、时间、资源类型筛选；
- 前端在查看页顶部常驻“本次查看已记录审计”提示。

**历史数据**：
- `LLMTrace` 增 `user_id/session_id`（可空），新调用必填；历史数据按
  `agent_ref/run_id` 能回填的回填，不能回填的标“历史未归属”；
- 会话级 Token 已有逐条 `token_usage_json`，可直接按 `created_by` 聚合。

#### 2.3.5 接口清单（第二波，节选）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/yards/current/members` | 建号或挂接已有账号（新字段） |
| PATCH | `/yards/current/members/{uid}` | 编辑资料/角色/状态 |
| POST | `/yards/current/members/{uid}/reset-password` | 生成临时密码 |
| GET/POST | `/yards/current/registrations` | 注册审核列表/通过/拒绝 |
| GET/PUT | `/yards/current/roles` | 角色列表/名称与权限矩阵 |
| GET/PUT | `/yards/current/settings/security` | 注册模式、密码策略、登录锁定 |
| GET/PATCH | `/auth/me` | 自助资料 |
| POST | `/auth/change-password` | 自助改密 |
| POST | `/yards/current/owner/transfer` | 负责人转让（二次确认 + 审计） |
| PUT | `/yards/current/owner/grants/{uid}` | 管理员敏感授权 |
| GET | `/yards/current/owner/overview` | 驾驶舱总览 |
| GET | `/yards/current/owner/member-usage` | 按人用量 |
| GET | `/yards/current/owner/sessions` | 全小院会话（负责人/授权管理员） |
| GET | `/yards/current/owner/sessions/{sid}/messages?reason=` | 会话正文（审计） |

### 2.4 数据迁移

| 迁移 | 内容 | 可逆 |
|---|---|---|
| `0030_search_settings` | `yard_settings`（搜索供应商/配额/注册开关/密码策略）、`system_settings` 全局默认 | 是 |
| `0031_market_items` | 市场条目缓存、Skill/Plugin 来源/许可证/校验和字段 | 是 |
| `0032_identity_rbac` | `yards.owner_user_id`、`yard_roles`、`yard_member_grants`、users 新字段、`yard_owner` 角色迁移 | 是（owner 回退为 yard_admin） |
| `0033_trace_user` | `llm_traces.user_id/session_id` + 索引、审计 actor/时间索引 | 是 |

迁移原则：先加列/表 → 回填 → 再收紧非空；所有迁移跑
`upgrade → downgrade → upgrade` 往返；开发库与测试库都验证。

### 2.5 安全与合规

- **供应链**：许可证白名单（MIT/BSD/Apache-2.0/MPL 仅服务化）；不执行第三方脚本；
  checksum 不一致必须人工审；来源/作者/许可证三件套缺失不允许安装。
- **AGPL 边界**：SearXNG 只作为独立服务经 HTTP 调用，不复制/修改其代码；随包分发时
  附许可证与源码地址，并在部署文档写明合规方式；不满足时改用商业搜索 API。
- **SSRF**：`web.fetch` 强制 `net_guard`；禁止内网、元数据、file://、重定向到内网；
  响应类型/大小/超时白名单。
- **提示注入**：网页内容按不可信数据处理；不得把抓取内容当作系统指令。
- **越权**：负责人/管理员/专家的所有敏感接口做“目标角色 vs 操作者角色”双重校验；
  防枚举统一 404。
- **审计**：成员变更、角色变更、重置密码、授权变更、敏感查看、市场安装、搜索配置
  全部落审计；敏感查看要求 reason。
- **令牌**：改密/重置/停用/改角色递增 `token_version/authz_version`，旧会话立即失效。

---

## 3. 视角二：产品经理方案

### 3.1 用户与核心任务

| 角色 | 核心任务 | 当前痛点 |
|---|---|---|
| 小院负责人 | 看全局、定规则、管管理员、看敏感数据、对未来财务 Agent 负责 | 没有负责人界面；看不到成员使用情况 |
| 小院管理员 | 日常运营：成员、Agent、知识、插件、审核 | 不能建号/改角色/重置密码；设置改不了 |
| 专家 | 审核知识/Skill、指导成员、配置专家技能 | 审核页可用，但技能创建 422；无农业技能库 |
| 科研操作员 | 跑任务、看数据、用 Agent | 权限基本够用，缺自助改密 |
| 普通成员 | 对话、查知识、提任务 | 注册无入口/无审核说明；忘记密码无法自助 |

### 3.2 页面与交互改造

1. **成员管理（重做）**
   - 顶部：搜索、角色筛选、状态筛选、批量导入、邀请、注册审核入口；
   - 表格列：成员、角色、状态、邮箱/手机号（按权限脱敏）、最近活跃、7 日 Token、操作；
   - 行操作：编辑（抽屉）、重置密码、停用/启用、移除；
   - 新建账号向导：基本信息 → 角色 → 初始密码（自动生成可复制）→ 完成；
   - 注册审核 tab：待审列表、通过/拒绝、拒绝原因。
2. **系统设置（新增 4 个 tab）**
   - 角色与权限：系统角色名称/权限矩阵、自定义角色；
   - 注册与安全：注册模式、默认角色、密码策略、登录锁定；
   - 网络与搜索：搜索供应商、SearXNG 地址、API Key、日配额、连通性测试；
   - 敏感数据授权：管理员逐人开关（仅负责人可见）。
3. **能力中心 → 插件与技能**
   - 「技能库」：内置精选 / 远程 / GitHub / 本地导入；搜索、预览、安装；
   - 「插件库」：manifest、许可证、风险、审核状态；安装后待审；
   - 「已安装」：版本、来源、启用/停用、更新；
   - 离线提示条 + 本地 Skill 管理跳转。
4. **专家审核 → Skill 管理**
   - 新增“技能库/导入”入口：GitHub URL、上传 SKILL.md/zip、内置农业技能包；
   - 预览转换结果（名称/描述/步骤/来源/许可证）→ 保存草稿/提交审核；
   - 修复现有创建表单与后端字段不一致。
5. **负责人驾驶舱 `/owner`**
   - 一屏 7 张指标卡 + 趋势图 + 待办（待审、失败任务、异常成员）；
   - 成员用量表可排序、导出 Excel（复用文档生成能力）；
   - 会话审计：列表 + 详情抽屉（原因输入、审计提示）；
   - 授权中心：管理员 × 三类敏感权限的开关矩阵。

### 3.3 文案与错误处理

- 离线提示使用 §2.2.3 的用户原文；
- 越权统一：“你没有查看该数据的权限，请联系小院负责人”；
- 敏感查看：“本次查看将记录到审计日志（原因：…）”；
- 重置密码：“临时密码仅显示一次，成员首次登录必须修改”。

### 3.4 验收标准（产品）

- 负责人可完整走完：建管理员 → 授权 → 查看成员用量 → 查看会话（留审计）→ 转让负责人；
- 管理员不能删除/停用负责人，不能给自己加负责人角色；
- 专家可审核 Skill、可给普通成员做角色调整（按配置），不能触碰管理员/负责人；
- 普通成员可自助注册（待审核）、登录后改自己的资料与密码；
- 断网状态下打开市场出现指定文案，本地 Skill 导入仍可用；
- 联网后 `web.search` 能返回来源并在回答中展示引用。

---

## 4. 视角三：资深重度用户方案

重度用户（负责人/管理员/专家）每天高频操作，方案要减少点击与“猜权限”：

1. **5 分钟晨检**：`/owner` 首屏固定 6 个数字（今日活跃、今日 Token、失败任务、
   待审 Skill/知识、异常成员、联网状态），点数字直接进对应列表。
2. **批量与快捷**：
   - 成员支持 CSV 批量导入（用户名/姓名/邮箱/手机号/角色）；
   - 列表支持多选批量停用/改角色/重置密码；
   - 技能/插件支持多选安装、批量启用；
   - 所有列表记住筛选与分页（localStorage，按用户隔离）。
3. **少弹窗**：成员编辑用右侧抽屉，保存即生效；重置密码用确认弹窗 + 一次性密码；
   市场安装用侧边预览，不跳页。
4. **权限可解释**：每个禁用按钮 hover 说明“为什么不能点”（需要负责人/需要授权）；
   设置页提供“当前角色能力一览”只读矩阵。
5. **审计透明**：敏感查看顶部提示、可追溯到人；负责人可一键导出审计 CSV。
6. **离线友好**：本地部署时市场/搜索入口不消失，而是明确“离线可用/不可用”；
   本地技能库始终可操作，避免“功能看起来坏了”。
7. **性能**：驾驶舱聚合查询走索引与缓存（60s），单页首屏 ≤1.5s；成员 1000 人
   分页 ≤300ms。

---

## 5. 非目标（本轮不做）

- 不做第三方插件代码的自动执行沙箱（只做声明式技能与受控 workflow）；
- 不做短信/邮件服务商接入（手机号先存储与唯一校验，验证码放二期）；
- 不做跨小院负责人、集团多小院合并报表（平台管理员保留）；
- 不做财务 Agent 本体，只预留驾驶舱数据接口；
- 不做 GitHub 任意仓库一键执行；只做 SKILL.md/manifest 转换与审核安装。

---

## 6. 分波计划与门禁

### 第一波：2.2.0（联网 + 市场 + 本地技能库）

| 阶段 | 内容 | 预估 |
|---|---|---|
| P1 | `search` 模块、SearXNG 编排、`web.search/web.fetch`、net_guard、缓存/配额 | 2.5d |
| P2 | 联网状态探测、离线错误码与前端提示、搜索设置页 | 1.5d |
| P3 | 市场条目/三来源搜索/GitHub 转换器/Skill 直接安装/Plugin 待审 | 3d |
| P4 | 技能库/插件库 UI、专家审核导入、离线导入 | 2d |
| P5 | 农业首批技能包转换（10–15 个）+ 许可证/出处 | 1.5d |
| P6 | 测试与门禁 | 1.5d |

**合计约 12 人日**（多 Agent 并行可压到 5–6 个工作日）。

### 第二波：3.0.0（身份权限 + 负责人驾驶舱）

| 阶段 | 内容 | 预估 |
|---|---|---|
| P1 | `identity` 模块、角色/权限矩阵、yard_owner 迁移、authz 唯一入口 | 3d |
| P2 | 成员建号/编辑/停用/重置密码/注册审核/自助资料 | 3d |
| P3 | 系统设置（角色权限、注册安全、授权） | 2d |
| P4 | 负责人驾驶舱（总览/成员/用量/会话审计/Agent） | 3.5d |
| P5 | 敏感授权、审计、导出、性能 | 1.5d |
| P6 | 测试与门禁 | 2d |

**合计约 15 人日**（并行 6–8 个工作日）。

### 每波通用门禁

- 后端：新增用例全绿、ruff/format/mypy 全绿；迁移往返；
- 前端：ESLint 0 error、`vue-tsc`、`vite build`；
- Playwright：负责人/管理员/专家/成员四条主旅程 + 离线旅程 + 搜索引用；
- 契约：OpenAPI 重新生成、错误码登记；
- 安全：SSRF、权限矩阵负向、负责人保护不变量、敏感查看审计；
- 文档：CHANGELOG、audit-report 新章节、计划状态。

---

## 7. 需要拍板的决策点

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| D1 | 搜索默认路线 | A. SearXNG 自建（免费/无 Key，但 AGPL）/ B. Tavily（1000 次/月免费）/ C. Brave（$5/月额度） | **A 作为自托管默认**，独立进程调用并做 AGPL 合规；商业发行可切 B/C，配置不改代码 |
| D2 | Skill 安装策略 | A. 直接发布 / B. 一律待审 / C. 低风险自动、其余待审 | **C** |
| D3 | Plugin 安装策略 | A. 直接启用 / B. 必须专家审核 | **B** |
| D4 | 负责人实现 | A. 新增 `yard_owner` + `yards.owner_user_id` / B. 复用 yard_admin | **A**（唯一、可转让、不可删） |
| D5 | 角色配置范围 | A. 仅系统角色改名/改权限 / B. 另支持自定义角色 | **B**（上限 20） |
| D6 | 敏感数据 | A. 管理员默认可见 / B. 默认不可见，负责人逐人授权 | **B** |
| D7 | 注册模式 | A. 关闭 / B. 开放直接注册 / C. 开放但需审核 | **C** 默认，可配置 |
| D8 | 版本节奏 | A. 合成一个 3.0.0 / B. 2.2.0 + 3.0.0 两波 | **B**（第一波可先交付） |
| D9 | 手机号/邮箱验证 | A. 本轮接短信/邮件 / B. 先存储唯一校验，验证二期 | **B** |
| D10 | GitHub 搜索出口 | A. 服务端匿名 / B. 服务端带可选 Token | **B**（只影响限流，不下发前端） |

---

## 8. 参考资料

- Bing Search APIs 退役公告（2025-08-11）：
  https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement
- SearXNG 官方文档（自建、免费、可输出 JSON）：
  https://searxng.org/
- Tavily 定价（每月 1000 免费 credits）：
  https://www.tavily.com/pricing
- Brave Search API 定价（已无长期免费套餐，$5/月额度）：
  https://brave.com/search/api/
- 农业技能库：
  - https://github.com/farmfriend-labs/agent-skills-farming （MIT）
  - https://github.com/PangenomeAI/academic-skills-food-nutrition （MIT）
  - https://github.com/bgtamang/AgriClaw （MIT）
  - https://github.com/Adilmunawar/ZD-claude-plugin （MIT）
  - https://github.com/MedouneSGB/AgriAgent （MIT）
  - https://github.com/Shuvam-Banerji-Seal/AgriIR （Apache-2.0）
  - https://github.com/microsoft/farmvibes-ai （MIT）
  - https://github.com/danforthcenter/plantcv （MPL-2.0，服务化接入）

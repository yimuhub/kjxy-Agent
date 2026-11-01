# 指挥官 Agent（Commander）升级报告

> 版本：v1（2026-11-01）
> 状态：已在 3.38.0 交付（P0 文档修复 + 指挥官主闭环；见下方「实施状态」）
> 范围：对话主入口的主 Agent 编排能力 + 本次 PPT 生成故障修复
> 关联：`backend/app/modules/agents/supervisor.py`、`backend/app/modules/chat/group.py`、`backend/app/modules/agents/teams/`、`backend/app/modules/agents/agent_builder.py`、`backend/techyard-sidecar-macos.spec`

---

## 实施状态（2026-11-01，3.38.0）

- **P0 文档渲染修复**：DeckPlan 先做形状修复再严格校验；sidecar 构建增加 `--collect-all pptx/docx`；渲染组件缺失转 `DOC_RENDER_FAILED` 并降级为 Markdown 大纲卡。15 条专项测试通过。
- **指挥官主闭环**：会话级执行模式（普通/指挥官）、计划预览后确认、DAG 并行与并发降级、复用已建助手 / 自动创建草稿（`AI 创建` 标记）、计划卡 / 实时运行卡 / 汇总卡、SSE 三帧与运行管理 REST 全部落地。
- **门禁**：后端 2092 passed；隔离栈全量 E2E 113 passed / 1 skipped；契约与版本 3.38.0 九处一致；界面规范检查通过。
- **3.39.0 补齐（本文件缺口清零）**：选兵接入 LLM 重排（`commander_match`，<0.6 视为无合适）、近 30 天成功率/频次/负载打分、90 天去重窗口、每日创建上限；计划卡可删除/改指令/换助手/调顺序；`POST /commander/preview` 与 `PUT /commander/runs/{id}/plan`；运行前配额预检；澄清阶段（`clarify_enabled`）；`commander_run_started` / `commander_plan_edited` 审计与 `GET /commander/stats`；SQLite 提交串行化与锁重试；会话列表「指挥官」徽章；打包冒烟新增 `.pptx/.docx` 生成断言（`--docs-selfcheck`）。
- **3.40.0 自主补救**：失败不再回问用户——按「原执行者重试 → 换相关助手 → 新建专用助手 → 指挥官带全部工具亲自执行」自救，`remediate_rounds`（默认 5）轮尽才以 `COMMANDER_CAPABILITY_GAP` 报告原因；完全控制继承（`commander_runs.approval_mode` 快照，`full` 不逐次审批、也不问澄清）；默认 `preview_plan=false` / `clarify_enabled=false`（一句话即开跑）；重规划自动带最近 6 条会话与上一版产物标题，成品反馈直接进入修订；子任务卡展示尝试轮次与补救轨迹，新增 `commander_subtask_remediated` 审计。
- **保留未做**：critic 质检（设置预留但未接入汇总）、跨进程后台队列（当前为进程内后台任务）。

---

## 0. 一页结论

1. **你在客户端遇到的"服务暂时不可用"不是模型不通**。引擎日志显示：模型正常返回，但演示计划（DeckPlan）字段类型不合规，系统降级走模板渲染；模板渲染时又因为桌面安装包**漏打包 `python-pptx` 的模板资源**（`notesMaster.xml`）直接崩溃，最终被兜底成 `INTERNAL_ERROR`。这是一个**打包缺陷 + 计划校验过严**的叠加问题，与网络、密钥、供应商都无关。修法明确（见 §1），建议先作为 3.37.1 热修。
2. 你想要的"指挥官"本质是**一个常驻的主 Agent + 动态任务分解（DAG）+ 多 Agent 并行执行 + 汇总答复**。现有系统已经具备一半底座：总控路由（`supervisor`）、群聊并行（并发 3、预算/超时/成员事件）、班组接力、一句话生成 Agent、会话锁、SSE 步骤流。**不需要推翻重做，扩展一条"指挥官模式"执行链即可**。
3. 建议把"指挥官"做成**会话级模式**（普通对话 / 指挥官对话可切换，新会话默认值可在设置里定），而不是替换所有对话。这样既满足"只跟一个大脑沟通"，又保留现有低成本单 Agent 路径。
4. 并发按你的口径落地：**默认 3**，可选 3/5/8/10/自定义（建议上限 10）；**上游异常时自动降到 2 并发**并把剩余子任务排队，界面明确提示"已降级"。
5. 选兵规则：**优先复用已发布 Agent**（规则打分 + 小模型重排）→ 没有合适才自动生成 → 新 Agent 进"我的智能体"并打 `AI 创建` 标记 → 同意图再次出现时直接复用，不重复造。
6. 权限口径必须澄清：指挥官的"最高权限"**不等于越权**。它以当前登录用户的有效权限为上限，不能看别人的私有 Agent、不能绕过工具审批和配额。否则就是一个合法的越权后门。
7. 建议分四步交付：P0 修 PPT（0.5 天）→ P1 指挥官最小闭环（2–3 天）→ P2 并行与降级（2–3 天）→ P3 动态选兵与自动建 Agent（2–3 天）。每个阶段都可独立上线验收。

---

## 1. 先修：本次 PPT 失败的完整诊断

### 1.1 证据链（来自客户端真实引擎日志）

日志文件：`~/Library/Application Support/科技小院/logs/engine.log`（本次会话 14:04–14:05）：

```
14:04:56 选择了模型提供方 supplier:科技小院（DB 供应商配置生效）
14:05:55 POST https://apihub.agnes-ai.com/v1/chat/completions 200 OK   ← 模型调用成功
14:05:55 演示计划校验失败，降级为 DocModel 模板：
         [{'loc': 'slides.0.timeline', 'message': 'Input should be a valid list'},
          {'loc': 'slides.0.process',  'message': 'Input should be a valid list'}, ...]
14:05:55 chat send unexpected error:
         [Errno 2] No such file or directory:
         '.../_MEI000166cdf0lbSM/pptx/oxml/../templates/notesMaster.xml'   ← 打包缺资源
14:05:55 chat stream execution failed: 同一异常
```

### 1.2 根因（两个缺陷叠加）

| # | 缺陷 | 位置 | 后果 |
|---|---|---|---|
| A | 模型返回的 DeckPlan 里 `timeline` / `process` 是 `null` 或对象，严格校验判失败 | `backend/app/modules/chat/document.py:304` 捕获后降级 | 明明能生成，退化成模板 |
| B | 桌面版 sidecar（PyInstaller）未收集 `pptx` 包的模板资源 | `backend/techyard-sidecar-macos.spec:5-37` 只收 `app/alembic/pydantic/...`，没有 `pptx` | 模板渲染抛 `FileNotFoundError` → 被 `chat/stream.py:278` 兜底成 `INTERNAL_ERROR` → 界面显示"服务暂时不可用" |

补充发现：`docx` 包同样带模板资源（`docx/templates/default.docx` 等），**当前 spec 也没有收集**。也就是说桌面版的 Word 文档生成同样存在这个隐患，只是这次先被 PPT 触发。`openpyxl` 无此类模板依赖，不受影响。

### 1.3 修复方案（P0，独立于指挥官）

1. **打包资源**：`backend/techyard-sidecar-macos.spec` 增加
   ```python
   for pkg in ("pptx", "docx"):
       tmp = collect_all(pkg)
       datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]
   ```
   并在冒烟脚本里加一条断言：生成一个最小 `.pptx` / `.docx` 成功（防止再次漏包）。
2. **计划校验宽松化**：`DeckPlan` 校验前做一次"形状修复"——`timeline/process/tables/charts` 为 `null` 时归一成 `[]`；数字/字符串互转；多余字段忽略；只对"页数、版式枚举、必填标题"做硬校验。修复后再失败才降级。
3. **错误可读化**：渲染失败时不再只回"服务暂时不可用"，而是返回 `DOC_RENDER_FAILED` + 一句人话（"PPT 渲染组件在安装包中缺失，请更新到 3.37.1"），并保留 Markdown 大纲作为可下载兜底，保证用户至少拿到内容。
4. **回归**：新增单测（畸形 DeckPlan 修复、模板渲染在打包环境可用的冒烟），E2E 增加"对话生成 PPT 成功并出现下载卡"。

> 结论：这一项和指挥官无关，建议立即热修，避免用户把"指挥官上线了但 PPT 还是挂"混为一谈。

---

## 2. 需求拆解与验收口径

### 2.1 你的原话 → 可验收的能力

| 需求 | 落地定义 |
|---|---|
| 对话里有一个"最高的指挥官" | 系统内置一个不可删除的主 Agent，展示名可自定义（默认"指挥官"），常驻在对话模式选择里 |
| 只跟指挥官沟通 | 用户只对指挥官说自然语言；子 Agent 的中间过程以"进度/子任务卡"呈现，不要求用户逐个对话 |
| 自动拆成多个小任务 | 指挥官输出结构化计划（目标 / 子任务 / 依赖 / 建议 Agent / 期望产出），计划可预览可改 |
| 多个 Agent 同时干 | 按依赖关系并行执行；无依赖的子任务同批并发 |
| 同时几个可设置 | 设置项提供 3 / 5 / 8 / 10 / 自定义（1–10），默认 3 |
| 上游不支持就兜底 | 出现 429 / 5xx / 连续超时 → 自动把并发降到 2，剩余任务排队继续，界面标注"已降级" |
| 子 Agent 从已有智能体里选 | 先在本院已发布 Agent 中匹配（规则 + 小模型重排） |
| 没有合适的就新建 | 自动生成新 Agent 落库，标记来源"AI 创建"，后续复用；同意图不重复生成 |
| 主 Agent 会分析自然语言并改写成指令 | 规划提示词 + 结构化 Schema + 校验修复，见 §5.3 |

### 2.2 非目标（本期不做）

- 不做跨小院的 Agent 调度（A2A 协作方已有独立通道，不混入指挥官）。
- 不做无限层级（子 Agent 不再派生孙子 Agent），避免成本与调试失控。
- 不做"无人值守自动跑业务"（定时、自动执行已有独立机制）。
- 不改变"新 Agent 默认草稿"的既有红线（见 §5.4 的例外申请方式）。

---

## 3. 现状底座盘点

| 能力 | 现状 | 复用方式 |
|---|---|---|
| 总控路由 | `agents/supervisor.py`：规则 → LLM 分类 → 频率兜底；`ask_directed` 可定向执行 | 指挥官复用 `list_candidates` 与 `ask_directed` 作为"子任务执行器" |
| 群聊并行 | `chat/group.py`：`GROUP_CONCURRENCY=3`、`MAX_ANSWERS_PER_TURN=6`、`MEMBER_TIMEOUT_SECONDS=60`、每人独立 Session、`member-event` 事件 | 并发骨架、预算/超时/事件形状直接借鉴 |
| 班组接力 | `agents/teams/`：固定成员、串行、断点续跑、成员台账 | 串行的"接力"不适合动态编排，仅复用运行台账思路 |
| 一句话生成 Agent | `agents/agent_builder.py:596 generate_agent`：检索→设计→技能匹配→落草稿 | 自动建 Agent 直接调用，补"去重"与"来源标记" |
| 声明式 Agent 执行 | `agents/declarative.py:1347 run_declarative_agent`（步骤、工具审批、Token 记账） | 子任务执行统一走它 |
| 会话锁 | `chat/models.py:58` `target/target_code`，群聊靠参与者表判定 | 新增 `execution_mode` 列区分普通/指挥官 |
| SSE 步骤流 | `chat/stream.py`：steps + 群聊 `member-event` | 新增 `commander-plan` / `subtask-status` / `commander-summary` |
| 设置框架 | `yards/settings_router.py:51 SETTINGS_PERMISSIONS` + `PUT /yards/current/settings/{key}` | 新增 `commander` 设置键与校验 |
| 模型解析链 | v3.37 绑定链（全局/能力/助手/助手专属能力） | 新增用途码 `commander_plan`、`commander_merge` |
| 配额 | `quota` 模块 + LLM 记账 | 每个子任务独立记账，运行级预算叠加 |
| 审计 | `audit.service.log_audit` | 计划、降级、自动建 Agent 全部留痕 |

一句话：**底座够用，缺的是"计划—选兵—并行—汇总"这一层编排壳**。

---

## 4. 总体架构

```
用户 ──自然语言──▶ 指挥官（规划 + 汇总，不直接干活）
                     │  ① 理解：目标/约束/交付物/成功标准
                     │  ② 规划：DAG 子任务（可预览、可编辑）
                     │  ③ 选兵：已有 Agent 优先，没有则自动建
                     │  ④ 执行：无依赖子任务并行（默认 3，异常降 2）
                     │  ⑤ 汇总：产物 + 引用 + 失败说明 + 下一步建议
                     ▼
        ┌───────────┬───────────┬───────────┐
     子 Agent A   子 Agent B   子 Agent C …（各自用自己的模型/技能/工具）
        └───────────┴───────────┴───────────┘
                     ▼
              运行台账（commander_runs / commander_subtasks）
```

### 4.1 三条硬不变量

1. **指挥官只做规划与汇总**，子任务必须落到具体 Agent 执行；避免"指挥官自己一把梭"导致过程不可见、无法复用。
2. **计划先可见**：默认"预览后执行"（可设置自动执行）；用户可删子任务、换 Agent、改指令。
3. **失败不连坐**：任一子任务失败只影响自身；能凑出部分成果就先交付，并明确列出失败项与重试入口。

---

## 5. 核心机制

### 5.1 会话模式与入口

- `chat_sessions` 增加 `execution_mode`（`standard` / `commander`），迁移 0058；默认值取设置 `commander.default_mode`。
- 输入框左侧增加模式切换（「指挥官」/「普通」），切换只影响本会话后续轮次。
- 会话列表徽章新增「指挥官」，与 群聊/班组/锁定助手 并列（`frontend/src/views/ChatView.vue:453` 同类逻辑）。
- 指挥官不走群聊参与者表，避免与群聊语义混淆。

### 5.2 四阶段流水线

| 阶段 | 输入 | 输出 | 失败处理 |
|---|---|---|---|
| 理解 | 用户消息 + 附件上下文 + 会话历史摘要 | `goal`、约束、交付物类型 | 信息不足 → 先问 1–2 个澄清问题（可设置关闭） |
| 规划 | 目标 + 可用 Agent 能力目录 | `Plan JSON`（§5.3） | 解析失败 → 修复一次 → 仍失败则退化为单 Agent 直答并如实说明 |
| 选兵 | 每个子任务的 `agent_hint` | 绑定的 `agent_id` 或"需新建" | 无候选 → 自动生成（受设置开关控制） |
| 执行 | 子任务指令 + 依赖产物 | 子任务结果（文本/文档/图片/链接） | 见 §5.6 失败矩阵 |
| 汇总 | 全部子任务结果 | 最终答复 + 产物卡 + 引用 | 部分失败 → 标注并给重试 |

### 5.3 计划 Schema 与提示词

```json
{
  "goal": "为丰收季苹果礼盒生成一份 8 页的销售提案 PPT",
  "success_criteria": ["含封面/卖点/价格/渠道/行动项", "中文排版", "可直接下载"],
  "assumptions": ["默认 16:9", "面向经销商"],
  "subtasks": [
    {
      "id": "t1",
      "title": "梳理提案结构与卖点",
      "instruction": "输出 8 页的页面清单，每页一句话主旨 + 3 个要点…",
      "agent_hint": "增长/营销策划",
      "agent_code": "growth_marketing",
      "depends_on": [],
      "expected_output": "页面大纲（Markdown）",
      "parallel_group": 1
    },
    {
      "id": "t2",
      "title": "生成 PPT 文件",
      "instruction": "基于 t1 的大纲，使用 office_ppt_deck 生成 .pptx…",
      "agent_hint": "办公文档/PPT",
      "depends_on": ["t1"],
      "expected_output": "pptx 下载卡",
      "parallel_group": 2
    }
  ],
  "needs_new_agent": [],
  "merge_notes": "最终给出下载链接 + 3 条使用建议"
}
```

约束：

- 子任务数默认 ≤ 6（可设置 2–12），每个子任务必须能在一轮 Agent 调用内完成。
- 字段宽松解析：`depends_on` 缺失视为 `[]`；`parallel_group` 缺失按拓扑层级自动计算；`agent_code` 不存在则进入"选兵"。
- 提示词要点：把"用户原话"改写成"可直接交给下游 Agent 的指令"，包含输入、输出格式、边界（不要做什么）、验收标准；禁止把多个目标塞进一个子任务。
- 规划模型与汇总模型可分开绑定（用途码 `commander_plan` / `commander_merge`），默认跟随全局。

### 5.4 动态选兵与"自动建 Agent"

**选兵顺序（便宜到贵）：**

1. **硬过滤**：已发布、当前用户可见（public 或本人私有）、状态正常、排除指挥官自身、能力覆盖（技能/工具/知识域）。
2. **规则打分**：子任务关键词 × Agent 名称/描述/技能/工具；叠加近 30 天该 Agent 成功率与使用频次；同分优先负载低。
3. **LLM 重排**：取前 8 个候选 + 子任务描述，要求输出 `agent_code + confidence + reason`；`confidence < 0.6` 视为"无合适"。
4. **去重检查**：无合适时先做一次"意图指纹"比对（规范化关键词 + 交付物类型），命中近 90 天自动创建的 Agent 且状态可用 → 直接复用。
5. **自动生成**：调用现有一句话生成（`agent_builder.generate_agent`），把子任务目标作为 utterance，自动绑定技能/工具；来源标记 `created_from=commander`，蓝图入库。
6. **可用性**：新 Agent 默认按既有红线创建为**草稿**（`declarative.py:566` 注释"一律 draft"）。本轮可由指挥官直接调用草稿执行；是否发布给普通路由使用，由设置决定：
   - 默认：草稿 + 对话内提示「已为你创建助手「PPT 设计师」，点此发布」；
   - 可选（保护型开关，需负责人确认）：**自动发布**，审计留痕 `commander_auto_publish`。

> 说明：这样既满足"后续类似事情直接调用"，又不破坏 ADR-0005（新 Agent 人工发布）的安全红线。

### 5.5 并发与上游兜底

| 项 | 建议值 | 说明 |
|---|---|---|
| 默认并发 | 3 | 你的口径；单供应商常见限流下较稳 |
| 可选档位 | 3 / 5 / 8 / 10 / 自定义 1–10 | 设置页单选 + 自定义输入 |
| 兜底并发 | 2 | 上游异常时自动降级，不再继续抛请求 |
| 触发条件 | 首波 30 秒内出现 ≥2 次 429/5xx/超时，或连续 2 个子任务失败 | 记 `degraded=true`，界面提示"已降为 2 个并发" |
| 子任务上限 | 默认 6，可设 2–12 | 控制"无数个小任务"导致的成本失控 |
| 单子任务超时 | 默认 180 秒（文档类可到 300） | 独立于群聊的 60 秒 |
| 单轮总超时 | 默认 600 秒 | 超时返回已完成部分 + 未完成清单 |
| 队列策略 | 拓扑分层，同层按优先级入信号量 | 不用"一次全抛" |

实现要点：

- 每个子任务使用**独立 DB Session**（沿用 `group._execute_one_member` 模式），完成后一次性落 `commander_subtasks`，避免长事务。
- 本地桌面版是 SQLite：已开 WAL；并发写仍可能 `database is locked`。需要设置 `busy_timeout`、失败重试 2 次、写操作串行化（单写队列），这是 P2 必须实测的点。
- 降级只影响"未开始"的子任务调度，不取消已经跑起来的请求；已失败的按 §5.6 决定是否重排。

### 5.6 失败处理矩阵

| 场景 | 处理 |
|---|---|
| 上游 429 / 5xx / 超时 | 子任务重试 1 次（指数退避）；整体并发降为 2；仍失败则标记 `error` 并继续其他子任务 |
| 子 Agent 模型未配置 | 该子任务失败，提示"该助手未绑定可用模型"，建议换 Agent 或去模型中心 |
| 依赖子任务失败 | 下游标记 `skipped`（不是 `error`），汇总里说明原因 |
| 计划 JSON 非法 | 修复提示一次；仍失败 → 单 Agent 直答 + 说明"未能拆分任务" |
| 自动建 Agent 失败 | 回退到"最接近的已有 Agent"或由指挥官代做该子任务（标记"指挥官代执行"） |
| 用户取消 | 取消未开始子任务；已完成的保留，可部分交付 |
| 配额不足 | 运行前预检；不足时拒绝开跑并给出额度入口，而不是跑到一半失败 |

### 5.7 成本与配额

- **运行级预算**：单轮最大 token（默认 12 万，可设），超限停止新子任务并汇总已有结果。
- **子任务级记账**：沿用 `token_usage`（输入/输出/总计）写入 `commander_subtasks`；汇总时展示"本次消耗 + 各 Agent 占比"。
- **配额预检**：每个子任务开跑前走现有配额引擎；被拒的子任务给出可读原因。
- **重复消耗防护**：同一会话内相同指令 10 分钟内再次发送 → 提示"复用上次结果？"（可关闭）。

### 5.8 权限与安全（必须写清楚）

- 指挥官**以当前用户身份运行**：候选 Agent 可见性、知识库范围、工具审批模式、配额全部沿用该用户的有效权限。
- "最高权限"的准确含义是**调度权**：可调度一切"该用户本来就能用"的 Agent；不是跨用户、跨小院的超级权限。
- 工具副作用仍需审批（`approval_mode`）；指挥官不能代替用户点"同意"。
- 自动建 Agent / 自动发布属于高影响动作：默认关闭发布，开启需 `settings:manage_protected`（负责人确认），全部审计。
- 指挥官人格提示词禁止出现"忽略权限/绕过审批"类指令；所有系统提示词集中维护，前端不落明文。

### 5.9 数据模型（迁移 0058）

```text
commander_runs
  id, yard_id, session_id, user_id, turn_id
  goal, plan_json
  status            queued | running | completed | partial | failed | cancelled
  max_parallel, effective_parallel, degraded(bool)
  tokens_total, latency_ms
  error_code, created_at, updated_at

commander_subtasks
  id, run_id, seq
  title, instruction, expected_output
  agent_id, agent_code, agent_source   existing | generated | commander
  status            pending | running | ok | error | skipped
  depends_on_json, attempts
  input_tokens, output_tokens
  output_excerpt, artifact_json        # 文档/图片/链接等产物卡
  error_code, started_at, finished_at
```

- 设置存 `yard_settings(key="commander")`，不新增表；值由 schema 校验。
- 自动生成的 Agent 复用 `yard_agents`，`created_from` 增加取值 `commander`（需同步校验白名单与前端徽章）。

### 5.10 API 与 SSE 契约

| 接口 | 说明 |
|---|---|
| `GET /yards/current/settings/commander` | 读取指挥官设置（名称/并发/开关/模型绑定） |
| `PUT /yards/current/settings/commander` | 保存；`settings:manage`（自动发布需保护确认） |
| `POST /chat/sessions/{id}/messages` | 新增可选 `execution_mode`；会话已有列时以会话为准 |
| `GET /chat/sessions/{id}/messages/stream` | 流式；新增事件见下 |
| `POST /commander/runs/{run_id}/retry` | 重试失败/跳过子任务 |
| `POST /commander/runs/{run_id}/cancel` | 取消未开始子任务 |
| `GET /commander/runs/{run_id}` | 运行详情（子任务表 + 用量 + 产物） |
| `POST /commander/preview` | 只规划不执行（计划预览/编辑用） |

SSE 事件（在现有 `steps` / `member-event` 之外新增，形状沿用）：

```json
{"event":"commander-plan","data":{"run_id":"...","goal":"...","subtasks":[{"id":"t1","title":"...","agent_name":"...","depends_on":[]}],"degraded":false}}
{"event":"subtask-status","data":{"run_id":"...","subtask_id":"t1","status":"running","agent_name":"增长策划","tokens":0}}
{"event":"commander-summary","data":{"run_id":"...","answer":"...","artifacts":[...],"failed":[{"id":"t3","reason":"..."}]}}
```

### 5.11 前端交互

1. **输入框模式切换**：「指挥官」/「普通」；切到指挥官时显示一句提示"描述目标即可，我会拆分并派给合适的助手"。
2. **计划卡**（默认先预览）：
   - 子任务列表（可删除、可改指令、可换 Agent、可调整先后）；
   - "开始执行 / 自动执行"按钮；
   - 顶部显示"将使用 3 个助手并行（可在设置中调整）"。
3. **执行视图**：
   - 每个子任务一张卡：`等待 / 进行中 / 完成 / 失败`，显示所属 Agent、耗时、Token；
   - 并行时多卡同时转；降级时顶部条幅"上游限流，已降为 2 个并发"。
4. **汇总卡**：最终答复 + 产物（文档/图片下载卡）+ "未完成项与原因" + 重试按钮 + 本次消耗。
5. **设置页新增「指挥官」页签**（`SettingsView.vue`）：

| 设置项 | 控件 | 默认 |
|---|---|---|
| 启用 | 开关 | 开 |
| 显示名称 / 头像 | 输入 / emoji | 指挥官 / 🧭 |
| 新会话默认进入指挥官 | 开关 | 关 |
| 同时执行 Agent 数 | 3/5/8/10/自定义 | 3 |
| 上游异常兜底并发 | 数字（1–10） | 2 |
| 单轮子任务上限 | 数字（2–12） | 6 |
| 先预览计划再执行 | 开关 | 开 |
| 允许自动创建助手 | 开关 | 开 |
| 自动发布新助手 | 保护型开关 | 关 |
| 规划/汇总模型 | 供应商→模型级联下拉 | 跟随中心 |
| 单轮 Token 预算 / 超时 | 数字 | 12 万 / 600 秒 |

6. **智能体库**：自动创建的 Agent 显示「AI 创建」徽章与"待发布"，支持一键发布/忽略；重复创建时提示"已存在相似助手「X」，直接复用？"

### 5.12 观测与审计

- 审计事件：`commander_plan_created`、`commander_run_started/finished`、`commander_degraded`、`commander_agent_created`、`commander_agent_published`、`commander_subtask_retried`。
- 指标（本地日志即可）：计划成功率、子任务成功率、平均并发、降级次数、自动建 Agent 命中率、运行时长分布。
- 运行详情页（可选 P3）：任务中心里新增"指挥官运行"筛选。

---

## 6. 与群聊、班组的关系

| 形态 | 目标 | 执行方式 | 适用 |
|---|---|---|---|
| 群聊（现有） | 同一个问题听多个视角 | 所有成员回答同一问题，**并行 ≤3** | 头脑风暴、多角色评审 |
| 班组（现有） | 固定角色按流程接力 | **串行**，成员带断点续跑 | 固定 SOP（调研→写作→质检） |
| 指挥官（新增） | 一个目标拆多任务动态派工 | **动态 DAG，并行可配**，成员可变 | 复合交付（PPT、方案、活动落地） |

三者共用：模型绑定链、技能/工具、配额、审计、SSE 形状。**不共用**：参与者表（群聊）、成员快照（班组），避免语义串味。

---

## 7. 交付计划

| 阶段 | 内容 | 估时 | 版本建议 | 验收 |
|---|---|---|---|---|
| P0 | PPT/Word 打包资源、DeckPlan 宽松校验、错误可读化、冒烟断言 | 0.5 天 | 3.37.1 | 桌面版生成 PPT 成功并可下载 |
| P1 | 设置项 + 会话模式 + 规划 + 串行执行 + 汇总 + SSE/UI（计划卡、汇总卡）+ 审计 | 2–3 天 | 3.38.0 | 一句话 → 2–3 个子任务 → 汇总交付 |
| P2 | 并行执行 + 降级到 2 + 子任务事件 + 计划编辑 + 重试/取消 | 2–3 天 | 3.39.0 | 模拟 429 时自动降级且剩余任务完成 |
| P3 | 动态选兵（规则+LLM 重排）+ 自动建 Agent + 去重复用 + 可选 critic 质检 | 2–3 天 | 3.40.0 | 无合适 Agent 时自动创建，第二次同意图直接复用 |

每一步都跑：后端全量 pytest、ruff/mypy、前端 lint/vue-tsc/build、契约生成、全量 E2E（隔离实例）、DMG 冒烟。

---

## 8. 测试计划

**后端单测**

- 计划解析：非法 JSON、缺字段、超上限、依赖成环、agent_code 不存在。
- 并发器：3 并发、降级到 2、超时、取消、重试、部分失败。
- 选兵：规则命中、LLM 重排、置信度阈值、去重指纹、自动创建失败回退。
- 配额/预算：子任务被拒、运行预算耗尽、记账准确。

**集成**

- 指挥官一轮完整运行（Mock LLM）：计划 → 2 个 Agent 并行 → 汇总 → 台账落库 → 审计。
- 自动建 Agent：草稿可见、本轮可执行、发布后进入普通路由。
- 会话隔离：私有 Agent 不被他人调度；审批模式生效。

**E2E（新增 v338/v339/v340）**

- 对话输入"帮我做一个 PPT"→ 计划卡出现 → 执行 → 下载卡。
- 设置并发 5 → 计划卡提示 5；模拟降级 → 条幅出现。
- 无合适 Agent → 自动创建 → 智能体库出现"AI 创建"徽标 → 二次同意图不重复创建。
- 移动端：模式切换与计划卡可用。

**回归**

- 群聊、班组、锁定助手、@点名、文档链路全部不受影响（全量 E2E）。

---

## 9. 风险与取舍

| 风险 | 影响 | 对策 |
|---|---|---|
| 成本放大（一个指令 N 个 Agent） | 账单飙升 | 子任务上限 + 运行预算 + 计划预览 + 配额预检 + 消耗展示 |
| 计划质量不稳定 | 拆错、重复、空转 | Schema 校验 + 修复 + 失败退化为单 Agent；P3 加 critic 质检 |
| SQLite 并发写 | `database is locked` | WAL + busy_timeout + 重试 + 写串行化；P2 必须做压力实测 |
| 自动建 Agent 泛洪 | 智能体库被垃圾占满 | 意图指纹去重 + 默认草稿 + 一键忽略/清理 + 每日创建上限 |
| 自动发布越权 | 不可控的"自生长" | 默认关闭，保护型开关 + 审计 |
| 上游限流 | 大面积失败 | 降级 2 并发 + 退避 + 部分交付 |
| "最高权限"被误解 | 安全预期偏差 | 文档与设置文案明确"调度权 ≠ 越权"；所有动作以用户权限为上限 |

---

## 10. 需要你拍板的 6 件事（含建议）

1. **默认模式**：新会话默认普通对话还是指挥官？
   建议：默认普通；设置里可设为"默认指挥官"，避免小问题也付出多 Agent 成本。
2. **计划是否先确认**：默认"先预览后执行"还是"直接执行"？
   建议：默认预览（首次），用户可勾选"以后都直接执行"。
3. **新 Agent 是否自动发布**：默认草稿还是一步到位？
   建议：默认草稿 + 本轮可用；"自动发布"作为保护型开关，默认关。
4. **并发上限**：10 是否保留？
   建议：设置里保留 10，但界面提示"超过 5 需要上游支持较高并发"；默认 3、兜底 2。
5. **子任务上限**：默认 6 是否够？
   建议：默认 6、上限 12；超过时指挥官必须先合并子任务。
6. **失败重试策略**：自动换 Agent 重试，还是只重试原 Agent？
   建议：仅重试原 Agent 1 次；换 Agent 需要用户在汇总卡上点"换个助手重试"，避免自动扩散成本。

---

## 附录 A：关键代码位置

| 主题 | 位置 |
|---|---|
| 对话分流（本地/定向/总控） | `backend/app/modules/chat/service.py:1525`、`:1530` |
| DeckPlan 降级点 | `backend/app/modules/chat/document.py:304` |
| PPT 渲染（python-pptx） | `backend/app/modules/docs/render_pptx.py` |
| sidecar 打包清单 | `backend/techyard-sidecar-macos.spec:5-37` |
| 群聊并发/预算/超时 | `backend/app/modules/chat/group.py:38-43`、`:580`、`:370` |
| 总控候选与执行 | `backend/app/modules/agents/supervisor.py:94`、`:253`、`:305`、`:397` |
| 班组运行台账 | `backend/app/modules/agents/teams/models.py`、`service.py` |
| 声明式 Agent 执行 | `backend/app/modules/agents/declarative.py:1347` |
| 自动创建 Agent（一律草稿） | `backend/app/modules/agents/declarative.py:566`、`agent_builder.py:596` |
| 会话模型 | `backend/app/modules/chat/models.py:58` |
| SSE 流 | `backend/app/modules/chat/stream.py:207`、`:231` |
| 设置权限表 | `backend/app/modules/yards/settings_router.py:51` |
| 前端设置页签 | `frontend/src/views/SettingsView.vue:28+` |
| 会话徽章 | `frontend/src/views/ChatView.vue:453` |

## 附录 B：术语

- **DeckPlan**：PPT 的结构化计划（页数/版式/内容），渲染器据此生成 `.pptx`。
- **子任务（subtask）**：指挥官拆分出的最小可执行单元，必落一个 Agent。
- **降级（degrade）**：上游限流/故障时把并发从 N 降到 2，保证任务继续。
- **意图指纹**：用于判断"这次要建的 Agent 是否已经存在"的规范化特征（关键词 + 交付物类型）。
- **critic**：可选的质检 Agent，对汇总结论或产物做一次结构/事实检查。

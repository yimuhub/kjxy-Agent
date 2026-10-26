# v1.3.0 设计契约：供应商体验 + 本地模型放行 + 自生长闭环

## A. 供应商表单（SettingsView）+ 模型列表探测

表单字段顺序：供应商类型 → 基础地址 → **API Key** → **默认模型（下拉）**：
- 基础地址或 API Key 输入完成（失焦）→ 自动调探测端点拉模型列表 → 默认模型变
  `el-select filterable`（纯选择，非输入；loading 态；失败显示重试 + 不显眼的
  「手动输入」兜底开关切回输入框，诚实提示原因）；成功后下拉记住列表，可手动刷新。
- 编辑已有供应商时：进入即自动探测一次；探测失败回落显示已存值。
- 探测端点：`POST /api/v1/settings/suppliers/probe-models`
  body `{provider_type, base_url, api_key?}`（api_key 缺省且传了 supplier_id 时用已存密钥）
  → `{models:[{id,label}], source:'remote'|'unsupported'|'error', message?}`
  - openai/deepseek/moonshot/custom(OpenAI 兼容)：GET {base}/models，Bearer 鉴权，
    超时 8s，响应 ≤1MB，model id 列表；
  - anthropic/gemini 无统一列表端点：source='unsupported' + 中文说明。
  - 不落库、不建供应商；SSRF 用本地放行策略（见 B）。

 约束：api_key 只在请求体瞬时使用，绝不落任何日志/审计（审计只记数量）。

## B. 本地模型 SSRF 放行策略（net_guard 扩展）

`check_egress_url(url, *, context="", allow_local=False)`：
- allow_local=True 时**额外允许**：127.0.0.0/8、::1、10/8、172.16/12、192.168/16、fc00::/7
- **永远禁止（无论开关）**：169.254.0.0/16 与 fe80::/10（云元数据/链路本地）、组播、
  0.0.0.0、其余保留段；DNS 重绑定双查逻辑保持
- 调用方分级：LLM 供应商（保存/测试/运行时/探测）→ allow_local=True；
  设备连接器、推送通道、MCP 服务器 → 保持 default 严格（小院管理员配供应商属可信输入，
  本地推理 Ollama/LM Studio 是正当场景；元数据地址仍封死）
- 错误文案区分：命中元数据/保留 → 「禁止访问元数据或保留地址」；
  非放行内网（strict 场景）→ 维持原文案

## C. 自生长闭环（evolution + plugins）

### C1 Skill 自动总结（复用现有 evolution_skills，无新表）
- `POST /api/v1/evolution/skills/suggest` body `{focus_hint?}`（yard_admin）：
  聚合近 7 天证据（成功 run 的任务样本 top、失败错误类别、高频工具序列）→
  LLM 产出 1-3 张技能候选卡 → 落 `evolution_skills`：status=`pending_review`、
  created_from=`auto:suggest`、definition_json={title,when_to_use,procedure[],
  examples[],evidence{run_count,success_rate,top_tools[],sample_tasks[]}}
- 证据不足（<5 次运行）→ 诚实拒绝 `EVOLUTION_INSUFFICIENT_EVIDENCE`，不硬凑
- LLM 不可用 → `EVOLUTION_LLM_UNAVAILABLE`（502），不落半成品
- 审核走**现有** skill review 队列/接口（ReviewView 零改造即可见），FE 仅在
  EvolutionView 加「AI 总结候选」按钮 + 结果提示；skill 列表 created_from 带 `auto:` 前缀
  时显示「AI 生成」徽章
- 审计 action=`skill_suggested`（metadata 只记数量与 skill_code，不存全文）

### C2 声明式插件（复用 plugin_manifests，type='workflow'，无新表）
manifest_json（workflow 型）：
```json
{"name":"...", "description":"...", "params":[{key,label,required}],
 "steps":[{"kind":"tool","tool":"sensor.query","args":{},"save_as":"r1"},
          {"kind":"llm","prompt":"基于 {{r1}} 给出建议 {{question}}"}]}
```
- 注册：`POST /api/v1/plugins/register` body {manifest}（yard_admin）→ 校验（步骤 ≤8、
  工具必须在注册表且小院可用、无 mcp__/未知工具直接 400）→ 落库 status=`pending_review`、
  type='workflow'、checksum=manifest sha256、created 走 created_by 审计
- 审核：`POST /api/v1/plugins/{id}/review` {decision:approve|reject}（yard_admin 批准；
  运行时高风险工具仍走既有逐次审批闸——发布闸与运行闸双保险，同 MCP 模式）
- approve → status=`enabled`；reject → status=`rejected`
- 執行：`POST /api/v1/plugins/{id}/run` {params}（enabled 才可跑）→ 顺序解释执行 steps
  （tool→tool_registry.execute 全复用风险闸/审计；llm→complete_with_logging；
  save_as 入上下文，模板 {{var}} 插值）→ {outputs:{...}, final:"末步文本"}；同步执行
- 自动生成：`POST /api/v1/plugins/suggest`（yard_admin）：挖掘近 7 天 agent_run_steps
  中「同一助手重复出现的工具调用序列」（≥3 次、长度≥2）→ 合成 workflow 草稿
  status=`pending_review` + manifest._auto={pattern,occurrences,source_runs sha12 列表}
  （**只产草稿走审核，绝不自动启用**——诚实：自动化止步于建议，启用永远经人）
- 插件列表（现有 GET /plugins）响应补 created_from 语义字段（auto/manual）

### 硬约束（全线）
- 不加新 pip/npm 依赖；小院边界；中文注释；审计不存正文全文（sha12 摘要）
- 迁移：本波**预计无新表**；若实现中确需列变更，迁移号 0018 并说明理由
- 门禁：backend ruff+mypy+pytest（基线 589）+ generate_openapi；前端 lint 0 error + build
- 禁止 git 操作；禁止动进程

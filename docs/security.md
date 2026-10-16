# 安全规范

## 身份与权限

- 账号密码登录 + JWT（access 30 分钟 / refresh 7 天，可配置）；密码 bcrypt 哈希存储。
- RBAC：platform_admin、tenant_admin、expert、operator、viewer、service_worker；权限点见 `app/modules/tenants/permissions.py`。
- 后端每个敏感操作重新鉴权；前端隐藏按钮仅为体验优化。
- API 不信任前端提交的 user_id / role / tenant_id；租户上下文来自已验证身份。

## 租户隔离

- 全部租户业务表强制 `tenant_id`；repository 默认加租户过滤。
- 缓存键包含租户维度；跨租户共享默认禁止。
- 跨租户越权负向测试：`backend/tests/security/`（替换 URL/请求体/文件 ID/任务 ID/知识 ID 尝试访问）。

## 输入与文件安全

- 全部输入经 Pydantic Schema 校验，限制大小与长度。
- 文件上传白名单：pdf、docx、txt、md、csv；单文件 ≤50MB（可配置）；存储键为服务端随机生成，防路径穿越；checksum 记录。
- 参数化查询 / ORM，禁止拼接 SQL；禁止执行模型生成的任意代码与 Shell。
- 外部网页/知识文件中的提示注入文本不提升为系统指令；工具执行前经服务端权限策略校验。

## 高风险动作

| 级别 | 示例 | 要求 |
|---|---|---|
| R0 | 知识查询 | 权限通过自动执行 |
| R1 | 农业建议、报表草案 | 展示来源与风险提示 |
| R2 | 工单草案、租户 Skill 发布 | 角色权限 + 审计 + 必要确认 |
| R3 | 设备控制、正式派单、对外提交 | 明确二次确认/审批，支持熔断与审计 |

工具 `risk_level=external_side_effect` 未获审批一律拦截（`TOOL_RISK_BLOCKED`）。

## 模型风险

- 模型输出经 Schema + 业务规则校验；不把模型文本当权限决策/法律结论/农业事实。
- 超时、最大步数、token/成本预算、按租户限流；调用记录供应商/模型/耗时/token/估算成本。
- 模型不可用返回降级状态，不悄悄替换。

## 日志与审计

- 日志分应用日志 / 任务事件 / 模型调用 / 工具调用 / 安全审计；清除密钥、密码、令牌与身份证件。
- 审计记录不可被普通租户成员修改；错误响应不含堆栈与内部路径。

## 备份恢复

见 `docs/operations.md` 与 `deploy/scripts/`。

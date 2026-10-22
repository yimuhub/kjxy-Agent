# 错误码规范

所有 API 错误响应格式：

```json
{
  "error": {"code": "TASK_NOT_FOUND", "message": "任务不存在或无权访问。", "details": {}},
  "request_id": "req_..."
}
```

- `code`：稳定机器可读，前端不得解析 `message` 做分支。
- `message`：面向用户中文，不含密钥、SQL、内部路径、堆栈。
- 访问不存在资源与无权资源在需防枚举场景统一返回 404 + `NOT_FOUND`。

## 通用

| code | HTTP | 说明 |
|---|---|---|
| `INVALID_REQUEST` | 400 | 请求体/参数校验失败，details 含字段错误 |
| `UNAUTHORIZED` | 401 | 未登录或令牌无效/过期 |
| `FORBIDDEN` | 403 | 已登录但无权限 |
| `NOT_FOUND` | 404 | 资源不存在或无权访问（防枚举） |
| `CONFLICT` | 409 | 冲突，如重复创建、幂等键结果不一致 |
| `RATE_LIMITED` | 429 | 触发限流 |
| `INTERNAL_ERROR` | 500 | 服务端未知错误 |
| `SERVICE_UNAVAILABLE` | 503 | 依赖不可用（降级提示） |

## 认证 / 租户

| code | HTTP | 说明 |
|---|---|---|
| `INVALID_CREDENTIALS` | 401 | 用户名或密码错误 |
| `ACCOUNT_DISABLED` | 403 | 账号已停用 |
| `TENANT_REQUIRED` | 400 | 用户归属多租户，登录需指定 tenant_id |
| `TENANT_FORBIDDEN` | 403 | 用户不属于该租户 |
| `MEMBER_EXISTS` | 409 | 成员已存在 |

## 任务

| code | HTTP | 说明 |
|---|---|---|
| `TASK_NOT_FOUND` | 404 | 任务不存在或无权访问 |
| `INVALID_TASK_TRANSITION` | 409 | 非法状态转移 |
| `TASK_NOT_CANCELLABLE` | 409 | 当前状态不允许取消 |
| `TASK_NOT_RETRYABLE` | 409 | 当前状态不允许重试 |

## 知识库

| code | HTTP | 说明 |
|---|---|---|
| `DOCUMENT_NOT_FOUND` | 404 | 文档不存在或无权访问 |
| `UNSUPPORTED_FILE_TYPE` | 415 | 不在白名单的文件类型 |
| `FILE_TOO_LARGE` | 413 | 超过单文件大小限制 |
| `DOCUMENT_PARSE_FAILED` | 422 | 解析失败，详情见文档 error_message |
| `CANDIDATE_NOT_FOUND` | 404 | 待审核知识不存在 |
| `INVALID_REVIEW_ACTION` | 409 | 非法审核动作/状态 |

## 问答 / 反馈 / Skill

| code | HTTP | 说明 |
|---|---|---|
| `QA_FAILED` | 502 | 问答执行失败（模型/检索异常），可重试 |
| `FEEDBACK_NOT_FOUND` | 404 | 反馈不存在 |
| `SKILL_NOT_FOUND` | 404 | Skill 不存在 |
| `SKILL_VERSION_CONFLICT` | 409 | 版本已存在 |
| `INVALID_SKILL_TRANSITION` | 409 | 非法 Skill 状态转移 |
| `EVOLUTION_INSUFFICIENT_EVIDENCE` | 409 | 窗口内运行样本不足，不生成技能建议（AI 总结诚实拒绝） |
| `EVOLUTION_LLM_UNAVAILABLE` | 502 | 模型不可用/输出不可解析，未生成技能建议（不落半成品） |
| `EVAL_GATE_BLOCKED` | 409 | 评测门禁未通过 / 无法执行评估，禁止发布；details 含 failed_case_ids 与通过率 |
| `EVAL_GATE_UNAVAILABLE` | 503 | 评测门禁所需问答 Agent 不可用 |

## 工具 / 模型

| code | HTTP | 说明 |
|---|---|---|
| `TOOL_NOT_FOUND` | 404 | 工具不存在或未启用 |
| `TOOL_VALIDATION_FAILED` | 400 | 工具参数 Schema 校验失败 |
| `TOOL_FORBIDDEN` | 403 | 无工具调用权限 |
| `TOOL_TIMEOUT` | 504 | 工具执行超时 |
| `TOOL_RISK_BLOCKED` | 403 | 高风险工具未获审批被拦截 |
| `LLM_ERROR` | 502 | 模型调用失败（供应商错误已分类） |
| `LLM_NOT_CONFIGURED` | 503 | 真实模型未配置 |

## Agent 运行 checkpoint（P0 内核，2026-10-21）

| code | HTTP | 说明 |
|---|---|---|
| `RUN_NOT_FOUND` | 404 | 运行步骤不存在或跨租户（防枚举） |
| `RUN_ALREADY_COMPLETED` | 409 | 运行已完成（final ok），拒绝断点续跑重放 |
| `RUN_CHECKPOINT_INVALID` | 409 | checkpoint 数据不完整或属于另一版本配置，无法续跑 |

## 政策模块（P1）

| code | HTTP | 说明 |
|---|---|---|
| `POLICY_MODULE_DISABLED` | 404 | 政策模块未启用 |
| `POLICY_NOT_FOUND` | 404 | 政策不存在 |

## 整改升级新增（2026-10-17）

| code | HTTP | 说明 |
|---|---|---|
| `SSRF_BLOCKED` | 400 | 出网目标指向回环/link-local/云元数据/保留（或策略禁用私有）地址 |
| `SIGNUP_DISABLED` | 403 | 自助注册已由部署方关闭（SIGNUP_ENABLED=false） |
| `RESERVED_USERNAME` | 400 | 使用了系统保留用户名（admin/root/system 等） |
| `WEAK_PASSWORD` | 400 | 密码长度或强度低于当前环境策略 |
| `QUEUED_TIMEOUT` | — | 任务 error_code：queued 超时被周期回收（queued→failed） |
| `RUNNING_TIMEOUT` | — | 任务 error_code：running 超时/卡死被周期回收 |
| `DOCUMENT_NOT_REPARSABLE` | 409 | 文档当前状态不允许重新解析（仅 failed 可调） |

## 插件 / 声明式 workflow（v1.3-B）

| code | HTTP | 说明 |
|---|---|---|
| `PLUGIN_NOT_FOUND` | 404 | 插件不存在或跨租户访问（workflow 按当前租户校验） |
| `PLUGIN_MANIFEST_INVALID` | 400/422 | manifest 校验失败（注册路径 400：步骤>8/未知或 mcp 工具/save_as 非法等） |
| `PLUGIN_TYPE_UNSUPPORTED` | 422 | 不支持的插件类型 |
| `PLUGIN_CONFLICT` | 409 | plugin_id 被其他类型占用 |
| `INVALID_PLUGIN_TRANSITION` | 409 | 非法插件审核状态转移（仅 pending_review 可审） |
| `PLUGIN_NOT_ENABLED` | 409 | 插件未通过发布闸（enabled）不可运行 |

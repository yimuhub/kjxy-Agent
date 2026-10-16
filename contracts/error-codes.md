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

## 政策模块（P1）

| code | HTTP | 说明 |
|---|---|---|
| `POLICY_MODULE_DISABLED` | 404 | 政策模块未启用 |
| `POLICY_NOT_FOUND` | 404 | 政策不存在 |

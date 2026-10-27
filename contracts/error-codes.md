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

## 认证 / 小院

| code | HTTP | 说明 |
|---|---|---|
| `INVALID_CREDENTIALS` | 401 | 用户名或密码错误 |
| `ACCOUNT_DISABLED` | 403 | 账号已停用 |
| `YARD_REQUIRED` | 400 | 用户归属多小院，登录需指定 yard_id |
| `YARD_FORBIDDEN` | 403 | 用户不属于该小院 |
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
| `RUN_NOT_FOUND` | 404 | 运行步骤不存在或跨小院（防枚举） |
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
| `PLUGIN_NOT_FOUND` | 404 | 插件不存在或跨小院访问（workflow 按当前小院校验） |
| `PLUGIN_MANIFEST_INVALID` | 400/422 | manifest 校验失败（注册路径 400：步骤>8/未知或 mcp 工具/save_as 非法等） |
| `PLUGIN_TYPE_UNSUPPORTED` | 422 | 不支持的插件类型 |
| `PLUGIN_CONFLICT` | 409 | plugin_id 被其他类型占用 |
| `INVALID_PLUGIN_TRANSITION` | 409 | 非法插件审核状态转移（仅 pending_review 可审） |
| `PLUGIN_NOT_ENABLED` | 409 | 插件未通过发布闸（enabled）不可运行 |

## 小院助手 / 群聊（v2.0）

| code | HTTP | 说明 |
|---|---|---|
| `AGENT_NOT_FOUND` | 404 | 声明式助手不存在、已删除或无权访问（防枚举） |
| `AGENT_STATE_CONFLICT` | 409 | 已停用的助手不可编辑/发布；非待审核状态不可驳回 |
| `AGENT_CODE_TAKEN` | 409 | agent_code 与平台内置 Agent 冲突 |
| `AGENT_VERSION_CONFLICT` | 409 | 同小院同编码该版本已存在 |
| `DUPLICATE_NAME` | 409 | 同小院已存在同名助手；details 带 {id, agent_code, name, status} 供前端弹「复用/复制/重命名」 |
| `AGENT_REVIEW_REQUIRED` | 403 | 私有助手含高风险能力，需小院管理员发布 |
| `AGENT_COVER_MISSING` | 404 | 助手未设置首图或首图文件已丢失 |
| `CHAT_ATTACHMENT_NOT_FOUND` | 404 | 附件不存在或无权访问 |
| `GROUP_PARTICIPANTS_TOO_MANY` | 400 | 群聊成员超过上限（8） |
| `GROUP_PARTICIPANT_INVALID` | 400 | 成员必须是本小院可见的已发布助手；details 含 unknown_agent_ids |
| `GROUP_NO_PARTICIPANTS` | 400 | 名单为空时发起群聊轮 |
| `GROUP_TARGET_REQUIRED` | 400 | named 策略未指定应答对象 |
| `GROUP_TARGET_NOT_PARTICIPANT` | 404 | 点名对象不在名单内 |
| `GROUP_MEMBER_TIMEOUT` | — | 消息级 error_code：成员回答超时（视为未回答） |
| `GROUP_MEMBER_ERROR` | — | 消息级 error_code：成员执行失败（视为未回答） |
| `GROUP_MEMBER_DECLINED` | — | 消息级 error_code：成员自判不开口（auto 判退） |
| `GROUP_NO_ANSWER` | — | 消息级 error_code：本轮无人回答 |

## 对话文档生成（v2.1）

| code | HTTP | 说明 |
|---|---|---|
| `DOC_MODEL_INVALID` | 400 | DocModel 校验失败（标题/块类型/表头列数/条目为空等）；details.errors 带前 5 条原因 |
| `DOC_TOO_LARGE` | 400 | 文档内容或生成文件超过上限（正文总字符 / `upload_max_mb`） |
| `DOC_FORMAT_UNSUPPORTED` | 400 | 不支持的导出格式；details.supported 列出 md/docx/xlsx/pptx/pdf |
| `DOC_RENDER_FAILED` | 500 | 写出器渲染失败（附 request_id，可重试或换格式） |
| `DOC_NOT_FOUND` | 404 | 文档不存在、跨会话或无权访问（防枚举，统一 404） |
| `DOC_PREVIEW_UNSUPPORTED` | 415 | 该附件没有 DocModel 快照（用户上传件），不支持在线预览，请下载 |

## 联网搜索与市场（v2.2 / v3.0）

| code | HTTP | 说明 |
|---|---|---|
| `NETWORK_UNAVAILABLE` | 409 | 设备/网络不可用（GitHub 检索、开源导入）；details.mode=`local`，前端提示本地部署形态并引导本地 Skill 管理 |
| `SEARCH_PROVIDER_UNAVAILABLE` | 503 | 搜索未配置供应商或全部供应商不可达；details 带 primary/failures |
| `SEARCH_QUOTA_EXCEEDED` | 429 | 当日联网搜索配额用尽（按小院与 UTC 日期计数） |
| `WEB_FETCH_BLOCKED` | 400 | `web.fetch` 目标非公网（回环/私网/link-local/元数据/保留地址）或重定向越界 |
| `MARKET_FORBIDDEN` | 403 | 插件安装仅限小院管理员/负责人 |
| `MARKET_ITEM_NOT_FOUND` | 404 | 市场条目不存在或已下架 |
| `MARKET_ITEM_INVALID` | 422 | 条目缺少可安装的 definition/manifest |
| `MARKET_ITEM_BLOCKED` | 403 | 条目被风控拦截，禁止安装 |
| `MARKET_AGENT_UNSUPPORTED` | 400 | Agent 安装仅支持内置种子包编码 |
| `IMPORT_TYPE_UNSUPPORTED` | 422 | 本地技能仅支持 .md/.markdown/.txt |
| `IMPORT_TOO_LARGE` | 413 | 技能 >256KB、插件包 >2MB 或解压后 >5MB |
| `IMPORT_INVALID` | 400 | 导入内容非 UTF-8/非法文件 |
| `IMPORT_CONFLICT` | 409 | 同名条目过多，无法生成唯一编码 |
| `PLUGIN_IMPORT_UNSAFE` | 422 | 插件包含绝对路径/`..`/盘符/符号链接/非法名 |
| `PLUGIN_IMPORT_FORBIDDEN_FILE` | 422 | 插件包含可执行或非白名单文件类型 |
| `PLUGIN_IMPORT_INVALID` | 400 | 非法 zip、加密包或损坏压缩流 |
| `SKILL_NOT_FOUND` | 404 | GitHub 仓库中未找到可用的 SKILL.md |
| `SKILL_TOO_LARGE` | 413 | SKILL.md 超过 256KB |
| `REPO_NOT_FOUND` | 404 | GitHub 仓库不存在或不可访问 |

## 一句话技能（v3.2）

| code | HTTP | 说明 |
|---|---|---|
| `SKILL_LAB_DISABLED` | 403 | 本小院已关闭技能生成服务（设置项 `skill_lab.enabled`） |
| `SKILL_QUOTA_EXCEEDED` | 429 | 当日技能生成配额用尽（设置项 `skill_lab.daily_limit`，按小院与 UTC 日期计数） |
| `VALIDATION_ERROR` | 422 | 技能规格未通过模板校验（字段、步骤数、工具白名单、风险等级等）；一句话生成链路内部消化为纠错重试，不直接抛出 |
| `SKILL_NOT_FOUND` | 404 | 按 `skill_id` 送审的技能不存在或不属于当前用户 |

## 身份、角色与负责人（v3.0）

| code | HTTP | 说明 |
|---|---|---|
| `ACCOUNT_PENDING` | 403 | 账号等待注册审核，暂不可登录 |
| `EMAIL_TAKEN` / `PHONE_TAKEN` | 409 | 邮箱/手机号已被占用（全局唯一） |
| `PASSWORD_UNCHANGED` | 400 | 新密码与当前密码相同 |
| `INVALID_OLD_PASSWORD` | 400 | 自助改密时旧密码不正确 |
| `REGISTRATION_CLOSED` | 403 | 该小院关闭自助注册 |
| `MEMBER_EXISTS` | 409 | 用户已是本小院成员 |
| `ROLE_NOT_FOUND` | 404 | 角色不存在 |
| `ROLE_EXISTS` | 409 | 角色编码已存在或与系统角色冲突 |
| `ROLE_LIMIT` | 400 | 自定义角色数量超限（20） |
| `ROLE_IN_USE` | 409 | 仍有成员使用该角色，不能删除 |
| `INVALID_GRANTS` | 400 | 授予了不可授予的敏感权限；details.grantable |
| `AUDIT_REASON_REQUIRED` | 400 | 查看会话正文必须填写 2–200 字原因 |
| `OWNER_TRANSFER_INVALID` | 400 | 负责人转让目标非法（不能转给自己） |
| `TARGET_NOT_YARD_ADMIN` | 400 | 转让目标不是本小院在职管理员 |
| `YARD_NAME_TAKEN` | 409 | 小院名称已被占用（自助开通） |

## v3.4 自我总结提升

| 错误码 | HTTP | 说明 |
|--------|------|------|
| `SELF_IMPROVE_DISABLED` | 409 | 自我总结尚未开启，手动运行被拒绝 |
| `SELF_IMPROVE_BUDGET_EXHAUSTED` | 409 | 当日 token 预算已用尽；details.used_today / budget |
| `SELF_IMPROVE_LLM_UNAVAILABLE` | 502 | 模型不可用或输出无法解析，本轮未产生总结 |
| `SELF_IMPROVE_CONFIRM_REQUIRED` | 400 | 开启 auto_publish 需携带 confirm_auto_publish=true |

## v3.5 一句话生成智能体

| 错误码 | HTTP | 说明 |
|--------|------|------|
| `AGENT_GENERATE_DISABLED` | 403 | 小院设置已关闭一句话生成智能体服务（`agent_generate.enabled`） |
| `AGENT_GENERATE_QUOTA` | 429 | 该成员当日生成次数已用尽（设置项 `agent_generate.daily_limit`，按 tasks 行计数） |
| `AGENT_GENERATE_LLM_UNAVAILABLE` | 502 | 模型完全不可用，本轮未生成任何草稿（不落兜底垃圾） |
| `AGENT_GENERATE_FAILED` | 502 | 生成任务执行失败（非模型原因），task 标记 failed 可重试 |

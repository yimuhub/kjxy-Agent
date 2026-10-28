# 安全规范

## 身份与权限

- 账号密码登录 + JWT（access 30 分钟 / refresh 7 天，可配置）；密码 bcrypt 哈希存储。
- RBAC：platform_admin、yard_admin、expert、operator、viewer、service_worker；权限点见 `app/modules/yards/permissions.py`。
- 后端每个敏感操作重新鉴权；前端隐藏按钮仅为体验优化。
- API 不信任前端提交的 user_id / role / yard_id；小院上下文来自已验证身份。

## 小院隔离

- 全部小院业务表强制 `yard_id`；repository 默认加小院过滤。
- 缓存键包含小院维度；跨小院共享默认禁止。
- 跨小院越权负向测试：`backend/tests/security/`（替换 URL/请求体/文件 ID/任务 ID/知识 ID 尝试访问）。

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
| R2 | 工单草案、小院 Skill 发布 | 角色权限 + 审计 + 必要确认 |
| R3 | 设备控制、正式派单、对外提交 | 明确二次确认/审批，支持熔断与审计 |

工具 `risk_level=external_side_effect` 未获审批一律拦截（`TOOL_RISK_BLOCKED`）。

## 技能供应链

- 技能上架前跑静态质量检查（空定义、提示词超长、步骤超限、工具步骤未声明直接拦截）与可选数据集评测，报告落 `eval_runs`。
- 发布/推广/市场安装后对定义做 Ed25519 签名（`skill_code + version + definition_sha256`）；回滚与灰度前验签，定义被改动返回 `SKILL_ATTESTATION_INVALID`。
- 签名私钥优先取 `SKILL_SIGNING_SEED`，未配置时由 `APP_SECRET_KEY` 域分离派生；公钥经 `GET /skills/signing-key` 公布，`key_id` 便于轮换后定位。
- 签名只证明「签名后未被改动」，不证明内容正确；3.17 之前的存量版本没有签名，接口如实返回 `unsigned`，不冒充已验证。

## 单点登录（SSO/OIDC）

- 授权码 + PKCE（S256）+ 签名 state + nonce；一次性短码换登录态（哈希存储、单次使用、默认 60 秒）。
- id_token 只接受非对称签名（RS/ES/EdDSA），按 JWKS 验签并校验 iss/aud/exp/nonce；拒绝 HS*。
- 角色只来自小院成员关系：IdP 分组最多映射到 yard_admin/expert/operator/viewer，负责人与平台管理员不可由 IdP 授予。
- JIT 建号要求邮箱已验证；可选关闭自动建号、关闭按邮箱关联，并可限制邮箱域名。
- client_secret 用与小院供应商密钥相同的 Fernet 主密钥加密存储，列表只回「是否已配置」；修改时留空表示沿用旧密钥。
- IdP 配置属保护型权限（`settings:manage_protected`），默认仅负责人或获逐人授权的管理员可改；只读列表对 yard_admin 开放。
- 访问 IdP 的 discovery/token/JWKS 统一走出网守卫与 1MB 响应上限；IdP 地址由负责人配置，按可信输入放行内网与本机（校园内网 Keycloak 等正当场景），元数据与链路本地地址始终封死；`ENFORCE_MODEL_EGRESS_GUARD=true` 时一并不再放行内网。错误只回稳定错误码。
- 公开端点按 IP 限流；回调失败不泄露 IdP 原始响应与内部细节；审计记录 `sso_login_success / sso_identity_linked / sso_provider_*`，不含令牌与密钥。

## 跨小院协作（A2A）

- 入站令牌与小院一一绑定，只存 sha256，接口仅展示前缀；令牌决定小院归属，请求体里的任何归属字段都不采信。
- 入站只开放只读技能白名单（小院概况、知识检索）；不暴露工具执行、写入与成员数据；`A2A_ENABLED=false` 时 Agent Card 与 RPC 一律 404。
- 协作方令牌 Fernet 加密存储、永不回显；配置属保护型权限（`settings:manage_protected`）。
- 保存地址、探测 Agent Card、每次发任务前都过出网守卫（元数据/链路本地始终封死，`ENFORCE_MODEL_EGRESS_GUARD=true` 时内网也阻断）；响应上限 1MB、超时按配置、最多再轮询两次。
- 任务带幂等键（入站 = 对端 messageId，出站 = 本平台生成），重放不重复落库；任务状态机 `submitted → working → completed|failed|canceled`。


## 模型风险

- 模型输出经 Schema + 业务规则校验；不把模型文本当权限决策/法律结论/农业事实。
- 超时、最大步数、token/成本预算、按小院限流；调用记录供应商/模型/耗时/token/估算成本。
- 模型不可用返回降级状态，不悄悄替换。

## 日志与审计

- 日志分应用日志 / 任务事件 / 模型调用 / 工具调用 / 安全审计；清除密钥、密码、令牌与身份证件。
- 审计记录不可被普通小院成员修改；错误响应不含堆栈与内部路径。

## 备份恢复

见 `docs/operations.md` 与 `deploy/scripts/`。

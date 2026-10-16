# ADR-0003：供应商 API Key 的存储边界——DB 内 Fernet 加密密文 + 密钥环境变量

- 状态：已采纳
- 日期：2026-10-17

## 背景

规范 §2.2.4 默认原则："模型密钥只从环境变量或秘密管理系统读取，绝不写入 Git、数据库明文、前端代码或日志。"

多租户 SaaS 形态下，各租户通过 Web 端自助配置自己的模型供应商（DeepSeek/OpenAI 兼容等），密钥由租户管理员在界面录入——若坚持"只进环境变量"，则每增一个租户都要运维手工改服务器 .env，SaaS 自助闭环断裂。

当前实现（`llm/supplier_crypto.py` + `suppliers.api_key_enc` 列）：

- 录入的明文密钥用 Fernet 对称加密后落库（`api_key_enc`，L3 级数据）；
- 加密密钥 `SUPPLIER_KEY_SECRET` 只来自环境变量（44 字符 urlsafe base64），**不在库、不在 Git、不返回前端**；
- 所有 API 出参打码（`sk-****abcd` 风格），明文密钥绝不打日志；
- Provider 构建时解密进内存，用完即弃（`finally: del api_key`）。

## 决策

接受"数据库内**密文**存储"作为对 §2.2.4 的受控偏差：原则中"数据库明文"仍被严格遵守（库中无明文密钥）；密文存储换取租户自助配置供应商的产品能力。数据分级按 §5.4 的 L3 处理：仅秘密管理方式（Fernet + 环境变量密钥）保存，日志不输出，访问有权限（`platform_admin`/`tenant_admin`）与审计。

## 备选方案

- **环境变量 only**：被否决——多租户自助配置不可行，运维成本随租户数线性增长。
- **外挂秘密管理系统（Vault 等）**：作为后续部署形态（私有化场景可接 Vault/KMS）；首版 Fernet 满足单机 SaaS 试点。
- **明文入库**：被否决——违反 §2.2.4 红线与 §5.4 L3 要求。

## 收益

租户自助配置供应商（改配置即生效、Provider 缓存热失效）；部署仍只需一个环境变量密钥。

## 代价

- DB 备份/转储泄露时，密文可被离线攻击（Fernet 为对称加密，密钥强度取决于 `SUPPLIER_KEY_SECRET`）；
- 密钥轮换需要全部供应商重新录入（`reset_fernet_cache` + 解密失败路径已给出提示文案）。

## 安全影响

- `SUPPLIER_KEY_SECRET` 缺失/非法时，供应商加密功能返回明确 500 错误码（`SUPPLIER_KEY_NOT_CONFIGURED` / `SUPPLIER_KEY_INVALID`），不静默降级为明文；
- 备份策略（§15.5）必须覆盖"DB 备份 + 密钥"的访问控制：备份文件与密钥同级别管理；
- 轮换演练：更换 `SUPPLIER_KEY_SECRET` 后旧密文全部解密失败 → 供应商状态标记为需重新录入（现有 `SUPPLIER_KEY_DECRYPT_FAILED` 路径）。

## 迁移/回滚

- 后续接 Vault/KMS：新增 `SupplierKeyBackend` 抽象（env-fernet / vault 两实现），迁移脚本将库中 Fernet 密文导出 → 写入 Vault → 切换后端；
- 回滚：切回 env-fernet 后端，数据无变化。

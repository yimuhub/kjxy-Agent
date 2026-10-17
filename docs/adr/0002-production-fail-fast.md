# ADR-0002：生产启动安全 fail-fast 与管理员 bootstrap 收紧

- 状态：已采纳
- 日期：2026-10-17
- 相关：五维测评报告 S3（默认 JWT 密钥 × admin 白名单提权 × 弱种子口令 = 平台接管链）

## 背景

测评发现：`.env` 保持占位 `APP_SECRET_KEY` 即可签发自称任意角色的 JWT；名为 `admin` 的账号一经
登录自动获得 `platform_admin`；seed 演示账号 `admin/admin123` 且运维手册将其列为标准步骤。
三者组合构成可公开复述的平台接管路径。

## 决策

1. `Settings.enforce_runtime_safety()` 在应用启动时执行：`APP_ENV=production` 且
   （占位/过短 `APP_SECRET_KEY` 或 弱管理员名单 或 SQLite 数据库）→ 抛异常拒绝启动。
2. 保留“用户名 bootstrap”机制（核心功能不删减），但生产环境名单含 `admin/root/system/
   administrator/platform_admin/service_worker` 任一弱名即拒绝启动；非生产使用弱名时写审计告警。
3. 数据纠错入口与开发流程守卫：注册拒绝保留用户名；seed 生产守卫 + 可选随机口令；
   `SIGNUP_ENABLED` 支持邀请制。

## 备选方案

- 直接删除 bootstrap 机制：被否，违反“核心功能只能增加不能删减”，且首任管理员创建需要 CLI 通道。
- 仅文档提示不改代码：被否，测评已证明文档路径在真实部署中被跳过。

## 收益 / 代价

消除静默接管链；代价是生产误配会“起不来”——这正是预期行为（fail-fast 优于带病上线）。

## 安全影响 / 迁移回滚

纯收紧，无接口破坏。回滚仅需移除启动校验调用，但不建议。

"""机器人网关模块（Wave C1/C2）。

IM 平台（企微智能机器人 / 钉钉 Stream / 飞书长连接）以**出向 WebSocket**接入本平台：
- ``protocol``：连接抽象 + 企微 aibot 帧协议（协议层可注入假连接，测试全离线）；
- ``dingtalk`` / ``feishu_adapter``：其余两个平台的适配器（飞书 SDK 为可选依赖）；
- ``gateway``：入站编排（幂等 → 会话批准 → @剥离 → /use 切换 → 上下文 →
  supervisor 路由 → 分段回复 → 审计只存 sha12）；
- ``service``：配置 CRUD（凭据 Fernet 加密 + 掩码回显 + fail 熔断）；
- ``manager``：进程内连接器管家（Redis 选主，只有 leader 建连）；
- ``router``：``/bots`` 管理接口（租户管理员口径同 pairing）。
"""

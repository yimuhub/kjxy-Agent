# 机器人网关方案 v2（已定稿：用户 2026-10-23 批准按 C1→C2→C3 实施）

> 调研对象：腾讯 WorkBuddy（用户所指 "workbuddy"）、OpenClaw（原 Clawdbot，180k star
> 开源 IM 网关）、AstrBot/LangBot、钉钉/飞书/企业微信官方开放平台（2026-10 现行文档）。
> 来源链接见文末。原 v1（plan-platform-and-bots.md 第五节）的出站推送通道保持不变，
> 本方案补的是**入向 + 双向对话**。

## 一、调研结论：业界都怎么做，我们学什么

| 对象 | 接入机制 | 借鉴点 |
|---|---|---|
| **WorkBuddy（腾讯）** | 手机 IM 发指令 → 平台云 → **桌面端主动出向长连接**收任务 → 本机执行 → 结果回 IM。支持 Slack/TG/Discord/微信/企微/QQ/钉钉/飞书 9 平台；企微走 aibot 长连接（Bot ID+Secret，无需公网），微信客服号直接扫码绑定 | 「电脑不用公网也能被 IM 遥控」——与我们桌面一键版天然契合 |
| **企业微信智能机器人（官方 2026-10 文档）** | 两种方式互斥：**长连接** wss://openws.work.weixin.qq.com（BotID+Secret 订阅，**无需公网 IP、无需消息加解密**，官方 Node/Python SDK；30s 心跳；单机器人同时只允许 1 条连接）或 URL 回调（需公网+AES）。支持流式回复（主动推增量直到 finish=true）、**aibot_send_msg 主动推送**、模板卡片按钮交互、欢迎语。限频 30 条/分/会话 | 微信生态最优官方入口；长连接模式=零公网零加解密 |
| **钉钉 Stream 模式（官方）** | SDK 反向外连 WebSocket，「五零接入」（零公网 IP/域名/证书/网关/内网穿透）；机器人消息 topic `/v1.0/im/bot/messages/get`；**回调 2 秒超时** → 耗时任务先 ACK 再异步更新 AI 卡片 | 与企微同级的低门槛；2s 超时教训：LLM 必须异步 |
| **飞书（官方）** | lark-oapi SDK 长连接，WebSocket 全双工，明文推送、SDK 内建鉴权，无需公网 | 同上，实现成本最低 |
| **OpenClaw（OpenClaw）** | Hub-and-spoke：网关独占各平台连接，Channel Adapter 归一化消息；会话隔离 `per-group` 独立 session、DM 默认 main；**未知发件人回复被拒→配对码批准**（pairing）；群内 requireMention；watchdog 重连 | 会话模型与安全模型直接抄：DM 配对批准、按群隔离、@才答 |
| **AstrBot/LangBot** | 事件总线+适配器；QQ 生态分「官方机器人」（WebSocket 扫码一键创建，免.pem）与「个人号协议端」（NapCat/OneBot，有风控） | QQ 走官方 WebSocket；个人号协议端只作可选外挂，不做承诺 |

**一个共同架构事实**：2026 年主流做法全部转向「**我们的服务主动向外连接 IM 平台**」
（outbound WebSocket），公网回调已成备选。这彻底改变了我们 v1 方案「必须等服务器版
+公网域名」的前提——**桌面版单机就能直连微信生态**。

## 二、修订后的架构（kjxy-Agent bots 模块）

```
企微aibot WS ─┐                    ┌─ supervisor.ask 自动路由（默认）
钉钉 Stream  ─┼→ bots 网关 worker → ├─ 指定助手/班组（会话内 /use 切换）
飞书 长连接   ─┤   (统一消息事件)    └─ 审批卡片（批准/驳回高风险工具）
URL回调模式   ─┘        ↓
        会话映射表：平台身份 ↔ 租户 ↔ 会话（群 per-group / 私聊 per-user，上下文 6 轮滑窗）
        安全：陌生会话配对批准 · @才答 · 限频 · 只读工具默认 · 审计存 sha12
```

与 v1 的关键修订：
1. **长连接优先**：首选三平台全是出向 WS，桌面版/内网服务器版开箱即用，零公网零穿透；
   URL 回调（企微 AES/钉钉 HTTP）作为服务器版可选模式保留。
2. **连接器跑在独立 worker 进程**（Celery worker 或独立 botd）：企微「单机器人单连接」
   + 钉钉连接数限制 → 多实例部署用 Redis 抢锁选主，只有 leader 建连（桌面版单进程天然满足）。
3. **流式回复**：Agent 运行本有 step/文本增量 → 节流（≥2s/次）推平台流式接口，
   企微推到 finish=true，钉钉/飞书用卡片更新。**先 2s ACK，LLM 全异步**（钉钉 2s 超时铁律）。
4. **审批闭环上手机**（WorkBuddy 同款杀手锏）：高风险工具进审批闸时，向绑定的企微/钉钉
   会话推「批准/驳回」模板卡片，管理员手机点按 → 回调驱动审批 → 结果回原会话。
5. **主动推送打通**：现有 push 通道事件（设备告警/班组失败/工单）改走 aibot_send_msg /
   钉钉主动消息——同一机器人既能问答又能当告警播报员。

## 三、微信生态三条官方路径（诚实口径）

| 路径 | 门槛 | 能力 | 建议 |
|---|---|---|---|
| **企微智能机器人长连接** | 免费注册企业微信即可（个人可建企业）；BotID+Secret | 私聊+群@+流式+卡片+主动推送 | **Wave C 首位**，微信用户经「企微↔微信互通」互联 |
| **微信客服**（公众号/小程序/App 挂客服入口，扫码关注即可对话——WorkBuddy「微信客服号扫码直绑」即此路线） | 需公众号/小程序主体，部分能力要认证 | 客服会话式对话，48h/条数限制按场景 | C3 可选，有主体就开 |
| **服务号消息接口** | 认证服务号（企业资质） | 模板消息+会话消息 | 可选，与企微回调同构 |
| ~~个人微信直连协议~~ | 无官方 API（hook/padlocal 逆向） | 封号+合规风险 | **不做**，引导走企微互通 |

QQ：官方机器人 WebSocket（q.qq.com 创建，扫码一键，appid/secret）列 C3 可选。

## 四、实施切片（确认后执行）

| 片 | 内容 | 验收 |
|---|---|---|
| **C1** | bots 模块核心：ChannelAdapter 抽象+统一消息事件+会话映射模型(迁移 0019)+陌生会话配对批准+supervisor 对接+管理页（凭据配置/连接状态/会话审批/绑定助手）+ **企微 aibot 长连接适配器** | 企微手机端对机器人说话，得到带诚实路由徽章口径的回答 |
| **C2** | 钉钉 Stream + 飞书长连接适配器 + Redis 选主 | 三平台同群并存互不串话 |
| **C3** | 审批卡片闭环 + 流式回复 + 主动推送事件打通 +（可选）微信客服/QQ 官方机器人 | 手机上点「批准」→ 高风险工具执行 → 结果回群 |

**依赖口径**：`aibot-python-sdk` / `dingtalk-stream` / `lark-oapi` 作 **optional extras**
（`pip install .[bots]`，bots 模块惰性 import，主安装零新增依赖；桌面版随 sidecar 打包）。
测试全离线：适配器对 WS 协议层抽象，单测用假连接对象，不真连平台。

## 五、主要来源
- WorkBuddy 助理远程控制（9 平台清单）: workbuddy.ai/docs（助理远程控制 / 接入企业微信指南）
- 企业微信智能机器人长连接（官方）: developer.work.weixin.qq.com/document/path/101463
- 企微频道接收与发送概述: developer.work.weixin.qq.com/document/path/101906
- 钉钉 Stream 模式（官方）: help.dingtalk.io/zh/open/development/introduction-to-stream-mode；
  open.dingtalk.com/document/orgapp/event-callback-card
- 飞书长连接接收事件（官方）: open.feishu.cn/document/server-side-sdk/python--sdk/handle-events
- OpenClaw 网关架构: docs.openclaw.ai/concepts/architecture；ppaolo.substack.com（架构解析）
- AstrBot 平台接入: docs.astrbot.app/platform/qqofficial/websockets
- CodeBuddy 企微 Bot 实操: codebuddy.cn/docs/cli/wecom-bot-setup

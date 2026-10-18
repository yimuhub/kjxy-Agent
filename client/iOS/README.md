# iOS 客户端（云端壳）

现状：**无 Apple 开发者账号（$99/年）时无法安装到他人 iPhone / 上 TestFlight**。
本目录给出账号到位前的准备与到位后的一步出包路径。

## 方案

Tauri 2 iOS 壳（与安卓共用 `client/desktop` 工程）：WebView 加载试点服务器。
iOS 系统禁止子进程 → **只能云端模式**，本地引擎方案在 iOS 不可行（系统硬约束）。

## 出包步骤（账号到位后）

```bash
cd client/desktop
npm install
npx tauri ios init         # 生成 src-tauri/gen/apple（Xcode 工程）
npx tauri ios open         # 打开 Xcode：Bundle ID / 签名 Team 选你的开发者账号
# Xcode: Product > Archive > Distribute（App Store / TestFlight / Ad-Hoc）
```

## 审核风险（提前规避）

- 纯远程内容壳容易被 Guideline 4.2 拒：需让 App 有独立价值——
  建议接 APNs 推送（新任务/专家回复提醒）+ 原生启动页 + Siri 快捷指令后再提审。
- 服务器必须是 HTTPS + 国内可直连（无证不发布，走 TestFlight 内测也要 HTTPS）。

## 账号到位前能做什么

- 本仓库骨架、推送服务端预留（后端加 APNs token 注册接口 + 发送封装）；
- Xcode 模拟器可跑通壳与 H5（不签名，自己电脑演示用）：`npx tauri ios dev`。

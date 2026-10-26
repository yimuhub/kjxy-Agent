# 全平台发行与机器人升级方案（v1.4.0 → v1.6.0）

> 状态基线（2026-10-22 实查）：桌面 Tauri 壳（本地引擎 sidecar + 双模式）与 CI 构建流
> `client.yml` 已就位但未实跑；deploy/{docker,native(三平台),nginx} 已就位；
> **缺口**：自动升级、手机扫码局域网接入、机器人网关。本方案分三波补齐并给出
> 各手机版的具体实现路径。

---

## 一、五发行版总览（现状 → 目标）

| 版本 | 形态 | 现状 | 本方案动作 |
|---|---|---|---|
| macOS | .dmg（Tauri 壳+内置引擎） | 壳已写，CI 未跑 | 实跑 CI 出 arm64/universal 包 + 自动更新 |
| Windows | .exe NSIS 安装包 | 同上 | 实跑 CI + WebView2 引导 + 自动更新 |
| Linux | .AppImage/.deb | 同上 | 实跑 CI + tauri updater |
| 服务器版 | systemd 原生部署 | deploy/native 已有 | 补升级脚本 upgrade.sh（git pull + alembic + 重启） |
| Docker 版 | compose 一键起 | 三个 Dockerfile 已有 | 实跑构建 + 一键 compose + 更新即 pull |

## 二、桌面客户端：双击即用 + 自动升级（Wave A）

### 已具备（无需重做）
- Tauri 壳启动即拉起 PyInstaller sidecar 引擎（仅监听 127.0.0.1，随机端口），
  splash 等 /health/ready，就绪后窗口指向本地 Web；退出级联杀进程。
- 数据落在用户目录（不污染系统）；首启自动建库+迁移+种子（standalone.py）。

### 要补：自动升级（GitHub Releases 通道）
1. **tauri.conf.json**：`plugins.updater` + `createUpdaterArtifacts: true`；
   发布流升级时生成 `latest.json`（含各平台签名包+版本号）作为 updater endpoint。
2. **签名**：生成 Tauri 密钥对（`tauri signer generate`），私钥入 GitHub Secrets
   `TAURI_SIGNING_PRIVATE_KEY`，公钥写进 tauri.conf。无签名无法增量更新——这是一次性动作。
3. **检查节奏**：壳启动后 30s 静默检查一次 + 设置页「检查更新」手动按钮；
   发现新版 → 托盘/角标提示 → 用户点「立即更新」→ 下载签名包 → 重启安装（Tauri updater 原生流程）。
4. **发布流水线**：新增 `release.yml`：打 tag → 三平台矩阵构建 → 产物 + `latest.json`
   上传 GitHub Release → CI 桌面产物不再手挂。
5. **升级服务器版**（桌面「连接服务器」模式）：不做静默升级，服务器版由服务器自身
   `deploy/native/server/upgrade.sh` 管（见第四节），客户端只提示「服务器版本过旧/过新」。

### 平台细节
- **macOS**：ad-hoc 签名起步（内测），双击 dmg 拖入 Applications；后续可加 Apple 公证。
- **Windows**：NSIS 安装（双击→自动安装→桌面+开始菜单快捷方式→完成后自启）；
  检测/引导 WebView2（Win10 1803+ 自带，Win7/8 不在支持范围，安装器给出明确提示）。
- **Linux**：AppImage（chmod +x 双击跑）+ .deb；updater 走 Tauri 内置。

## 三、手机端：扫码进局域网（Wave B）

### 核心机制（一次开发，三端复用）
手机版是**壳 + 远程 Web**（不重复实现业务）：桌面/服务器版本来就提供全部 Web+API，
手机壳只做三件事：扫码配对、 WebView 承载、原生能力桥（通知/拍照上传）。

**扫码配对协议（新增）**：
1. 桌面版「设置 → 手机接入」或服务器版管理页 → `POST /api/v1/pairing`：
   后端枚举本机 LAN IPv4（不含 loopback/元数据段），生成一次性配对码
   （128bit 随机数，只存 sha256，默认 5 分钟有效，可设长期设备令牌）。
   返回 `kjxy://pair?host=<ip:port>&code=<一次性码>`。
2. 前端渲染二维码（qrcode 库，纯 canvas，无新依赖则后端出 PNG base64）。
3. 手机壳扫码 → `POST /pairing/exchange` {code} → 服务端校验哈希、标记已用 →
   换发 **pair token**（JWT：kind=pair，含设备名/小院/30 天有效期，可在管理页吊销）
   → 壳把 `http://<ip:port>` + token 存本地 → WebView 加载并带 `Authorization`。
4. **局域网安全**：默认仅接受 RFC1918/链路本地来源访问 pair 换发接口；HTTP 明文仅限
   LAN 场景并在 UI 明示风险；服务器版建议 TLS（deploy/nginx 已带证书挂点）。
5. 桌面引擎默认只听 127.0.0.1——手机要访问需用户显式开启「局域网共享」开关：
   sidecar 增加 `--lan` 启动参数（改绑 0.0.0.0 并打印局域网地址+二维码），
   关闭即回 127.0.0.1；开关状态入审计。

### Android（具体实现）
- **v1（本周可出）**：CI 已有 debug APK 任务（Tauri android build）——直接用 Tauri 壳，
  内置扫码页 + WebView 指向配对得到的地址；内测 APK 直装，无需商店账号。
- **v2 备选**：纯 Kotlin WebView 壳（约 600 行）+ CameraX 扫码，更轻、可进商店；
  需要时另立 client/android-native。
- 分发：GitHub Releases 挂 APK（内测）→ 有签名账号后 Google Play/国内商店。

### iOS（具体实现）
- 路线唯一合法：**Tauri iOS 工程 + TestFlight**（免费开发者账号 $99/年即可内测分发，
  无需上架审核通过）；`tauri android/ios build` 产出 .ipa，CI macOS runner 归档。
- 短期零门槛替代：iPhone Safari 直接访问配对地址（Web 已是响应式），
  「添加到主屏幕」当 PWA 用——**今天就能用**，壳只是体验增强。
- 本地网络权限：iOS 首次弹窗授权「本地网络」，壳文案预写好。

### 鸿蒙 HarmonyOS NEXT（具体实现）
- 现有 client/harmony ArkTS 工程为壳：扫码页（@kit.ScanKit）+ Web 组件（ArkWeb）
  指向配对地址；cookie/token 注入用 `runJavaScript`。
- 构建需 DevEco Studio + 签名证书（华为开发者账号）；CI 无官方 Linux runner，
  **走本地/自建 runner 出 .hap**，内测用企业签名或内部测试分发（TestFlight 对位：
  华为 AppGallery Connect 开放测试）。
- 短期替代：鸿蒙浏览器直接访问配对地址（同 PWA 思路）。

## 四、服务器版 & Docker 版（Wave B 顺带）

- **服务器版**：deploy/native/server 已有 systemd+nginx；补 `upgrade.sh`
  （git fetch/checkout tag → pip install → alembic upgrade head → 备份 DB → systemctl restart →
  health check 失败自动回滚上一 tag）；管理页显示版本号与「有新版本」提示（拉 Releases latest）。
- **Docker 版**：三镜像已有；根目录补 `compose.prod.yaml`（api+worker+web+postgres(pgvector)+redis，
  一条 `docker compose up -d` 起全栈）；升级 = `git pull && docker compose up -d --build`（或镜像仓库 pull）；
  数据卷与备份脚本联动 deploy/scripts/backup.sh。
- 两个发布产物附「版本自检」页脚：前端页脚显示构建号，服务器模式时显示远端版本对比。

## 五、机器人网关：微信 / 钉钉 / 企业微信（Wave C）

### 架构（新增 app/modules/bots/）
```
入站: 平台回调 → POST /api/v1/bots/webhook/{platform}（签名校验→幂等→去@）
        ↓
会话映射: bots_conversations（平台用户 ↔ 小院 ↔ 会话号，多轮上下文 6 轮滑窗）
        ↓
执行: supervisor.ask(自动路由) 或 绑定指定助手/班组（对话内 /use <助手名> 切换）
        ↓
出站: 复用 push 通道（已有 企微/钉钉 webhook）+ 各平台被动回复/主动消息 API
审计: bot_message_in/out（消息只存 sha12+长度，不存全文）
```

### 各平台接入形态（按可行性排序）
1. **钉钉群机器人（最容易，先行）**：企业内部机器人「消息回调」模式，
   HTTP 回调 + 签名头校验；回复走 sessionWebhook。**无需公网域名也行**（可选 Stream 模式）。
2. **企业微信自建应用**：接收消息服务器回调（URL 验证 + AES 加解密，官方 SDK 逻辑自实现，
   纯 stdlib crypto 可完成）；回复被动消息或主动应用消息。个人微信用户可通过「微工作台」触达。
3. **微信公众号（服务号）**：消息接收/回复 API 完整，但需认证服务号（企业资质）；
   实现与企微回调同构（同为 XML/AES 体系），列为「有账号即可开」。
4. **个人微信直连**：无官方 API；不提供非官方协议接入（封号+合规风险），
   引导用「企微 ↔ 微信互通」或公众号替代。文档写明原因（诚实口径）。

### 配置与安全
- yard_admin 在「设置 → 机器人」配置各平台凭据（Fernet 加密存储、掩码回显、
  连通测试按钮），一小院可挂多机器人；绑定「默认应答方式」：总控自动路由 / 指定助手。
- 白名单：会话首次接入可选「需管理员在管理页批准」开关（防陌生人借用算力）。
- 限频：每会话 6 msg/min、每机器人 60 msg/min（复用 push 的限频器）。
- 危险操作：机器人侧只暴露 supervisor 只读问答；带副作用工具在机器人上下文**默认禁用**
  （可在机器人配置显式放开，放开后进审批闸）。

## 六、实施波次与验收

| 波 | 内容 | 交付物 | 验收 |
|---|---|---|---|
| A | 桌面自动升级 + 发布流水线 | 签名密钥、updater 配置、release.yml、三平台产物挂 Releases、latest.json | 装旧版→发新版→壳自动提示并升级成功 |
| B | 扫码配对 + 手机壳实跑 + compose.prod + upgrade.sh | pairing API+二维码 UI+sidecar --lan、Android APK 实产、iOS/鸿蒙构建文档与工程 | 手机扫码进桌面版局域网，全功能可用 |
| C | 机器人网关（钉钉→企微→公众号） | bots 模块+管理页+三平台适配器+测试 | 钉钉群里 @机器人 问「棚室要不要通风」得到正确路由回答 |

每波独立发版：A→v1.4.0，B→v1.5.0，C→v1.6.0；全程 CI 门禁 + CHANGELOG + Release。

### 需要用户提供（不阻塞开工）
- Tauri 更新签名：本地生成密钥即可（我来做，私钥入 GitHub Secrets）
- Apple 开发者账号（iOS TestFlight 分发时）；华为开发者账号（鸿蒙 .hap 分发时）
- 钉钉/企微的机器人或自建应用凭据（Wave C 联调时）；微信公众号需企业资质（可选）

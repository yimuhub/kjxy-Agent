# 客户端矩阵（六端"点开就用"）

> 架构决策（2026-10-17，借鉴 agency-orchestrator 的"四层同一引擎"）：
> **桌面=本地引擎（sidecar，数据不出本机）**；**移动/鸿蒙=云端壳（连试点服务器）**。
> 一套 FastAPI 引擎 + 一套 Vue 前端，壳只做"拉起/指向/生命周期"四件事。

## 现状矩阵

| 端 | 形态 | 现在能产什么 | 差什么 | 怎么产 |
|---|---|---|---|---|
| Windows | Tauri 2 + PyInstaller sidecar | CI 手动触发产**未签名 exe**（内测：右键→仍要运行） | 代码签名证书（EV/OV，杀软信誉） | Actions → Run workflow `Client Build`/desktop |
| macOS | 同上 | CI 产 **ad-hoc 签名** .app（本机可跑；分发需用户 `xattr -dr` 或公证） | Apple 开发者证书 + 公证（$99/年） | 同上；参考 `docs/SIGNING`（待有证书后补） |
| Linux | 同上 | CI 产 ELF 可执行 + deb/rpm（bundle 阶段放开后） | 无（内测零资质） | 同上 |
| Android | Tauri 2 云端壳 | **debug APK 直装**（零资质） | 国内商店上架=软著+实名（上架时才需要） | `client/android/README.md` |
| iOS | Tauri 2 云端壳 | 模拟器演示；Archive 需签名 | **Apple 账号 $99/年**（TestFlight/真机） | `client/iOS/README.md` |
| 鸿蒙 NEXT | ArkWeb 原生壳 | DevEco 调试 HAP（个人实名免费） | 上架 AppGallery 需加原生能力过 4.2 | `client/harmony/README.md` |

## 桌面本地引擎要点（client/desktop）

- 引擎= `backend/standalone.py`（PyInstaller 单文件），SQLite + inline 任务 + 本地文件存储，
  数据全在系统标准数据目录（macOS `~/Library/Application Support/科技小院`，
  Windows `%LOCALAPPDATA%`，Linux `$XDG_DATA_HOME`）；只监听 `127.0.0.1:18700-18710`。
- 首启自动：迁移 → seed 演示空间 → 生成随机管理员口令写 `desktop-login.txt`（splash 指路）。
- JWT 密钥每桌面实例独立生成（`secret.key`），与服务器部署完全隔离。
- 关窗级联杀 sidecar；再开复用健康引擎不重拉；启动失败给原生对话框+原因+下一步（失败即教学）。
- `client.json` 可切 `{"mode":"remote","url":"https://试点"}`——桌面也能连服务器（数据回云端）。

## 桌面伙伴（v3.47 / v3.49 主动陪伴 / v3.50 页面内悬浮伙伴）

- **独立透明窗口**：`pet` 窗口无边框、置顶、不占 Dock/任务栏，默认落屏幕右下角；拖动顶部三点可换位置。
- **交互**：左键呼出/聚焦主窗；右键打开 8 套皮肤面板；悬停出现收起按钮。皮肤与开关存 `localStorage`（`ky_pet_skin` / `ky_pet_enabled`），主窗与桌宠窗口同源同步。
- **8 套皮肤**：小芽、向日、芋泥、糯米、小轨、小火、团子、田伯（农民，v3.50 新增），全部内联 SVG，不依赖外部图片；待机上下浮动 + 眨眼，`prefers-reduced-motion` 时自动静止。
- **全局快捷键**：`Cmd/Ctrl + Shift + Space` 呼出主窗；`Cmd/Ctrl + Shift + P` 显示/收起桌面伙伴。主窗关闭后仍可用快捷键重建。
- **状态与场景（v3.49）**：对话发送、步骤流转、失败、结束与指挥官计划/子任务/预算会映射到发呆/思考/忙碌/开心/担心/打盹六种状态；主窗切换页面同步当前场景，桌宠刷新后仍保留。
- **陪伴文案（v3.49）**：按时段问候，固定节日优先，最近节气在 5 天内才补一句；每个场景一句轻提示；空闲 5 分钟打盹，指针移动或新事件唤醒。
- **事件通道与隐私**：`BroadcastChannel` + `localStorage` + Tauri `pet:copilot` 三通道同步，只传状态与文案，不传消息正文、会话 ID 或任何密钥。
- **设置入口**：桌面版「系统设置 → 桌面伙伴」可启用、选择皮肤、立即显示/收起，并用「任务气泡」开关静音任务提醒（场景提示与问候不受影响）；浏览器预览可打开 `/pet` 查看皮肤。
- **边界**：桌面伙伴透明窗口只在本地模式可用；远程模式（`client.json` 指向服务器）不创建桌宠窗口，避免把服务器页面当成本地伙伴。

## 页面内悬浮指挥官伙伴（v3.50）

透明桌宠窗口只有桌面版本有，网页端与远程模式的桌面端改用「页面内悬浮伙伴」，能力等价于把指挥官装在每个页面右下角。

- **常驻与唤起**：登录后任意页面右下角常驻伙伴头像，点击展开对话面板（内嵌 `ChatPanel`，默认进入指挥官模式），面板可直接下指令、看计划卡；面板头部提供换肤、跳转「指挥官工作台」与收起。窄屏（视口 <900px）只保留头像，避免遮挡正文。
- **跟随屏幕拖动**：按住头像拖动即可放到任意位置，位置写 `ky_pet_pos` 并在刷新后恢复；越界自动收敛回视口，窗口缩放会重新夹紧，「重置位置」回到右下角默认点。
- **开关**：「系统设置 → 桌面伙伴 → 页面内悬浮伙伴」（`ky_pet_overlay`，默认开启），浏览器与桌面端都可用；关闭后伙伴从所有页面消失，独立桌宠窗口不受影响。
- **陪伴同源**：与桌宠共用 `petCopilot` 事件总线（新增同文档直投通道），任务气泡、场景提示、问候与表情状态一致；只传状态与文案，不传消息正文或密钥。
- **老菜单缓存自愈**：自定义菜单布局存 `localStorage` 且本地优先，新版本新增的功能入口（如「指挥官工作台」）过去会被老缓存永久隐藏。v3.50 起按默认布局自动补回一次，并用 `kjxy_menu_surfaced_v1_<用户_小院>` 记录基线——用户之后主动隐藏就不再反复弹回。因此升级后无需清理本地缓存即可看到新入口（桌面 WebView 同样自愈）。

## 多集群节点（v3.48）

- **入口**：平台管理员在「系统设置 → 集群节点」（`/settings?tab=cluster`）登记其他部署实例；小院管理员与普通成员看不到该页。
- **能做什么**：新增/编辑/删除节点，单点探测或全部探测；表格展示状态、版本、迁移 head、延迟与最近检测时间，超过 600 秒未探测标「已过期」。
- **只读与安全**：探测只访问目标实例公开的 `/api/v1/meta`，不保存任何令牌；地址不允许带用户名/口令、查询串或片段，3 秒超时且不跟随重定向。
- **判定口径**：版本与迁移 head 都一致为「在线」；可达但不一致为「版本/迁移不一致」；不可达为「离线」，失败原因只留短文本。

## 语音输入（v3.30，离线优先）

- **录音端**：前端用 WebAudio 采集并**自己编码成 16kHz 单声道 PCM16 WAV**（`frontend/src/utils/wavRecorder.ts`），
  不再依赖 `MediaRecorder` 的 webm/ogg/mp4——后端因此不需要 ffmpeg，三端内嵌 WebView 行为一致。
- **引擎**：`sherpa-onnx`（PyPI wheel 覆盖 macOS x64/arm64、Windows amd64/arm64、Linux x86_64/aarch64）；
  模型是**同一份 ONNX 文件**，跨系统通用，不打进安装包，首次使用按需下载。
- **两个可选模型**（设置页可下载、切换）：
  「中文语音转写（轻量）」Paraformer-zh-small int8 ≈78MB / 内存 1GB，最快、无标点；
  「中文语音转写（高精度）」SenseVoice-Small int8 ≈228MB / 内存 2GB，自带标点与数字规整。
  两者都只在本机 CPU 上跑；极少数专业术语仍可能听错，重要内容请人工核对。
- **模型目录**（都可用 `ASR_MODEL_DIR` 覆盖）：
  macOS `~/Library/Application Support/tech-yard/models/asr`，
  Windows `%LOCALAPPDATA%\tech-yard\models\asr`，
  Linux `$XDG_DATA_HOME/tech-yard/models/asr`；
  桌面壳显式传 `<数据目录>/models/asr`，升级/重装不会重下模型。
- **下载**：`/asr/models/{id}/download` 后台线程，镜像回退 + Range 断点续传 + SHA256 校验 +
  `.part` 原子落盘；`/asr/status` 给进度，前端设置页轮询展示。
- **通道**：`auto`（本机就绪走本机，否则在线兜底）/ `local`（只本机，未就绪直接报错，绝不偷偷上传）/
  `online`（走模型中心的 `asr` 能力绑定）/ `off`。对话页遇到不可用会弹「去设置」直接跳到设置页签。
- **CI 冒烟**：`.github/workflows/ci.yml` 的 `asr-smoke` 在 macOS/Linux/Windows 三端跑
  `backend/scripts/asr_smoke.py`（下载→识别固定中文音频→删除），模型按校验和缓存。

## 服务器地址配置

前端构建期 `VITE_API_BASE`（云端壳）；桌面壳由 sidecar 本机提供，无需服务器。
试点上线后把 `https://REPLACE_PILOT_HOST` 占位统一替换（android/harmony 各一处）。

## 升级策略（AO 模式）

安装包升级=**备份旧版→staging→原子替换→失败回滚**；数据目录与程序目录分离，
升级永不触碰用户数据；数据库版本随 app 打包，启动时 alembic 自动升级（只向前）。

## 未验证项（诚实清单）

- Rust 启动器/CI 构建流**未经本地编译**（开发机无 Rust 工具链），首次以 CI 实跑为准；
- PyInstaller 对 alembic/fastapi 的隐藏依赖收集需 CI 首跑校准（脚本已列坑位注释）；
- 安卓/鸿蒙壳未过真机；iOS 无账号未 Archive。以上都在 CI 首跑与真机迭代中收敛。

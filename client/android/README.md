# 安卓客户端（云端壳）

形态：**Tauri 2 Android 壳 + 云端引擎**（决策：移动端不携带本地 Python 引擎；
科技小院的私有数据/模型在试点服务器上，App 只做"点开→登录→用"）。

## 与桌面共用同一工程

App 逻辑就是 `client/desktop` 的 Tauri 工程开 mobile 目标：

```bash
cd client/desktop
npm install
npx tauri android init        # 首次：生成 src-tauri/gen/android
npm run build                 # 先出前端 dist → 拷入 dist-web（CI 已自动化）
npx tauri android apk         # release APK（需 keystore）；内测可先 --debug
```

## 内测 APK（零资质，最快路径）

1️⃣ 生成调试签名：

```bash
keytool -genkeypair -keystore debug.keystore -alias techyard \
  -keyalg RSA -keysize 2048 -validity 10000 -storepass techyard
```

2️⃣ `npx tauri android apk --debug` 产物在
`src-tauri/gen/android/app/build/outputs/apk/`，直传手机安装（允许"未知来源"）。

3️⃣ 上架国内商店（华为/小米/应用宝）才需要软著+实名——那是商店的事，内测不受限。

## 服务器地址配置

壳首启读 `TECHYARD_API_BASE`（构建期 env `VITE_API_BASE` 打进前端 bundle）：
内测默认 `https://REPLACE_PILOT_HOST`——CI 或本地构建时替换为试点服务器域名。

## 诚实清单（未验证项）

- `tauri android init/build` 需要 Android SDK + NDK，本机未装，**流程未在真机验证**；
  CI（client.yml 的 android job）首次跑通后以 CI 产物为准。
- WebView 版本碎片化（低版本安卓）需实测 SSE/fetch-stream 兼容性，异常时前端已内置
  轮询降级（见 `frontend/src/utils/sse.ts` onFallback）。

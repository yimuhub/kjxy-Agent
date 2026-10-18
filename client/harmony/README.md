# 鸿蒙 NEXT 客户端（云端壳）

形态：ArkTS + ArkWeb 原生壳加载试点服务器（决策同安卓：移动端走云端）。
壳源码：`entry/src/main/ets/pages/Index.ets`。

## 产出内测包（免费个人开发者即可）

1. 装 **DevEco Studio**（华为官网，Win/Mac）→ New Project → Empty Ability（API 12, Stage 模型）。
2. 用本目录的 `Index.ets` 替换工程同名文件；`entry/src/main/module.json5` 的
   `requestPermissions` 加 `ohos.permission.INTERNET`。
3. `REMOTE_URL` 常量改为试点服务器域名。
4. Build → Build Hap(s)/APP(s)：自动调试签名（需登录华为开发者账号，**个人实名免费**）。
5. USB 真机或模拟器安装运行。

## 上架（AppGallery）待办

- 个人/企业开发者实名（企业需营业执照）；
- 纯 WebView 壳有被驳回风险：接入至少一项原生能力（推送 Push Kit / 分享 / 扫码）后再提审；
- 隐私政策 URL 与权限说明（服务器在境内可过备案核对）。

## 诚实清单

- 本机无 DevEco/鸿蒙真机：`Index.ets` 按 API 12 文档编写，**未经编译验证**；
- WebView 对 SSE（fetch-stream）的支持随 ArkWeb 内核版本而定，前端已有轮询降级兜底
  （`frontend/src/utils/sse.ts`），实测若异常会自动落轮询，功能不阻断。

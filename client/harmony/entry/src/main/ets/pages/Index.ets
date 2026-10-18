// 鸿蒙 NEXT 云端壳：ArkWeb 全屏加载试点服务器，登录态留在 H5 内（localStorage）。
// 定位：Web 产品的原生皮——先满足"App 图标点开即用"，后续再逐步桥接原生能力
// （推送/分享/扫码）以满足 AppGallery 审核对"原生体验"的要求。
//
// 工程接入：用 DevEco Studio 新建 Empty Ability 工程，把本文件替换
// entry/src/main/ets/pages/Index.ets，并把 module.json5 加 INTERNET 权限。
// 未经 DevEco 实测（本机无 DevEco/真机），API 以 API 12 Stage 模型为基线。

import { webview } from '@kit.ArkWeb';
import { common } from '@kit.AbilityKit';

// 试点服务器地址：构建前替换（与安卓壳同一配置项来源）
const REMOTE_URL = 'https://REPLACE_PILOT_HOST';

@Entry
@Component
struct Index {
  controller: webview.WebviewController = new webview.WebviewController();

  build() {
    Column() {
      Web({ src: REMOTE_URL, controller: this.controller })
        .domStorageAccess(true)          // 登录 token 存 localStorage，必须开启
        .fileAccess(false)
        .mixedContentMode(MixedContentMode.None) // 只允许 HTTPS 资源
        .javaScriptAccess(true)
        .onlineImageAccess(true)
        .onTitleReceive((event) => {
          // 页面标题同步（可选：让系统任务栏显示中文页名）
          console.info(`page title: ${event?.title}`);
        })
    }
    .width('100%')
    .height('100%')
  }

  // 返回键：优先页面内回退，根页面时退出 App（否则安卓式返回在 H5 里"失灵"）
  onBackPress(): boolean | undefined {
    if (this.controller.accessBackward()) {
      this.controller.backward();
      return true; // 已消费：页面回退
    }
    return undefined; // 交回系统：退出应用
  }

  aboutToDisappear(): void {
    this.controller.stop();
  }
}

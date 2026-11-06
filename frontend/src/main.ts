import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/design-system.css'
import { initTheme } from './utils/theme'

import App from './App.vue'
import { colResize } from './directives/columnResize'
import router from './router'
import { useAuthStore } from './stores/auth'
import { useBrandingStore } from './stores/branding'
import { syncDesktopPetOnStartup } from './utils/desktop'

// 主题三态（v3.69）：跟随系统/亮/暗——样式装载后、挂载前应用，避免闪色
initTheme()

/**
 * 桌面安装包：壳用 URL hash 传入一次性令牌（不进服务器日志），
 * 换到登录态后立刻从地址栏抹掉，用户看到的直接是主界面。
 */
async function bootstrapDesktop(pinia: ReturnType<typeof createPinia>) {
  const match = window.location.hash.match(/(?:^#|&)desktop_token=([^&]+)/)
  if (!match) return
  history.replaceState(null, '', window.location.pathname + window.location.search)
  try {
    await useAuthStore(pinia).bootstrapDesktop(decodeURIComponent(match[1]))
  } catch {
    // 令牌失效/引擎尚未初始化：交给路由守卫回登录页，不阻塞启动
  }
}

async function start() {
  const app = createApp(App)
  app.directive('col-resize', colResize)
  const pinia = createPinia()
  app.use(pinia)
  // 品牌包：不阻塞挂载（失败回落默认值），拿到后立刻注入主色与文案
  void useBrandingStore(pinia).load()
  await bootstrapDesktop(pinia)
  app.use(router)
  app.mount('#app')
  // 桌面版：用户上次开启过桌面伙伴时，随主窗一起恢复
  void syncDesktopPetOnStartup()
  // Vue Router 初始化时会按首次读取的地址恢复 hash，这里再抹一次
  void router.isReady().then(() => {
    if (window.location.hash.includes('desktop_token')) {
      history.replaceState(null, '', window.location.pathname + window.location.search)
    }
  })

  // PWA 外壳缓存（仅生产构建）：离线可打开界面，数据仍需联网；/api/ 永不缓存
  if (import.meta.env.PROD && 'serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => {
        /* 注册失败不影响在线使用 */
      })
    })
  }
}

void start()

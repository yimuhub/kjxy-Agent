/**
 * 主题三态（v3.69）：跟随系统 / 亮色 / 暗色。
 *
 * - 用户偏好存 localStorage（kjxy-theme-mode），默认 system；
 * - 应用方式：html 根元素切 dark/light class（design-system.css 两套
 *   --ds-* token 与 Element Plus 桥接随类切换）；
 * - system 态监听 matchMedia 变化实时响应；
 * - main.ts 挂载前先 apply（避免闪暗）。
 */

export type ThemeMode = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'kjxy-theme-mode'
const mql = window.matchMedia('(prefers-color-scheme: dark)')

let mode: ThemeMode = readStored()

function readStored(): ThemeMode {
  const raw = localStorage.getItem(STORAGE_KEY)
  return raw === 'light' || raw === 'dark' ? raw : 'system'
}

export function themeMode(): ThemeMode {
  return mode
}

export function resolvedTheme(): 'light' | 'dark' {
  return mode === 'system' ? (mql.matches ? 'dark' : 'light') : mode
}

function apply(): void {
  const resolved = resolvedTheme()
  const root = document.documentElement
  root.classList.toggle('dark', resolved === 'dark')
  root.classList.toggle('light', resolved === 'light')
  // Element Plus 官方暗色变量随 dark class 生效；亮色由 design-system html.light 提供
  root.style.colorScheme = resolved
}

/** 初始化（main.ts 挂载前调用）；返回当前 resolved 供首屏背景预置 */
export function initTheme(): 'light' | 'dark' {
  mql.addEventListener('change', () => {
    if (mode === 'system') apply()
  })
  apply()
  return resolvedTheme()
}

export function setThemeMode(next: ThemeMode): void {
  mode = next
  localStorage.setItem(STORAGE_KEY, next)
  apply()
  // 品牌主色的 EP 派生量（--el-color-primary-light-N 等）随类切换已由 CSS 处理
  window.dispatchEvent(new CustomEvent('kjxy-theme-changed', { detail: resolvedTheme() }))
}

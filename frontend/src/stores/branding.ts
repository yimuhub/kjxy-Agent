/** 品牌与门户 store（v3.67）：启动时读一次，注入 CSS 变量与文案。
 *
 * 设计口径：
 * - **不阻塞首屏**：先用默认值渲染，拿到品牌包后再补丁（避免登录页白屏等接口）；
 * - 只改「看得见」的东西：名称、标语、主色、logo、页脚；安装包名/图标是构建期产物，
 *   运行期改不了，设置页会明确标注；
 * - 换 logo / 换色靠 `brand_version` 加 `?v=`，避免浏览器缓存旧图；
 * - 未上传白标 logo 时统一显示内置徽标（随前端静态资源发布），不再回落文字方块；
 *   设置页用 `hasCustomLogo` 区分「管理员上传的图」与「内置徽标」。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiUrl } from '@/api/client'
import { brandingApi, type BrandingPublic } from '@/api/branding'
import { resolvedTheme } from '@/utils/theme'

export const BRANDING_DEFAULTS = {
  app_name: '科技小院',
  short_name: '科技小院',
  login_tagline: '多智能体自生长开发平台',
  footer_text: '由 科技小院 · 多智能体平台 生成',
  primary_color: '#6366F1',
} as const

/** 内置徽标：与 favicon / PWA / 桌面启动屏共用同一张图；用 BASE_URL 兼容子路径部署 */
const DEFAULT_LOGO_URL = `${import.meta.env.BASE_URL}brand-logo.png`

function clamp(value: number): number {
  return Math.max(0, Math.min(255, Math.round(value)))
}

/** 与白色/黑色混合，用于推导 Element Plus 的浅色/深色档位 */
function mix(hex: string, target: string, ratio: number): string {
  const parse = (raw: string): [number, number, number] => {
    const text = raw.replace('#', '')
    return [
      Number.parseInt(text.slice(0, 2), 16),
      Number.parseInt(text.slice(2, 4), 16),
      Number.parseInt(text.slice(4, 6), 16),
    ]
  }
  const [r1, g1, b1] = parse(hex)
  const [r2, g2, b2] = parse(target)
  const blend = (a: number, b: number) => clamp(a + (b - a) * ratio)
  const toHex = (n: number) => n.toString(16).padStart(2, '0')
  return `#${toHex(blend(r1, r2))}${toHex(blend(g1, g2))}${toHex(blend(b1, b2))}`
}

function applyTheme(color: string): void {
  if (typeof document === 'undefined') return
  appliedColor = color
  const root = document.documentElement
  const light = resolvedTheme() === 'light'
  // 亮底上原样注入品牌色会打穿亮色主题的校准值（#6366F1 白底仅 4.47:1）。
  // 这里按对比度把「实底/文字/渐变」三处各自压到 AA 以上，品牌色相不变。
  const solid = ensureContrast(color, 4.8)
  root.style.setProperty('--ds-accent', solid)
  if (light) {
    root.style.setProperty('--ds-accent-text', ensureContrast(color, 5.4))
    root.style.setProperty('--ds-accent-ring', `color-mix(in srgb, ${solid} 22%, transparent)`)
    root.style.setProperty('--ds-btn-primary-shadow', `0 4px 14px color-mix(in srgb, ${solid} 28%, transparent)`)
  } else {
    root.style.removeProperty('--ds-accent-text')
    root.style.removeProperty('--ds-accent-ring')
    root.style.removeProperty('--ds-btn-primary-shadow')
  }
  root.style.setProperty('--ds-grad-brand', `linear-gradient(135deg, ${solid} 0%, ${mix(solid, '#000000', 0.2)} 100%)`)
  root.style.setProperty('--ds-grad-brand-hover', `linear-gradient(135deg, ${mix(solid, '#000000', 0.06)} 0%, ${mix(solid, '#000000', 0.3)} 100%)`)
  // Element Plus 主色阶：按钮/链接/选中态跟着品牌走
  root.style.setProperty('--el-color-primary', solid)
  if (light) {
    // 亮色阶只能向白混；暗色若也向白混，选中态会变成浅紫底 + 白字（不可读），
    // 因此暗色直接沿用 design-system 里按暗底校准的 rgba 阶。
    for (const level of [3, 5, 7, 8, 9]) {
      root.style.setProperty(`--el-color-primary-light-${level}`, mix(solid, '#ffffff', level / 10))
    }
    root.style.setProperty('--el-color-primary-dark-2', mix(solid, '#000000', 0.2))
  } else {
    for (const level of [3, 5, 7, 8, 9]) {
      root.style.removeProperty(`--el-color-primary-light-${level}`)
    }
    root.style.removeProperty('--el-color-primary-dark-2')
  }
}

function relativeLuminance(hex: string): number {
  const text = hex.replace('#', '')
  const channel = (raw: string) => {
    const value = Number.parseInt(raw, 16) / 255
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(text.slice(0, 2)) + 0.7152 * channel(text.slice(2, 4)) + 0.0722 * channel(text.slice(4, 6))
}

/** 白底对比度不足时逐步加深（保持色相），供亮色主题的实底与文字使用 */
function ensureContrast(hex: string, minimum: number): string {
  let output = hex
  for (let step = 0; step < 24; step += 1) {
    const ratio = (1.05) / (relativeLuminance(output) + 0.05)
    if (ratio >= minimum) return output
    output = mix(output, '#000000', 0.06)
  }
  return output
}

let appliedColor: string = BRANDING_DEFAULTS.primary_color

if (typeof window !== 'undefined') {
  window.addEventListener('kjxy-theme-changed', () => applyTheme(appliedColor))
}

export const useBrandingStore = defineStore('branding', () => {
  const appName = ref<string>(BRANDING_DEFAULTS.app_name)
  const shortName = ref<string>(BRANDING_DEFAULTS.short_name)
  const loginTagline = ref<string>(BRANDING_DEFAULTS.login_tagline)
  const footerText = ref<string>(BRANDING_DEFAULTS.footer_text)
  const primaryColor = ref<string>(BRANDING_DEFAULTS.primary_color)
  const logoPath = ref<string | null>(null)
  const brandVersion = ref(0)
  const loaded = ref(false)

  /** logo 完整地址（自定义图带版本戳）；未上传白标图时回落到内置徽标 */
  const logoUrl = computed(() =>
    logoPath.value
      ? `${apiUrl(logoPath.value)}${brandVersion.value ? `?v=${brandVersion.value}` : ''}`
      : DEFAULT_LOGO_URL,
  )

  /** 是否由管理员上传了自定义 logo：设置页据此决定「上传/更换」与「移除」 */
  const hasCustomLogo = computed(() => logoPath.value !== null)

  function apply(payload: BrandingPublic): void {
    appName.value = payload.app_name || BRANDING_DEFAULTS.app_name
    shortName.value = payload.short_name || BRANDING_DEFAULTS.short_name
    loginTagline.value = payload.login_tagline || BRANDING_DEFAULTS.login_tagline
    footerText.value = payload.footer_text || BRANDING_DEFAULTS.footer_text
    primaryColor.value = payload.primary_color || BRANDING_DEFAULTS.primary_color
    logoPath.value = payload.logo_url ?? null
    brandVersion.value = payload.brand_version ?? 0
    applyTheme(primaryColor.value)
    loaded.value = true
  }

  let pending: Promise<void> | null = null

  /** 解析品牌包；失败静默回落默认值（品牌挂了不能挡住登录） */
  async function load(force = false): Promise<void> {
    if (loaded.value && !force) return
    if (pending) return pending
    pending = brandingApi
      .public()
      .then(apply)
      .catch(() => {
        /* 默认值即可，不打扰用户 */
      })
      .finally(() => {
        pending = null
      })
    return pending
  }

  /** 设置页保存/换图后立即生效（省一次刷新） */
  function applyAdmin(payload: BrandingPublic): void {
    apply(payload)
  }

  function reset(): void {
    apply({ ...BRANDING_DEFAULTS, logo_url: null, brand_version: 0 })
    loaded.value = false
  }

  return {
    appName,
    shortName,
    loginTagline,
    footerText,
    primaryColor,
    logoPath,
    brandVersion,
    loaded,
    logoUrl,
    hasCustomLogo,
    load,
    apply,
    applyAdmin,
    reset,
  }
})

/** 路由标题用：`页面 · 品牌名`；未初始化时回落默认品牌 */
export function appTitle(title?: string): string {
  let name: string = BRANDING_DEFAULTS.app_name
  try {
    name = useBrandingStore().appName
  } catch {
    /* 路由守卫早于 pinia 安装时走默认值 */
  }
  return title ? `${title} · ${name}` : name
}

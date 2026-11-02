/** 桌面壳能力探测与桌宠命令封装；浏览器环境全部安全降级为 no-op。 */

import {
  petEnabled,
  readStoredPetSkin,
  type PetSkinId,
} from './petSkins'

type InvokeFn = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>
type UnlistenFn = () => void

interface TauriEventApi {
  listen(event: string, handler: (event: { payload: unknown }) => void): Promise<UnlistenFn>
}

function tauriInternals(): { invoke?: InvokeFn } | null {
  if (typeof window === 'undefined') return null
  return (
    (window as unknown as { __TAURI_INTERNALS__?: { invoke?: InvokeFn } }).__TAURI_INTERNALS__ ??
    null
  )
}

function tauriEvents(): TauriEventApi | null {
  if (typeof window === 'undefined') return null
  const api = (window as unknown as { __TAURI__?: { event?: TauriEventApi } }).__TAURI__
  return api?.event ?? null
}

export function isDesktopShell(): boolean {
  return Boolean(tauriInternals()?.invoke)
}

export async function invokeDesktop<T>(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<T | null> {
  const invoke = tauriInternals()?.invoke
  if (!invoke) return null
  return (await invoke(cmd, args)) as T
}

export async function setDesktopPetVisible(
  visible: boolean,
  skin: PetSkinId = readStoredPetSkin(),
): Promise<boolean | null> {
  return invokeDesktop<boolean>('set_pet_visible', { visible, skin })
}

export async function setDesktopPetSkin(skin: PetSkinId): Promise<PetSkinId | null> {
  return invokeDesktop<PetSkinId>('set_pet_skin', { skin })
}

export async function showDesktopMainWindow(): Promise<boolean | null> {
  const result = await invokeDesktop<void>('show_main_window')
  return result === null ? null : true
}

export async function hideDesktopPet(): Promise<boolean | null> {
  const result = await invokeDesktop<void>('hide_pet_window')
  return result === null ? null : true
}

/** 主窗首次加载时按本地开关恢复桌宠；失败不阻塞界面 */
export async function syncDesktopPetOnStartup(): Promise<void> {
  if (!isDesktopShell() || !petEnabled()) return
  try {
    await setDesktopPetVisible(true, readStoredPetSkin())
  } catch {
    /* 引擎尚未就绪时允许失败，用户可在设置页重新显示 */
  }
}

/** 订阅桌宠换肤事件；浏览器/无事件 API 时返回 null */
export async function listenPetSkinChange(
  handler: (skin: PetSkinId) => void,
): Promise<UnlistenFn | null> {
  const events = tauriEvents()
  if (!events) return null
  try {
    return await events.listen('pet:skin-changed', event => {
      handler(String(event.payload ?? '') as PetSkinId)
    })
  } catch {
    return null
  }
}

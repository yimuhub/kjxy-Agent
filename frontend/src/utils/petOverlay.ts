/**
 * 页面内悬浮伙伴（v3.50）：开关与可拖拽位置的存取。
 * 位置以左上角坐标存储并始终夹在视口内，窗口缩放后会自动收回边界。
 */

export interface PetPos {
  x: number
  y: number
}

export const PET_OVERLAY_KEY = 'ky_pet_overlay'
export const PET_POS_KEY = 'ky_pet_pos'
/** 同一页面内的开关广播（storage 事件不会回送给自己，设置页切换需要即时生效） */
export const PET_OVERLAY_EVENT = 'ky:pet-overlay-change'
/** 悬浮体占位尺寸（含气泡留白），用于边界夹取 */
export const PET_OVERLAY_SIZE = { width: 76, height: 76 }
export const PET_OVERLAY_MARGIN = 12

export function petOverlayEnabled(): boolean {
  try {
    return localStorage.getItem(PET_OVERLAY_KEY) !== '0'
  } catch {
    return true
  }
}

export function setPetOverlayEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(PET_OVERLAY_KEY, enabled ? '1' : '0')
  } catch {
    /* 存储不可用时本次会话仍然生效 */
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(PET_OVERLAY_EVENT, { detail: { enabled } }))
  }
}

/** 把左上角坐标夹进视口，保证悬浮体任何时刻都完整可见 */
export function clampPetPos(
  x: number,
  y: number,
  viewportWidth: number,
  viewportHeight: number,
  size: { width: number; height: number } = PET_OVERLAY_SIZE,
): PetPos {
  const maxX = Math.max(PET_OVERLAY_MARGIN, viewportWidth - size.width - PET_OVERLAY_MARGIN)
  const maxY = Math.max(PET_OVERLAY_MARGIN, viewportHeight - size.height - PET_OVERLAY_MARGIN)
  const safeX = Number.isFinite(x) ? x : maxX
  const safeY = Number.isFinite(y) ? y : maxY
  return {
    x: Math.min(Math.max(safeX, PET_OVERLAY_MARGIN), maxX),
    y: Math.min(Math.max(safeY, PET_OVERLAY_MARGIN), maxY),
  }
}

/** 默认落位：屏幕右下角 */
export function defaultPetPos(
  viewportWidth: number,
  viewportHeight: number,
  size: { width: number; height: number } = PET_OVERLAY_SIZE,
): PetPos {
  return clampPetPos(
    viewportWidth - size.width - 24,
    viewportHeight - size.height - 24,
    viewportWidth,
    viewportHeight,
    size,
  )
}

export function readPetPos(): PetPos | null {
  try {
    const raw = localStorage.getItem(PET_POS_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { x?: unknown; y?: unknown }
    if (typeof parsed?.x !== 'number' || typeof parsed?.y !== 'number') return null
    return { x: parsed.x, y: parsed.y }
  } catch {
    return null
  }
}

export function writePetPos(pos: PetPos): void {
  try {
    localStorage.setItem(PET_POS_KEY, JSON.stringify(pos))
  } catch {
    /* 位置写不进去只影响下次打开的默认落点 */
  }
}

export function resetPetPos(): void {
  try {
    localStorage.removeItem(PET_POS_KEY)
  } catch {
    /* 忽略 */
  }
}

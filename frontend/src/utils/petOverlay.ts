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

/**
 * 面板位置与尺寸（v3.51）：伙伴面板既能整体拖走，也能拖边/拖角改大小，
 * 结果与头像位置一样持久化，并按视口夹取，避免出现比屏幕还大的窗口。
 */
export const PET_PANEL_RECT_KEY = 'ky_pet_panel_rect'
export const PET_PANEL_MIN = { width: 320, height: 360 }
export const PET_PANEL_MAX = { width: 720, height: 900 }

export interface PetRect {
  x: number
  y: number
  width: number
  height: number
}

function fit(value: number, min: number, ceiling: number): number {
  const safe = Number.isFinite(value) ? value : min
  return Math.min(Math.max(safe, min), Math.max(min, ceiling))
}

/** 夹进视口：尺寸受最小/最大与视口约束，坐标保证整块可见 */
export function clampPetRect(rect: PetRect, viewportWidth: number, viewportHeight: number): PetRect {
  const width = fit(rect.width, PET_PANEL_MIN.width, Math.min(PET_PANEL_MAX.width, viewportWidth - PET_OVERLAY_MARGIN * 2))
  const height = fit(rect.height, PET_PANEL_MIN.height, Math.min(PET_PANEL_MAX.height, viewportHeight - PET_OVERLAY_MARGIN * 2))
  return {
    width,
    height,
    x: fit(rect.x, PET_OVERLAY_MARGIN, Math.max(PET_OVERLAY_MARGIN, viewportWidth - width - PET_OVERLAY_MARGIN)),
    y: fit(rect.y, PET_OVERLAY_MARGIN, Math.max(PET_OVERLAY_MARGIN, viewportHeight - height - PET_OVERLAY_MARGIN)),
  }
}

/** 默认面板：贴在头像左上方，放不下时向视口内收 */
export function defaultPetPanelRect(
  anchor: PetPos,
  viewportWidth: number,
  viewportHeight: number,
): PetRect {
  return clampPetRect(
    {
      x: anchor.x + PET_OVERLAY_SIZE.width - 380,
      y: anchor.y - 540 - 10,
      width: 380,
      height: 540,
    },
    viewportWidth,
    viewportHeight,
  )
}

/**
 * 页面输入条的右下角让位区（v3.52）：伙伴放大到 86px 后，默认落点会把对话页
 * 30px 的发送圆钮整颗盖住——小人停在右下角时，输入条要把右侧让出来。
 */
export const COMPOSER_DOCK_ZONE = { width: 230, height: 150 }

/** 伙伴矩形是否压在视口右下角的让位区内 */
export function overlapsComposerDock(
  rect: Pick<PetRect, 'x' | 'y' | 'width' | 'height'>,
  viewportWidth: number,
  viewportHeight: number,
): boolean {
  return (
    rect.x + rect.width > viewportWidth - COMPOSER_DOCK_ZONE.width &&
    rect.y + rect.height > viewportHeight - COMPOSER_DOCK_ZONE.height
  )
}

export function readPetPanelRect(): PetRect | null {
  try {
    const raw = localStorage.getItem(PET_PANEL_RECT_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PetRect>
    if (
      typeof parsed?.x !== 'number' ||
      typeof parsed?.y !== 'number' ||
      typeof parsed?.width !== 'number' ||
      typeof parsed?.height !== 'number'
    ) {
      return null
    }
    return { x: parsed.x, y: parsed.y, width: parsed.width, height: parsed.height }
  } catch {
    return null
  }
}

export function writePetPanelRect(rect: PetRect): void {
  try {
    localStorage.setItem(PET_PANEL_RECT_KEY, JSON.stringify(rect))
  } catch {
    /* 写不进去只影响下次打开时的默认位置 */
  }
}

export function resetPetPanelRect(): void {
  try {
    localStorage.removeItem(PET_PANEL_RECT_KEY)
  } catch {
    /* 忽略 */
  }
}

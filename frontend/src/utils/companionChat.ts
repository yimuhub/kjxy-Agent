/**
 * 悬浮指挥官伙伴的专属会话（v3.51）。
 *
 * 伙伴的定位是「随时能指挥、历史一直在」：每个用户固定一条指挥官会话，
 * 刷新、换页、重开桌面都续接同一条历史往下写，而不是每次新开空会话。
 * 会话正文始终落在服务端 chat_messages，本地只记会话 id。
 */

export const COMPANION_SESSION_KEY = 'ky_companion_session'
export const COMPANION_SESSION_TITLE = '指挥官伙伴'

export function readCompanionSessionId(): string {
  try {
    return localStorage.getItem(COMPANION_SESSION_KEY) ?? ''
  } catch {
    return ''
  }
}

export function writeCompanionSessionId(id: string): void {
  try {
    localStorage.setItem(COMPANION_SESSION_KEY, id)
  } catch {
    /* 存储不可用时本次会话照常，只是下次打开会重新锚定 */
  }
}

export function clearCompanionSessionId(): void {
  try {
    localStorage.removeItem(COMPANION_SESSION_KEY)
  } catch {
    /* 忽略 */
  }
}

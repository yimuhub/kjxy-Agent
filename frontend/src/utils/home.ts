/** 登录后的默认落地页（v3.13→v3.66）：按角色与枢纽落工作台——
 * 枢纽管理者/成员落枢纽工作台、管理看行动板、专家看审核、研究生/技术员看工作日志、
 * 其余进对话。 */

import type { HubBrief } from '@/types/api'

export const HOME_BOARD_ROLES = ['yard_owner', 'yard_admin', 'platform_admin', 'expert']

export function defaultHomePath(roles: string[] = [], hubs: HubBrief[] = []): string {
  // 驻点一线（operator）的日常是写日志——全国平台日志考核的工作台优先
  if (roles.includes('operator') && !roles.includes('yard_admin')) return '/journal'
  // 枢纽账号（含只有 hub_members、不属任何小院的纯枢纽账号）直落枢纽工作台
  if (hubs.length) return '/hubs'
  return roles.some(role => HOME_BOARD_ROLES.includes(role)) ? '/' : '/chat'
}

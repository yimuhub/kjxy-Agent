/** 登录后的默认落地页（v3.13→v3.58）：按角色落工作台——
 * 管理看行动板、专家看审核、研究生/技术员看工作日志、其余进对话。 */

export const HOME_BOARD_ROLES = ['yard_owner', 'yard_admin', 'platform_admin', 'expert']

export function defaultHomePath(roles: string[] = []): string {
  // 驻点一线（operator）的日常是写日志——全国平台日志考核的工作台优先
  if (roles.includes('operator') && !roles.includes('yard_admin')) return '/journal'
  return roles.some(role => HOME_BOARD_ROLES.includes(role)) ? '/' : '/chat'
}

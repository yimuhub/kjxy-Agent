/** 登录后的默认落地页（v3.13）：管理/专家先看行动板，一线成员直接进对话。 */

export const HOME_BOARD_ROLES = ['yard_owner', 'yard_admin', 'platform_admin', 'expert']

export function defaultHomePath(roles: string[] = []): string {
  return roles.some(role => HOME_BOARD_ROLES.includes(role)) ? '/' : '/chat'
}

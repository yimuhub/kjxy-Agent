/**
 * v2.2 / v3.0 API 层（搜索、市场、身份权限、负责人驾驶舱…）。
 * v3.82 起全部端点已并入 ./domains/* 统一域层，本文件保留为**兼容桶**：
 * 历史 44 处 `@/api/v3` 导入继续可用；新代码请统一从 `@/api/modules` 导入。
 */
export * from './domains/common'
export * from './domains/core'
export * from './domains/admin'
export * from './domains/tasks'
export * from './domains/knowledge'
export * from './domains/llm'
export * from './domains/teams'
export * from './domains/agents'
export * from './domains/skills'
export * from './domains/governance'
export * from './domains/devices'
export * from './domains/farm'
export * from './domains/bots'
export * from './domains/billing'

/**
 * 统一 API 层（v3.82 拆分）：全部端点按业务域迁至 ./domains/*，本文件只做桶式再导出。
 * 业务侧 67 处 `@/api/modules` 导入保持不变；新增端点请写到对应域文件，勿再往本文件堆。
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

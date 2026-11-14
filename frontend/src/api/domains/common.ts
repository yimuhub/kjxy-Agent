/**
 * 共享类型与请求工具（实现在 ./client，这里做域层聚合导出）（v3.82 由 modules.ts / v3.ts 拆分而来）。
 */

// 共享分页/信封类型与请求工具：实现集中在 ./client，
// 经此文件再导出，保证 `@/api/modules` 桶的对外面（v3.82 拆分前 modules.ts 顶部的私有 helpers）。
export { silent, toParams, unwrap, unwrapList } from '../client'
export type { ListResult } from '../client'

/** 前后端版本比对（v3.21）：只用于提示，不参与权限判断。 */

export const APP_VERSION: string =
  typeof __APP_VERSION__ === 'string' && __APP_VERSION__ ? __APP_VERSION__ : '0.0.0'

const SEMVER = /^\d+\.\d+\.\d+/

/** 语义化版本比较：a<b 返回 -1，相等 0，a>b 返回 1；无法识别返回 null。 */
export function compareVersions(a: string, b: string): number | null {
  if (!SEMVER.test(a) || !SEMVER.test(b)) return null
  const pa = a.split('-')[0].split('.').map(Number)
  const pb = b.split('-')[0].split('.').map(Number)
  for (let i = 0; i < 3; i += 1) {
    const na = pa[i] ?? 0
    const nb = pb[i] ?? 0
    if (na !== nb) return na < nb ? -1 : 1
  }
  return 0
}

export type VersionRelation = 'equal' | 'backend_behind' | 'backend_ahead' | 'unknown'

/** 后端版本相对前端的落后/领先关系。 */
export function versionRelation(backend: string, frontend: string): VersionRelation {
  const diff = compareVersions(backend, frontend)
  if (diff === null) return 'unknown'
  if (diff === 0) return 'equal'
  return diff < 0 ? 'backend_behind' : 'backend_ahead'
}

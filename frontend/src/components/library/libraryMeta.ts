import type { MarketItem, SearchStatus } from '@/api/v3'

/**
 * 插件与技能库的本地视图模型。
 *
 * 后端 market 接口在 v3 契约之外还会返回 verified / installed / repo 等字段，
 * 详情接口使用 definition_json / manifest_json（v3.ts 里写作 definition / manifest）。
 * 这里统一做兼容，页面只依赖本文件的归一化结果，避免各处散落 as any。
 */
export type LibraryItem = MarketItem & {
  verified?: boolean
  installed?: boolean
  repo?: string
  stars?: number
  tags?: string[]
  definition?: Record<string, unknown> | null
  definition_json?: Record<string, unknown> | null
  manifest?: Record<string, unknown> | null
  manifest_json?: Record<string, unknown> | null
}

export interface MarketSearchResponse {
  items?: LibraryItem[]
  total?: number
  warning?: string | null
  offline?: boolean
  network?: SearchStatus | null
}

export interface GithubSearchResponse {
  items?: LibraryItem[]
  offline?: boolean
  warning?: string | null
}

/** 安装接口实际返回 installed_as / skill_status / review_status 等字段。 */
export interface InstallResultLike {
  item_key?: string
  code?: string
  installed_as?: string
  status?: string
  skill_status?: string
  plugin_status?: string
  review_status?: string
  needs_review?: boolean
}

/** 导入接口实际返回 skill_status / plugin_status / verified 等字段。 */
export interface ImportResultLike extends InstallResultLike {
  verified?: boolean
}

export interface InstalledItem {
  key: string
  id: string
  code: string
  name: string
  version: string
  status: string
  source: string
  type: 'skill' | 'plugin'
  item_key?: string
  created_at?: string
}

const SOURCE_LABELS: Record<string, string> = {
  builtin: '内置',
  github: 'GitHub',
  local: '本地',
  remote: '远程',
  market: '市场',
}

const TYPE_LABELS: Record<string, string> = {
  skill: '技能',
  plugin: '插件',
  agent: '智能体',
}

const RISK_LABELS: Record<string, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  critical: '极高风险',
}

const STATUS_LABELS: Record<string, string> = {
  available: '可用',
  installed: '已安装',
  draft: '草稿',
  pending_review: '待审核',
  published: '已发布',
  rejected: '已驳回',
  deprecated: '已停用',
  disabled: '已禁用',
}

type TagType = 'success' | 'warning' | 'danger' | 'info'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function sourceLabel(source?: string | null): string {
  if (!source) return '未知来源'
  return SOURCE_LABELS[source] ?? source
}

export function sourceTagType(source?: string | null): TagType {
  if (source === 'builtin') return 'success'
  if (source === 'github') return 'info'
  if (source === 'local') return 'warning'
  return 'info'
}

export function typeLabel(type?: string | null): string {
  if (!type) return '未知类型'
  return TYPE_LABELS[type] ?? type
}

export function riskLabel(risk?: string | null): string {
  if (!risk) return '未评估'
  return RISK_LABELS[risk] ?? risk
}

export function riskTagType(risk?: string | null): TagType {
  if (risk === 'low') return 'success'
  if (risk === 'medium') return 'warning'
  if (risk === 'high' || risk === 'critical') return 'danger'
  return 'info'
}

export function statusLabel(status?: string | null): string {
  if (!status) return '—'
  return STATUS_LABELS[status] ?? status
}

export function statusTagType(status?: string | null): TagType {
  if (status === 'published' || status === 'installed') return 'success'
  if (status === 'pending_review') return 'warning'
  if (status === 'rejected' || status === 'disabled') return 'danger'
  return 'info'
}

export function isPlugin(item?: LibraryItem | null): boolean {
  return item?.item_type === 'plugin'
}

/** 后端统一返回 installed 布尔值（按小院已安装索引计算）。 */
export function itemInstalled(item?: LibraryItem | null): boolean {
  return item?.installed === true
}

export function normalizeSearchItems(res?: MarketSearchResponse | null): LibraryItem[] {
  return Array.isArray(res?.items) ? res.items : []
}

export interface DefinitionCarrier {
  definition?: Record<string, unknown> | null
  definition_json?: Record<string, unknown> | null
  manifest?: Record<string, unknown> | null
  manifest_json?: Record<string, unknown> | null
}

export function definitionOf(item?: DefinitionCarrier | null): Record<string, unknown> {
  const definition = item?.definition ?? item?.definition_json
  return isRecord(definition) ? definition : {}
}

export function manifestOf(item?: DefinitionCarrier | null): Record<string, unknown> {
  const manifest = item?.manifest ?? item?.manifest_json
  return isRecord(manifest) ? manifest : {}
}

export function promptText(definition: Record<string, unknown>): string {
  const candidates = [
    definition.prompt,
    definition.content,
    definition.instructions,
    definition.description,
  ]
  for (const candidate of candidates) {
    const value = text(candidate)
    if (value) return value
  }
  return ''
}

/** 定义摘要：优先提示词，缺失时退化为 JSON 片段（最多 800 字）。 */
export function definitionExcerpt(definition: Record<string, unknown>, limit = 800): string {
  const prompt = promptText(definition)
  if (prompt) return prompt.slice(0, limit)
  if (!Object.keys(definition).length) return ''
  try {
    return JSON.stringify(definition, null, 2).slice(0, limit)
  } catch {
    return ''
  }
}

/** manifest 摘要：格式化 JSON，最多 1200 字。 */
export function manifestExcerpt(
  item?: DefinitionCarrier | null,
  limit = 1200,
): string {
  const manifest = manifestOf(item)
  if (!Object.keys(manifest).length) return ''
  try {
    return JSON.stringify(manifest, null, 2).slice(0, limit)
  } catch {
    return ''
  }
}

export function installNeedsReview(result?: InstallResultLike | null): boolean {
  if (typeof result?.needs_review === 'boolean') return result.needs_review
  const status =
    result?.review_status ?? result?.skill_status ?? result?.plugin_status ?? result?.status
  if (status === 'published') return false
  return status === 'pending_review' || status === 'draft'
}

export function installResultMessage(result?: InstallResultLike | null): string {
  return installNeedsReview(result) ? '已安装，等待专家审核' : '已安装并启用'
}

export function importSkillMessage(result?: ImportResultLike | null): string {
  const status = result?.skill_status ?? result?.status
  return status === 'published' || result?.verified
    ? '已导入并发布'
    : '已导入，等待专家审核'
}

/**
 * 兼容 /market/installed 的两种返回：
 * - v3 契约：{ items: MarketItem[] }
 * - 当前后端：{ skills: [...], plugins: [...], total }
 */
export function normalizeInstalled(raw: unknown): InstalledItem[] {
  const data = isRecord(raw) ? raw : {}
  if (Array.isArray(data.items)) {
    return data.items.filter(isRecord).map(row => {
      const type = row.item_type === 'plugin' ? 'plugin' : 'skill'
      const code = text(row.code)
      return {
        key: `${type}:${text(row.item_key) || code}`,
        id: text(row.item_key) || code,
        code,
        name: text(row.name) || code,
        version: text(row.version) || '—',
        status: text(row.status),
        source: text(row.source),
        type,
        item_key: text(row.item_key),
      }
    })
  }

  const output: InstalledItem[] = []
  const skills = Array.isArray(data.skills) ? data.skills : []
  for (const row of skills) {
    if (!isRecord(row)) continue
    const code = text(row.code) || text(row.skill_code)
    output.push({
      key: `skill:${text(row.id) || code}`,
      id: text(row.id),
      code,
      name: text(row.name) || code,
      version: text(row.version) || '—',
      status: text(row.status),
      source: text(row.source) || 'market',
      type: 'skill',
      item_key: text(row.item_key) || undefined,
      created_at: text(row.created_at) || undefined,
    })
  }
  const plugins = Array.isArray(data.plugins) ? data.plugins : []
  for (const row of plugins) {
    if (!isRecord(row)) continue
    const code = text(row.code) || text(row.plugin_id)
    output.push({
      key: `plugin:${text(row.id) || code}`,
      id: text(row.id),
      code,
      name: text(row.name) || code,
      version: text(row.version) || '—',
      status: text(row.status),
      source: text(row.source) || 'local',
      type: 'plugin',
      item_key: text(row.item_key) || undefined,
      created_at: text(row.created_at) || undefined,
    })
  }
  return output
}

/** GitHub 仓库输入归一化：支持 URL、.git 后缀与首尾空白。 */
export function normalizeRepoInput(value: string): string {
  return value
    .trim()
    .replace(/^https?:\/\/(www\.)?github\.com\//i, '')
    .replace(/\.git$/i, '')
    .replace(/\/+$/, '')
}

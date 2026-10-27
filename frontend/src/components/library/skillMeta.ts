import type { Skill, SkillVisibility } from '@/types/api'
import { riskLabel, riskTagType } from './libraryMeta'

/**
 * 技能归属视图（我的 / 小院 / 平台）的本地视图模型。
 *
 * 后端 SkillOut 把 name/summary/risk 等业务字段放在 definition 内，顶层只有
 * skill_code/status/visibility/owner_user_id 等；这里统一做兼容，页面不写 as any。
 */

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  testing: '测试中',
  pending_review: '审核中',
  published: '已发布',
  rejected: '已驳回',
  deprecated: '已下线',
  disabled: '已停用',
}

const VISIBILITY_META: Record<SkillVisibility, { label: string; type: TagType; tip: string }> = {
  private: { label: '私有', type: 'info', tip: '仅创建者与小院负责人可见' },
  yard: { label: '小院', type: 'success', tip: '本小院成员可用' },
  platform: { label: '平台', type: 'primary', tip: '平台共享，所有小院可用' },
}

const ORIGIN_LABELS: Record<string, string> = {
  seed: '内置',
  builtin: '内置',
  manual: '手工创建',
  ai: 'AI 生成',
  generated: 'AI 生成',
  market: '市场安装',
  market_import: '本地导入',
  local: '本地导入',
  import: '本地导入',
  github: 'GitHub',
  gitee: 'Gitee',
  upload: '上传',
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function str(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

/** 技能定义（SkillOut 的 definition / definition_json 二选一） */
export function skillDefinition(skill?: Skill | null): Record<string, unknown> {
  return record(skill?.definition ?? skill?.definition_json)
}

/** 展示名：definition.name → 顶层 name → skill_code → id */
export function skillName(skill?: Skill | null): string {
  const definition = skillDefinition(skill)
  return (
    str(definition.name) ||
    str(skill?.name) ||
    str(skill?.skill_code) ||
    str(skill?.code) ||
    str(skill?.id) ||
    '未命名技能'
  )
}

/** 业务编码：skill_code 优先（SkillOut 顶层字段），兼容历史 code */
export function skillCode(skill?: Skill | null): string {
  return str(skill?.skill_code) || str(skill?.code)
}

/** 一句话摘要：definition.summary → description → when_to_use */
export function skillSummary(skill?: Skill | null): string {
  const definition = skillDefinition(skill)
  return (
    str(definition.summary) ||
    str(definition.description) ||
    str(skill?.description) ||
    str(definition.when_to_use) ||
    ''
  )
}

/** 风险等级：definition.guardrails.risk_level → definition.risk_level → 顶层 */
export function skillRiskLevel(skill?: Skill | null): string {
  const definition = skillDefinition(skill)
  const guardrails = record(definition.guardrails)
  return (
    str(guardrails.risk_level) || str(definition.risk_level) || str(definition.risk) || ''
  )
}

/** 可见性：老后端没下发时按 yard 处理（历史技能都是小院级） */
export function skillVisibility(skill?: Skill | null): SkillVisibility {
  const visibility = skill?.visibility
  return visibility === 'private' || visibility === 'platform' ? visibility : 'yard'
}

export function visibilityLabel(visibility?: SkillVisibility | string | null): string {
  const key = (visibility || 'yard') as SkillVisibility
  return VISIBILITY_META[key]?.label ?? '小院'
}

export function visibilityTagType(visibility?: SkillVisibility | string | null): TagType {
  const key = (visibility || 'yard') as SkillVisibility
  return VISIBILITY_META[key]?.type ?? 'success'
}

export function visibilityTip(visibility?: SkillVisibility | string | null): string {
  const key = (visibility || 'yard') as SkillVisibility
  return VISIBILITY_META[key]?.tip ?? ''
}

export function skillStatusLabel(status?: string | null): string {
  if (!status) return '—'
  return STATUS_LABELS[status] ?? status
}

export function skillStatusTagType(status?: string | null): TagType {
  if (status === 'published') return 'success'
  if (status === 'pending_review') return 'warning'
  if (status === 'rejected' || status === 'deprecated' || status === 'disabled') return 'danger'
  if (status === 'testing') return 'primary'
  return 'info'
}

/** 来源：origin 优先，兜底 created_from（老后端字段） */
export function skillOriginLabel(skill?: Skill | null): string {
  const origin = str(skill?.origin) || str(skill?.created_from)
  if (!origin) return '未知来源'
  return ORIGIN_LABELS[origin] ?? origin
}

export interface SkillRiskMeta {
  label: string
  type: TagType
}

/** 风险标签（复用市场条目的口径；未评估时显示「未评估」） */
export function skillRiskMeta(skill?: Skill | null): SkillRiskMeta {
  const level = skillRiskLevel(skill)
  return { label: riskLabel(level), type: riskTagType(level) }
}

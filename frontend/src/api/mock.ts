import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import {
  FALLBACK_PERMISSION_GROUPS,
  PERMISSION_LABELS,
  PROTECTED_PERMISSIONS,
  ROLE_LABELS,
  permissionsOfRoles,
} from '@/components/settings/permissions'

/** Mock 模式下可逐人授权的权限点（与后端 GRANTABLE_PERMISSIONS 同口径） */
const ALL_GRANTABLE = new Set(
  Object.keys(PERMISSION_LABELS).filter(
    permission => permission !== 'owner:transfer' && permission !== 'members:manage_admins',
  ),
)

function parseBody(data: unknown): unknown {
  if (typeof data === 'string') {
    try {
      return JSON.parse(data)
    } catch {
      return {}
    }
  }
  return data ?? {}
}

// 内置 mock 开关：后端未就绪时在 dev 下演示用。
// 开启方式：.env.development 或 .env.local 中设置 VITE_USE_MOCK=true。
// 默认关闭（未设置或为其他值时走真实后端）。
// Mock 数据仅为页面演示的固定 fixture，不代表真实后端契约字段。

export function isMockEnabled(): boolean {
  return import.meta.env.VITE_USE_MOCK === 'true'
}

const ok = (data: unknown, meta?: unknown): unknown => ({
  data,
  ...(meta ? { meta } : {}),
  request_id: `mock_${Date.now().toString(36)}`,
})

const pageMeta = (total: number, page = 1, page_size = 20) => ({ page, page_size, total })

const MOCK_USER = { user_id: 'u-demo-1', username: 'demo', display_name: '演示用户', status: 'active' }
const MOCK_YARD = {
  yard_id: 't-demo-1',
  yard_name: '演示科技小院',
  name: '演示科技小院',
  roles: ['yard_admin'],
  status: 'active',
}

const MOCK_TASKS = [
  {
    id: 'task-1',
    task_type: 'knowledge_qa',
    status: 'succeeded',
    priority: 'normal',
    input: { question: '水稻分蘖期需要重点观察什么？' },
    output: { answer: '分蘖期应重点观察分蘖数、叶色与病虫害迹象……' },
    progress: 100,
    created_at: '2026-10-16T08:00:00Z',
    finished_at: '2026-10-16T08:00:42Z',
  },
  {
    id: 'task-2',
    task_type: 'knowledge_parse',
    status: 'running',
    priority: 'normal',
    input: { document_id: 'doc-1' },
    output: null,
    progress: 55,
    created_at: '2026-10-16T09:12:00Z',
    finished_at: null,
  },
  {
    id: 'task-3',
    task_type: 'knowledge_qa',
    status: 'failed',
    priority: 'high',
    input: { question: '小麦赤霉病如何防治？' },
    output: null,
    progress: 30,
    error_code: 'LLM_ERROR',
    error_message: '模型调用超时，可重试。',
    created_at: '2026-10-16T09:40:00Z',
    finished_at: '2026-10-16T09:41:03Z',
  },
  {
    id: 'task-4',
    task_type: 'knowledge_qa',
    status: 'awaiting_review',
    priority: 'normal',
    input: { question: '玉米螟虫害发生规律？' },
    output: { answer: '玉米螟一年发生 2-3 代……（待专家审核）' },
    progress: 100,
    created_at: '2026-10-16T10:05:00Z',
    finished_at: '2026-10-16T10:05:51Z',
  },
]

const MOCK_EVENTS = [
  { id: 'e1', task_id: 'task-2', seq: 1, event_type: 'started', message: '解析任务已启动', progress: 5, created_at: '2026-10-16T09:12:01Z' },
  { id: 'e2', task_id: 'task-2', seq: 2, event_type: 'progress', message: '已提取 12 / 20 页文本', progress: 55, created_at: '2026-10-16T09:12:40Z' },
]

const MOCK_DOCS = [
  { id: 'doc-1', title: '水稻栽培技术资料', filename: 'rice-tech.pdf', file_type: 'pdf', status: 'succeeded', chunk_count: 128, created_at: '2026-10-15T14:00:00Z' },
  { id: 'doc-2', title: '小麦病虫害图谱', filename: 'wheat-pest.docx', file_type: 'docx', status: 'parsing', chunk_count: null, created_at: '2026-10-16T08:30:00Z' },
  { id: 'doc-3', title: '损坏的扫描件', filename: 'broken.pdf', file_type: 'pdf', status: 'failed', error_message: 'PDF 文本层缺失，无法提取。', chunk_count: null, created_at: '2026-10-16T07:20:00Z' },
]

const MOCK_HITS = [
  {
    chunk_id: 'c1d2e3f4-9d65-45bd-9c04-e1fbc2f4f8a1',
    document_id: 'b0f3c76e-9d65-45bd-9c04-e1fbc2f4f8a1',
    title: '水稻栽培技术资料',
    source: 'yard_upload',
    quote: '分蘖期……（用于核验的短片段）',
    score: 0.91,
    trust_level: 'high',
    page_number: 12,
  },
]

const MOCK_QA = {
  answer: '根据已检索资料，水稻分蘖期应重点观察分蘖数、叶色与病虫害迹象……',
  citations: [
    {
      chunk_id: 'c1d2e3f4-9d65-45bd-9c04-e1fbc2f4f8a1',
      document_id: 'b0f3c76e-9d65-45bd-9c04-e1fbc2f4f8a1',
      title: '水稻栽培技术资料',
      source: 'yard_upload',
      quote: '分蘖期……（用于核验的短片段）',
      page_number: 12,
    },
  ],
  warnings: ['本回答仅供技术参考，关键农事操作需结合现场情况复核。'],
  needs_expert_review: false,
}

const MOCK_CANDIDATES = [
  { id: 'cand-1', title: '玉米螟发生规律（用户纠错沉淀）', content: '玉米螟一年发生 2-3 代，越冬代成虫 5 月中下旬羽化……', source: 'feedback', status: 'pending_review', created_at: '2026-10-16T10:06:00Z' },
  { id: 'cand-2', title: '水稻纹枯病防治要点', content: '纹枯病以农业防治为主，配合药剂……', source: 'agent_extract', status: 'pending_review', created_at: '2026-10-16T09:00:00Z' },
]

const MOCK_SKILLS = [
  { id: 'skill-1', name: '水稻分蘖诊断', code: 'crop.rice.tillering', version: '0.1.0', description: '基于分蘖数与叶色判断长势的问答技能', status: 'published', created_at: '2026-10-14T03:00:00Z' },
  { id: 'skill-2', name: '病虫害识别草稿', code: 'crop.pest.identify', version: '0.0.1', description: '上传叶片照片识别病虫害', status: 'draft', created_at: '2026-10-16T06:00:00Z' },
]

const MOCK_MEMBERS = [
  { user_id: 'u-demo-1', username: 'demo', display_name: '演示用户', role: 'yard_admin', joined_at: '2026-10-01T00:00:00Z' },
  { user_id: 'u-demo-2', username: 'expert_wang', display_name: '王专家', role: 'expert', joined_at: '2026-10-05T00:00:00Z' },
]

const MOCK_LLM = { provider: 'deepseek', model: 'deepseek-chat', configured: true, healthy: true, latency_ms: 812, checked_at: '2026-10-16T10:00:00Z' }

const MOCK_SUPPLIERS = [
  { id: 'sp-main', yard_id: null, name: 'deepseek-main', provider_type: 'deepseek', base_url: 'https://api.deepseek.com', model_id: 'deepseek-chat', api_key_masked: 'sk-s****8888', models: null, priority: 10, status: 1, is_active: true, supports_tools: true, consecutive_failures: 0, test_time: '2026-10-28T09:00:00Z', response_time_ms: 780, last_error: null, readonly: false, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-28T09:00:00Z' },
  { id: 'sp-backup', yard_id: 'yard-demo', name: 'qwen-backup', provider_type: 'qwen', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model_id: 'qwen-max', api_key_masked: 'sk-a****2222', models: null, priority: 5, status: 1, is_active: false, supports_tools: false, consecutive_failures: 0, test_time: '2026-10-27T09:00:00Z', response_time_ms: 940, last_error: null, readonly: false, created_at: '2026-10-02T00:00:00Z', updated_at: '2026-10-27T09:00:00Z' },
]

const MOCK_CAPABILITIES = [
  { code: 'declarative', label: '专业助手对话', group: '对话', hint: '内置/自建助手在问答与任务运行时的主模型调用' },
  { code: 'chat_document', label: '对话内文档生成', group: '文档', hint: '会话中直接产出 md/docx 文档正文' },
  { code: 'embedding', label: '知识向量化', group: '知识', hint: '知识库切片入库的向量计算' },
  { code: 'device_analysis', label: '设备数据解读', group: '设备', hint: '设备分析结论的自然语言解释' },
]

let MOCK_BINDINGS = [
  { id: 'bd-1', yard_id: 'yard-demo', scope_type: 'global', scope_key: '', supplier_id: 'sp-main', supplier_name: 'deepseek-main', provider_type: 'deepseek', model_id: 'deepseek-chat', model_name: null, priority: 20, enabled: true, created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z' },
  { id: 'bd-2', yard_id: 'yard-demo', scope_type: 'global', scope_key: '', supplier_id: 'sp-backup', supplier_name: 'qwen-backup', provider_type: 'qwen', model_id: 'qwen-max', model_name: 'qwen-plus', priority: 10, enabled: true, created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z' },
]

const MOCK_CATALOG = [
  { code: 'knowledge.search', version: '1.0.0', name: '知识库检索', description: '在本小院知识库中检索证据片段', input_schema: { type: 'object', properties: { query: { type: 'string', description: '检索问题' } }, required: ['query'] }, output_schema: null, required_permissions: ['knowledge:read'], risk_level: 'read_only', side_effect: false, timeout_seconds: 30, enabled: true, source: 'builtin', impl_kind: 'registry', yard_id: null, readonly: true, implementation: { kind: 'registry' } },
  { code: 'pest.alert_read', version: '0.1.0', name: '告警速读', description: '把今天的设备告警读成人话', input_schema: { type: 'object', properties: { crop: { type: 'string', description: '作物名' } }, required: ['crop'] }, output_schema: null, required_permissions: ['tools:execute'], risk_level: 'read_only', side_effect: false, timeout_seconds: 60, enabled: true, source: 'custom', impl_kind: 'prompt', yard_id: 'yard-demo', readonly: false, implementation: { kind: 'prompt', prompt_template: '请用三句话总结 {crop} 的今日告警' } },
  { code: 'mcp__abcd1234__today_summary', version: 'mcp:abcd1234', name: 'today_summary', description: '演示 MCP：返回小院今日概况', input_schema: { type: 'object', additionalProperties: false }, output_schema: null, required_permissions: ['tools:execute'], risk_level: 'external_side_effect', side_effect: true, timeout_seconds: 20, enabled: true, source: 'mcp', impl_kind: 'mcp', yard_id: 'yard-demo', readonly: true, implementation: { kind: 'mcp', server: '演示 MCP', server_enabled: true } },
]

const MOCK_TOOLS = [
  { code: 'weather.query', version: '1.0.0', name: '天气查询', description: '查询指定区域未来天气', input_schema: { type: 'object' }, required_permissions: ['tool:weather'], risk_level: 'read_only', side_effect: false, timeout_seconds: 30 },
]

const MOCK_MENU_CONFIG = {
  default_mode: 'standard',
  role_modes: {
    yard_owner: 'standard',
    yard_admin: 'standard',
    expert: 'standard',
    operator: 'minimal',
    viewer: 'minimal',
  },
  profiles: [],
  allow_member_customize: true,
}

const MOCK_GRANTS: Record<string, string[]> = {}

const MOCK_STATS = {
  tasks_total: 128,  tasks_by_status: { succeeded: 110, failed: 9, running: 3, queued: 2, awaiting_review: 4 },
  tasks_failed_by_code: { LLM_ERROR: 5, DOCUMENT_PARSE_FAILED: 4 },
  avg_task_duration_seconds: 41.2,
  llm_cost_total: 12.58,
  documents_total: 36,
  feedback_total: 17,
}

export function getMockAdapter(): AxiosAdapter {
  return async (config: InternalAxiosRequestConfig): Promise<AxiosResponse> => {
    const method = (config.method ?? 'get').toLowerCase()
    const url = (config.url ?? '').replace(/\/$/, '')
    const respond = (payload: unknown, status = 200): AxiosResponse => ({
      data: payload,
      status,
      statusText: status === 200 ? 'OK' : 'Created',
      headers: {},
      config,
    })

    if (method === 'post' && url === '/auth/login')
      return respond(ok({ access_token: 'mock-access', refresh_token: 'mock-refresh', token_type: 'bearer', user: MOCK_USER, roles: ['yard_admin'], yards: [MOCK_YARD] }))
    if (method === 'post' && url === '/auth/refresh')
      return respond(ok({ access_token: 'mock-access', refresh_token: 'mock-refresh' }))
    if (method === 'post' && url === '/auth/logout') return respond(ok(null))
    if (method === 'get' && url === '/auth/me') {
      const role = MOCK_YARD.roles[0] ?? 'viewer'
      return respond(
        ok({
          user: MOCK_USER,
          roles: MOCK_YARD.roles,
          yards: [MOCK_YARD],
          yard_id: MOCK_YARD.yard_id,
          is_owner: role === 'yard_owner',
          permissions: [...permissionsOfRoles(MOCK_YARD.roles)],
        }),
      )
    }

    if (method === 'get' && url === '/yards/current/menu') return respond(ok(MOCK_MENU_CONFIG))
    if (method === 'put' && url === '/yards/current/menu') {
      const patch = parseBody(config.data) as Record<string, unknown>
      return respond(ok({ ...MOCK_MENU_CONFIG, ...patch }))
    }
    if (url.startsWith('/yards/current/members/') && url.endsWith('/permissions')) {
      const memberId = url.split('/')[3]
      const member = MOCK_MEMBERS.find(row => row.user_id === memberId) ?? MOCK_MEMBERS[0]
      const grants = MOCK_GRANTS[member.user_id] ?? []
      const rolePermissions = [...permissionsOfRoles([member.role])]
      const payload = {
        user_id: member.user_id,
        username: member.username,
        display_name: member.display_name,
        role: member.role,
        role_name: ROLE_LABELS[member.role] ?? member.role,
        role_permissions: rolePermissions,
        grants,
        effective_permissions: Array.from(new Set([...rolePermissions, ...grants])).sort(),
        is_owner: member.role === 'yard_owner',
        permission_groups: FALLBACK_PERMISSION_GROUPS,
        permission_labels: PERMISSION_LABELS,
        protected_permissions: PROTECTED_PERMISSIONS,
        grantable_permissions: Array.from(ALL_GRANTABLE),
      }
      if (method === 'put') {
        const body = parseBody(config.data) as { grants?: string[] }
        MOCK_GRANTS[member.user_id] = [...(body.grants ?? [])]
      }
      return respond(ok(payload))
    }

    if (method === 'get' && url === '/yards/current') return respond(ok(MOCK_YARD))
    if (method === 'get' && url === '/yards/current/members')
      return respond(ok(MOCK_MEMBERS, pageMeta(MOCK_MEMBERS.length)))
    if (method === 'post' && url === '/yards/current/members')
      return respond(ok({ user_id: 'u-demo-new', username: 'new_member', role: 'operator' }), 201)
    if (method === 'delete' && url.startsWith('/yards/current/members/')) return respond(ok(null))

    if (method === 'post' && url === '/tasks') return respond(ok({ ...MOCK_TASKS[0], id: 'task-new' }), 201)
    if (method === 'get' && url === '/tasks') return respond(ok(MOCK_TASKS, pageMeta(MOCK_TASKS.length)))
    if (method === 'get' && url.endsWith('/events'))
      return respond(ok(MOCK_EVENTS.filter(e => e.task_id === url.split('/')[2])))
    if (method === 'get' && url.startsWith('/tasks/'))
      return respond(ok(MOCK_TASKS.find(t => t.id === url.split('/')[2]) ?? null))
    if (method === 'post' && url.endsWith('/cancel'))
      return respond(ok({ ...MOCK_TASKS[1], status: 'cancelled' }))
    if (method === 'post' && url.endsWith('/retry'))
      return respond(ok({ ...MOCK_TASKS[2], status: 'queued' }))

    if (method === 'post' && url === '/knowledge/documents')
      return respond(ok({ document_id: 'doc-new', task_id: 'task-new' }), 201)
    if (method === 'get' && url === '/knowledge/documents')
      return respond(ok(MOCK_DOCS, pageMeta(MOCK_DOCS.length)))
    if (method === 'get' && url.startsWith('/knowledge/documents/'))
      return respond(ok(MOCK_DOCS.find(d => d.id === url.split('/')[3]) ?? null))
    if (method === 'delete' && url.startsWith('/knowledge/documents/')) return respond(ok(null))
    if (method === 'post' && url === '/knowledge/search') return respond(ok({ hits: MOCK_HITS }))
    if (method === 'get' && url === '/knowledge/candidates')
      return respond(ok(MOCK_CANDIDATES, pageMeta(MOCK_CANDIDATES.length)))
    if (method === 'post' && url.includes('/knowledge/candidates/') && url.endsWith('/review'))
      return respond(ok({ ...MOCK_CANDIDATES[0], status: 'approved' }))

    if (method === 'post' && url === '/qa/answers') return respond(ok(MOCK_QA))
    if (method === 'post' && url === '/feedback') return respond(ok({ id: 'fb-1' }), 201)
    if (method === 'get' && url === '/feedback') return respond(ok([], pageMeta(0)))
    if (method === 'get' && url === '/skills') return respond(ok(MOCK_SKILLS, pageMeta(MOCK_SKILLS.length)))
    if (method === 'post' && url === '/skills') return respond(ok({ ...MOCK_SKILLS[1], id: 'skill-new' }), 201)
    if (method === 'post' && url.includes('/skills/') && url.endsWith('/review'))
      return respond(ok({ ...MOCK_SKILLS[1], status: 'published' }))
    if (method === 'get' && url === '/llm/status') return respond(ok(MOCK_LLM))
    if (method === 'get' && url === '/tools') return respond(ok(MOCK_TOOLS, pageMeta(MOCK_TOOLS.length)))

    // ---------- 模型中心（v3.25）：供应商、能力清单与三层绑定 ----------
    if (method === 'get' && url === '/llm/providers') return respond(ok(MOCK_SUPPLIERS))
    if (method === 'get' && url === '/llm/provider-templates') return respond(ok([]))
    if (method === 'get' && url === '/llm/capabilities')
      return respond(ok({ capabilities: MOCK_CAPABILITIES, purpose_codes: MOCK_CAPABILITIES.map(c => c.code), valid_purposes: MOCK_CAPABILITIES.map(c => c.code), scope_types: [{ type: 'global', label: '全局默认', need_key: false }, { type: 'purpose', label: '按能力', need_key: true }, { type: 'agent', label: '按智能体', need_key: true }], levels: [{ level: 'agent_bound', label: '助手指定模型' }, { level: 'purpose_binding', label: '按能力绑定' }, { level: 'yard_default', label: '小院默认+兜底' }], max_fallback: 3 }))
    if (method === 'get' && url === '/llm/bindings') return respond(ok({ items: MOCK_BINDINGS, scope_type_labels: { global: '全局默认', purpose: '按能力', agent: '按智能体' } }))
    if (method === 'put' && url === '/llm/bindings') {
      const body = JSON.parse(String(config.data ?? '{}'))
      MOCK_BINDINGS = MOCK_BINDINGS.filter(b => !(b.scope_type === body.scope_type && b.scope_key === (body.scope_key ?? '')))
        .concat((body.entries ?? []).map((e: any, i: number) => {
          const sp = MOCK_SUPPLIERS.find(x => x.id === e.supplier_id)
          return { id: `bd-${Date.now()}-${i}`, yard_id: 'yard-demo', scope_type: body.scope_type, scope_key: body.scope_key ?? '', supplier_id: e.supplier_id, supplier_name: sp?.name ?? '', provider_type: sp?.provider_type ?? '', model_id: sp?.model_id ?? '', model_name: e.model_name ?? null, priority: e.priority ?? (body.entries.length - i) * 10, enabled: true, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }
        }))
      return respond(ok({ items: MOCK_BINDINGS }))
    }
    if (method === 'delete' && url.startsWith('/llm/bindings/')) {
      MOCK_BINDINGS = MOCK_BINDINGS.filter(b => b.id !== url.split('/').pop())
      return respond(ok({ deleted: url.split('/').pop() }))
    }
    if (method === 'get' && url === '/llm/bindings/resolution') {
      const hops = MOCK_BINDINGS.map(b => ({ level: b.scope_type === 'global' ? 'yard_default' : 'purpose_binding', level_label: b.scope_type === 'global' ? '小院默认+兜底' : '按能力绑定', reason: '绑定命中', supplier_id: b.supplier_id, supplier_name: b.supplier_name, model_name: b.model_name || b.model_id, usable: true }))
      return respond(ok({ purpose: null, purpose_label: null, agent_code: null, candidate_supplier_ids: hops.map(h => h.supplier_id), resolved_supplier_id: hops[0]?.supplier_id ?? null, resolved_model: hops[0]?.model_name ?? null, resolved_level: hops[0]?.level_label ?? '环境变量兜底', hops }))
    }
    if (method === 'post' && url === '/llm/bindings/test') return respond(ok({ ok: true, model: 'deepseek-chat', latency_ms: 620, reply: 'ok（演示数据）', attempted: [] }))

    // ---------- 可用工具目录（v3.25） ----------
    if (method === 'get' && url === '/tools/catalog')
      return respond(ok({ items: MOCK_CATALOG, counts: { builtin: 1, custom: 1, mcp: 1 }, risk_labels: { read_only: '只读：只查询，不改动任何数据', draft: '起草：产出草稿，等待人工确认', write_reversible: '可回滚写入：改动本系统内数据，可撤销', external_side_effect: '外部副作用：改动外部世界，需人工审批' }, source_labels: { builtin: '内置', custom: '本小院添加', mcp: '来自 MCP 服务器' } }))
    if (method === 'post' && url === '/tools/generate') {
      const body = JSON.parse(String(config.data ?? '{}'))
      const title = String(body.utterance ?? '自定义工具').slice(0, 20)
      return respond(ok({ spec: { code: `custom.demo_${Date.now().toString(36)}`, name: title, description: `${title}（演示草稿）`, input_schema: { type: 'object', properties: { request: { type: 'string', description: '本次请求内容' } }, required: ['request'], additionalProperties: false }, impl_kind: 'prompt', impl: { prompt_template: '请完成任务：{request}' }, risk_level: 'read_only', side_effect: false, required_permissions: ['tools:execute'] }, source: 'fallback', model: '', note: '演示模式下给出保守草稿，请人工确认后启用。' }))
    }
    if (method === 'post' && url === '/tools') {
      const body = JSON.parse(String(config.data ?? '{}'))
      const item = { code: body.code, version: '0.1.0', name: body.name, description: body.description ?? '', input_schema: body.input_schema ?? { type: 'object' }, output_schema: null, required_permissions: body.required_permissions ?? ['tools:execute'], risk_level: body.risk_level ?? 'read_only', side_effect: !!body.side_effect, timeout_seconds: 60, enabled: !!body.enabled, source: 'custom', impl_kind: body.impl_kind ?? 'prompt', yard_id: 'yard-demo', readonly: false, implementation: body.impl ?? { kind: 'prompt' } }
      MOCK_CATALOG.push(item)
      MOCK_TOOLS.push({ code: item.code, version: item.version, name: item.name, description: item.description, input_schema: item.input_schema, required_permissions: item.required_permissions, risk_level: item.risk_level, side_effect: item.side_effect, timeout_seconds: item.timeout_seconds })
      return respond(ok(item))
    }
    if (method === 'get' && url.startsWith('/tools/')) {
      const code = decodeURIComponent(url.split('/tools/')[1])
      const base = MOCK_CATALOG.find(t => t.code === code)
      if (!base) return respond(ok(null))
      return respond(ok({ ...base, definition_markdown: base.description, call_example: JSON.stringify(Object.fromEntries(Object.keys((base.input_schema as any)?.properties ?? {}).map(k => [k, '示例值'])), null, 2), recent_calls: [] }))
    }
    if (method === 'post' && /\/tools\/.+\/(enable|disable)$/.test(url)) {
      const code = decodeURIComponent(url.split('/tools/')[1].split('/')[0])
      const item = MOCK_CATALOG.find(t => t.code === code)
      if (item) item.enabled = url.endsWith('/enable')
      return respond(ok(item ?? null))
    }
    if (method === 'put' && url.startsWith('/tools/')) {
      const code = decodeURIComponent(url.split('/tools/')[1])
      const body = JSON.parse(String(config.data ?? '{}'))
      const item = MOCK_CATALOG.find(t => t.code === code)
      if (item) Object.assign(item, { name: body.name ?? item.name, description: body.description ?? item.description, input_schema: body.input_schema ?? item.input_schema, risk_level: body.risk_level ?? item.risk_level })
      return respond(ok(item ?? null))
    }
    if (method === 'delete' && url.startsWith('/tools/')) {
      const code = decodeURIComponent(url.split('/tools/')[1])
      const idx = MOCK_CATALOG.findIndex(t => t.code === code)
      if (idx >= 0) MOCK_CATALOG.splice(idx, 1)
      return respond(ok({ deleted: code }))
    }
    if (method === 'get' && url === '/audit/logs') return respond(ok([], pageMeta(0)))
    if (method === 'get' && url === '/admin/stats') return respond(ok(MOCK_STATS))

    return respond({ error: { code: 'NOT_FOUND', message: 'Mock 未覆盖该接口' }, request_id: 'mock' }, 404)
  }
}

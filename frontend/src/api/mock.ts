import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from 'axios'

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

const MOCK_TOOLS = [
  { code: 'weather.query', version: '1.0.0', name: '天气查询', description: '查询指定区域未来天气', input_schema: { type: 'object' }, required_permissions: ['tool:weather'], risk_level: 'read_only', side_effect: false, timeout_seconds: 30 },
]

const MOCK_STATS = {
  tasks_total: 128,
  tasks_by_status: { succeeded: 110, failed: 9, running: 3, queued: 2, awaiting_review: 4 },
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
    if (method === 'get' && url === '/auth/me')
      return respond(ok({ user: MOCK_USER, roles: ['yard_admin'], yards: [MOCK_YARD] }))

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
    if (method === 'get' && url === '/audit/logs') return respond(ok([], pageMeta(0)))
    if (method === 'get' && url === '/admin/stats') return respond(ok(MOCK_STATS))

    return respond({ error: { code: 'NOT_FOUND', message: 'Mock 未覆盖该接口' }, request_id: 'mock' }, 404)
  }
}

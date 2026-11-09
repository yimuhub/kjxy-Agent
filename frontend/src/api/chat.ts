import type { AxiosRequestConfig, AxiosResponse } from 'axios'
import { api, apiUrl } from './client'
import type { ApiResponse } from '@/types/api'

/**
 * 对话模式 API 模块（docs/DESIGN-v2.md §7 对话模式 / §10 后端契约）。
 *
 * ---------- 对话 API 契约（DESIGN-v2.md §10，前后端共同遵守） ----------
 *
 * - `GET  /api/v1/chat/sessions` → `{items:[{id,title,target,target_code,updated_at,last_excerpt}]}`（当前用户自己的会话，新→旧）
 * - `POST /api/v1/chat/sessions` body `{title?, target?: 'auto'|'agent'|'team', target_code?}` → 会话对象
 * - `PATCH /api/v1/chat/sessions/{id}` body `{title}` → 会话对象
 * - `DELETE /api/v1/chat/sessions/{id}` → 204 语义（ok 包）
 * - `GET  /api/v1/chat/sessions/{id}/messages` → `{items:[{id,role:'user'|'assistant',content,choice?,routed_by?,reason?,created_at}]}`（升序）
 * - `POST /api/v1/chat/sessions/{id}/messages` body `{content}` → `{user_message, assistant_message}`
 *   - target=auto：走 supervisor 三级路由；assistant_message.choice = {kind,code?,name}、routed_by、reason
 *   - target=agent/team：直跑指定对象；routed_by='manual'
 *   - 执行失败：assistant_message.content = 中文错误说明，routed_by='error'（会话不死，可继续问）
 * - 全部登录可用（成员级），跨小院/跨用户会话一律 404
 *
 * ---------- 附件与流式（v1.8） ----------
 *
 * - `POST /api/v1/chat/sessions/{id}/attachments`（multipart `file`）→ 附件对象（201）
 * - `GET  /api/v1/chat/sessions/{id}/attachments` → `{items:[附件]}`
 * - `DELETE /api/v1/chat/sessions/{id}/attachments/{aid}` → ok 包
 * - `POST /api/v1/chat/sessions/{id}/messages` body `{content, attachment_ids?}` → 同步一问一答（流式不可用时的兜底）
 * - `POST /api/v1/chat/sessions/{id}/messages/stream`（SSE）→ 过程步骤 + 最终回答
 *
 * ---------- 点名 / 多助手群聊 / 徽章下发（v2.0，后端并行开发中） ----------
 *
 * - 发送请求体新增两个可选字段：`target_code`（@ 点名对象，可传 id/code/展示名）、
 *   `policy`（群聊本轮策略 all|named|auto）；**都不传 = 现有单聊三级路由，行为不变**。
 * - `GET  /api/v1/chat/mention-candidates`
 *      → `{data:{items:[{kind,id,code,name,display_name,visibility,emoji,blurb}]}}`
 *      （private 助手的 display_name 由后端派生成「原名（用户名私有）」，前端不再拼）
 * - `GET  /api/v1/chat/sessions/{id}/participants` → `{data:{items|list:[参与者]}}`（也可能裸数组）
 * - `PUT  /api/v1/chat/sessions/{id}/participants` body `{agent_ids:[...]}` 有序整表覆盖（1..8 位，超出后端报错）
 * - SSE 新增一帧 `member_event`：`{turn_id, member_index, role_name, agent_id, status, error_text}`
 * - assistant 消息出参新增 `routed_label` / `routed_tone`（后端受控词表；老数据没有这两个字段时前端兜底）
 *
 * ----------------------------------------------------------------------
 * 约定：axios baseURL 已含 /api/v1（见 client.ts），本模块路径省略该前缀。
 */

/** 会话执行对象：auto 走总控三级路由；agent/team 直跑指定对象 */
export type ChatTarget = 'auto' | 'agent' | 'team'

/**
 * 本会话的高风险工具授权模式（后端 0043，前后端同一契约）：
 * - ask ：每次都要人工批准（默认，与历史行为一致）
 * - auto：本会话内批准过的同一调用不再反复问（首次仍问）
 * - full：高风险工具直接执行、不产生审批单（仅小院/平台管理员可开，全程留痕）
 */
export type ChatApprovalMode = 'ask' | 'auto' | 'full'

/** 三点菜单 / 批量条共用的会话标记位局部更新（只传要改的字段） */
export interface SessionPatch {
  title?: string
  pinned?: boolean
  archived?: boolean
  unread?: boolean
  approval_mode?: ChatApprovalMode
  /** 执行模式：切换指挥官后写回会话，左侧列表据此显示徽章（避免反向依赖 commander.ts） */
  execution_mode?: 'standard' | 'commander'
}

/** 批量动作（与后端 BATCH_ACTIONS 同口径） */
export type SessionBatchAction =
  | 'pin'
  | 'unpin'
  | 'archive'
  | 'unarchive'
  | 'read'
  | 'unread'
  | 'delete'

/** 批量操作响应：受影响的自己的会话 id；missing=他人/不存在的条数（不点名，防枚举） */
export interface SessionBatchResult {
  action: SessionBatchAction
  affected: string[]
  deleted: number
  missing: number
}

/** 授权模式选择器的展示字典（后端只下发事实，文案与色调在这里单点定义） */
export const APPROVAL_MODE_META: Record<
  ChatApprovalMode,
  { label: string; short: string; hint: string; adminsOnly: boolean }
> = {
  ask: {
    label: '每次询问',
    short: '询问',
    hint: '高风险操作（对外发送、写生产数据等）每次都先挂审批单，人工批准后才执行。',
    adminsOnly: false,
  },
  auto: {
    label: '会话内自动放行',
    short: '自动',
    hint: '同一操作在本会话里批准过一次，后续同样参数不再重复问；首次仍会询问。',
    adminsOnly: true,
  },
  full: {
    label: '完全访问',
    short: '完全',
    hint: '本会话的高风险工具直接执行、不再产生审批单。仅小院/平台管理员可开，全程留痕。',
    adminsOnly: true,
  },
}

/** 路由来源（诚实标记口径）：规则命中 / 智能分类 / 频率兜底 / 手动指定 / 对话直办 / 执行失败 */
export type ChatRoutedBy =
  | 'rules'
  | 'llm'
  | 'fallback'
  | 'manual'
  | 'local'
  | 'qa'
  | 'plan'
  | 'error'

/**
 * 徽章色调白名单：与后端 routed_tone 受控词表对齐，同时正好是 el-tag 的合法 type。
 * 白名单而非映射表：新值一律落 'info'，避免未知色调把消息渲染打断。
 */
export type RoutedTone = 'success' | 'primary' | 'warning' | 'info' | 'danger'
const ROUTED_TONES: readonly RoutedTone[] = ['success', 'primary', 'warning', 'info', 'danger']

/** routed_tone 收敛为合法 el-tag type；未知/缺失走 info（v1.8.0 徽章崩溃的根治点之一） */
export function normalizeRoutedTone(tone?: string | null): RoutedTone {
  return ROUTED_TONES.includes(tone as RoutedTone) ? (tone as RoutedTone) : 'info'
}

/** 群聊本轮应答策略：全员答 / 点名答 / 自愿答（named 必须带 target_code） */
export type ChatGroupPolicy = 'all' | 'named' | 'auto'

/** 点名候选对象类型：已发布助手 / 班组 */
export type MentionKind = 'agent' | 'team'

/** @ 点名候选（后端已派生 display_name 与可见性，前端不再加工） */
export interface MentionCandidate {
  kind: MentionKind
  id: string
  code?: string | null
  name?: string | null
  display_name?: string | null
  visibility?: string | null
  emoji?: string | null
  blurb?: string | null
}

/** 点名候选列表响应：{items}（容忍后端改用 list 键或裸数组） */
export interface MentionCandidateList {
  items?: MentionCandidate[]
  list?: MentionCandidate[]
}

/** 会话群聊参与者（GET/PUT participants 出参） */
export interface ChatParticipant {
  id?: string
  session_id?: string
  agent_id?: string
  agent_code?: string | null
  agent_name?: string | null
  position?: number | null
}

/** 参与者列表响应：包 items / 包 list / 裸数组三种形状都可能 */
export type ParticipantListPayload =
  | { items?: ChatParticipant[]; list?: ChatParticipant[] }
  | ChatParticipant[]

/** 成员事件三态（未知取值收敛为 unknown，界面走兜底分支） */
export type MemberEventStatus = 'running' | 'done' | 'error' | 'unknown'

/** member_event 帧载荷 */
export interface ChatMemberEvent {
  turn_id?: string | null
  member_index?: number | string | null
  role_name?: string | null
  agent_id?: string | null
  status?: string | null
  error_text?: string | null
}

/**
 * 后端 MEMBER_RUNNING / MEMBER_DONE / MEMBER_ERROR 等取值 → 前端三态词表。
 * 只认这三态，其余一律 unknown，不崩。
 */
const MEMBER_STATUS_WORDS: Record<string, MemberEventStatus> = {
  running: 'running',
  started: 'running',
  pending: 'running',
  done: 'done',
  completed: 'done',
  finished: 'done',
  ok: 'done',
  success: 'done',
  error: 'error',
  failed: 'error',
  failure: 'error',
}

/** 去掉 MEMBER_ 前缀与分隔符后查三态词表 */
export function normalizeMemberStatus(raw?: string | null): MemberEventStatus {
  const key = String(raw ?? '')
    .trim()
    .toLowerCase()
    .replace(/^member[_-]?/, '')
  return MEMBER_STATUS_WORDS[key] ?? 'unknown'
}

/** 消息执行状态：正常 / 失败 / 被中止 */
export type ChatMessageStatus = 'ok' | 'failed' | 'aborted'

/** 单条消息或会话的 Token 消耗统计 */
export interface ChatTokenUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  model?: string | null
  latency_ms?: number | null
  estimated?: boolean
}

/** 会话对象（列表项与创建/改名返回同构） */
export interface ChatSession {
  id: string
  title: string
  /** 会话固定执行对象：auto=自动路由，agent=锁定某助手，team=班组（见后端 0028） */
  target?: ChatTarget
  target_code?: string | null
  /** target=agent 时后端解析出的助手展示名 */
  target_name?: string | null
  /** 群聊成员数（>0 即群聊；与 target 取值正交） */
  participant_count?: number
  /** 建会话的时间：左侧列表按它显示「哪一年几月几日几点几分」创建的（不是最后一条消息时间） */
  created_at: string
  updated_at: string
  last_excerpt?: string | null
  message_count?: number
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  /** ---- 0043 会话列表管理（后端下发事实，前端不自行猜）---- */
  /** 置顶：列表里恒排在最前 */
  pinned?: boolean
  pinned_at?: string | null
  /** 归档：默认列表不显示，切「已归档」才看得到 */
  archived?: boolean
  archived_at?: string | null
  /** 手动「标记未读」；打开会话即清除 */
  unread?: boolean
  /** 本会话的高风险工具授权模式（后端会按角色静默降级，取回来的就是生效值） */
  approval_mode?: ChatApprovalMode
  /** 本会话的执行模式：standard=普通路由；commander=指挥官编排（v3.38） */
  execution_mode?: 'standard' | 'commander'
  /** 分支来源：从哪个会话的哪条回答拉出来的（原会话不受影响） */
  parent_session_id?: string | null
  parent_message_id?: string | null
}

/** 会话列表响应体：{items}，新→旧 */
export interface ChatSessionList {
  items: ChatSession[]
}

/** 实际执行的回答对象标记 */
export interface ChatChoice {
  kind: 'agent' | 'team'
  code?: string | null
  name: string
}

/** 附件类型：图片 / 文档 */
export type ChatAttachmentKind = 'image' | 'document'

/** 附件解析状态：已就绪 / 解析失败 */
export type ChatAttachmentStatus = 'ready' | 'failed'

/** 会话附件对象 */
export interface ChatAttachment {
  id: string
  session_id: string
  filename: string
  mime: string
  size_bytes: number
  kind: ChatAttachmentKind
  status: ChatAttachmentStatus
  chars: number
  error?: string | null
  /** 生成文档的格式（md/docx/xlsx/pptx/pdf）；上传附件为空 */
  doc_format?: string | null
  /** 是否由对话生成（可在线预览） */
  generated?: boolean
  created_at?: string
}

/** 附件列表响应体：{items} */
export interface ChatAttachmentList {
  items: ChatAttachment[]
}

/** 消息里引用的附件摘要（存证用，历史回放可直接展示文件名） */
export interface ChatAttachmentRef {
  id: string
  filename: string
  kind: ChatAttachmentKind
  mime: string
  size_bytes: number
  status: ChatAttachmentStatus
}

/** 过程阶段（与后端 app/core/progress.py 词表一致，只增不改） */
export type ChatStepPhase =
  | 'reading'
  | 'routing'
  | 'retrieving'
  | 'thinking'
  | 'tool_call'
  | 'generating'

/** 步骤状态：进行中 / 已完成 / 失败 */
export type ChatStepStatus = 'running' | 'done' | 'failed'

/** 单条过程步骤 */
export interface ChatStep {
  phase: ChatStepPhase
  title: string
  detail: string
  status: ChatStepStatus
  ts: string
}

/** 卡片动作项 */
export interface ChatCardAction {
  key: string
  label: string
  requires_role?: string
}

/** 能力卡片（对话内生成助手草稿） */
export interface ChatCapabilityCard {
  type: 'capability'
  state: 'created' | 'requested'
  name: string
  description: string
  agent_id: string
  code: string
  risk_level: 'low' | 'high'
  status: string
  tools: string[]
  note: string
  actions: ChatCardAction[]
}

/** 市场卡片（对话内装载能力包） */
export interface ChatMarketCard {
  type: 'market'
  state: 'suggested' | 'requested' | 'installed' | 'failed'
  name: string
  description: string
  items: { code: string; name: string }[]
  note: string
  actions: ChatCardAction[]
}

/** 文档卡片（对话内生成 Word/Excel/PPT/PDF/Markdown） */
export interface ChatDocumentCard {
  type: 'document'
  attachment_id: string
  session_id: string
  filename: string
  /** md | docx | xlsx | pptx | pdf */
  format: string
  mime: string
  size_bytes: number
  previewable: boolean
  actions: ChatCardAction[]
}

/** 方案分节（后端按二级标题切好；正文为 Markdown） */
export interface ChatPlanSection {
  heading: string
  body: string
}

/** 方案取证统计（与后端 payload.evidence 同形） */
export interface ChatPlanEvidence {
  knowledge?: number
  web?: number
  skills?: string[]
  /** 未联网的原因：knowledge_enough（知识库够了）/ disabled（本次不联网）/ 空 = 已联网 */
  web_skipped?: string
}

/**
 * 方案卡片（v3.14 对话内多源合成）：方案正文就是消息 content（Markdown），
 * 卡片只负责「完整还是残缺、依据来自哪、下一步做什么」，不重复渲染正文。
 */
export interface ChatPlanCard {
  type: 'plan'
  title?: string
  sections?: ChatPlanSection[]
  required_sections?: string[]
  missing_sections?: string[]
  complete?: boolean
  truncated?: boolean
  repair_passes?: number
  needs_expert_review?: boolean
  evidence?: ChatPlanEvidence
  warnings?: string[]
}

/** 技能卡片里的候选（与 /market/agent/request 的 matches 同形） */
export interface ChatSkillCardMatch {
  item_key: string
  name: string
  summary?: string
  source?: string
  license?: string | null
  risk_level?: string
  installed?: boolean
  score?: number
}

/**
 * 技能卡片（v3.2 一句话技能）：
 * matched=找到可安装的候选；created=已生成私有草稿；failed=检索/生成失败（带 note 可重试）。
 */
export interface ChatSkillCard {
  type: 'skill_card'
  state: 'matched' | 'created' | 'failed'
  utterance: string
  skill: {
    id: string
    code: string
    name: string
    visibility: string
    status: string
  } | null
  matches?: ChatSkillCardMatch[]
  sources?: { title: string; url: string }[]
  note?: string
}

/** 插件卡片里的工作流步骤（与 workflow manifest steps 同形） */
export interface ChatPluginCardStep {
  kind: 'tool' | 'llm'
  tool?: string
  args?: Record<string, unknown>
  prompt?: string
  save_as?: string | null
}

/** 插件卡片的 manifest（后端已规范化，只保留已知字段） */
export interface ChatPluginManifest {
  name: string
  description: string
  params: { key: string; label: string; required: boolean }[]
  steps: ChatPluginCardStep[]
}

/**
 * 插件卡片（v3.3 一句话插件）：created=已生成待审工作流；failed=生成失败（带 note 可重试）。
 * 插件没有"私有即用"：created 一律 pending_review，审核通过后才能运行。
 */
export interface ChatPluginCard {
  type: 'plugin_card'
  state: 'created' | 'failed'
  utterance: string
  plugin: {
    plugin_id: string
    name: string
    description: string
    status: string
    step_count: number
  } | null
  manifest?: ChatPluginManifest | null
  sources?: { title: string; url: string }[]
  note?: string
}

/**
 * 高风险工具待批准卡片（后端 0043）：本轮被权限闸拦下的调用，
 * 给出审批单号与「去批准」入口，而不是让用户自己去审批页翻。
 */
/**
 * 一条被风险闸拦下、等人工批准的调用。
 *
 * 服务端可能把它放在 approval 卡的 items 里，也可能附在别的卡片 payload 的
 * approvals 字段上（本轮既产出了方案/文档卡又拦下了工具时，卡片不该被顶掉）。
 */
export interface ChatApprovalItem {
  tool_code: string
  approval_id: string
}

export interface ChatApprovalCard {
  type: 'approval'
  title?: string
  hint?: string
  items: ChatApprovalItem[]
}

/** 对话内生成媒体（v3.73）：图片/视频成品与生成中状态 */
export interface ChatMediaAsset {
  id: string
  kind: 'image' | 'video'
  status: 'awaiting_confirmation' | 'running' | 'ready' | 'failed'
  url: string
  mime: string
  bytes: number
  width?: number | null
  height?: number | null
  duration_seconds?: number | null
  filename: string
  provider?: string | null
  model?: string | null
  error_code?: string | null
  error_message?: string | null
  expires_at?: string | null
  cached?: boolean
  fallback?: boolean
  overlaid?: boolean
}

export interface ChatMediaCard {
  type: 'media'
  kind: 'image' | 'video'
  status: 'awaiting_confirmation' | 'running' | 'ready' | 'failed'
  assets: ChatMediaAsset[]
  prompt?: string
  notice?: string
  hint?: string
  retention_days?: number
}

/** 结构化载荷（助手消息的 payload 字段） */
export type ChatCard =
  | ChatCapabilityCard
  | ChatMarketCard
  | ChatDocumentCard
  | ChatSkillCard
  | ChatPluginCard
  | ChatPlanCard
  | ChatApprovalCard
  | ChatMediaCard

/**
 * 通用卡片组件（ChatCard.vue）接收的形状：技能/插件/方案卡片各自单独渲染，
 * 其中方案卡片必须排除在外——通用卡片按 card.type 取标题，未知类型会渲染成「能力市场」。
 */
export type ChatStandardCard = Exclude<
  ChatCard,
  ChatSkillCard | ChatPluginCard | ChatPlanCard | ChatApprovalCard | ChatMediaCard
>

/** 回答引用的知识/网页来源（v3.7 可信引用） */
export interface ChatCitation {
  document_id?: string
  chunk_id?: string
  title?: string
  source?: string
  page_number?: number | null
  quote?: string
  score?: number
  trust_level?: string
  url?: string
  snippet?: string
  reference?: string
  kind?: 'knowledge' | 'web' | string
}

/** 消息对象；choice/routed_by/reason 仅助手消息携带 */
export interface ChatMessage {
  id: string
  seq?: number
  role: 'user' | 'assistant'
  content: string
  status?: ChatMessageStatus
  choice?: ChatChoice | null
  routed_by?: ChatRoutedBy | null
  /** 后端受控词表下发的徽章文案（v2.0 起优先于本地映射表） */
  routed_label?: string | null
  /** 后端受控词表下发的徽章色调；未知值由 normalizeRoutedTone 收敛 */
  routed_tone?: string | null
  reason?: string | null
  error_code?: string | null
  payload?: ChatCard | null
  steps?: ChatStep[] | null
  attachments?: ChatAttachmentRef[] | null
  citations?: ChatCitation[] | null
  token_usage?: ChatTokenUsage | null
  created_at?: string
}

/** 消息列表响应体：{items}，时间升序 */
export interface ChatMessageList {
  items: ChatMessage[]
}

/** 发送后同步返回的一问一答 */
export interface ChatSendResult {
  user_message: ChatMessage
  assistant_message: ChatMessage
}

/** 创建会话入参：全部可选，缺省 target=auto */
export interface CreateSessionPayload {
  title?: string
  target?: ChatTarget
  target_code?: string
  /** 会话执行模式（v3.51 悬浮伙伴建专属会话时直接指定 commander） */
  execution_mode?: 'standard' | 'commander'
}

/**
 * 发送消息入参扩展（v2.0）：两个字段都可缺省。
 * 缺省时请求体不出现任何新键，后端按现有单聊三级路由处理，行为完全不变。
 */
export interface SendMessageExtras {
  /** @ 点名的助手：可传 id/code/展示名；named 策略必填 */
  target_code?: string | null
  /** 群聊本轮应答策略：不传 = 单聊三级路由 */
  policy?: ChatGroupPolicy | null
}

/** SSE 帧名：与后端协议保持一致，集中一处便于改 */
export const CHAT_SSE_EVENTS = {
  init: 'chat-init',
  step: 'step',
  card: 'card',
  message: 'message',
  error: 'error',
  /** 群聊成员事件帧（v2.0 新增） */
  memberEvent: 'member_event',
  end: 'chat-end',
} as const

/** @ 候选端点路径（后端为集合资源，不挂在会话下） */
export const MENTION_CANDIDATES_PATH = '/chat/mention-candidates'

/** 群聊成员条上限（与后端一致：1..8，超出后端报错，前端也先拦住） */
export const MAX_GROUP_MEMBERS = 8

/** target_code 取值优先级：code > id > display_name/name（契约说这三种后端都认） */
export function mentionTargetCode(c?: MentionCandidate | null): string {
  return c?.code || c?.id || c?.display_name || c?.name || ''
}

/** 展示名：优先用后端派生好的 display_name（private 助手已带「（xx私有）」后缀） */
export function mentionDisplayName(c?: MentionCandidate | null): string {
  return c?.display_name || c?.name || c?.code || c?.id || ''
}

/** DocModel 单块（与后端 docs/model.py 的最小块集同形） */
export interface DocModelBlock {
  type: 'heading' | 'paragraph' | 'bullets' | 'numbered' | 'table' | 'code' | 'pagebreak'
  level?: number
  text?: string
  items?: string[]
}

/** 后端 DocModel 上限：超块数时截断并提示，避免整份导出被 400 拒掉 */
const DOC_MAX_BLOCKS = 300

const MD_HEAVY = /^#{1,6}\s*/
const MD_BULLET = /^[-*+]\s+(.+)$/
const MD_ORDERED = /^\d+[.)]\s+(.+)$/
const MD_INLINE = /\*\*?|`/g

/** 去掉行内 Markdown 标记：导出的 Word/PDF 里不该出现裸的 ** 与 ` */
function planPlainText(line: string): string {
  return line
    .replace(MD_INLINE, '')
    .replace(/\[([^\]]*)\]\(([^)\s]*)\)/g, (_m, text: string, url: string) =>
      text && /^https?:/i.test(url) ? `${text}（${url}）` : text,
    )
    .trim()
}

/**
 * 方案卡片 → DocModel（v3.14 导出）：复用既有文档端点，不新增接口。
 * 分节标题成 heading，列表行归并成 bullets/numbered，其余按段落；
 * 末尾追加「依据来源」把引用编号与出处写进文档，导出的文件也能溯源。
 */
export function planDocModel(
  card: ChatPlanCard,
  fallbackTitle: string,
  citations: ChatCitation[] = [],
): { title: string; blocks: DocModelBlock[] } {
  const title = ((card.title || fallbackTitle).trim() || '方案').slice(0, 100)
  const blocks: DocModelBlock[] = [{ type: 'heading', level: 1, text: title }]
  const push = (block: DocModelBlock) => {
    if (blocks.length < DOC_MAX_BLOCKS) blocks.push(block)
  }
  for (const section of card.sections || []) {
    const heading = planPlainText((section.heading || '').replace(MD_HEAVY, ''))
    if (heading) push({ type: 'heading', level: 2, text: heading.slice(0, 200) })
    let bullets: string[] = []
    let numbered: string[] = []
    const flushLists = () => {
      if (bullets.length) push({ type: 'bullets', items: bullets })
      if (numbered.length) push({ type: 'numbered', items: numbered })
      bullets = []
      numbered = []
    }
    for (const rawLine of (section.body || '').split('\n')) {
      const line = rawLine.trim()
      if (!line) {
        flushLists()
        continue
      }
      const heavy = line.match(MD_HEAVY)
      if (heavy) {
        flushLists()
        const text = planPlainText(line.replace(MD_HEAVY, ''))
        if (text) push({ type: 'heading', level: Math.min(3, heavy[0].length), text })
        continue
      }
      const bullet = line.match(MD_BULLET)
      if (bullet) {
        const text = planPlainText(bullet[1])
        if (text) {
          if (numbered.length) flushLists()
          bullets.push(text)
        }
        continue
      }
      const ordered = line.match(MD_ORDERED)
      if (ordered) {
        const text = planPlainText(ordered[1])
        if (text) {
          if (bullets.length) flushLists()
          numbered.push(text)
        }
        continue
      }
      flushLists()
      const text = planPlainText(line)
      if (text) push({ type: 'paragraph', text })
    }
    flushLists()
  }
  const sources = citations
    .map(c => {
      const label = c.reference ? `[${c.reference}]` : ''
      const name = c.title || c.source || c.url || ''
      const where = c.url && c.url !== name ? `（${c.url}）` : ''
      return planPlainText(`${label} ${name}${where}`.trim())
    })
    .filter(Boolean)
  if (sources.length) {
    push({ type: 'heading', level: 2, text: '依据来源' })
    push({ type: 'bullets', items: sources.slice(0, 200) })
  }
  return { title, blocks }
}

/** 解包成功信封 {data, request_id} -> data */
function unwrap<T>(promise: Promise<AxiosResponse<ApiResponse<T>>>): Promise<T> {
  return promise.then(res => res.data.data)
}

/**
 * 列表载荷形状收敛：后端可能包一层 items / list，也可能直接返回裸数组。
 * 只在这一处兼容，接口上线后若有出入只改这里。
 */
function pickRows<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (payload && typeof payload === 'object') {
    const obj = payload as Record<string, unknown>
    for (const key of ['items', 'list']) {
      if (Array.isArray(obj[key])) return obj[key] as T[]
    }
  }
  return []
}

/** 发送请求体构造：单聊（不传新字段）与群聊/点名共用一条通路 */
export function buildSendBody(
  content: string,
  attachmentIds: string[] = [],
  extras: SendMessageExtras = {},
): Record<string, unknown> {
  const body: Record<string, unknown> = { content, attachment_ids: attachmentIds }
  if (extras.target_code) body.target_code = extras.target_code
  if (extras.policy) body.policy = extras.policy
  return body
}

/** 发送失败由页面做「撤回气泡+保留草稿」处理，屏蔽全局重复 toast */
const silentCfg: AxiosRequestConfig & { skipErrorToast?: boolean } = { skipErrorToast: true }

export const chatApi = {
  /** 会话列表：默认「进行中」（置顶恒在最前）；archived=true 给「已归档」 */
  listSessions(archived = false): Promise<ChatSessionList> {
    return unwrap(
      api.get<ApiResponse<ChatSessionList>>('/chat/sessions', { params: { archived } })
    )
  },
  createSession(payload: CreateSessionPayload = {}): Promise<ChatSession> {
    return unwrap(api.post<ApiResponse<ChatSession>>('/chat/sessions', payload))
  },
  /** 改会话标题（标题由首条用户消息前 20 字自动生成，可再改） */
  renameSession(id: string, title: string): Promise<ChatSession> {
    return unwrap(api.patch<ApiResponse<ChatSession>>(`/chat/sessions/${id}`, { title }))
  },
  /** 三点菜单：局部更新标记位（置顶/归档/未读/授权模式/标题），只传要改的字段 */
  updateSession(id: string, patch: SessionPatch): Promise<ChatSession> {
    return unwrap(api.patch<ApiResponse<ChatSession>>(`/chat/sessions/${id}`, patch))
  },
  /** 批量操作自己的会话；他人或不存在的 id 由服务端静默跳过并计入 missing */
  batchSessions(sessionIds: string[], action: SessionBatchAction): Promise<SessionBatchResult> {
    return unwrap(
      api.post<ApiResponse<SessionBatchResult>>('/chat/sessions/batch', {
        session_ids: sessionIds,
        action,
      })
    )
  },
  /** 分支：把「到这条回答为止」的历史复制成新会话，原会话不受影响 */
  forkSession(id: string, messageId: string): Promise<ChatSession> {
    return unwrap(
      api.post<ApiResponse<ChatSession>>(`/chat/sessions/${id}/fork`, { message_id: messageId })
    )
  },
  deleteSession(id: string): Promise<void> {
    return api.delete<ApiResponse<null>>(`/chat/sessions/${id}`).then(() => undefined)
  },
  /** 历史消息（升序） */
  listMessages(id: string): Promise<ChatMessageList> {
    return unwrap(api.get<ApiResponse<ChatMessageList>>(`/chat/sessions/${id}/messages`))
  },
  /** 发送消息：同步等待，返回一问一答；执行失败也返回 200 + routed_by='error' */
  sendMessage(
    id: string,
    content: string,
    attachmentIds: string[] = [],
    extras: SendMessageExtras = {},
  ): Promise<ChatSendResult> {
    return unwrap(
      api.post<ApiResponse<ChatSendResult>>(
        `/chat/sessions/${id}/messages`,
        buildSendBody(content, attachmentIds, extras),
        silentCfg,
      ),
    )
  },
  /** 上传会话附件（照片/文档）：multipart */
  uploadAttachment(id: string, file: File): Promise<ChatAttachment> {
    const form = new FormData()
    form.append('file', file)
    return unwrap(
      api.post<ApiResponse<ChatAttachment>>(`/chat/sessions/${id}/attachments`, form, {
        ...silentCfg,
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
  },
  /** 会话附件列表 */
  listAttachments(id: string): Promise<ChatAttachmentList> {
    return unwrap(api.get<ApiResponse<ChatAttachmentList>>(`/chat/sessions/${id}/attachments`))
  },
  /** 对话内知识问答（v3.13）：单轮、严格引用，问答消息落库可回放 */
  askKnowledge(
    sessionId: string,
    question: string,
    topK = 5,
  ): Promise<{ user_message: ChatMessage; assistant_message: ChatMessage }> {
    return unwrap(
      api.post<ApiResponse<{ user_message: ChatMessage; assistant_message: ChatMessage }>>(
        `/chat/sessions/${sessionId}/qa`,
        { question, top_k: topK },
        // 检索 + 生成同步返回，慢模型可到 60 秒以上；全局 30 秒会提前掐断，
        // 表现为“页面一直没有回答”，但服务端其实已落库
        { ...silentCfg, timeout: 180_000 },
      ),
    )
  },
  /** 语音转写（v3.11）：音频只在服务端内存中转发上游，不落库 */
  transcribeAudio(file: File): Promise<{ text: string; model: string; engine?: 'local' | 'online' }> {
    const form = new FormData()
    form.append('file', file)
    return unwrap(
      api.post<
        ApiResponse<{ text: string; model: string; engine?: 'local' | 'online' }>
      >('/chat/transcribe', form, {
        ...silentCfg,
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
  },
  /** 图片理解（v3.11，用户手动触发）：结果写回附件文本，随后随消息进入上下文 */
  recognizeImage(
    sessionId: string,
    attachmentId: string,
  ): Promise<{ text: string; chars: number; model: string; attachment: ChatAttachment }> {
    return unwrap(
      api.post<
        ApiResponse<{ text: string; chars: number; model: string; attachment: ChatAttachment }>
      >(`/chat/sessions/${sessionId}/attachments/${attachmentId}/vision`, {}, silentCfg),
    )
  },
  /** 删附件（连带删存储文件） */
  deleteAttachment(id: string, attachmentId: string): Promise<void> {
    return api
      .delete<ApiResponse<null>>(`/chat/sessions/${id}/attachments/${attachmentId}`, silentCfg)
      .then(() => undefined)
  },
  /** 生成文档（DocModel → md/docx/xlsx/pptx/pdf），返回文档卡片与预览/下载地址 */
  createDocument(
    sessionId: string,
    format: string,
    model: Record<string, unknown>,
  ): Promise<ChatDocumentCard> {
    return unwrap(
      api.post<ApiResponse<ChatDocumentCard>>(
        `/chat/sessions/${sessionId}/documents`,
        { format, model },
        silentCfg,
      ),
    )
  },
  /** 在线预览（后端返回自包含 HTML；前端交给无脚本沙箱 iframe 的 srcdoc 渲染） */
  async previewDocument(sessionId: string, attachmentId: string): Promise<string> {
    const res = await api.get<string>(
      `/chat/sessions/${sessionId}/attachments/${attachmentId}/preview`,
      { ...silentCfg, responseType: 'text' },
    )
    return typeof res.data === 'string' ? res.data : String(res.data)
  },
  /** 下载原文件（带登录态，越权 404） */
  async downloadDocument(sessionId: string, attachmentId: string): Promise<Blob> {
    const res = await api.get<Blob>(
      `/chat/sessions/${sessionId}/attachments/${attachmentId}/download`,
      { ...silentCfg, responseType: 'blob' },
    )
    return res.data
  },
  /** 流式发送的完整地址（fetch/SSE 用） */
  streamUrl(id: string): string {
    return apiUrl(`/chat/sessions/${id}/messages/stream`)
  },
  /**
   * @ 点名候选：本小院可见的已发布助手 + 班组。
   * 后端尚未上线：任何失败都只让候选为空（输入区不崩、不弹 toast）。
   */
  async mentionCandidates(): Promise<MentionCandidate[]> {
    try {
      const res = await api.get<ApiResponse<MentionCandidateList>>(
        MENTION_CANDIDATES_PATH,
        silentCfg,
      )
      return pickRows<MentionCandidate>(res.data.data)
    } catch {
      /* 端点不可用时只让候选为空：输入区不崩、不弹 toast */
      return []
    }
  },
  /** 会话群聊成员（按 position 排序；升序由后端保证，前端再排一次防乱序） */
  async listParticipants(sessionId: string): Promise<ChatParticipant[]> {
    const payload = await unwrap<ParticipantListPayload>(
      api.get<ApiResponse<ParticipantListPayload>>(
        `/chat/sessions/${sessionId}/participants`,
        silentCfg,
      ),
    )
    return pickRows<ChatParticipant>(payload).sort(
      (a, b) => (a.position ?? 0) - (b.position ?? 0),
    )
  },
  /** 成员整表覆盖：有序 agent_ids（1..8 位），超出后端报错并走 toast */
  saveParticipants(sessionId: string, agentIds: string[]): Promise<ChatParticipant[]> {
    return unwrap<ParticipantListPayload>(
      api.put<ApiResponse<ParticipantListPayload>>(
        `/chat/sessions/${sessionId}/participants`,
        { agent_ids: agentIds },
        silentCfg,
      ),
    ).then(payload => pickRows<ChatParticipant>(payload))
  },
}

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { compileScript, parse } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as presentation from './presentation.ts'
import * as markdown from './safeMarkdown.ts'
import { createTeamRunTracker } from './teamRunTracker.ts'

const { safeHttpUrl, scheduleError, scheduleIntervalLabel, canExportRun, runStatusLabel, isRunActive, saveDocumentBlob, teamDocumentFilename } = presentation
const run = (id = 'run-1', status = 'queued', extra = {}) => ({
  id, status, team_id: 'deleted-team', team_name: '已删除班组快照', answer: null,
  research: null, output_format: 'text', document_available: false, ...extra,
})
const validSchedule = { enabled: true, interval_minutes: 5, input: '问题', output_format: 'text' }

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function trackerFixture(t, getRun = async () => run()) {
  const states = [], errors = [], connections = [], loading = []
  let closeCount = 0, streamCallbacks
  const tracker = createTeamRunTracker({
    getRun, onRun: value => states.push(value), onError: value => errors.push(value),
    onConnection: value => connections.push(value), onLoading: value => loading.push(value),
    subscribe: (id, refresh, disconnected) => {
      streamCallbacks = { id, refresh, disconnected }
      return { close: () => { closeCount += 1 } }
    },
  })
  t.after(() => tracker.stop())
  return { tracker, states, errors, connections, loading, get closeCount() { return closeCount }, get callbacks() { return streamCallbacks } }
}

// 在 Node 内编译真实 Vue SFC 并 SSR，不启动或占用浏览器，也不引入新测试依赖。
function component(filename, dependencies = {}) {
  const source = readFileSync(new URL(filename, import.meta.url), 'utf8')
  const { descriptor } = parse(source, { filename })
  const compiled = compileScript(descriptor, { id: filename, inlineTemplate: true })
  const code = ts.transpileModule(compiled.content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const exports = {}
  const modules = { vue: Vue, './presentation': presentation, './safeMarkdown': markdown, ...dependencies }
  new Function('require', 'exports', code)(id => {
    assert.ok(id in modules, `未提供测试依赖：${id}`)
    return modules[id]
  }, exports)
  return exports.default
}
const SafeMarkdown = component('./SafeMarkdown.vue')
const TeamRunResult = component('./TeamRunResult.vue', {
  '@/api/modules': { teamApi: {} }, './SafeMarkdown.vue': { default: SafeMarkdown },
})
async function render(component, props) {
  const app = Vue.createSSRApp(component, props)
  for (const name of ['el-card', 'el-button', 'el-alert']) {
    app.component(name, Vue.defineComponent({
      inheritAttrs: false,
      setup(_, { attrs, slots }) {
        return () => Vue.h('div', attrs, [slots.header?.(), slots.default?.()])
      },
    }))
  }
  return renderToString(app)
}

test('queued 与 running 均为活动状态，终态不继续追踪', () => {
  assert.equal(isRunActive('queued'), true)
  assert.equal(isRunActive('running'), true)
  assert.equal(isRunActive('failed'), false)
  assert.equal(isRunActive('completed'), false)
  assert.equal(runStatusLabel('queued'), '排队中')
})

test('所有 completed 非空答案均可导出，不依赖创建格式或 document_available', () => {
  assert.equal(canExportRun(run('r', 'completed', { answer: '# 历史答案' })), true)
  for (const status of ['queued', 'running', 'failed']) assert.equal(canExportRun(run('r', status, { answer: '部分输出' })), false)
  for (const answer of [null, '', ' \n ']) assert.equal(canExportRun(run('r', 'completed', { answer })), false)
})

test('定时间隔下界上界合法，越界、空值与小数拒绝', () => {
  for (const interval_minutes of [5, 60, 43200]) assert.equal(scheduleError({ ...validSchedule, interval_minutes }), '')
  for (const interval_minutes of [4, 43201, 5.5, 0, null, undefined, NaN]) assert.match(scheduleError({ ...validSchedule, interval_minutes }), /整数分钟/)
})

test('卡片周期按天/小时/分钟可读展示，默认 1440 分钟显示为每天', () => {
  assert.equal(scheduleIntervalLabel(1440), '每 1 天')
  assert.equal(scheduleIntervalLabel(2880), '每 2 天')
  assert.equal(scheduleIntervalLabel(120), '每 2 小时')
  assert.equal(scheduleIntervalLabel(90), '每 90 分钟')
  assert.equal(scheduleIntervalLabel(5), '每 5 分钟')
})

test('启用需问题，关闭允许空问题，限制长度与格式', () => {
  assert.match(scheduleError({ ...validSchedule, input: '  ' }), /填写/)
  assert.equal(scheduleError({ ...validSchedule, enabled: false, input: '' }), '')
  assert.match(scheduleError({ ...validSchedule, input: '字'.repeat(8001) }), /8000/)
  assert.match(scheduleError({ ...validSchedule, output_format: 'pdf' }), /有效/)
})

test('引用仅允许 http(s)，拦截协议混淆与控制字符', () => {
  assert.equal(safeHttpUrl('https://example.org/path?q=1'), 'https://example.org/path?q=1')
  assert.equal(safeHttpUrl('HTTP://example.org'), 'http://example.org/')
  for (const value of [null, '', 'javascript:alert(1)', 'data:text/html,x', '//evil.test', '/relative', ' https://evil.test', 'https://exa\nmple.org', 'https://']) {
    assert.equal(safeHttpUrl(value), undefined, String(value))
  }
})

test('Markdown 支持标题、强调、表格、代码、列表与引用', () => {
  const blocks = markdown.parseMarkdown('# 标题\n\n**重点**\n\n| 列 | 值 |\n| --- | --- |\n| a | b |\n\n1. 一\n2. 二\n\n> 引用\n\n```js\n<x>\n```')
  assert.deepEqual(blocks.map(item => item.kind), ['heading', 'paragraph', 'table', 'list', 'quote', 'code'])
  assert.deepEqual(blocks[3].items, ['一', '二'])
  assert.equal(blocks[3].ordered, true)
  assert.equal(blocks[5].text, '<x>')
  assert.equal(markdown.parseInline('**重点**')[0].kind, 'strong')
  assert.deepEqual(markdown.parseMarkdown(''), [])
  assert.equal(markdown.parseMarkdown('```\n未闭合')[0].text, '未闭合')
})

test('实际 Markdown 组件转义 HTML，链接携带 noopener，脚本链接不渲染', async () => {
  const html = await render(SafeMarkdown, { content: '# 标题\n\n<script>alert(1)</script>\n\n[安全](https://example.org) [危险](javascript:alert)\n\n**结论**' })
  assert.match(html, /<h1>标题<\/h1>/)
  assert.match(html, /&lt;script&gt;/)
  assert.doesNotMatch(html, /<script>|href="javascript:/)
  assert.match(html, /rel="noopener noreferrer"/)
  assert.match(html, /<strong>结论<\/strong>/)
})

test('实际结果组件显示历史答案、双格式导出与无证据警告', async () => {
  const html = await render(TeamRunResult, { run: run('r', 'completed', { answer: '## 历史答案' }) })
  assert.match(html, /<h2>历史答案<\/h2>/)
  assert.match(html, /下载 Markdown/)
  assert.match(html, /下载 Word/)
  assert.match(html, /未返回知识库引用/)
  assert.match(html, /联网服务离线/)
})

test('实际结果组件分别展示知识/联网引用及警告，拒绝不安全来源', async () => {
  const html = await render(TeamRunResult, { run: run('r', 'completed', {
    answer: '结论', output_format: 'docx', research: {
      knowledge: [{ document_id: 'd', chunk_id: 'c', title: '技术手册', source: '资料', page_number: 2, quote: '<img onerror=alert(1)>', score: 0.9, trust_level: 'verified' }],
      web: [{ title: '网页', url: 'https://example.org', snippet: '搜索摘要' }, { title: '恶意链接', url: 'javascript:alert(1)', snippet: '' }],
      warnings: ['联网检索部分服务离线'],
    },
  }) })
  assert.match(html, /文档结果已就绪/)
  assert.match(html, /技术手册/)
  assert.match(html, /第 2 页/)
  assert.match(html, /联网检索部分服务离线/)
  assert.match(html, /链接不可访问/)
  assert.doesNotMatch(html, /<img|href="javascript:/)
})

test('未完成与空答案不显示下载按钮，文档结果等待提示正确', async () => {
  const html = await render(TeamRunResult, { run: run('r', 'queued', { output_format: 'md' }) })
  assert.match(html, /运行完成并生成答案后可下载/)
  assert.doesNotMatch(html, /下载 Markdown/)
  const empty = await render(TeamRunResult, { run: run('r', 'completed') })
  assert.match(empty, /未返回答案/)
})

test('排队打开即订阅，刷新经过 running 到 completed 后关闭观察流', async t => {
  let next = run('r', 'running')
  const f = trackerFixture(t, async () => next)
  f.tracker.open(run('r'))
  assert.equal(f.callbacks.id, 'r')
  await f.tracker.refresh()
  assert.equal(f.states.at(-1).status, 'running')
  next = run('r', 'completed', { answer: '完整答案' })
  await f.tracker.refresh()
  assert.equal(f.states.at(-1).answer, '完整答案')
  assert.equal(f.closeCount, 1)
})

test('排队轮询五秒刷新且终态后停止', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let calls = 0
  const f = trackerFixture(t, async () => { calls += 1; return run('r', 'failed') })
  f.tracker.open(run('r'))
  t.mock.timers.tick(5000)
  await f.tracker.refresh()
  assert.equal(calls, 1)
  assert.equal(f.states.at(-1).status, 'failed')
  t.mock.timers.tick(10000)
  assert.equal(calls, 1)
})

test('重复刷新合并请求，不产生重叠轮询', async t => {
  const request = deferred()
  let calls = 0
  const f = trackerFixture(t, () => { calls += 1; return request.promise })
  f.tracker.open(run())
  const first = f.tracker.refresh()
  const second = f.tracker.refresh()
  assert.equal(first, second)
  assert.equal(calls, 1)
  request.resolve(run('run-1', 'completed'))
  await first
})

test('切换历史记录后旧响应不得覆盖新记录', async t => {
  const old = deferred()
  const f = trackerFixture(t, id => id === 'old' ? old.promise : Promise.resolve(run(id, 'completed')))
  const oldOpen = f.tracker.openId('old')
  await f.tracker.openId('new')
  old.resolve(run('old', 'failed'))
  await oldOpen
  assert.deepEqual(f.states.map(value => value.id), ['new'])
})

test('关闭抽屉不回写进行中的请求，重新按 run ID 打开恢复', async t => {
  const request = deferred()
  let fresh = false
  const f = trackerFixture(t, id => fresh ? Promise.resolve(run(id, 'completed')) : request.promise)
  f.tracker.open(run('r'))
  const refresh = f.tracker.refresh()
  f.tracker.stop()
  request.resolve(run('r', 'running'))
  await refresh
  assert.equal(f.states.length, 1)
  fresh = true
  await f.tracker.openId('r')
  assert.equal(f.states.at(-1).status, 'completed')
})

test('断线与请求失败明确展示，可手动恢复；旧 SSE 通知被忽略', async t => {
  let fail = true
  const f = trackerFixture(t, async () => { if (fail) throw new Error('offline'); return run('r', 'completed') })
  f.tracker.open(run('r'))
  const stale = f.callbacks
  stale.disconnected()
  assert.match(f.connections.at(-1), /已断开/)
  await f.tracker.refresh()
  assert.match(f.errors.at(-1), /刷新重试/)
  fail = false
  await f.tracker.refresh()
  assert.equal(f.errors.at(-1), '')
  f.tracker.stop()
  const count = f.connections.length
  stale.disconnected()
  assert.equal(f.connections.length, count)
})

test('详情首次失败仍可刷新，不需要 teams 中存在该班组', async t => {
  let fail = true
  const f = trackerFixture(t, async id => { if (fail) throw new Error('offline'); return run(id, 'completed') })
  await f.tracker.openId('deleted-run')
  assert.match(f.errors.at(-1), /加载失败/)
  fail = false
  await f.tracker.refresh()
  assert.equal(f.states.at(-1).team_name, '已删除班组快照')
})

test('下载使用 Blob 临时链接并按惯例延迟释放，文件名清理非法字符', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let clicked = false, removed = false, revoked = ''
  const anchor = { click() { clicked = true }, remove() { removed = true } }
  const prior = globalThis.document
  globalThis.document = { createElement: () => anchor, body: { appendChild() {} } }
  t.after(() => { if (prior === undefined) delete globalThis.document; else globalThis.document = prior })
  t.mock.method(URL, 'createObjectURL', () => 'blob:unit')
  t.mock.method(URL, 'revokeObjectURL', value => { revoked = value })
  const filename = teamDocumentFilename('班/组\n', 'r', 'docx')
  saveDocumentBlob(new Blob(['真实文档内容']), filename)
  assert.equal(filename, '班_组_-r.docx')
  assert.equal(anchor.href, 'blob:unit')
  assert.equal(anchor.download, filename)
  assert.equal(clicked, true)
  assert.equal(removed, true)
  assert.equal(revoked, '')
  t.mock.timers.tick(1000)
  assert.equal(revoked, 'blob:unit')
})

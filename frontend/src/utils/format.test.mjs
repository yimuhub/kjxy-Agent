import assert from 'node:assert/strict'
import { test } from 'node:test'
import { apiTimeMs, parseApiTime, formatDateTime, formatTime, formatDuration } from './format.ts'

const pad = n => String(n).padStart(2, '0')
/** 用同一时刻的本地字段推出期望值：断言不依赖机器时区 */
function localStamp(ms) {
  const d = new Date(ms)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

test('裸 UTC 时间戳按 UTC 解析（修复国内整体早 8 小时）', () => {
  assert.equal(parseApiTime('2026-10-28T10:11:13.194318').getTime(), Date.UTC(2026, 9, 28, 10, 11, 13, 194))
})

test('已带时区的时间戳原样解析，不重复补 Z', () => {
  assert.equal(parseApiTime('2026-10-28T10:11:13Z').getTime(), Date.UTC(2026, 9, 28, 10, 11, 13))
  assert.equal(parseApiTime('2026-10-28T18:11:13+08:00').getTime(), Date.UTC(2026, 9, 28, 10, 11, 13))
})

test('纯日期保持 UTC 零点（补 Z 会让它解析失败）', () => {
  assert.equal(parseApiTime('2026-10-28').getTime(), Date.UTC(2026, 9, 28))
})

test('缺失或非法时间返回 null', () => {
  assert.equal(parseApiTime(null), null)
  assert.equal(parseApiTime('   '), null)
  assert.equal(parseApiTime('not-a-date'), null)
})

test('会话列表时间格式为年-月-日 时:分', () => {
  const ms = Date.UTC(2026, 9, 28, 10, 11)
  assert.equal(formatDateTime('2026-10-28T10:11:13.194318'), localStamp(ms))
  assert.match(formatDateTime('2026-10-28T10:11:13.194318'), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
  assert.equal(formatDateTime(null), '—')
  assert.equal(formatTime(''), '—')
})

test('耗时计算同样按 UTC 解析起止时间', () => {
  assert.equal(formatDuration('2026-10-28T10:11:00', '2026-10-28T10:11:12'), '12.0s')
  assert.equal(formatDuration('2026-10-28T10:11:00', null), '—')
})

test('apiTimeMs 与 parseApiTime 同口径：列表排序键就靠它', () => {
  assert.equal(apiTimeMs('2026-10-28T10:11:13.194318'), Date.UTC(2026, 9, 28, 10, 11, 13, 194))
  assert.equal(apiTimeMs('2026-10-28T10:11:13Z'), Date.UTC(2026, 9, 28, 10, 11, 13))
  assert.equal(apiTimeMs('2026-10-28'), Date.UTC(2026, 9, 28))
  assert.equal(apiTimeMs(null), 0)
  assert.equal(apiTimeMs('not-a-date'), 0)
  // 带缓存：第二次取的是缓存值，必须和第一次一致，否则排序会随调用次数漂移
  assert.equal(apiTimeMs('2026-10-28T10:11:13.194318'), apiTimeMs('2026-10-28T10:11:13.194318'))
})

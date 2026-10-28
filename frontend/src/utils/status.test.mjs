import assert from 'node:assert/strict'
import { test } from 'node:test'
import { classifyResponseError, friendlyErrorMessage } from './status.ts'

test('统一错误信封按后端错误码归类', () => {
  const err = classifyResponseError(409, { error: { code: 'CONFLICT', message: '工单已存在' } })
  assert.equal(err.code, 'CONFLICT')
  assert.equal(err.message, '工单已存在')
})

test('FastAPI 默认 detail 体按 HTTP 状态归类', () => {
  const err = classifyResponseError(405, { detail: 'Method Not Allowed' })
  assert.equal(err.code, 'HTTP_405')
  assert.equal(err.message, 'Method Not Allowed')
})

test('有响应但没有错误体时不再误报「网络连接失败」', () => {
  const err = classifyResponseError(404, undefined)
  assert.equal(err.code, 'HTTP_404')
  assert.notEqual(err.code, 'NETWORK_ERROR')
})

test('完全没有响应才算真的断网', () => {
  assert.equal(classifyResponseError(undefined, undefined).code, 'NETWORK_ERROR')
})

test('405 提示重启后端，而不是让人去检查网络', () => {
  const msg = friendlyErrorMessage('HTTP_405', 'Method Not Allowed')
  assert.match(msg, /重启后端/)
  assert.doesNotMatch(msg, /检查网络/)
})

test('后端自带中文说明优先于通用状态文案', () => {
  assert.equal(friendlyErrorMessage('HTTP_409', '工单已存在'), '工单已存在')
})

test('错误码映射优先于英文原文', () => {
  assert.equal(friendlyErrorMessage('UNAUTHORIZED', 'Unauthorized'), '登录状态已失效，请重新登录')
})

test('既无错误码也无中文时给通用兜底文案', () => {
  assert.equal(friendlyErrorMessage('HTTP_599', ''), '操作失败，请稍后重试；若反复失败请联系管理员')
})

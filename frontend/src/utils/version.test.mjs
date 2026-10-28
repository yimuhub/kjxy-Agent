import assert from 'node:assert/strict'
import { test } from 'node:test'
import { compareVersions, versionRelation } from './version.ts'

test('按语义化版本逐段比较', () => {
  assert.equal(compareVersions('3.21.0', '3.20.0'), 1)
  assert.equal(compareVersions('3.20.0', '3.21.0'), -1)
  assert.equal(compareVersions('3.21.0', '3.21.0'), 0)
  assert.equal(compareVersions('3.21.10', '3.21.9'), 1)
})

test('带预发布后缀时只看主版本段', () => {
  assert.equal(compareVersions('3.21.0-rc.1', '3.21.0'), 0)
})

test('无法识别的版本返回 null，不误判', () => {
  assert.equal(compareVersions('dev', '3.21.0'), null)
  assert.equal(versionRelation('unknown', '3.21.0'), 'unknown')
})

test('后端落后/领先/一致的判定', () => {
  assert.equal(versionRelation('3.20.0', '3.21.0'), 'backend_behind')
  assert.equal(versionRelation('3.22.0', '3.21.0'), 'backend_ahead')
  assert.equal(versionRelation('3.21.0', '3.21.0'), 'equal')
})

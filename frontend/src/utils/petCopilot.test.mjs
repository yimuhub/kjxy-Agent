import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  greetingFor,
  isPetMood,
  isPetScene,
  sceneForRoute,
  sceneTip,
} from './petCopilot.ts'

test('路由名映射到伙伴场景，未知路由回到工作台', () => {
  assert.equal(sceneForRoute('Farm'), 'farm')
  assert.equal(sceneForRoute('Diagnosis'), 'farm')
  assert.equal(sceneForRoute('Commander'), 'commander')
  assert.equal(sceneForRoute('Settings'), 'settings')
  assert.equal(sceneForRoute('Pet'), 'general')
  assert.equal(sceneForRoute(undefined), 'general')
})

test('每个场景都有可读提示，农业场景能落到地块', () => {
  assert.match(sceneTip('farm'), /地块|农事/)
  assert.match(sceneTip('commander'), /计划/)
  assert.match(sceneTip('general'), /今天想先做哪件事/)
})

test('问候随时段变化，节气只在临近时出现', () => {
  const morning = greetingFor(new Date(2026, 10, 2, 8, 0))
  assert.ok(morning.startsWith('早上好'))
  assert.ok(morning.includes('立冬将近'))

  const lateNight = greetingFor(new Date(2026, 10, 2, 2, 0))
  assert.ok(lateNight.startsWith('夜深了'))
})

test('固定节日优先于普通时段问候', () => {
  const nationalDay = greetingFor(new Date(2026, 9, 1, 10, 0))
  assert.ok(nationalDay.startsWith('国庆节快乐'))
})

test('状态与场景白名单拒绝未知值', () => {
  assert.equal(isPetMood('busy'), true)
  assert.equal(isPetMood('angry'), false)
  assert.equal(isPetScene('farm'), true)
  assert.equal(isPetScene('warehouse'), false)
})

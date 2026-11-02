import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  PET_OVERLAY_MARGIN,
  clampPetPos,
  defaultPetPos,
} from './petOverlay.ts'

test('悬浮伙伴位置被夹在视口内，任何时刻都完整可见', () => {
  assert.deepEqual(clampPetPos(-200, -200, 1200, 800), {
    x: PET_OVERLAY_MARGIN,
    y: PET_OVERLAY_MARGIN,
  })
  assert.deepEqual(clampPetPos(99999, 99999, 1200, 800), { x: 1200 - 76 - 12, y: 800 - 76 - 12 })
})

test('非法坐标回退到右下边界，不会把伙伴甩出屏幕', () => {
  const clamped = clampPetPos(Number.NaN, undefined, 1000, 700)
  assert.equal(clamped.x, 1000 - 76 - 12)
  assert.equal(clamped.y, 700 - 76 - 12)
})

test('默认落位在屏幕右下角', () => {
  const pos = defaultPetPos(1440, 900)
  assert.ok(pos.x > 1440 - 200)
  assert.ok(pos.y > 900 - 200)
  assert.ok(pos.x + 76 <= 1440)
  assert.ok(pos.y + 76 <= 900)
})

test('极小视口也不会出现负坐标', () => {
  const pos = clampPetPos(500, 500, 120, 90)
  assert.ok(pos.x >= 0)
  assert.ok(pos.y >= 0)
})

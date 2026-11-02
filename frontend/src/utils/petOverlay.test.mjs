import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  PET_OVERLAY_MARGIN,
  PET_PANEL_MAX,
  PET_PANEL_MIN,
  clampPetPos,
  overlapsComposerDock,
  clampPetRect,
  defaultPetPanelRect,
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

test('面板尺寸夹在最小与最大之间，不会缩到不可用或撑破屏幕', () => {
  const shrunk = clampPetRect({ x: 100, y: 100, width: 100, height: 100 }, 1200, 800)
  assert.deepEqual(
    { width: shrunk.width, height: shrunk.height },
    { width: PET_PANEL_MIN.width, height: PET_PANEL_MIN.height },
  )

  const grown = clampPetRect({ x: 100, y: 100, width: 9999, height: 9999 }, 1200, 800)
  assert.equal(grown.width, Math.min(PET_PANEL_MAX.width, 1200 - PET_OVERLAY_MARGIN * 2))
  assert.equal(grown.height, Math.min(PET_PANEL_MAX.height, 800 - PET_OVERLAY_MARGIN * 2))
})

test('面板坐标被夹进视口，任何尺寸下都完整可见', () => {
  const off = clampPetRect({ x: 9999, y: 9999, width: 400, height: 400 }, 1200, 800)
  assert.equal(off.x, 1200 - 400 - PET_OVERLAY_MARGIN)
  assert.equal(off.y, 800 - 400 - PET_OVERLAY_MARGIN)

  const negative = clampPetRect({ x: -800, y: -800, width: 400, height: 400 }, 1200, 800)
  assert.deepEqual({ x: negative.x, y: negative.y }, { x: PET_OVERLAY_MARGIN, y: PET_OVERLAY_MARGIN })
})

test('损坏的坐标与宽高回退到默认，不会渲染出 0 尺寸面板', () => {
  const recovered = clampPetRect(
    { x: Number.NaN, y: undefined, width: Number.NaN, height: undefined },
    1000,
    700,
  )
  assert.deepEqual(recovered, {
    x: PET_OVERLAY_MARGIN,
    y: PET_OVERLAY_MARGIN,
    width: PET_PANEL_MIN.width,
    height: PET_PANEL_MIN.height,
  })
})

test('默认面板贴在头像左上方，且整块落在视口内', () => {
  const anchor = defaultPetPos(1440, 900)
  const rect = defaultPetPanelRect(anchor, 1440, 900)
  assert.deepEqual({ width: rect.width, height: rect.height }, { width: 380, height: 540 })
  assert.ok(rect.y + rect.height <= anchor.y + 20, '面板应贴在头像上方')
  assert.ok(rect.x >= 0)
  assert.ok(rect.x + rect.width <= 1440)
})

test('小屏幕放不下时，默认面板收回视口并保留最小可用尺寸', () => {
  const rect = defaultPetPanelRect(defaultPetPos(400, 500), 400, 500)
  assert.deepEqual({ x: rect.x, y: rect.y }, { x: PET_OVERLAY_MARGIN, y: PET_OVERLAY_MARGIN })
  assert.ok(rect.width >= PET_PANEL_MIN.width)
  assert.ok(rect.x + rect.width <= 400)
  assert.ok(rect.y + rect.height <= 500)
})

test('伙伴停在右下角时判定为占住输入条，拖开后立刻解除', () => {
  // 1440×900 下默认落点的真实矩形（头像含名称标签）
  assert.equal(overlapsComposerDock({ x: 1335, y: 800, width: 86, height: 127 }, 1440, 900), true)
  // 拖到左上、左下、右上都不该让页面输入条白白收窄
  assert.equal(overlapsComposerDock({ x: 200, y: 200, width: 86, height: 127 }, 1440, 900), false)
  assert.equal(overlapsComposerDock({ x: 20, y: 761, width: 86, height: 127 }, 1440, 900), false)
  assert.equal(overlapsComposerDock({ x: 1335, y: 12, width: 86, height: 127 }, 1440, 900), false)
  // 只压进让位区一条边也算占住
  assert.equal(overlapsComposerDock({ x: 1200, y: 740, width: 86, height: 20 }, 1440, 900), true)
})

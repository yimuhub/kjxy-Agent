import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  alertLine,
  overdueOrderLine,
  weatherLine,
  workOrderLine,
  workOrderPriorityRank,
} from './petBriefLines.ts'

test('未配置天气或字段缺失时不产句', () => {
  assert.equal(weatherLine({ configured: false, brief: null, warning: '未配置' }), '')
  assert.equal(
    weatherLine({
      configured: true,
      brief: {
        temp_max: null,
        temp_min: null,
        rain_mm: null,
        wind_max_ms: null,
        humidity_mean: null,
        tomorrow_temp_min: null,
        generated_at: null,
      },
      warning: null,
    }),
    '',
  )
})

test('有雨天气提到带伞，低温提到保暖', () => {
  const line = weatherLine({
    configured: true,
    brief: {
      temp_max: 22.34,
      temp_min: 12.11,
      rain_mm: 3.26,
      wind_max_ms: 5.1,
      humidity_mean: 68.5,
      tomorrow_temp_min: 3.9,
      generated_at: null,
    },
    warning: null,
  })
  assert.match(line, /12\.1~22\.3℃/)
  assert.match(line, /3\.3mm/)
  assert.match(line, /带伞/)
  assert.match(line, /明天最低3\.9℃/)
})

test('晴朗天气说基本无雨，暖和就不提保暖', () => {
  const line = weatherLine({
    configured: true,
    brief: {
      temp_max: 24,
      temp_min: 15,
      rain_mm: 0,
      wind_max_ms: 2,
      humidity_mean: 50,
      tomorrow_temp_min: 14,
      generated_at: null,
    },
    warning: null,
  })
  assert.match(line, /基本无雨/)
  assert.doesNotMatch(line, /保暖/)
})

const order = (priority, title, status = 'open') => ({
  work_order_id: 'w1',
  title,
  description: '',
  priority,
  status,
  source: 'manual',
  source_ref: null,
  plot_id: null,
  assignee_id: null,
  assignee_name: '',
  due_on: null,
})

test('工单按紧急度取最急的一张说话', () => {
  assert.equal(workOrderLine([]), '')
  const one = workOrderLine([order('high', '水泵检修')])
  assert.match(one, /1 张工单/)
  assert.match(one, /急的/)
  assert.match(one, /水泵检修/)
  const many = workOrderLine([order('critical', '大棚漏雨'), order('low', '补苗')])
  assert.match(many, /2 张工单/)
  assert.match(many, /大棚漏雨/)
})

test('紧急警报换语气置顶，一般警报温和提醒', () => {
  const alert = (severity, title) => ({
    alert_id: 'a1',
    title,
    detail: '',
    severity,
    source: 'device',
    plot_id: null,
    status: 'new',
    work_order_id: null,
    created_at: '',
  })
  assert.equal(alertLine([]), '')
  assert.match(alertLine([alert('normal', '土壤偏干')]), /1 条未处理/)
  assert.match(alertLine([alert('critical', '大棚温度过高')]), /⚠/)
  assert.match(alertLine([alert('critical', '大棚温度过高')]), /大棚温度过高/)
})

test('紧急度排序权重 critical 最先', () => {
  assert.equal(workOrderPriorityRank('critical'), 0)
  assert.equal(workOrderPriorityRank('high'), 1)
  assert.equal(workOrderPriorityRank('unknown'), 4)
})

test('逾期工单单独提醒，未到期不产句', () => {
  const orders = [
    { due_on: '2026-11-01', status: 'in_progress', title: '大棚检修' },
    { due_on: '2026-11-05', status: 'open', title: '补苗' },
    { due_on: '2026-11-20', status: 'assigned', title: '巡田' },
    { due_on: '2026-11-01', status: 'done', title: '已闭环不算逾期' },
  ]
  const line = overdueOrderLine(orders, '2026-11-07')
  assert.match(line, /2 张工单已逾期/)
  assert.match(line, /大棚检修/)
  assert.equal(overdueOrderLine(orders, '2026-11-01'), '')
  const single = overdueOrderLine([{ due_on: '2026-11-01', status: 'open', title: '追肥' }], '2026-11-07')
  assert.match(single, /1 张工单已逾期/)
  assert.match(single, /追肥/)
})

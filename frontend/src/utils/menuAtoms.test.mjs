import assert from 'node:assert/strict'
import { test } from 'node:test'
import { collectMenuKeys, surfaceMissingAtoms } from './menuAtoms.ts'

const atoms = [
  { key: 'chat', title: '智能对话', iconName: 'ChatDotRound', path: '/chat' },
  { key: 'commander', title: '指挥官工作台', iconName: 'Compass', path: '/commander' },
  { key: 'cluster', title: '集群节点', iconName: 'Connection', path: '/settings' },
]

const defaultGroups = [
  {
    id: 'grp-workbench',
    label: '工作台',
    items: [
      { key: 'chat', title: '智能对话', iconName: 'ChatDotRound', path: '/chat' },
      { key: 'commander', title: '指挥官工作台', iconName: 'Compass', path: '/commander' },
    ],
  },
  {
    id: 'grp-admin',
    label: '管理',
    items: [
      {
        key: 'settings',
        title: '系统设置',
        iconName: 'Setting',
        children: [{ key: 'cluster', title: '集群节点', iconName: 'Connection', path: '/settings' }],
      },
    ],
  },
]

test('老缓存布局缺失的新入口按默认布局位置补回', () => {
  // 3.32 时代的缓存：只有对话，没有指挥官工作台，也没有系统设置里的集群节点
  const cached = [
    {
      id: 'grp-workbench',
      label: '工作台',
      items: [{ key: 'chat', title: '智能对话', iconName: 'ChatDotRound', path: '/chat' }],
    },
    {
      id: 'grp-admin',
      label: '管理',
      items: [{ key: 'settings', title: '系统设置', iconName: 'Setting', children: [] }],
    },
  ]

  const result = surfaceMissingAtoms(cached, atoms, defaultGroups, [])

  assert.deepEqual(result.added, ['commander', 'cluster'])
  assert.deepEqual(collectMenuKeys(result.groups), ['chat', 'commander', 'settings', 'cluster'])
  // 一级项落回原分组，二级子项落回父折叠夹
  assert.equal(result.groups[0].items[1].key, 'commander')
  assert.equal(result.groups[1].items[0].children[0].key, 'cluster')
})

test('补齐过的入口被用户再次隐藏后不会反复弹出', () => {
  const cached = [
    {
      id: 'grp-workbench',
      label: '工作台',
      items: [{ key: 'chat', title: '智能对话', iconName: 'ChatDotRound', path: '/chat' }],
    },
  ]
  const first = surfaceMissingAtoms(cached, atoms, defaultGroups, [])
  assert.ok(first.added.includes('commander'))

  // 用户把指挥官工作台从菜单里拖掉了，seen 标记仍记得它
  const hidden = [
    {
      id: 'grp-workbench',
      label: '工作台',
      items: [{ key: 'chat', title: '智能对话', iconName: 'ChatDotRound', path: '/chat' }],
    },
  ]
  const second = surfaceMissingAtoms(hidden, atoms, defaultGroups, first.seen)
  assert.deepEqual(second.added, [])
  assert.ok(second.seen.includes('commander'))
})

test('默认布局里没有的原子落到最后一个分组，不会丢失', () => {
  const cached = [
    { id: 'a', label: 'A', items: [{ key: 'chat', title: '对话', iconName: 'x', path: '/chat' }] },
    { id: 'b', label: 'B', items: [] },
  ]
  const result = surfaceMissingAtoms(
    cached,
    [{ key: 'orphan', title: '孤儿功能', iconName: 'x', path: '/orphan' }],
    defaultGroups,
    [],
  )
  assert.deepEqual(result.added, ['orphan'])
  assert.equal(result.groups[1].items[0].key, 'orphan')
})

/**
 * 自定义菜单缓存的新功能补齐：
 * 菜单 DIY 布局存在 localStorage 且本地优先，老缓存里没有的新菜单原子
 * （如 3.39 的指挥官工作台）会被永久隐藏，这里按默认布局的位置补回来一次；
 * 用户再次手动隐藏后由 seen 标记记住，不再反复补。
 */

export interface MenuAtomShape {
  key: string
  title: string
  iconName: string
  path: string
  roles?: string[]
  permission?: string
  locked?: boolean
}

export interface MenuNodeShape {
  key: string
  title: string
  iconName: string
  path?: string
  children?: MenuAtomShape[]
}

export interface MenuGroupShape {
  id: string
  label: string
  items: MenuNodeShape[]
}

export interface SurfaceResult<G> {
  groups: G[]
  added: string[]
  seen: string[]
}

/** 布局中出现过的全部 key（含二级子项） */
export function collectMenuKeys(groups: MenuGroupShape[]): string[] {
  const keys: string[] = []
  for (const group of groups) {
    for (const node of group.items) {
      keys.push(node.key)
      for (const child of node.children ?? []) keys.push(child.key)
    }
  }
  return keys
}

function cloneGroups<G extends MenuGroupShape>(groups: G[]): G[] {
  return JSON.parse(JSON.stringify(groups)) as G[]
}

/** 在默认布局里找到该 key 的落点：一级项归组，二级子项归到父折叠夹 */
function locate(
  groups: MenuGroupShape[],
  key: string,
): { group: MenuGroupShape; parent?: MenuNodeShape } | null {
  for (const group of groups) {
    for (const node of group.items) {
      if (node.key === key) return { group }
      if (node.children?.some(child => child.key === key)) return { group, parent: node }
    }
  }
  return null
}

/**
 * 把 atoms 中「缓存布局没有、且从未补齐过」的项补回布局。
 * @param groups 当前本地缓存布局
 * @param atoms 全量功能原子池
 * @param defaultGroups 默认布局（决定补齐后落在哪个分组）
 * @param seenKeys 已补齐过的 key（含用户后来主动隐藏的）
 */
export function surfaceMissingAtoms<G extends MenuGroupShape, A extends MenuAtomShape>(
  groups: G[],
  atoms: A[],
  defaultGroups: G[],
  seenKeys: Iterable<string>,
): SurfaceResult<G> {
  const next = cloneGroups(groups)
  const seen = new Set(seenKeys)
  const present = new Set(collectMenuKeys(next))
  const added: string[] = []

  for (const atom of atoms) {
    if (present.has(atom.key) || seen.has(atom.key)) continue
    const node: MenuAtomShape = {
      key: atom.key,
      title: atom.title,
      iconName: atom.iconName,
      path: atom.path,
      roles: atom.roles,
      permission: atom.permission,
      locked: atom.locked,
    }
    const place = locate(defaultGroups, atom.key)
    const target = place?.group
      ? next.find(group => group.id === place.group.id)
      : next[next.length - 1]
    const parent = place?.parent
      ? next.flatMap(group => group.items).find(item => item.key === place.parent?.key)
      : undefined
    if (parent) {
      parent.children = parent.children ?? []
      parent.children.push(node as unknown as (typeof parent.children)[number])
    } else if (target) {
      target.items.push(node as unknown as (typeof target.items)[number])
    } else {
      next[next.length - 1]?.items.push(node as unknown as (typeof next)[number]['items'][number])
    }
    present.add(atom.key)
    added.push(atom.key)
  }

  return { groups: next, added, seen: [...new Set([...seen, ...collectMenuKeys(next)])] }
}

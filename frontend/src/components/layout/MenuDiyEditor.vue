<template>
  <div class="diy-editor">
    <div class="diy-toolbar">
      <div class="diy-tips">
        <span>工作台视图：支持<strong>鼠标按住拖拽</strong>调整顺序、在<strong>一级直达</strong>与<strong>二级折叠夹</strong>之间自由升降级；也可一键套用内置视图。</span>
      </div>
      <div class="diy-actions">
        <el-button
          v-for="preset in MENU_PRESETS"
          :key="preset.id"
          size="small"
          :title="preset.description"
          @click="onApplyPreset(preset.id, preset.name)"
        >
          {{ preset.name }}
        </el-button>
      </div>
    </div>

    <div class="diy-mode-row">
      <span class="diy-mode-label">视图密度</span>
      <el-radio-group
        :model-value="menuStore.currentMode"
        size="small"
        @change="onChangeMode"
      >
        <el-radio-button
          v-for="(label, mode) in MENU_MODE_LABELS"
          :key="mode"
          :value="mode"
        >
          {{ label }}
        </el-radio-button>
      </el-radio-group>
      <span class="diy-mode-hint">负责人/管理员可在「系统设置 → 工作台视图」设置各角色默认密度</span>
    </div>

    <div class="diy-tool-row">
      <div class="diy-actions">
        <el-button
          size="small"
          @click="onApplyQuick"
        >
          高频一级直达模板
        </el-button>
        <el-button
          size="small"
          @click="onAddGroup"
        >
          + 新建分组
        </el-button>
        <el-button
          size="small"
          @click="onAddFolder"
        >
          + 新建二级菜单夹
        </el-button>
        <el-button
          size="small"
          type="warning"
          plain
          @click="onReset"
        >
          恢复默认布局
        </el-button>
      </div>
    </div>

    <div class="diy-grid">
      <!-- 左栏：当前侧栏菜单树（可拖拽排序、升降级） -->
      <div class="diy-tree-col">
        <div
          v-for="(grp, gIdx) in menuStore.groups"
          :key="grp.id"
          class="diy-group-card"
          @dragover.prevent
          @drop="onDropToGroup(grp.id)"
        >
          <div class="diy-group-head">
            <el-input
              v-model="grp.label"
              size="small"
              class="diy-group-input"
              placeholder="分组名称"
              @change="menuStore.saveToStorage()"
            />
            <div class="diy-group-btns">
              <el-button
                link
                size="small"
                :disabled="gIdx === 0"
                @click="moveGroup(gIdx, -1)"
              >
                上移分组
              </el-button>
              <el-button
                link
                size="small"
                :disabled="gIdx === menuStore.groups.length - 1"
                @click="moveGroup(gIdx, 1)"
              >
                下移分组
              </el-button>
              <el-button
                v-if="menuStore.groups.length > 1 && grp.items.length === 0"
                link
                type="danger"
                size="small"
                @click="removeGroup(gIdx)"
              >
                删除空组
              </el-button>
            </div>
          </div>

          <div class="diy-node-list">
            <div
              v-for="(node, nIdx) in grp.items"
              :key="node.key"
              class="diy-node-box"
              draggable="true"
              @dragstart.stop="onDragStartNode(grp.id, nIdx, node)"
              @dragover.prevent
              @drop.stop="onDropOnNode(grp.id, nIdx)"
            >
              <!-- 二级折叠菜单夹 -->
              <template v-if="node.children">
                <div class="diy-node-row is-folder">
                  <span
                    class="drag-handle"
                    title="按住拖拽排序"
                  >⠿</span>
                  <el-tag
                    size="small"
                    type="primary"
                    effect="plain"
                  >
                    二级菜单夹
                  </el-tag>
                  <el-input
                    v-model="node.title"
                    size="small"
                    class="node-title-input"
                    @change="menuStore.saveToStorage()"
                  />
                  <span class="node-sub-count">（含 {{ node.children.length }} 项）</span>
                  <div class="node-row-actions">
                    <el-button
                      link
                      size="small"
                      :disabled="nIdx === 0"
                      @click="menuStore.moveItemInGroup(grp.id, nIdx, nIdx - 1)"
                    >
                      ↑
                    </el-button>
                    <el-button
                      link
                      size="small"
                      :disabled="nIdx === grp.items.length - 1"
                      @click="menuStore.moveItemInGroup(grp.id, nIdx, nIdx + 1)"
                    >
                      ↓
                    </el-button>
                    <el-button
                      link
                      type="primary"
                      size="small"
                      @click="unpackFolder(grp.id, nIdx)"
                    >
                      全部展开为一级
                    </el-button>
                  </div>
                </div>

                <!-- 二级菜单子项列表 -->
                <div class="diy-children-list">
                  <div
                    v-for="(child, cIdx) in node.children"
                    :key="child.key"
                    class="diy-child-row"
                    draggable="true"
                    @dragstart.stop="onDragStartChild(grp.id, node.key, cIdx, child)"
                    @dragover.prevent
                    @drop.stop="onDropOnChild(grp.id, node.key, cIdx)"
                  >
                    <span
                      class="drag-handle"
                      title="按住拖拽排序"
                    >⠿</span>
                    <el-tag
                      size="small"
                      type="info"
                    >
                      二级
                    </el-tag>
                    <el-input
                      v-model="child.title"
                      size="small"
                      class="node-title-input"
                      @change="menuStore.saveToStorage()"
                    />
                    <code class="node-path">{{ child.path }}</code>
                    <div class="node-row-actions">
                      <el-button
                        link
                        size="small"
                        :disabled="cIdx === 0"
                        @click="menuStore.moveChildInFolder(grp.id, node.key, cIdx, cIdx - 1)"
                      >
                        ↑
                      </el-button>
                      <el-button
                        link
                        size="small"
                        :disabled="cIdx === node.children.length - 1"
                        @click="menuStore.moveChildInFolder(grp.id, node.key, cIdx, cIdx + 1)"
                      >
                        ↓
                      </el-button>
                      <el-button
                        link
                        type="success"
                        size="small"
                        @click="menuStore.promoteToTopLevel(grp.id, node.key, child.key)"
                      >
                        设为一级直达
                      </el-button>
                      <el-button
                        v-if="!child.locked"
                        link
                        type="danger"
                        size="small"
                        @click="hideChild(grp.id, node.key, cIdx)"
                      >
                        隐藏
                      </el-button>
                    </div>
                  </div>
                  <div
                    v-if="!node.children.length"
                    class="diy-empty-drop"
                  >
                    可将功能拖入此二级菜单夹
                  </div>
                </div>
              </template>

              <!-- 一级直达菜单项 -->
              <template v-else>
                <div class="diy-node-row is-leaf">
                  <span
                    class="drag-handle"
                    title="按住拖拽排序"
                  >⠿</span>
                  <el-tag
                    size="small"
                    type="success"
                  >
                    一级直达
                  </el-tag>
                  <el-input
                    v-model="node.title"
                    size="small"
                    class="node-title-input"
                    @change="menuStore.saveToStorage()"
                  />
                  <code class="node-path">{{ node.path }}</code>
                  <div class="node-row-actions">
                    <el-button
                      link
                      size="small"
                      :disabled="nIdx === 0"
                      @click="menuStore.moveItemInGroup(grp.id, nIdx, nIdx - 1)"
                    >
                      ↑
                    </el-button>
                    <el-button
                      link
                      size="small"
                      :disabled="nIdx === grp.items.length - 1"
                      @click="menuStore.moveItemInGroup(grp.id, nIdx, nIdx + 1)"
                    >
                      ↓
                    </el-button>
                    <el-button
                      link
                      type="warning"
                      size="small"
                      @click="menuStore.demoteToSubmenu(grp.id, node.key)"
                    >
                      收纳进二级
                    </el-button>
                    <el-button
                      v-if="!node.locked"
                      link
                      type="danger"
                      size="small"
                      @click="hideNode(grp.id, nIdx)"
                    >
                      隐藏
                    </el-button>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- 右栏：未放入菜单栏的隐藏功能池 -->
      <aside class="diy-pool-col">
        <div class="pool-head">
          <h4>全系统功能池（{{ menuStore.hiddenAtoms.length }}）</h4>
          <p class="pool-sub">
            展示全系统所有用户组的功能；点击可放回左侧菜单栏。实际是否可见由成员权限决定。
          </p>
        </div>
        <div
          v-if="menuStore.hiddenAtoms.length"
          class="pool-list"
        >
          <div
            v-for="atom in menuStore.hiddenAtoms"
            :key="atom.key"
            class="pool-item"
          >
            <div class="pool-info">
              <span class="pool-title">{{ atom.title }}</span>
              <code class="node-path">{{ atom.path }}</code>
              <span
                v-if="atom.permission"
                class="pool-perm"
              >需要权限：{{ permissionLabel(atom.permission) }}</span>
            </div>
            <div class="pool-btns">
              <el-button
                size="small"
                type="primary"
                plain
                @click="restoreAtomTop(atom)"
              >
                + 一级直达
              </el-button>
              <el-button
                size="small"
                @click="restoreAtomSub(atom)"
              >
                + 二级菜单
              </el-button>
            </div>
          </div>
        </div>
        <el-empty
          v-else
          description="全部功能均已显示在左侧菜单栏中"
          :image-size="64"
        />
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import {
    MENU_MODE_LABELS,
    MENU_PRESETS,
    useMenuStore,
    type DiyMenuLeaf,
    type DiyMenuNode,
    type MenuMode,
  } from '@/stores/menu'
  import { PERMISSION_LABELS } from '@/components/settings/permissions'

  const menuStore = useMenuStore()

  function permissionLabel(permission: string): string {
    return PERMISSION_LABELS[permission] ?? permission
  }

  function onApplyPreset(id: string, name: string) {
    menuStore.applyPreset(id)
    ElMessage.success(`已套用「${name}」`)
  }

  function onChangeMode(value: string | number | boolean | undefined) {
    if (value === 'minimal' || value === 'standard' || value === 'full') {
      menuStore.applyMode(value as MenuMode)
    }
  }

  interface DragContext {
    kind: 'node' | 'child'
    groupId: string
    folderKey?: string
    index: number
    payload: DiyMenuNode | DiyMenuLeaf
  }

  const dragCtx = ref<DragContext | null>(null)

  function onDragStartNode(groupId: string, index: number, node: DiyMenuNode) {
    dragCtx.value = { kind: 'node', groupId, index, payload: node }
  }

  function onDragStartChild(
    groupId: string,
    folderKey: string,
    index: number,
    child: DiyMenuLeaf,
  ) {
    dragCtx.value = { kind: 'child', groupId, folderKey, index, payload: child }
  }

  function onDropOnNode(targetGroupId: string, targetIndex: number) {
    const ctx = dragCtx.value
    dragCtx.value = null
    if (!ctx) return

    const srcGroup = menuStore.groups.find(g => g.id === ctx.groupId)
    const dstGroup = menuStore.groups.find(g => g.id === targetGroupId)
    if (!srcGroup || !dstGroup) return

    if (ctx.kind === 'node') {
      const [removed] = srcGroup.items.splice(ctx.index, 1)
      dstGroup.items.splice(targetIndex, 0, removed)
      menuStore.saveToStorage()
    } else if (ctx.kind === 'child' && ctx.folderKey) {
      const folder = srcGroup.items.find(x => x.key === ctx.folderKey && x.children)
      if (!folder?.children) return
      const [leaf] = folder.children.splice(ctx.index, 1)
      const targetNode = dstGroup.items[targetIndex]
      if (targetNode && Array.isArray(targetNode.children)) {
        targetNode.children.push(leaf)
      } else {
        dstGroup.items.splice(targetIndex, 0, {
          key: leaf.key,
          title: leaf.title,
          iconName: leaf.iconName,
          path: leaf.path,
          roles: leaf.roles,
          locked: leaf.locked,
        })
      }
      menuStore.saveToStorage()
    }
  }

  function onDropOnChild(targetGroupId: string, targetFolderKey: string, targetIdx: number) {
    const ctx = dragCtx.value
    dragCtx.value = null
    if (!ctx) return

    const srcGroup = menuStore.groups.find(g => g.id === ctx.groupId)
    const dstGroup = menuStore.groups.find(g => g.id === targetGroupId)
    if (!srcGroup || !dstGroup) return
    const dstFolder = dstGroup.items.find(x => x.key === targetFolderKey && x.children)
    if (!dstFolder) return
    dstFolder.children = dstFolder.children || []

    if (ctx.kind === 'child' && ctx.folderKey) {
      const srcFolder = srcGroup.items.find(x => x.key === ctx.folderKey && x.children)
      if (!srcFolder?.children) return
      const [leaf] = srcFolder.children.splice(ctx.index, 1)
      dstFolder.children.splice(targetIdx, 0, leaf)
      menuStore.saveToStorage()
    } else if (ctx.kind === 'node') {
      const node = srcGroup.items[ctx.index]
      if (!node || node.children || !node.path) return
      srcGroup.items.splice(ctx.index, 1)
      dstFolder.children.splice(targetIdx, 0, {
        key: node.key,
        title: node.title,
        iconName: node.iconName,
        path: node.path,
        roles: node.roles,
        locked: node.locked,
      })
      menuStore.saveToStorage()
    }
  }

  function onDropToGroup(targetGroupId: string) {
    const ctx = dragCtx.value
    dragCtx.value = null
    if (!ctx) return
    const srcGroup = menuStore.groups.find(g => g.id === ctx.groupId)
    const dstGroup = menuStore.groups.find(g => g.id === targetGroupId)
    if (!srcGroup || !dstGroup) return

    if (ctx.kind === 'node') {
      const [removed] = srcGroup.items.splice(ctx.index, 1)
      dstGroup.items.push(removed)
      menuStore.saveToStorage()
    } else if (ctx.kind === 'child' && ctx.folderKey) {
      const srcFolder = srcGroup.items.find(x => x.key === ctx.folderKey && x.children)
      if (!srcFolder?.children) return
      const [leaf] = srcFolder.children.splice(ctx.index, 1)
      dstGroup.items.push({
        key: leaf.key,
        title: leaf.title,
        iconName: leaf.iconName,
        path: leaf.path,
        roles: leaf.roles,
        locked: leaf.locked,
      })
      menuStore.saveToStorage()
    }
  }

  function moveGroup(idx: number, delta: number) {
    const to = idx + delta
    if (to < 0 || to >= menuStore.groups.length) return
    const [g] = menuStore.groups.splice(idx, 1)
    menuStore.groups.splice(to, 0, g)
    menuStore.saveToStorage()
  }

  function removeGroup(idx: number) {
    menuStore.groups.splice(idx, 1)
    menuStore.saveToStorage()
  }

  function unpackFolder(groupId: string, folderIdx: number) {
    const g = menuStore.groups.find(x => x.id === groupId)
    if (!g) return
    const folder = g.items[folderIdx]
    if (!folder || !folder.children) return
    const promoted: DiyMenuNode[] = folder.children.map(c => ({
      key: c.key,
      title: c.title,
      iconName: c.iconName,
      path: c.path,
      roles: c.roles,
      locked: c.locked,
    }))
    g.items.splice(folderIdx, 1, ...promoted)
    menuStore.saveToStorage()
    ElMessage.success(`已将「${folder.title}」全部展开为一级直达菜单`)
  }

  function hideNode(groupId: string, idx: number) {
    const g = menuStore.groups.find(x => x.id === groupId)
    if (!g) return
    const node = g.items[idx]
    if (node?.locked) {
      ElMessage.warning('系统设置不可隐藏')
      return
    }
    g.items.splice(idx, 1)
    menuStore.saveToStorage()
  }

  function hideChild(groupId: string, folderKey: string, cIdx: number) {
    const g = menuStore.groups.find(x => x.id === groupId)
    if (!g) return
    const folder = g.items.find(x => x.key === folderKey && x.children)
    if (!folder?.children) return
    if (folder.children[cIdx]?.locked) {
      ElMessage.warning('系统设置不可隐藏')
      return
    }
    folder.children.splice(cIdx, 1)
    menuStore.saveToStorage()
  }

  function restoreAtomTop(atom: DiyMenuLeaf) {
    const firstGrp = menuStore.groups[0]
    if (!firstGrp) return
    firstGrp.items.push({
      key: atom.key,
      title: atom.title,
      iconName: atom.iconName,
      path: atom.path,
      roles: atom.roles,
      locked: atom.locked,
    })
    menuStore.saveToStorage()
    ElMessage.success(`已将「${atom.title}」添加至一级直达菜单`)
  }

  function restoreAtomSub(atom: DiyMenuLeaf) {
    for (const g of menuStore.groups) {
      const folder = g.items.find(x => Array.isArray(x.children))
      if (folder) {
        folder.children = folder.children || []
        folder.children.push({ ...atom })
        menuStore.saveToStorage()
        ElMessage.success(`已将「${atom.title}」添加至二级菜单「${folder.title}」`)
        return
      }
    }
    restoreAtomTop(atom)
  }

  async function onAddGroup() {
    try {
      const { value } = await ElMessageBox.prompt('请输入新导航分组名称', '新建导航分组', {
        inputValue: '自定义分组',
        confirmButtonText: '创建',
        cancelButtonText: '取消',
      })
      if (!value?.trim()) return
      menuStore.groups.push({
        id: `grp-${Date.now()}`,
        label: value.trim(),
        items: [],
      })
      menuStore.saveToStorage()
    } catch {
      /* cancelled */
    }
  }

  async function onAddFolder() {
    try {
      const { value } = await ElMessageBox.prompt('请输入新二级折叠菜单名称', '新建二级菜单夹', {
        inputValue: '快捷收纳夹',
        confirmButtonText: '创建',
        cancelButtonText: '取消',
      })
      if (!value?.trim()) return
      const targetGroup = menuStore.groups[0]
      if (!targetGroup) return
      targetGroup.items.push({
        key: `folder-${Date.now()}`,
        title: value.trim(),
        iconName: 'Menu',
        children: [],
      })
      menuStore.saveToStorage()
    } catch {
      /* cancelled */
    }
  }

  function onApplyQuick() {
    menuStore.applyQuickTemplate()
    ElMessage.success('已切换为「高频全一级直达」导航布局')
  }

  function onReset() {
    menuStore.resetToDefault()
    ElMessage.success('已恢复默认导航菜单布局')
  }
</script>

<style scoped>
  .diy-editor {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .diy-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 16px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
  }
  .diy-tips {
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .diy-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .diy-mode-row,
  .diy-tool-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
  }
  .diy-mode-label {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-2);
  }
  .diy-mode-hint {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .pool-perm {
    font-size: 11px;
    color: var(--ds-text-3);
  }
  .diy-grid {
    display: grid;
    grid-template-columns: 1fr 320px;
    gap: 16px;
    align-items: start;
  }
  .diy-tree-col {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .diy-group-card {
    padding: 14px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
  }
  .diy-group-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 10px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--ds-border);
  }
  .diy-group-input {
    max-width: 200px;
  }
  .diy-node-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .diy-node-box {
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    background: var(--ds-glass);
    padding: 8px 10px;
  }
  .diy-node-row,
  .diy-child-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .drag-handle {
    cursor: grab;
    color: var(--ds-text-3);
    font-size: 15px;
    user-select: none;
    padding: 0 4px;
  }
  .node-title-input {
    width: 150px;
  }
  .node-path {
    font-size: 11.5px;
    color: var(--ds-text-3);
    background: var(--ds-bg-1);
    padding: 2px 6px;
    border-radius: 4px;
  }
  .node-sub-count {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .node-row-actions {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .diy-children-list {
    margin-top: 8px;
    margin-left: 22px;
    padding-left: 12px;
    border-left: 2px dashed var(--ds-border-strong);
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .diy-child-row {
    padding: 6px 8px;
    border-radius: 6px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
  }
  .diy-empty-drop {
    font-size: 12px;
    color: var(--ds-text-3);
    padding: 8px;
    text-align: center;
    border: 1px dashed var(--ds-border);
    border-radius: 6px;
  }
  .diy-pool-col {
    padding: 14px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
  }
  .pool-head h4 {
    margin: 0 0 6px;
    font-size: 14px;
    color: var(--ds-text-1);
  }
  .pool-sub {
    margin: 0 0 12px;
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.5;
  }
  .pool-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .pool-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 8px 10px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    background: var(--ds-glass);
  }
  .pool-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .pool-title {
    font-size: 13px;
    font-weight: 500;
    color: var(--ds-text-1);
  }
  .pool-btns {
    display: flex;
    gap: 4px;
  }
  @media (max-width: 960px) {
    .diy-grid {
      grid-template-columns: 1fr;
    }
  }
</style>

<template>
  <el-card class="section">
    <template #header>
      <div class="card-header">
        <span>角色与权限</span>
        <div class="page-actions">
          <el-button
            size="small"
            @click="loadRoles"
          >
            刷新
          </el-button>
          <el-button
            size="small"
            type="primary"
            @click="openCreate"
          >
            新建自定义角色
          </el-button>
        </div>
      </div>
    </template>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="role-hint"
      title="小院负责人拥有全部权限且只读；管理员只能维护自定义角色；带「受保护」标记的权限只能由负责人逐人授权。"
    />

    <AsyncState
      :state="state"
      empty-text="暂无角色定义"
      @retry="loadRoles"
    >
      <div class="roles-layout">
        <aside class="role-list">
          <div class="role-list-title">
            系统角色
          </div>
          <button
            v-for="role in systemRoles"
            :key="role.code"
            type="button"
            class="role-item"
            :class="{ 'is-active': role.code === selectedCode }"
            @click="selectRole(role.code)"
          >
            <span class="role-item-name">{{ role.name }}</span>
            <span class="role-item-code">{{ role.code }}</span>
          </button>
          <div class="role-list-title">
            自定义角色（{{ customRoles.length }}）
          </div>
          <button
            v-for="role in customRoles"
            :key="role.code"
            type="button"
            class="role-item"
            :class="{ 'is-active': role.code === selectedCode }"
            @click="selectRole(role.code)"
          >
            <span class="role-item-name">{{ role.name }}</span>
            <span class="role-item-code">{{ role.code }}</span>
          </button>
          <p
            v-if="!customRoles.length"
            class="role-list-empty"
          >
            暂无自定义角色
          </p>
        </aside>

        <section
          v-if="selectedRole"
          class="role-detail"
        >
          <div class="role-detail-head">
            <div class="role-identity">
              <span class="role-name">{{ selectedRole.name }}</span>
              <el-tag
                size="small"
                :type="selectedRole.is_system ? 'info' : 'primary'"
                effect="plain"
              >
                {{ selectedRole.is_system ? '系统角色' : '自定义角色' }}
              </el-tag>
              <span class="role-code">{{ selectedRole.code }}</span>
            </div>
            <div class="page-actions">
              <el-button
                v-if="canDelete"
                size="small"
                type="danger"
                plain
                :loading="deleting"
                @click="removeRole"
              >
                删除角色
              </el-button>
              <el-tooltip
                v-if="!canEdit"
                :content="readonlyReason"
                placement="top"
              >
                <span>
                  <el-button
                    size="small"
                    type="primary"
                    disabled
                  >
                    保存
                  </el-button>
                </span>
              </el-tooltip>
              <el-button
                v-else
                size="small"
                type="primary"
                :loading="saving"
                @click="saveRole"
              >
                保存
              </el-button>
            </div>
          </div>

          <el-form
            label-width="80px"
            class="role-form"
          >
            <el-form-item label="角色名称">
              <el-input
                v-model="editName"
                maxlength="64"
                :disabled="!canEdit"
                placeholder="展示给成员的角色名"
              />
            </el-form-item>
            <el-form-item label="角色标识">
              <code class="role-code-block">{{ selectedRole.code }}</code>
              <span class="note">{{ selectedRole.is_system ? '系统内置标识不可修改' : '角色标识创建后不可修改' }}</span>
            </el-form-item>
          </el-form>

          <PermissionMatrix
            v-model="editPermissions"
            :groups="groups"
            :labels="labels"
            :disabled="!canEdit"
          />
        </section>
      </div>
    </AsyncState>

    <el-dialog
      v-model="createDialog"
      title="新建自定义角色"
      width="640px"
    >
      <el-form
        label-width="90px"
      >
        <el-form-item
          label="角色标识"
          required
        >
          <el-input
            v-model="createForm.code"
            maxlength="32"
            placeholder="小写字母开头，如 field_operator"
          />
          <div class="note">
            3-32 位小写字母、数字或下划线，创建后不可修改
          </div>
        </el-form-item>
        <el-form-item
          label="角色名称"
          required
        >
          <el-input
            v-model="createForm.name"
            maxlength="64"
            placeholder="如：田间操作员"
          />
        </el-form-item>
      </el-form>
      <PermissionMatrix
        v-model="createPermissions"
        :groups="groups"
        :labels="labels"
      />
      <template #footer>
        <el-button @click="createDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="creating"
          @click="createRole"
        >
          创建
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { governanceApi } from '@/api/v3'
  import type { PermissionGroup, RoleDef } from '@/api/v3'
  import AsyncState from '@/components/AsyncState.vue'
  import PermissionMatrix from '@/components/settings/PermissionMatrix.vue'
  import { FALLBACK_PERMISSION_GROUPS, PERMISSION_LABELS, PROTECTED_PERMISSION_SET } from './permissions'
  import { classifyError, type ViewState } from '@/utils/status'

  const props = defineProps<{ isOwner: boolean }>()

  /** 后端当前返回 roles / role_labels；v3 类型为 items / permission_labels，做双向兼容 */
  interface RolesRaw {
    roles?: RoleDef[]
    items?: RoleDef[]
    permission_groups?: PermissionGroup[]
    permission_labels?: Record<string, string>
  }

  const state = ref<ViewState>('loading')
  const roles = ref<RoleDef[]>([])
  const groups = ref<PermissionGroup[]>(FALLBACK_PERMISSION_GROUPS)
  const labels = ref<Record<string, string>>({ ...PERMISSION_LABELS })
  const selectedCode = ref('')
  const editName = ref('')
  const editPermissions = ref<string[]>([])
  const saving = ref(false)
  const deleting = ref(false)

  const systemRoles = computed(() => roles.value.filter(role => role.is_system))
  const customRoles = computed(() => roles.value.filter(role => !role.is_system))
  const selectedRole = computed(() => roles.value.find(role => role.code === selectedCode.value) ?? null)
  const canEdit = computed(() => {
    if (!selectedRole.value) return false
    if (selectedRole.value.is_protected || selectedRole.value.code === 'yard_owner') return false
    if (props.isOwner) return true
    return !selectedRole.value.is_system
  })
  const readonlyReason = computed(() => {
    if (selectedRole.value?.code === 'yard_owner') return '负责人权限固定为全集，不可修改'
    return '只有小院负责人可以修改系统角色'
  })
  const canDelete = computed(() => !!selectedRole.value && !selectedRole.value.is_system)

  function applyRole(code: string | undefined): void {
    selectedCode.value = code ?? ''
    const role = roles.value.find(item => item.code === selectedCode.value)
    editName.value = role?.name ?? ''
    editPermissions.value = role ? [...role.permissions] : []
  }

  async function loadRoles(): Promise<void> {
    state.value = 'loading'
    try {
      const raw = (await governanceApi.roles()) as unknown as RolesRaw
      roles.value = raw.roles ?? raw.items ?? []
      groups.value = raw.permission_groups?.length ? raw.permission_groups : FALLBACK_PERMISSION_GROUPS
      labels.value = { ...PERMISSION_LABELS, ...(raw.permission_labels ?? {}) }
      const keep = roles.value.some(role => role.code === selectedCode.value)
      applyRole(keep ? selectedCode.value : roles.value[0]?.code)
      state.value = roles.value.length ? 'ready' : 'empty'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  function selectRole(code: string): void {
    applyRole(code)
  }

  async function saveRole(): Promise<void> {
    if (!selectedRole.value) return
    const name = editName.value.trim()
    if (!name) {
      ElMessage.warning('角色名称不能为空')
      return
    }
    saving.value = true
    try {
      await governanceApi.updateRole(selectedRole.value.code, {
        name,
        permissions: editPermissions.value.filter(permission => !PROTECTED_PERMISSION_SET.has(permission)),
      })
      ElMessage.success('角色已保存')
      await loadRoles()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }

  async function removeRole(): Promise<void> {
    if (!selectedRole.value) return
    try {
      await ElMessageBox.confirm(
        `确定删除自定义角色「${selectedRole.value.name}」？已使用该角色的成员将无法再分配此角色。`,
        '删除角色',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    deleting.value = true
    try {
      await governanceApi.deleteRole(selectedRole.value.code)
      ElMessage.success('角色已删除')
      await loadRoles()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      deleting.value = false
    }
  }

  // ---------- 新建自定义角色 ----------
  const createDialog = ref(false)
  const creating = ref(false)
  const createForm = reactive({ code: '', name: '' })
  const createPermissions = ref<string[]>([])

  function openCreate(): void {
    createForm.code = ''
    createForm.name = ''
    createPermissions.value = []
    createDialog.value = true
  }

  async function createRole(): Promise<void> {
    const code = createForm.code.trim()
    const name = createForm.name.trim()
    if (!/^[a-z][a-z0-9_]{2,31}$/.test(code)) {
      ElMessage.warning('角色标识需为 3-32 位小写字母、数字或下划线，且以小写字母开头')
      return
    }
    if (!name) {
      ElMessage.warning('请填写角色名称')
      return
    }
    creating.value = true
    try {
      const role = await governanceApi.createRole({
        code,
        name,
        permissions: createPermissions.value.filter(
          permission => !PROTECTED_PERMISSION_SET.has(permission),
        ),
      })
      ElMessage.success(`角色「${role.name}」已创建`)
      createDialog.value = false
      await loadRoles()
      applyRole(role.code)
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      creating.value = false
    }
  }

  onMounted(() => {
    void loadRoles()
  })
</script>

<style scoped>
  .section {
    margin-bottom: 16px;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .role-hint {
    margin-bottom: 14px;
  }
  .roles-layout {
    display: flex;
    gap: 18px;
    align-items: flex-start;
  }
  .role-list {
    width: 232px;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .role-list-title {
    margin: 6px 0 2px;
    font-size: 12px;
    color: var(--ds-text-3);
    letter-spacing: 0.02em;
  }
  .role-item {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
    width: 100%;
    padding: 8px 12px;
    text-align: left;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    color: var(--ds-text-1);
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
  }
  .role-item:hover {
    border-color: var(--ds-border-strong);
    background: var(--ds-glass-strong);
  }
  .role-item.is-active {
    border-color: var(--ds-accent);
    background: rgba(99, 102, 241, 0.16);
  }
  .role-item-name {
    font-size: 13px;
    font-weight: 600;
  }
  .role-item-code {
    font-family: ui-monospace, monospace;
    font-size: 11px;
    color: var(--ds-text-3);
  }
  .role-list-empty {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .role-detail {
    flex: 1;
    min-width: 0;
  }
  .role-detail-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }
  .role-identity {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .role-name {
    font-size: 15px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .role-code {
    font-family: ui-monospace, monospace;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .role-form {
    max-width: 480px;
  }
  .role-code-block {
    font-family: ui-monospace, monospace;
    font-size: 12px;
    color: var(--ds-text-2);
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: 6px;
    padding: 2px 8px;
  }
  .note {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
</style>

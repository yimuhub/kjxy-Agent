<template>
  <el-dialog
    :model-value="modelValue"
    title="成员权限与逐人授权"
    width="760px"
    @update:model-value="emit('update:modelValue', $event)"
    @open="load"
  >
    <div
      v-if="state === 'loading'"
      class="perm-loading"
    >
      <el-skeleton
        :rows="6"
        animated
      />
    </div>
    <AsyncState
      v-else
      :state="state"
      empty-text="暂无可配置权限"
      @retry="load"
    >
      <template v-if="data">
        <div class="perm-head">
          <div>
            <span class="perm-name">{{ data.display_name || data.username }}</span>
            <span class="perm-meta">@{{ data.username }} · {{ data.role_name }}</span>
          </div>
          <el-tag
            v-if="data.is_owner"
            type="warning"
            effect="plain"
          >
            负责人拥有全部权限
          </el-tag>
          <el-tag
            v-else
            type="info"
            effect="plain"
          >
            有效权限 {{ data.effective_permissions.length }} 项
          </el-tag>
        </div>

        <el-alert
          type="info"
          :closable="false"
          show-icon
          class="perm-alert"
          title="有效权限 = 角色默认权限 ∪ 逐人授权。勾选下方权限只会在角色之上追加，不会削弱角色权限；带「受保护」的权限需要二次确认。"
        />

        <div
          v-if="!data.is_owner"
          class="perm-groups"
        >
          <section
            v-for="group in data.permission_groups"
            :key="group.key"
            class="perm-group"
          >
            <header class="perm-group-head">
              <span>{{ group.label }}</span>
            </header>
            <el-checkbox-group
              v-model="selected"
              class="perm-items"
            >
              <el-checkbox
                v-for="permission in group.permissions"
                :key="permission"
                :value="permission"
                :disabled="!canManage || roleSet.has(permission)"
              >
                <span>{{ labelOf(permission) }}</span>
                <el-tag
                  v-if="roleSet.has(permission)"
                  size="small"
                  type="success"
                  effect="plain"
                  class="perm-tag"
                >
                  角色自带
                </el-tag>
                <el-tag
                  v-else-if="protectedSet.has(permission)"
                  size="small"
                  type="warning"
                  effect="plain"
                  class="perm-tag"
                >
                  受保护
                </el-tag>
              </el-checkbox>
            </el-checkbox-group>
          </section>
        </div>
      </template>
    </AsyncState>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">
        关闭
      </el-button>
      <el-button
        v-if="data && !data.is_owner && canManage"
        type="primary"
        :loading="saving"
        @click="save"
      >
        保存授权
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  import { computed, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { memberPermissionApi, type MemberPermissionPayload } from '@/api/v3'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'

  const props = defineProps<{
    modelValue: boolean
    userId: string
    canManage: boolean
  }>()

  const emit = defineEmits<{
    (e: 'update:modelValue', value: boolean): void
    (e: 'saved'): void
  }>()

  const state = ref<ViewState>('loading')
  const data = ref<MemberPermissionPayload | null>(null)
  const selected = ref<string[]>([])
  const saving = ref(false)

  const roleSet = computed(() => new Set(data.value?.role_permissions ?? []))
  const protectedSet = computed(() => new Set(data.value?.protected_permissions ?? []))
  const grantableSet = computed(() => new Set(data.value?.grantable_permissions ?? []))

  function labelOf(permission: string): string {
    return data.value?.permission_labels?.[permission] ?? permission
  }

  async function load(): Promise<void> {
    if (!props.userId) return
    state.value = 'loading'
    try {
      const payload = await memberPermissionApi.get(props.userId)
      data.value = payload
      selected.value = [...payload.grants]
      state.value = 'ready'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  async function save(): Promise<void> {
    if (!data.value) return
    const next = selected.value.filter(permission => grantableSet.value.has(permission))
    const addedProtected = next.filter(
      permission => protectedSet.value.has(permission) && !data.value!.grants.includes(permission),
    )
    let confirmProtected = false
    if (addedProtected.length) {
      try {
        await ElMessageBox.confirm(
          `新增受保护权限：${addedProtected.map(labelOf).join('、')}。确认授予该成员？`,
          '保护型权限二次确认',
          { type: 'warning', confirmButtonText: '确认授予', cancelButtonText: '取消' },
        )
        confirmProtected = true
      } catch {
        return
      }
    }
    saving.value = true
    try {
      const payload = await memberPermissionApi.update(props.userId, {
        grants: next,
        confirm_protected: confirmProtected,
      })
      data.value = payload
      selected.value = [...payload.grants]
      ElMessage.success('成员权限已更新')
      emit('saved')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }
</script>

<style scoped>
  .perm-loading {
    padding: 8px 0;
  }
  .perm-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
  }
  .perm-name {
    font-size: 15px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .perm-meta {
    margin-left: 10px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .perm-alert {
    margin-bottom: 14px;
  }
  .perm-groups {
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-height: 52vh;
    overflow-y: auto;
  }
  .perm-group {
    padding: 10px 12px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    background: var(--ds-glass);
  }
  .perm-group-head {
    margin-bottom: 8px;
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .perm-items {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
    gap: 4px 12px;
  }
  .perm-tag {
    margin-left: 6px;
  }
</style>

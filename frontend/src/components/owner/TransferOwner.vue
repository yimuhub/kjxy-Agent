<template>
  <div class="transfer-owner">
    <el-alert
      type="error"
      :closable="false"
      show-icon
      title="危险操作"
      description="负责人转让后，你将变为小院管理员且不再拥有负责人专属权限；此操作不可撤销，请谨慎执行。"
    />

    <div
      v-if="admins.length"
      class="transfer-form"
    >
      <el-form
        label-width="110px"
      >
        <el-form-item label="目标管理员">
          <el-select
            v-model="selectedId"
            placeholder="选择一位小院管理员"
            style="width: 320px"
          >
            <el-option
              v-for="admin in admins"
              :key="admin.user_id"
              :label="`${admin.display_name || admin.username}（@${admin.username}）`"
              :value="admin.user_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="确认用户名">
          <el-input
            v-model="confirmName"
            placeholder="请输入目标管理员的用户名以确认"
            style="width: 320px"
            @keyup.enter="transfer"
          />
        </el-form-item>
      </el-form>
      <el-button
        type="danger"
        :disabled="!canSubmit"
        :loading="submitting"
        @click="transfer"
      >
        转让负责人
      </el-button>
      <p
        v-if="selectedId && !nameMatched"
        class="hint"
      >
        用户名与所选管理员不一致，请核对后再提交
      </p>
    </div>
    <p
      v-else
      class="hint"
    >
      暂无其他小院管理员可接收转让。请先在「成员管理」中设置管理员。
    </p>
  </div>
</template>

<script setup lang="ts">
  import { computed, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { ownerApi, type GrantRow } from '@/api/v3'

  const props = withDefaults(defineProps<{ admins?: GrantRow[] }>(), { admins: () => [] })

  const emit = defineEmits<{ (e: 'transferred'): void }>()

  const selectedId = ref('')
  const confirmName = ref('')
  const submitting = ref(false)

  const selectedAdmin = computed(
    () => props.admins.find(admin => admin.user_id === selectedId.value) ?? null,
  )
  const nameMatched = computed(
    () =>
      !!selectedAdmin.value &&
      confirmName.value.trim() === selectedAdmin.value.username,
  )
  const canSubmit = computed(() => !!selectedAdmin.value && nameMatched.value && !submitting.value)

  async function transfer(): Promise<void> {
    if (!selectedAdmin.value || !nameMatched.value) {
      ElMessage.warning('请选择目标管理员并输入其用户名确认')
      return
    }
    const admin = selectedAdmin.value
    try {
      await ElMessageBox.confirm(
        `确认将小院负责人转让给「${admin.display_name || admin.username}」？转让后你将变为小院管理员，且此操作不可撤销。`,
        '负责人转让',
        { type: 'error', confirmButtonText: '确认转让', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    submitting.value = true
    try {
      await ownerApi.transfer(admin.user_id)
      ElMessage.success('负责人已转让，即将返回工作台')
      emit('transferred')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      submitting.value = false
    }
  }
</script>

<style scoped>
  .transfer-owner {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .transfer-form {
    padding: 14px 16px;
    border: 1px dashed rgba(248, 113, 113, 0.45);
    border-radius: var(--ds-radius-control);
    background: rgba(248, 113, 113, 0.05);
  }
  .hint {
    margin: 8px 0 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
</style>

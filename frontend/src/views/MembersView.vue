<template>
  <div class="members">
    <h2 class="page-title">成员管理</h2>
    <el-card>
      <div class="toolbar">
        <el-button type="primary" @click="addDialog = true">添加成员</el-button>
        <el-button link type="primary" @click="loadMembers">刷新</el-button>
      </div>
      <AsyncState :state="listState" empty-text="暂无成员" @retry="loadMembers">
        <el-table :data="members" stripe>
          <el-table-column prop="username" label="用户名" />
          <el-table-column prop="display_name" label="姓名" />
          <el-table-column prop="role" label="角色" width="160">
            <template #default="{ row }">
              <el-tag>{{ roleLabel(row.role) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="joined_at" label="加入时间" width="180">
            <template #default="{ row }">{{ formatTime(row.joined_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button link type="danger" @click="confirmRemove(row)">移除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </AsyncState>
    </el-card>

    <el-dialog v-model="addDialog" title="添加成员" width="480px">
      <el-form :model="addForm" :rules="addRules" ref="addFormRef" label-width="80px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="addForm.username" placeholder="已注册用户的用户名" />
        </el-form-item>
        <el-form-item label="角色" prop="role">
          <el-select v-model="addForm.role" placeholder="请选择角色" style="width: 100%">
            <el-option label="租户管理员" value="tenant_admin" />
            <el-option label="专家" value="expert" />
            <el-option label="操作员" value="operator" />
            <el-option label="访客" value="viewer" />
          </el-select>
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="addForm.display_name" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialog = false">取消</el-button>
        <el-button type="primary" :loading="adding" @click="doAdd">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { tenantApi } from '@/api/modules'
import AsyncState from '@/components/AsyncState.vue'
import { classifyError, type ViewState } from '@/utils/status'
import type { Member, MemberRole } from '@/types/api'

const members = ref<Member[]>([])
const listState = ref<ViewState>('loading')

const addDialog = ref(false)
const adding = ref(false)
const addFormRef = ref<FormInstance>()
const addForm = reactive({ username: '', role: '' as '' | MemberRole, display_name: '' })
const addRules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  role: [{ required: true, message: '请选择角色', trigger: 'change' }],
}

const ROLE_LABELS: Record<string, string> = {
  tenant_admin: '租户管理员',
  expert: '专家',
  operator: '操作员',
  viewer: '访客',
}
function roleLabel(role?: string): string {
  return ROLE_LABELS[role ?? ''] ?? role ?? '—'
}

function formatTime(t?: string): string {
  if (!t) return '—'
  const d = new Date(t)
  return isNaN(d.getTime()) ? t : d.toLocaleString('zh-CN')
}

async function loadMembers() {
  listState.value = 'loading'
  try {
    const res = await tenantApi.members()
    members.value = res.items
    listState.value = res.items.length ? 'ready' : 'empty'
  } catch (e) {
    listState.value = classifyError(e)
  }
}

async function doAdd() {
  await addFormRef.value?.validate()
  adding.value = true
  try {
    await tenantApi.addMember({
      username: addForm.username,
      role: addForm.role as MemberRole,
      display_name: addForm.display_name || undefined,
    })
    ElMessage.success('成员已添加')
    addDialog.value = false
    addForm.username = ''
    addForm.role = ''
    addForm.display_name = ''
    loadMembers()
  } catch {
    /* 错误 toast 已由 client 统一处理（如 MEMBER_EXISTS） */
  } finally {
    adding.value = false
  }
}

async function confirmRemove(m: Member) {
  try {
    await ElMessageBox.confirm(
      `确定移除成员「${m.display_name || m.username}」吗？该操作将收回其租户访问权限。`,
      '移除确认',
      { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await tenantApi.removeMember(m.user_id ?? '')
    ElMessage.success('已移除')
    loadMembers()
  } catch {
    /* 错误 toast 已由 client 统一处理 */
  }
}

onMounted(loadMembers)
</script>

<style scoped>
.members {
  padding: 4px;
}
.page-title {
  margin: 0 0 16px;
}
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 16px;
}
</style>

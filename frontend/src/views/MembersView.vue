<template>
  <div class="members">
    <div class="page-head">
      <h2 class="page-title">
        成员管理
      </h2>
      <div class="page-actions">
        <el-button
          v-if="activeTab === 'members'"
          type="primary"
          @click="addDialog = true"
        >
          添加成员
        </el-button>
        <el-button
          v-if="activeTab === 'members'"
          @click="batchDialog = true"
        >
          批量导入
        </el-button>
        <el-button
          v-if="activeTab === 'invites'"
          type="primary"
          @click="inviteDialog = true"
        >
          生成邀请链接
        </el-button>
        <el-button
          link
          type="primary"
          @click="activeTab === 'members' ? loadMembers() : loadInvitations()"
        >
          刷新
        </el-button>
      </div>
    </div>

    <el-tabs
      v-model="activeTab"
      class="member-tabs"
      @tab-change="onTabChange"
    >
      <el-tab-pane
        label="成员"
        name="members"
      >
        <el-card>
          <AsyncState
            :state="listState"
            empty-text="暂无成员，先添加第一位同事吧"
            empty-action-text="添加成员"
            @action="addDialog = true"
            @retry="loadMembers"
          >
            <el-table
              :data="members"
              stripe
            >
              <el-table-column
                prop="username"
                label="用户名"
              />
              <el-table-column
                prop="display_name"
                label="姓名"
              />
              <el-table-column
                prop="role"
                label="角色"
                width="160"
              >
                <template #default="{ row }">
                  <el-tag>{{ roleLabel(row.role) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column
                prop="joined_at"
                label="加入时间"
                width="180"
              >
                <template #default="{ row }">
                  {{ formatTime(row.joined_at) }}
                </template>
              </el-table-column>
              <el-table-column
                label="操作"
                width="120"
              >
                <template #default="{ row }">
                  <el-button
                    link
                    type="danger"
                    @click="confirmRemove(row)"
                  >
                    移除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </AsyncState>
        </el-card>
      </el-tab-pane>

      <el-tab-pane
        label="邀请链接"
        name="invites"
      >
        <el-alert
          type="info"
          :closable="false"
          show-icon
          class="invite-hint"
          title="邀请链接仅在生成时显示一次，请立即复制发给对方；遗失可撤销后重新生成。"
        />
        <el-card>
          <AsyncState
            :state="inviteState"
            empty-text="还没有邀请记录，生成一条邀请链接发给同事"
            empty-action-text="生成邀请链接"
            @action="inviteDialog = true"
            @retry="loadInvitations"
          >
            <el-table
              :data="invitations"
              stripe
            >
              <el-table-column
                prop="role"
                label="授予角色"
                width="150"
              >
                <template #default="{ row }">
                  <el-tag>{{ roleLabel(row.role) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column
                prop="note"
                label="备注"
              >
                <template #default="{ row }">
                  {{ row.note || '—' }}
                </template>
              </el-table-column>
              <el-table-column
                prop="status"
                label="状态"
                width="110"
              >
                <template #default="{ row }">
                  <el-tag :type="inviteTagType(row.status)">
                    {{ inviteStatusLabel(row.status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column
                prop="expires_at"
                label="有效期至"
                width="180"
              >
                <template #default="{ row }">
                  {{ formatTime(row.expires_at) }}
                </template>
              </el-table-column>
              <el-table-column
                label="操作"
                width="100"
              >
                <template #default="{ row }">
                  <el-button
                    v-if="row.status === 'pending'"
                    link
                    type="danger"
                    @click="doRevoke(row as Invitation)"
                  >
                    撤销
                  </el-button>
                  <span v-else>—</span>
                </template>
              </el-table-column>
            </el-table>
          </AsyncState>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog
      v-model="addDialog"
      title="添加成员"
      width="480px"
    >
      <el-form
        ref="addFormRef"
        :model="addForm"
        :rules="addRules"
        label-width="80px"
      >
        <el-form-item
          label="用户名"
          prop="username"
        >
          <el-input
            v-model="addForm.username"
            placeholder="已注册用户的用户名"
          />
        </el-form-item>
        <el-form-item
          label="角色"
          prop="role"
        >
          <el-select
            v-model="addForm.role"
            placeholder="请选择角色"
            style="width: 100%"
          >
            <el-option
              label="使用空间管理员"
              value="tenant_admin"
            />
            <el-option
              label="专家"
              value="expert"
            />
            <el-option
              label="操作员"
              value="operator"
            />
            <el-option
              label="访客"
              value="viewer"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="姓名">
          <el-input
            v-model="addForm.display_name"
            placeholder="可选"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="adding"
          @click="doAdd"
        >
          添加
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="batchDialog"
      title="批量导入成员"
      width="560px"
      @closed="resetBatch"
    >
      <p class="dialog-tip">
        每行一个成员，格式：<code>用户名,角色</code>。角色可填
        <code>tenant_admin</code> / <code>expert</code> /
        <code>operator</code> / <code>viewer</code>，或直接写中文。
        用户名必须是已注册账号；需要邀请新同事请使用「邀请链接」。
      </p>
      <el-input
        v-model="batchText"
        type="textarea"
        :rows="8"
        placeholder="zhang_san,operator&#10;li_si,专家&#10;wang_wu,viewer"
      />
      <div
        v-if="batchPreview.failed.length"
        class="batch-preview"
      >
        <el-alert
          type="warning"
          :closable="false"
          :title="`${batchPreview.failed.length} 行格式有误，将被跳过`"
        >
          <ul class="error-list">
            <li
              v-for="item in batchPreview.failed"
              :key="item.line"
            >
              第 {{ item.line }} 行：{{ item.message }}
            </li>
          </ul>
        </el-alert>
      </div>
      <div
        v-if="batchResult"
        class="batch-result"
      >
        <el-alert
          :type="batchResult.failed.length ? 'warning' : 'success'"
          :closable="false"
          :title="`成功添加 ${batchResult.added.length} 人，失败 ${batchResult.failed.length} 条`"
        >
          <ul
            v-if="batchResult.failed.length"
            class="error-list"
          >
            <li
              v-for="item in batchResult.failed"
              :key="item.username"
            >
              {{ item.username }}：{{ item.message }}
            </li>
          </ul>
        </el-alert>
      </div>
      <template #footer>
        <el-button @click="batchDialog = false">
          关闭
        </el-button>
        <el-button
          type="primary"
          :loading="batchSubmitting"
          :disabled="!batchPreview.items.length"
          @click="doBatchAdd"
        >
          导入 {{ batchPreview.items.length }} 人
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="inviteDialog"
      title="生成邀请链接"
      width="520px"
      @closed="resetInviteForm"
    >
      <el-form
        ref="inviteFormRef"
        :model="inviteForm"
        :rules="inviteRules"
        label-width="90px"
      >
        <el-form-item
          label="授予角色"
          prop="role"
        >
          <el-select
            v-model="inviteForm.role"
            style="width: 100%"
          >
            <el-option
              label="使用空间管理员"
              value="tenant_admin"
            />
            <el-option
              label="专家"
              value="expert"
            />
            <el-option
              label="操作员"
              value="operator"
            />
            <el-option
              label="访客"
              value="viewer"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="有效期">
          <el-select
            v-model="inviteForm.expires_in_days"
            style="width: 100%"
          >
            <el-option
              :value="1"
              label="1 天"
            />
            <el-option
              :value="3"
              label="3 天"
            />
            <el-option
              :value="7"
              label="7 天"
            />
            <el-option
              :value="30"
              label="30 天"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="inviteForm.note"
            maxlength="120"
            placeholder="可选，如：生产部新同事"
          />
        </el-form-item>
      </el-form>

      <div
        v-if="inviteLink"
        class="invite-link-box"
      >
        <el-input
          :model-value="inviteLink"
          readonly
        >
          <template #append>
            <el-button @click="copyInviteLink">
              复制
            </el-button>
          </template>
        </el-input>
        <p class="dialog-tip">
          对方打开链接即可创建账号并加入空间，链接仅本次显示。
        </p>
      </div>

      <template #footer>
        <el-button @click="inviteDialog = false">
          关闭
        </el-button>
        <el-button
          type="primary"
          :loading="inviting"
          @click="doCreateInvitation"
        >
          {{ inviteLink ? '再生成一个' : '生成链接' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
  import { tenantApi } from '@/api/modules'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'
  import type { BatchAddMembersResult, Invitation, Member, MemberRole } from '@/types/api'

  const activeTab = ref<'members' | 'invites'>('members')

  const members = ref<Member[]>([])
  const listState = ref<ViewState>('loading')

  const invitations = ref<Invitation[]>([])
  const inviteState = ref<ViewState>('loading')

  // ---------- 角色与格式化 ----------
  const ROLE_LABELS: Record<string, string> = {
    tenant_admin: '使用空间管理员',
    expert: '专家',
    operator: '操作员',
    viewer: '访客',
  }
  const ROLE_ALIASES: Record<string, MemberRole> = {
    tenant_admin: 'tenant_admin',
    管理员: 'tenant_admin',
    空间管理员: 'tenant_admin',
    使用空间管理员: 'tenant_admin',
    expert: 'expert',
    专家: 'expert',
    operator: 'operator',
    操作员: 'operator',
    viewer: 'viewer',
    访客: 'viewer',
  }

  function roleLabel(role?: string): string {
    return ROLE_LABELS[role ?? ''] ?? role ?? '—'
  }

  function formatTime(t?: string): string {
    if (!t) return '—'
    const d = new Date(t)
    return isNaN(d.getTime()) ? t : d.toLocaleString('zh-CN')
  }

  const INVITE_STATUS_LABELS: Record<string, string> = {
    pending: '待接受',
    accepted: '已接受',
    revoked: '已撤销',
    expired: '已过期',
  }
  function inviteStatusLabel(status: string): string {
    return INVITE_STATUS_LABELS[status] ?? status
  }
  function inviteTagType(status: string): 'success' | 'warning' | 'info' | 'danger' {
    if (status === 'accepted') return 'success'
    if (status === 'pending') return 'warning'
    if (status === 'expired') return 'danger'
    return 'info'
  }

  // ---------- 成员列表 ----------
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

  async function loadInvitations() {
    inviteState.value = 'loading'
    try {
      invitations.value = await tenantApi.invitations()
      inviteState.value = invitations.value.length ? 'ready' : 'empty'
    } catch (e) {
      inviteState.value = classifyError(e)
    }
  }

  function onTabChange(name: string | number) {
    if (name === 'invites' && inviteState.value === 'loading') void loadInvitations()
  }

  // ---------- 添加单个成员 ----------
  const addDialog = ref(false)
  const adding = ref(false)
  const addFormRef = ref<FormInstance>()
  const addForm = reactive({ username: '', role: '' as '' | MemberRole, display_name: '' })
  const addRules: FormRules = {
    username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
    role: [{ required: true, message: '请选择角色', trigger: 'change' }],
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
        `确定移除成员「${m.display_name || m.username}」吗？该操作将收回其使用空间访问权限。`,
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

  // ---------- 批量导入 ----------
  const batchDialog = ref(false)
  const batchText = ref('')
  const batchSubmitting = ref(false)
  const batchResult = ref<BatchAddMembersResult | null>(null)

  interface ParsedBatch {
    items: Array<{ username: string; role: MemberRole }>
    failed: Array<{ line: number; message: string }>
  }

  /** 解析逐行文本：支持逗号/中文逗号/制表符/空格分隔，角色支持中文别名。 */
  const batchPreview = computed<ParsedBatch>(() => {
    const items: ParsedBatch['items'] = []
    const failed: ParsedBatch['failed'] = []
    const seen = new Set<string>()
    batchText.value.split('\n').forEach((raw, index) => {
      const lineNo = index + 1
      const line = raw.trim()
      if (!line || line.startsWith('#')) return
      const parts = line.split(/[,，\t\s]+/).filter(Boolean)
      if (parts.length < 2) {
        failed.push({ line: lineNo, message: '缺少角色，请使用「用户名,角色」' })
        return
      }
      const [username, rawRole] = parts
      const role = ROLE_ALIASES[rawRole.toLowerCase()]
      if (!role) {
        failed.push({ line: lineNo, message: `未知角色「${rawRole}」` })
        return
      }
      if (seen.has(username)) {
        failed.push({ line: lineNo, message: '本批次内用户名重复' })
        return
      }
      seen.add(username)
      items.push({ username, role })
    })
    return { items, failed }
  })

  function resetBatch() {
    batchText.value = ''
    batchResult.value = null
  }

  async function doBatchAdd() {
    if (!batchPreview.value.items.length) return
    batchSubmitting.value = true
    try {
      batchResult.value = await tenantApi.batchAddMembers({
        members: batchPreview.value.items.map(i => ({ username: i.username, role: i.role })),
      })
      if (batchResult.value.added.length) {
        ElMessage.success(`已添加 ${batchResult.value.added.length} 位成员`)
        loadMembers()
      }
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      batchSubmitting.value = false
    }
  }

  // ---------- 邀请链接 ----------
  const inviteDialog = ref(false)
  const inviting = ref(false)
  const inviteLink = ref('')
  const inviteFormRef = ref<FormInstance>()
  const inviteForm = reactive<{ role: MemberRole; expires_in_days: number; note: string }>({
    role: 'operator',
    expires_in_days: 7,
    note: '',
  })
  const inviteRules: FormRules = {
    role: [{ required: true, message: '请选择角色', trigger: 'change' }],
  }

  function resetInviteForm() {
    inviteLink.value = ''
    inviteForm.note = ''
  }

  async function doCreateInvitation() {
    await inviteFormRef.value?.validate()
    inviting.value = true
    try {
      const inv = await tenantApi.createInvitation({
        role: inviteForm.role,
        expires_in_days: inviteForm.expires_in_days,
        note: inviteForm.note || undefined,
      })
      inviteLink.value = `${window.location.origin}${inv.invite_path ?? ''}`
      ElMessage.success('邀请链接已生成')
      void loadInvitations()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      inviting.value = false
    }
  }

  async function copyInviteLink() {
    if (!inviteLink.value) return
    try {
      await navigator.clipboard.writeText(inviteLink.value)
      ElMessage.success('链接已复制')
    } catch {
      ElMessage.warning('复制失败，请手动选择链接复制')
    }
  }

  async function doRevoke(inv: Invitation) {
    try {
      await ElMessageBox.confirm('撤销后该链接立即失效，确定撤销吗？', '撤销邀请', {
        type: 'warning',
        confirmButtonText: '撤销',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
    try {
      await tenantApi.revokeInvitation(inv.id)
      ElMessage.success('邀请已撤销')
      loadInvitations()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  onMounted(() => {
    void loadMembers()
    void loadInvitations()
  })
</script>

<style scoped>
  .members {
    padding: 4px;
  }
  .page-head {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 16px;
  }
  .page-head .page-title {
    margin-bottom: 0;
  }
  .page-actions .el-button + .el-button {
    margin-left: 0;
  }
  .member-tabs {
    margin-top: 4px;
  }
  .invite-hint {
    margin-bottom: 12px;
  }
  .dialog-tip {
    margin: 0 0 12px;
    color: var(--el-text-color-secondary);
    font-size: 13px;
    line-height: 1.6;
  }
  .batch-preview,
  .batch-result {
    margin-top: 12px;
  }
  .error-list {
    margin: 6px 0 0;
    padding-left: 18px;
    font-size: 13px;
    line-height: 1.7;
  }
  .invite-link-box {
    margin-top: 16px;
    padding-top: 16px;
    border-top: 1px dashed var(--el-border-color);
  }
</style>

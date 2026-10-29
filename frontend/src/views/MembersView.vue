<template>
  <div class="members">
    <div class="page-head">
      <h2 class="page-title">
        成员管理
      </h2>
      <div class="page-actions">
        <template v-if="activeTab === 'members'">
          <el-tooltip
            :disabled="canManage"
            content="专家只能维护普通成员与操作员，新建/导入成员需管理员权限"
            placement="top"
          >
            <span>
              <el-button
                type="primary"
                :disabled="!canManage"
                @click="openCreate"
              >
                新建成员
              </el-button>
            </span>
          </el-tooltip>
          <el-tooltip
            :disabled="canManage"
            content="批量导入需要成员管理权限"
            placement="top"
          >
            <span>
              <el-button
                :disabled="!canManage"
                @click="batchDialog = true"
              >
                批量导入
              </el-button>
            </span>
          </el-tooltip>
        </template>
        <el-button
          v-if="activeTab === 'registrations'"
          @click="loadRegistrations"
        >
          刷新
        </el-button>
        <el-button
          v-if="activeTab === 'invites'"
          type="primary"
          @click="inviteDialog = true"
        >
          生成邀请链接
        </el-button>
        <el-button
          v-if="activeTab === 'invites'"
          @click="loadInvitations"
        >
          刷新
        </el-button>
        <el-button
          v-if="activeTab === 'members'"
          link
          type="primary"
          @click="loadMembers"
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
          <div class="table-toolbar">
            <el-input
              v-model="keyword"
              placeholder="搜索用户名 / 姓名 / 邮箱 / 手机号"
              clearable
              style="width: 260px"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
            <el-select
              v-model="roleFilter"
              placeholder="全部角色"
              clearable
              style="width: 160px"
            >
              <el-option
                v-for="option in roleFilterOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
            <el-select
              v-model="statusFilter"
              placeholder="全部状态"
              clearable
              style="width: 140px"
            >
              <el-option
                v-for="option in STATUS_OPTIONS"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
            <span class="toolbar-total">共 {{ memberTotal }} 位成员</span>
          </div>

          <AsyncState
            :state="listState"
            empty-text="暂无成员，先添加第一位同事吧"
            :empty-action-text="canManage ? '新建成员' : ''"
            @action="openCreate"
            @retry="loadMembers"
          >
            <el-table
              :data="filteredMembers"
              stripe
              :empty-text="keyword || roleFilter || statusFilter ? '没有匹配的成员' : '暂无成员'"
            >
              <el-table-column
                label="成员"
                min-width="170"
              >
                <template #default="{ row }">
                  <div class="member-cell">
                    <span class="member-name">{{ row.display_name || row.username }}</span>
                    <span class="member-sub">@{{ row.username }}</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column
                label="角色"
                width="130"
              >
                <template #default="{ row }">
                  <el-tag effect="plain">
                    {{ row.role_name || roleLabel(row.role) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column
                label="状态"
                width="100"
              >
                <template #default="{ row }">
                  <el-tag :type="statusMeta(row.status).type">
                    {{ statusMeta(row.status).label }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column
                label="邮箱 / 手机"
                min-width="190"
              >
                <template #default="{ row }">
                  <div class="contact-cell">
                    <span>{{ row.email || '—' }}</span>
                    <span class="member-sub">{{ row.phone || '—' }}</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column
                label="最近活跃"
                width="170"
              >
                <template #default="{ row }">
                  {{ formatTime(row.last_login_at) }}
                </template>
              </el-table-column>
              <el-table-column
                label="7 日 Token"
                width="110"
              >
                <template #default="{ row }">
                  {{ formatNumber(row.tokens_7d) }}
                </template>
              </el-table-column>
              <el-table-column
                label="操作"
                width="310"
                fixed="right"
              >
                <template #default="{ row }">
                  <el-tooltip
                    :disabled="canOperate(row as MemberRow)"
                    content="无权管理该成员（负责人与管理员不可被下级修改）"
                    placement="top"
                  >
                    <span>
                      <el-button
                        link
                        type="primary"
                        :disabled="!canOperate(row as MemberRow)"
                        @click="openEdit(row as MemberRow)"
                      >
                        编辑
                      </el-button>
                    </span>
                  </el-tooltip>

                  <el-tooltip
                    :disabled="canGrant && row.role !== 'yard_owner'"
                    :content="
                      row.role === 'yard_owner'
                        ? '负责人始终拥有全部权限'
                        : '只有小院负责人可以逐人授权'
                    "
                    placement="top"
                  >
                    <span>
                      <el-button
                        link
                        type="primary"
                        :disabled="!canGrant || row.role === 'yard_owner'"
                        @click="openPermissions(row as MemberRow)"
                      >
                        权限
                      </el-button>
                    </span>
                  </el-tooltip>

                  <el-tooltip
                    :disabled="canReset && !isSelf(row as MemberRow)"
                    :content="
                      isSelf(row as MemberRow)
                        ? '请在「个人资料」中修改自己的密码'
                        : '无重置密码权限'
                    "
                    placement="top"
                  >
                    <span>
                      <el-button
                        link
                        type="primary"
                        :disabled="!canReset || isSelf(row as MemberRow)"
                        @click="confirmReset(row as MemberRow)"
                      >
                        重置密码
                      </el-button>
                    </span>
                  </el-tooltip>

                  <el-tooltip
                    :disabled="canToggleStatus(row as MemberRow)"
                    :content="statusLockReason(row as MemberRow)"
                    placement="top"
                  >
                    <span>
                      <el-button
                        link
                        :type="row.status === 'active' ? 'warning' : 'success'"
                        :disabled="!canToggleStatus(row as MemberRow)"
                        @click="toggleStatus(row as MemberRow)"
                      >
                        {{ row.status === 'active' ? '停用' : '启用' }}
                      </el-button>
                    </span>
                  </el-tooltip>

                  <el-tooltip
                    :disabled="canRemove(row as MemberRow)"
                    :content="removeLockReason(row as MemberRow)"
                    placement="top"
                  >
                    <span>
                      <el-button
                        link
                        type="danger"
                        :disabled="!canRemove(row as MemberRow)"
                        @click="confirmRemove(row as MemberRow)"
                      >
                        移除
                      </el-button>
                    </span>
                  </el-tooltip>
                </template>
              </el-table-column>
            </el-table>
            <p
              v-if="memberTotal > members.length"
              class="pagination-tip"
            >
              当前仅加载前 {{ members.length }} 位成员（共 {{ memberTotal }} 位），
              更多成员请使用搜索或筛选定位。
            </p>
          </AsyncState>
        </el-card>
      </el-tab-pane>

      <el-tab-pane
        v-if="canApprove"
        :label="registrationTabLabel"
        name="registrations"
      >
        <el-alert
          type="info"
          :closable="false"
          show-icon
          class="invite-hint"
          title="申请加入小院的账号会进入待审核队列。通过后自动成为成员，拒绝需填写原因。"
        />
        <el-card>
          <AsyncState
            :state="registrationState"
            empty-text="暂无待审核的注册申请。成员通过「加入小院」提交申请后会出现在这里"
            @retry="loadRegistrations"
          >
            <el-table
              :data="registrationItems"
              stripe
            >
              <el-table-column
                label="申请人"
                min-width="160"
              >
                <template #default="{ row }">
                  <div class="member-cell">
                    <span class="member-name">{{ row.display_name || row.username }}</span>
                    <span class="member-sub">@{{ row.username }}</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column
                label="联系方式"
                min-width="190"
              >
                <template #default="{ row }">
                  <div class="contact-cell">
                    <span>{{ row.email || '—' }}</span>
                    <span class="member-sub">{{ row.phone || '—' }}</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column
                label="申请时间"
                width="180"
              >
                <template #default="{ row }">
                  {{ formatTime(row.created_at) }}
                </template>
              </el-table-column>
              <el-table-column
                label="备注"
                min-width="180"
                show-overflow-tooltip
              >
                <template #default="{ row }">
                  {{ row.note || '—' }}
                </template>
              </el-table-column>
              <el-table-column
                label="操作"
                width="150"
              >
                <template #default="{ row }">
                  <el-button
                    link
                    type="success"
                    @click="approveRegistration(row as RegistrationRequest)"
                  >
                    通过
                  </el-button>
                  <el-button
                    link
                    type="danger"
                    @click="rejectRegistration(row as RegistrationRequest)"
                  >
                    拒绝
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </AsyncState>
        </el-card>
      </el-tab-pane>

      <el-tab-pane
        v-if="canManage"
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

    <!-- 新建成员 -->
    <el-dialog
      v-model="createDialog"
      append-to-body
      title="新建成员"
      width="520px"
      @closed="resetCreateForm"
    >
      <el-form
        ref="createFormRef"
        :model="createForm"
        :rules="createRules"
        label-width="90px"
      >
        <el-form-item
          label="用户名"
          prop="username"
        >
          <el-input
            v-model="createForm.username"
            placeholder="3-32 位字母、数字或下划线"
          />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input
            v-model="createForm.display_name"
            maxlength="64"
            placeholder="可选"
          />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input
            v-model="createForm.email"
            placeholder="可选"
          />
        </el-form-item>
        <el-form-item label="手机号">
          <el-input
            v-model="createForm.phone"
            placeholder="可选"
          />
        </el-form-item>
        <el-form-item
          label="角色"
          prop="role"
        >
          <el-select
            v-model="createForm.role"
            style="width: 100%"
          >
            <el-option
              v-for="option in assignableRoleOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="初始密码">
          <el-input
            v-model="createForm.password"
            type="password"
            show-password
            placeholder="留空自动生成一次性初始密码"
          />
          <div class="field-note">
            留空由系统生成；填写需满足小院密码策略，成员首次登录后需修改。
          </div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="createForm.note"
            type="textarea"
            :rows="2"
            maxlength="300"
            placeholder="可选，仅记录在审计日志中"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="creating"
          @click="doCreate"
        >
          创建成员
        </el-button>
      </template>
    </el-dialog>

    <!-- 编辑成员 -->
    <el-dialog
      v-model="editDrawer"
      append-to-body
      :title="`编辑成员：${editTarget?.display_name || editTarget?.username || ''}`"
      width="460px"
      align-center
      @closed="editTarget = null"
    >
      <el-form
        v-if="editTarget"
        label-width="90px"
      >
        <el-form-item label="用户名">
          <el-input
            :model-value="editTarget.username"
            disabled
          />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input
            v-model="editForm.display_name"
            maxlength="64"
            :disabled="!canEditProfile"
          />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input
            v-model="editForm.email"
            :disabled="!canEditProfile"
            placeholder="留空表示清除"
          />
        </el-form-item>
        <el-form-item label="手机号">
          <el-input
            v-model="editForm.phone"
            :disabled="!canEditProfile"
            placeholder="留空表示清除"
          />
        </el-form-item>
        <el-form-item label="角色">
          <el-select
            v-model="editForm.role"
            style="width: 100%"
            :disabled="!roleEditable"
          >
            <el-option
              v-for="option in editRoleOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <div
            v-if="!roleEditable"
            class="field-note"
          >
            {{ roleLockReason }}
          </div>
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group
            v-model="editForm.status"
            :disabled="!statusEditable"
          >
            <el-radio value="active">
              在用
            </el-radio>
            <el-radio value="disabled">
              停用
            </el-radio>
          </el-radio-group>
          <div
            v-if="!statusEditable"
            class="field-note"
          >
            {{ statusLockReason(editTarget) }}
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDrawer = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="savingEdit"
          :disabled="!canEditProfile"
          @click="saveEdit"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 批量导入（保留用户名 + 角色模式） -->
    <el-dialog
      v-model="batchDialog"
      append-to-body
      title="批量导入成员"
      width="560px"
      @closed="resetBatch"
    >
      <p class="dialog-tip">
        每行一个已有账号，格式：<code>用户名,角色</code>。角色可填
        <code>yard_admin</code> / <code>expert</code> / <code>operator</code> / <code>viewer</code>，或直接写中文。
        后端暂未提供批量新建账号能力，因此批量导入仅支持「已有账号加入小院」模式；新同事请使用「邀请链接」。
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

    <!-- 邀请链接 -->
    <el-dialog
      v-model="inviteDialog"
      append-to-body
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
              v-for="option in assignableRoleOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
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
          对方打开链接即可创建账号并加入小院，链接仅本次显示。
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

    <!-- 初始密码 / 临时密码仅显示一次 -->
    <el-dialog
      v-model="passwordDialog"
      append-to-body
      :title="passwordTitle"
      width="460px"
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="密码仅显示一次，请立即复制并通过安全渠道告知成员；关闭后无法再次查看。"
      />
      <div class="password-box">
        <code class="password-text">{{ passwordValue }}</code>
        <el-button
          size="small"
          @click="copyPassword"
        >
          复制
        </el-button>
      </div>
      <p class="dialog-tip">
        成员首次登录后需修改密码。
      </p>
      <template #footer>
        <el-button
          type="primary"
          @click="passwordDialog = false"
        >
          我已保存
        </el-button>
      </template>
    </el-dialog>

    <MemberPermissionDialog
      v-model="permissionDialog"
      :user-id="permissionUserId"
      :can-manage="canGrant"
      @saved="loadMembers"
    />
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
  import { Search } from '@element-plus/icons-vue'
  import {
    governanceApi,
    type MemberRow,
    type MemberRole,
    type RegistrationRequest,
    type UpdateMemberPayload,
  } from '@/api/v3'
  import { yardApi } from '@/api/modules'
  import AsyncState from '@/components/AsyncState.vue'
  import MemberPermissionDialog from '@/components/settings/MemberPermissionDialog.vue'
  import { ROLE_LABELS } from '@/components/settings/permissions'
  import { useAuthStore } from '@/stores/auth'
  import { formatTime } from '@/utils/format'
  import { classifyError, type ViewState } from '@/utils/status'
  import type { BatchAddMembersResult, Invitation } from '@/types/api'

  const auth = useAuthStore()

  const activeTab = ref<'members' | 'registrations' | 'invites'>('members')

  // ---------- 角色 / 权限判定 ----------
  const isOwner = computed(
    () => auth.roles.includes('yard_owner') || auth.roles.includes('platform_admin'),
  )
  const isAdmin = computed(() => auth.roles.includes('yard_admin'))
  /** 成员管理（新建/移除/邀请）：负责人或管理员 */
  const canManage = computed(() => isOwner.value || isAdmin.value)
  const canApprove = computed(() => canManage.value)
  const canReset = computed(() => canManage.value)
  /** 逐人授权仅负责人可操作（与后端保持一致） */
  const canGrant = computed(() => isOwner.value)

  const customRoleOptions = ref<Array<{ value: string; label: string }>>([])

  const assignableRoleOptions = computed(() => {
    const all = [
      { value: 'yard_admin', label: '小院管理员' },
      { value: 'expert', label: '专家' },
      { value: 'operator', label: '科研操作员' },
      { value: 'viewer', label: '普通成员' },
      ...customRoleOptions.value,
    ]
    if (isOwner.value) return all
    if (isAdmin.value) return all.filter(option => option.value !== 'yard_admin')
    return all.filter(option => option.value === 'operator' || option.value === 'viewer')
  })

  const customRoleLabels = computed(
    () => new Map(customRoleOptions.value.map(option => [option.value, option.label])),
  )

  function roleLabel(role?: string): string {
    if (!role) return '—'
    return ROLE_LABELS[role] ?? customRoleLabels.value.get(role) ?? role
  }

  async function loadCustomRoles(): Promise<void> {
    if (!canManage.value) return
    try {
      const payload = await governanceApi.roles()
      customRoleOptions.value = payload.roles
        .filter(role => !role.is_system)
        .map(role => ({ value: role.code, label: role.name }))
    } catch {
      /* 角色列表加载失败不影响成员管理主流程 */
    }
  }

  // ---------- 逐人授权 ----------
  const permissionDialog = ref(false)
  const permissionUserId = ref('')

  function openPermissions(row: MemberRow): void {
    permissionUserId.value = row.user_id
    permissionDialog.value = true
  }

  function formatNumber(value?: number | null): string {
    return new Intl.NumberFormat('zh-CN').format(Number(value ?? 0))
  }

  const STATUS_META: Record<
    string,
    { label: string; type: 'success' | 'danger' | 'warning' | 'info' }
  > = {
    active: { label: '在用', type: 'success' },
    disabled: { label: '已停用', type: 'danger' },
    pending: { label: '待审核', type: 'warning' },
    invited: { label: '已邀请', type: 'info' },
  }
  const STATUS_OPTIONS = [
    { value: 'active', label: '在用' },
    { value: 'disabled', label: '已停用' },
    { value: 'pending', label: '待审核' },
  ]

  function statusMeta(status: string): { label: string; type: 'success' | 'danger' | 'warning' | 'info' } {
    return STATUS_META[status] ?? { label: status || '未知', type: 'info' }
  }

  function isSelf(row: MemberRow): boolean {
    if (!auth.user) return false
    if (auth.user.id && row.user_id) return auth.user.id === row.user_id
    return auth.user.username === row.username
  }

  /** 角色层级：负责人不可被下级修改；管理员不能动管理员；专家只能动操作员/普通成员 */
  function canOperate(row: MemberRow): boolean {
    if (isSelf(row)) return true
    if (row.role === 'yard_owner') return false
    if (isOwner.value) return true
    if (isAdmin.value) return row.role !== 'yard_admin'
    if (auth.roles.includes('expert')) return row.role === 'operator' || row.role === 'viewer'
    return false
  }

  function canToggleStatus(row: MemberRow): boolean {
    return canOperate(row) && !isSelf(row) && row.role !== 'yard_owner'
  }

  function statusLockReason(row: MemberRow): string {
    if (isSelf(row)) return '不能修改自己的状态，请联系其他管理员'
    if (row.role === 'yard_owner') return '小院负责人不能被停用'
    return '无权管理该成员'
  }

  function canRemove(row: MemberRow): boolean {
    return canManage.value && !isSelf(row) && row.role !== 'yard_owner'
  }

  function removeLockReason(row: MemberRow): string {
    if (isSelf(row)) return '不能移除自己'
    if (row.role === 'yard_owner') return '不能移除小院负责人'
    return '无移除成员权限'
  }

  // ---------- 成员列表 ----------
  const members = ref<MemberRow[]>([])
  const memberTotal = ref(0)
  const listState = ref<ViewState>('loading')
  const keyword = ref('')
  const roleFilter = ref('')
  const statusFilter = ref('')

  const roleFilterOptions = computed(() => {
    const map = new Map<string, string>()
    members.value.forEach(row => {
      if (!row.role) return
      map.set(row.role, row.role_name || roleLabel(row.role))
    })
    return Array.from(map.entries()).map(([value, label]) => ({ value, label }))
  })

  const filteredMembers = computed(() => {
    const key = keyword.value.trim().toLowerCase()
    return members.value.filter(row => {
      if (roleFilter.value && row.role !== roleFilter.value) return false
      if (statusFilter.value && row.status !== statusFilter.value) return false
      if (!key) return true
      return [row.username, row.display_name, row.email, row.phone]
        .filter(Boolean)
        .some(value => String(value).toLowerCase().includes(key))
    })
  })

  async function loadMembers(): Promise<void> {
    listState.value = 'loading'
    try {
      const data = await governanceApi.listMembers()
      members.value = data.items ?? []
      memberTotal.value = data.total || members.value.length
      listState.value = members.value.length ? 'ready' : 'empty'
    } catch (e) {
      listState.value = classifyError(e)
    }
  }

  function onTabChange(name: string | number): void {
    if (name === 'registrations' && registrationState.value === 'loading') {
      void loadRegistrations()
    }
    if (name === 'invites' && inviteState.value === 'loading') {
      void loadInvitations()
    }
  }

  // ---------- 新建成员 ----------
  const createDialog = ref(false)
  const creating = ref(false)
  const createFormRef = ref<FormInstance>()
  const createForm = reactive({
    username: '',
    display_name: '',
    email: '',
    phone: '',
    role: 'viewer',
    password: '',
    note: '',
  })
  const createRules: FormRules = {
    username: [
      { required: true, message: '请输入用户名', trigger: 'blur' },
      {
        pattern: /^[A-Za-z0-9_]{3,32}$/,
        message: '用户名需为 3-32 位字母、数字或下划线',
        trigger: 'blur',
      },
    ],
    role: [{ required: true, message: '请选择角色', trigger: 'change' }],
  }

  function openCreate(): void {
    if (!canManage.value) return
    createDialog.value = true
  }

  function resetCreateForm(): void {
    createForm.username = ''
    createForm.display_name = ''
    createForm.email = ''
    createForm.phone = ''
    createForm.role = 'viewer'
    createForm.password = ''
    createForm.note = ''
    createFormRef.value?.clearValidate()
  }

  interface CreateMemberRaw {
    member?: MemberRow
    initial_password?: string | null
    temporary_password?: string | null
    user_id?: string
    username?: string
  }

  interface CreateMemberInput {
    username: string
    role: MemberRole
    display_name?: string
    email?: string
    phone?: string
    password?: string
    note?: string
  }

  async function doCreate(): Promise<void> {
    await createFormRef.value?.validate()
    creating.value = true
    try {
      // 后端 MemberCreateRequest 额外接收 note（v3 类型尚未补充），故走 unknown 断言
      const payload: CreateMemberInput = {
        username: createForm.username.trim(),
        display_name: createForm.display_name.trim() || undefined,
        email: createForm.email.trim() || undefined,
        phone: createForm.phone.trim() || undefined,
        role: createForm.role,
        password: createForm.password || undefined,
        note: createForm.note.trim() || undefined,
      }
      const raw = (await governanceApi.createMember(
        payload as unknown as Parameters<typeof governanceApi.createMember>[0],
      )) as unknown as CreateMemberRaw
      createDialog.value = false
      await loadMembers()
      const initial = raw?.initial_password ?? raw?.temporary_password ?? null
      if (initial) {
        passwordTitle.value = '初始密码仅显示一次'
        passwordValue.value = initial
        passwordDialog.value = true
      } else {
        const name = raw?.member?.display_name || raw?.member?.username || payload.username
        ElMessage.success(`成员「${name}」已添加`)
      }
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      creating.value = false
    }
  }

  // ---------- 编辑成员 ----------
  const editDrawer = ref(false)
  const savingEdit = ref(false)
  const editTarget = ref<MemberRow | null>(null)
  const editForm = reactive({
    display_name: '',
    email: '',
    phone: '',
    role: '',
    status: 'active',
  })

  const canEditProfile = computed(
    () => !!editTarget.value && (isSelf(editTarget.value) || canOperate(editTarget.value)),
  )
  const roleEditable = computed(
    () => !!editTarget.value && !isSelf(editTarget.value) && canOperate(editTarget.value),
  )
  const statusEditable = computed(
    () =>
      !!editTarget.value &&
      !isSelf(editTarget.value) &&
      canOperate(editTarget.value) &&
      editTarget.value.role !== 'yard_owner',
  )
  const roleLockReason = computed(() => {
    if (!editTarget.value) return ''
    if (isSelf(editTarget.value)) return '不能修改自己的角色'
    if (editTarget.value.role === 'yard_owner') return '负责人角色只能通过「负责人转让」变更'
    return '无权修改该成员的角色'
  })
  const editRoleOptions = computed(() => {
    const options = [...assignableRoleOptions.value]
    const current = editTarget.value?.role
    if (current && !options.some(option => option.value === current)) {
      options.unshift({
        value: current,
        label: editTarget.value?.role_name || roleLabel(current),
      })
    }
    return options
  })

  function openEdit(row: MemberRow): void {
    if (!canOperate(row)) return
    editTarget.value = row
    editForm.display_name = row.display_name ?? ''
    editForm.email = row.email ?? ''
    editForm.phone = row.phone ?? ''
    editForm.role = row.role
    editForm.status = row.status === 'disabled' ? 'disabled' : 'active'
    editDrawer.value = true
  }

  type MemberPatch = Omit<UpdateMemberPayload, 'email' | 'phone'> & {
    email?: string | null
    phone?: string | null
  }

  async function saveEdit(): Promise<void> {
    if (!editTarget.value) return
    const target = editTarget.value
    if (editForm.display_name.trim() === '') {
      ElMessage.warning('姓名不能为空')
      return
    }
    savingEdit.value = true
    try {
      const payload: MemberPatch = {
        display_name: editForm.display_name.trim(),
        email: editForm.email.trim() || null,
        phone: editForm.phone.trim() || null,
      }
      if (roleEditable.value) payload.role = editForm.role
      if (statusEditable.value) payload.status = editForm.status as 'active' | 'disabled'
      await governanceApi.updateMember(target.user_id, payload as UpdateMemberPayload)
      ElMessage.success('成员资料已更新')
      editDrawer.value = false
      await loadMembers()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      savingEdit.value = false
    }
  }

  // ---------- 初始密码 / 重置密码 ----------
  const passwordDialog = ref(false)
  const passwordTitle = ref('初始密码仅显示一次')
  const passwordValue = ref('')

  async function copyPassword(): Promise<void> {
    if (!passwordValue.value) return
    try {
      await navigator.clipboard.writeText(passwordValue.value)
      ElMessage.success('密码已复制')
    } catch {
      ElMessage.warning('复制失败，请手动选择后复制')
    }
  }

  async function confirmReset(row: MemberRow): Promise<void> {
    try {
      await ElMessageBox.confirm(
        `确定重置「${row.display_name || row.username}」的密码？重置后旧密码立即失效。`,
        '重置密码',
        { type: 'warning', confirmButtonText: '重置', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    try {
      const res = await governanceApi.resetPassword(row.user_id)
      passwordTitle.value = '临时密码仅显示一次'
      passwordValue.value = res.temporary_password
      passwordDialog.value = true
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  // ---------- 停用 / 启用 / 移除 ----------
  async function toggleStatus(row: MemberRow): Promise<void> {
    const next = row.status === 'active' ? 'disabled' : 'active'
    if (next === 'disabled') {
      try {
        await ElMessageBox.confirm(
          `确定停用「${row.display_name || row.username}」？停用后该成员无法登录本小院。`,
          '停用成员',
          { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' },
        )
      } catch {
        return
      }
    }
    try {
      await governanceApi.updateMember(row.user_id, { status: next })
      ElMessage.success(next === 'disabled' ? '成员已停用' : '成员已启用')
      await loadMembers()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  async function confirmRemove(row: MemberRow): Promise<void> {
    try {
      await ElMessageBox.confirm(
        `确定移除成员「${row.display_name || row.username}」吗？该操作将收回其小院访问权限。`,
        '移除确认',
        { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    try {
      await governanceApi.removeMember(row.user_id)
      ElMessage.success('已移除')
      await loadMembers()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  // ---------- 注册审核 ----------
  const registrationItems = ref<RegistrationRequest[]>([])
  const registrationState = ref<ViewState>('loading')
  const registrationTabLabel = computed(() =>
    registrationItems.value.length
      ? `注册审核（${registrationItems.value.length}）`
      : '注册审核',
  )

  interface RegistrationEnvelope {
    items?: RegistrationRequest[]
  }

  async function loadRegistrations(): Promise<void> {
    registrationState.value = 'loading'
    try {
      const raw = (await governanceApi.registrations('pending')) as unknown
      let items: RegistrationRequest[] = []
      if (Array.isArray(raw)) items = raw as RegistrationRequest[]
      else if (raw && typeof raw === 'object' && Array.isArray((raw as RegistrationEnvelope).items)) {
        items = (raw as RegistrationEnvelope).items ?? []
      }
      registrationItems.value = items
      registrationState.value = items.length ? 'ready' : 'empty'
    } catch (e) {
      registrationState.value = classifyError(e)
    }
  }

  async function approveRegistration(row: RegistrationRequest): Promise<void> {
    try {
      await ElMessageBox.confirm(
        `通过「${row.display_name || row.username}」的加入申请？通过后将自动成为本小院成员。`,
        '通过申请',
        { type: 'warning', confirmButtonText: '通过', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    try {
      await governanceApi.approveRegistration(row.id)
      ElMessage.success('已通过，申请人已加入小院')
      await Promise.all([loadRegistrations(), loadMembers()])
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  async function rejectRegistration(row: RegistrationRequest): Promise<void> {
    let reason = ''
    try {
      const result = await ElMessageBox.prompt(
        `拒绝「${row.display_name || row.username}」的加入申请，请填写原因（将展示给申请人）。`,
        '拒绝申请',
        {
          inputType: 'textarea',
          inputPlaceholder: '如：联系方式不完整，请补充后重新申请',
          confirmButtonText: '拒绝',
          cancelButtonText: '取消',
          inputValidator: (value: string) =>
            (value && value.trim().length >= 2) || '请填写至少 2 个字的拒绝原因',
        },
      )
      reason = result.value.trim()
    } catch {
      return
    }
    try {
      await governanceApi.rejectRegistration(row.id, reason)
      ElMessage.success('已拒绝该申请')
      await loadRegistrations()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  // ---------- 批量导入 ----------
  const batchDialog = ref(false)
  const batchText = ref('')
  const batchSubmitting = ref(false)
  const batchResult = ref<BatchAddMembersResult | null>(null)

  const ROLE_ALIASES: Record<string, string> = {
    yard_admin: 'yard_admin',
    管理员: 'yard_admin',
    小院管理员: 'yard_admin',
    expert: 'expert',
    专家: 'expert',
    operator: 'operator',
    操作员: 'operator',
    viewer: 'viewer',
    访客: 'viewer',
    普通成员: 'viewer',
  }

  interface ParsedBatch {
    items: Array<{ username: string; role: string }>
    failed: Array<{ line: number; message: string }>
  }

  const batchPreview = computed<ParsedBatch>(() => {
    const items: ParsedBatch['items'] = []
    const failed: ParsedBatch['failed'] = []
    const seen = new Set<string>()
    const allowed = new Set(assignableRoleOptions.value.map(option => option.value))
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
      if (!allowed.has(role)) {
        failed.push({ line: lineNo, message: `当前账号无权授予「${rawRole}」` })
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

  function resetBatch(): void {
    batchText.value = ''
    batchResult.value = null
  }

  async function doBatchAdd(): Promise<void> {
    if (!batchPreview.value.items.length) return
    batchSubmitting.value = true
    try {
      batchResult.value = await yardApi.batchAddMembers({
        members: batchPreview.value.items.map(item => ({
          username: item.username,
          role: item.role as 'yard_admin' | 'expert' | 'operator' | 'viewer',
        })),
      })
      if (batchResult.value.added.length) {
        ElMessage.success(`已添加 ${batchResult.value.added.length} 位成员`)
        await loadMembers()
      }
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      batchSubmitting.value = false
    }
  }

  // ---------- 邀请链接 ----------
  const invitations = ref<Invitation[]>([])
  const inviteState = ref<ViewState>('loading')
  const inviteDialog = ref(false)
  const inviting = ref(false)
  const inviteLink = ref('')
  const inviteFormRef = ref<FormInstance>()
  const inviteForm = reactive({ role: 'operator' as string, expires_in_days: 7, note: '' })
  const inviteRules: FormRules = {
    role: [{ required: true, message: '请选择角色', trigger: 'change' }],
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

  async function loadInvitations(): Promise<void> {
    inviteState.value = 'loading'
    try {
      invitations.value = await yardApi.invitations()
      inviteState.value = invitations.value.length ? 'ready' : 'empty'
    } catch (e) {
      inviteState.value = classifyError(e)
    }
  }

  function resetInviteForm(): void {
    inviteLink.value = ''
    inviteForm.note = ''
  }

  async function doCreateInvitation(): Promise<void> {
    await inviteFormRef.value?.validate()
    inviting.value = true
    try {
      const invitation = await yardApi.createInvitation({
        role: inviteForm.role as 'yard_admin' | 'expert' | 'operator' | 'viewer',
        expires_in_days: inviteForm.expires_in_days,
        note: inviteForm.note || undefined,
      })
      inviteLink.value = `${window.location.origin}${invitation.invite_path ?? ''}`
      ElMessage.success('邀请链接已生成')
      void loadInvitations()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      inviting.value = false
    }
  }

  async function copyInviteLink(): Promise<void> {
    if (!inviteLink.value) return
    try {
      await navigator.clipboard.writeText(inviteLink.value)
      ElMessage.success('链接已复制')
    } catch {
      ElMessage.warning('复制失败，请手动选择链接复制')
    }
  }

  async function doRevoke(inv: Invitation): Promise<void> {
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
      await yardApi.revokeInvitation(inv.id)
      ElMessage.success('邀请已撤销')
      await loadInvitations()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  onMounted(() => {
    void loadMembers()
    void loadCustomRoles()
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
  .page-actions > .el-button + .el-button {
    margin-left: 0;
  }
  .member-tabs {
    margin-top: 4px;
  }
  .table-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 12px;
  }
  .toolbar-total {
    margin-left: auto;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .member-cell {
    display: flex;
    flex-direction: column;
    line-height: 1.4;
  }
  .member-name {
    color: var(--ds-text-1);
    font-weight: 500;
  }
  .member-sub {
    font-size: 11px;
    color: var(--ds-text-3);
    font-family: ui-monospace, monospace;
  }
  .contact-cell {
    display: flex;
    flex-direction: column;
    line-height: 1.4;
  }
  .pagination-tip {
    margin: 10px 0 0;
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.6;
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
  .field-note {
    margin-top: 4px;
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.5;
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
  .password-box {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 14px;
    padding: 12px 14px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    background: var(--ds-field);
  }
  .password-text {
    flex: 1;
    font-family: ui-monospace, monospace;
    font-size: 16px;
    letter-spacing: 0.06em;
    color: var(--ds-accent-text);
    word-break: break-all;
  }
</style>

<template>
  <div class="settings-sso">
    <el-alert
      class="section"
      type="info"
      :closable="false"
      show-icon
      title="单点登录（OIDC）"
      description="用户通过单位身份提供方（如学校统一认证）登录。角色只来自小院成员关系，IdP 的角色映射最多授予访客/操作员/专家/管理员，永远不会授予负责人或平台管理员。"
    />

    <el-alert
      v-if="!canManage"
      class="section"
      type="warning"
      :closable="false"
      show-icon
      title="仅负责人或获得敏感授权的管理员可修改身份源配置。"
    />

    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>身份提供方（{{ providers.length }}）</span>
          <div class="page-actions">
            <el-button
              size="small"
              @click="load"
            >
              刷新
            </el-button>
            <el-button
              v-if="canManage"
              size="small"
              type="primary"
              @click="openCreate"
            >
              新增身份源
            </el-button>
          </div>
        </div>
      </template>

      <AsyncState
        :state="state"
        empty-text="尚未配置身份提供方；配置后登录页会出现第三方登录入口"
        @retry="load"
      >
        <el-table
          v-col-resize
          :data="providers"
          size="small"
          row-key="id"
        >
          <el-table-column
            prop="name"
            label="名称"
            min-width="140"
          />
          <el-table-column
            prop="issuer"
            label="Issuer"
            min-width="220"
            show-overflow-tooltip
          />
          <el-table-column
            label="角色映射"
            min-width="180"
          >
            <template #default="{ row }">
              <span
                v-if="!Object.keys(row.role_mapping || {}).length"
                class="muted"
              >全部默认 {{ roleLabel(row.default_role) }}</span>
              <template v-else>
                <el-tag
                  v-for="(role, group) in row.role_mapping"
                  :key="group"
                  size="small"
                  effect="plain"
                  class="mapping-tag"
                >
                  {{ group }} → {{ roleLabel(role) }}
                </el-tag>
              </template>
            </template>
          </el-table-column>
          <el-table-column
            label="密钥"
            width="90"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="row.client_secret_configured ? 'success' : 'danger'"
              >
                {{ row.client_secret_configured ? '已配置' : '缺失' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="状态"
            width="80"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="row.enabled ? 'success' : 'info'"
              >
                {{ row.enabled ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="最近探测"
            min-width="170"
          >
            <template #default="{ row }">
              <span v-if="row.last_error" class="error-text">{{ row.last_error }}</span>
              <span v-else-if="row.last_test_at">{{ formatTime(row.last_test_at) }}</span>
              <span v-else class="muted">未探测</span>
            </template>
          </el-table-column>
          <el-table-column
            v-if="canManage"
            label="操作"
            width="200"
            fixed="right"
          >
            <template #default="{ row }">
              <el-button
                text
                size="small"
                :loading="testingId === row.id"
                @click="testProvider(row as SsoProviderAdmin)"
              >
                测试
              </el-button>
              <el-button
                text
                size="small"
                @click="openEdit(row as SsoProviderAdmin)"
              >
                编辑
              </el-button>
              <el-button
                text
                size="small"
                type="danger"
                @click="removeProvider(row as SsoProviderAdmin)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </AsyncState>
    </el-card>

    <el-dialog
      v-model="dialogVisible"
      append-to-body
      :title="editingId ? '编辑身份提供方' : '新增身份提供方'"
      width="680px"
    >
      <el-form
        label-width="130px"
        :model="form"
      >
        <el-form-item
          label="名称"
          required
        >
          <el-input
            v-model="form.name"
            maxlength="120"
            placeholder="如：学校统一认证"
          />
        </el-form-item>
        <el-form-item
          label="Issuer"
          required
        >
          <el-input
            v-model="form.issuer"
            placeholder="https://sso.example.edu.cn"
          />
          <div class="note">
            后端会请求 {{ form.issuer || '<issuer>' }}/.well-known/openid-configuration 校验端。
          </div>
        </el-form-item>
        <el-form-item
          label="Client ID"
          required
        >
          <el-input v-model="form.client_id" />
        </el-form-item>
        <el-form-item label="Client Secret">
          <el-input
            v-model="form.client_secret"
            type="password"
            show-password
            :placeholder="editingId ? '留空表示沿用已存密钥' : '请输入客户端密钥'"
          />
        </el-form-item>
        <el-form-item
          label="回调地址"
          required
        >
          <el-input v-model="form.redirect_uri">
            <template #append>
              <el-button @click="fillDefaultRedirect">
                填默认
              </el-button>
            </template>
          </el-input>
          <div class="note">
            需在 IdP 侧登记同一地址；默认 {{ defaultRedirect }}。
          </div>
        </el-form-item>
        <el-form-item label="前端回跳地址">
          <el-input
            v-model="form.frontend_redirect_uri"
            placeholder="http://localhost:5173/sso/callback"
          />
          <div class="note">
            登录成功后浏览器回到这里；留空则用后端默认。
          </div>
        </el-form-item>
        <el-form-item label="Scopes">
          <el-input v-model="form.scopes" />
        </el-form-item>
        <el-form-item label="分组声明">
          <el-input v-model="form.group_claim" />
          <div class="note">
            IdP 在 id_token 里放用户分组的字段名，默认 groups。
          </div>
        </el-form-item>
        <el-form-item label="角色映射">
          <el-input
            v-model="roleMappingText"
            type="textarea"
            :rows="4"
            placeholder='{"lab": "expert", "ops": "operator"}'
          />
          <div class="note">
            JSON 对象：IdP 分组 → 小院角色，仅允许 {{ assignableRoles.join(' / ') }}；无命中时用默认角色。
          </div>
        </el-form-item>
        <el-form-item label="默认角色">
          <el-select
            v-model="form.default_role"
            style="width: 200px"
          >
            <el-option
              v-for="role in assignableRoles"
              :key="role"
              :label="roleLabel(role)"
              :value="role"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="邮箱域名">
          <el-select
            v-model="form.email_domains"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="留空表示不限制，如 cau.edu.cn"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
          <span class="note">停用后登录页不再展示该入口。</span>
        </el-form-item>
        <el-form-item label="自动建号">
          <el-switch v-model="form.auto_create_users" />
          <span class="note">关闭后只有已存在且邮箱匹配的账号能登录。</span>
        </el-form-item>
        <el-form-item label="按邮箱关联账号">
          <el-switch v-model="form.link_by_email" />
          <span class="note">首次登录时把身份绑定到同邮箱的本地账号。</span>
        </el-form-item>
        <el-form-item label="要求邮箱已验证">
          <el-switch v-model="form.require_email_verified" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="save"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="testResultVisible"
      append-to-body
      title="身份源探测结果"
      width="560px"
    >
      <el-descriptions
        v-if="testResult"
        :column="1"
        border
      >
        <el-descriptions-item label="Issuer">
          {{ testResult.issuer }}
        </el-descriptions-item>
        <el-descriptions-item label="授权端点">
          {{ testResult.authorization_endpoint }}
        </el-descriptions-item>
        <el-descriptions-item label="令牌端点">
          {{ testResult.token_endpoint }}
        </el-descriptions-item>
        <el-descriptions-item label="JWKS">
          {{ testResult.jwks_uri }}
        </el-descriptions-item>
        <el-descriptions-item label="签名公钥">
          {{ testResult.signing_keys }} 把
        </el-descriptions-item>
        <el-descriptions-item label="可用算法">
          {{ testResult.supported_algorithms.join(', ') || '—' }}
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { extractErrorMessage } from '@/api/client'
  import AsyncState from '@/components/AsyncState.vue'
  import {
    ssoApi,
    type SsoProviderAdmin,
    type SsoProviderPayload,
    type SsoTestResult,
  } from '@/api/v3'
  import { classifyError, type ViewState } from '@/utils/status'
  import { formatTime } from '@/utils/format'

  defineProps<{ canManage: boolean }>()

  const assignableRoles = ['yard_admin', 'expert', 'operator', 'viewer']
  const roleNames: Record<string, string> = {
    yard_admin: '小院管理员',
    expert: '专家',
    operator: '操作员',
    viewer: '访客',
  }

  const state = ref<ViewState>('loading')
  const providers = ref<SsoProviderAdmin[]>([])
  const testingId = ref('')

  const dialogVisible = ref(false)
  const editingId = ref<string | null>(null)
  const saving = ref(false)
  const roleMappingText = ref('')
  const testResultVisible = ref(false)
  const testResult = ref<SsoTestResult | null>(null)

  const defaultRedirect = computed(() => `${window.location.origin}/api/v1/auth/sso/callback`)

  function emptyForm() {
    return {
      name: '',
      issuer: '',
      client_id: '',
      client_secret: '',
      redirect_uri: defaultRedirect.value,
      frontend_redirect_uri: `${window.location.origin}/sso/callback`,
      scopes: 'openid email profile',
      group_claim: 'groups',
      default_role: 'viewer',
      email_domains: [] as string[],
      enabled: true,
      auto_create_users: true,
      link_by_email: true,
      require_email_verified: true,
    }
  }

  const form = reactive(emptyForm())

  function roleLabel(role: string): string {
    return roleNames[role] ?? role
  }

  async function load() {
    state.value = 'loading'
    try {
      providers.value = await ssoApi.list()
      state.value = providers.value.length ? 'ready' : 'empty'
    } catch (err) {
      state.value = classifyError(err)
    }
  }

  function openCreate() {
    editingId.value = null
    Object.assign(form, emptyForm())
    roleMappingText.value = ''
    dialogVisible.value = true
  }

  function openEdit(row: SsoProviderAdmin) {
    editingId.value = row.id
    Object.assign(form, {
      name: row.name,
      issuer: row.issuer,
      client_id: row.client_id,
      client_secret: '',
      redirect_uri: row.redirect_uri,
      frontend_redirect_uri: row.frontend_redirect_uri ?? '',
      scopes: row.scopes,
      group_claim: row.group_claim,
      default_role: row.default_role,
      email_domains: [...(row.email_domains ?? [])],
      enabled: row.enabled,
      auto_create_users: row.auto_create_users,
      link_by_email: row.link_by_email,
      require_email_verified: row.require_email_verified,
    })
    roleMappingText.value = Object.keys(row.role_mapping ?? {}).length
      ? JSON.stringify(row.role_mapping, null, 2)
      : ''
    dialogVisible.value = true
  }

  function fillDefaultRedirect() {
    form.redirect_uri = defaultRedirect.value
  }

  function parseRoleMapping(): Record<string, string> | null {
    const text = roleMappingText.value.trim()
    if (!text) return {}
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch {
      ElMessage.error('角色映射不是合法 JSON。')
      return null
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      ElMessage.error('角色映射必须是 JSON 对象。')
      return null
    }
    const out: Record<string, string> = {}
    for (const [group, role] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof role !== 'string' || !assignableRoles.includes(role)) {
        ElMessage.error(`分组「${group}」的角色非法：只允许 ${assignableRoles.join(' / ')}。`)
        return null
      }
      out[group] = role
    }
    return out
  }

  async function save() {
    if (!form.name.trim() || !form.issuer.trim() || !form.client_id.trim()) {
      ElMessage.warning('名称、Issuer 与 Client ID 必填。')
      return
    }
    if (!editingId.value && !form.client_secret.trim()) {
      ElMessage.warning('首次配置必须填写 Client Secret。')
      return
    }
    const mapping = parseRoleMapping()
    if (mapping === null) return

    const payload: SsoProviderPayload = {
      name: form.name.trim(),
      issuer: form.issuer.trim(),
      client_id: form.client_id.trim(),
      redirect_uri: form.redirect_uri.trim(),
      frontend_redirect_uri: form.frontend_redirect_uri.trim() || null,
      scopes: form.scopes.trim() || 'openid email profile',
      group_claim: form.group_claim.trim() || 'groups',
      role_mapping: mapping,
      default_role: form.default_role,
      email_domains: form.email_domains,
      enabled: form.enabled,
      auto_create_users: form.auto_create_users,
      link_by_email: form.link_by_email,
      require_email_verified: form.require_email_verified,
    }
    if (form.client_secret.trim()) payload.client_secret = form.client_secret.trim()

    saving.value = true
    try {
      if (editingId.value) {
        await ssoApi.update(editingId.value, payload)
        ElMessage.success('身份源已更新。')
      } else {
        await ssoApi.create(payload)
        ElMessage.success('身份源已创建，可在登录页看到入口。')
      }
      dialogVisible.value = false
      await load()
    } catch (err) {
      const { message } = extractErrorMessage(err)
      ElMessage.error(message)
    } finally {
      saving.value = false
    }
  }

  async function testProvider(row: SsoProviderAdmin) {
    testingId.value = row.id
    try {
      testResult.value = await ssoApi.test(row.id)
      testResultVisible.value = true
      await load()
    } catch (err) {
      const { message } = extractErrorMessage(err)
      ElMessage.error(`探测失败：${message}`)
      await load()
    } finally {
      testingId.value = ''
    }
  }

  async function removeProvider(row: SsoProviderAdmin) {
    try {
      await ElMessageBox.confirm(
        `删除「${row.name}」后，已绑定该身份源的登录方式立即失效（本地账号保留）。`,
        '删除身份提供方',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    try {
      await ssoApi.remove(row.id)
      ElMessage.success('已删除。')
      await load()
    } catch (err) {
      const { message } = extractErrorMessage(err)
      ElMessage.error(message)
    }
  }

  onMounted(load)
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
  .page-actions {
    display: flex;
    gap: 8px;
  }
  .mapping-tag {
    margin: 0 6px 4px 0;
  }
  .muted {
    color: var(--ds-text-3);
  }
  .error-text {
    color: var(--el-color-danger);
  }
  .note {
    margin-top: 4px;
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.5;
  }
</style>

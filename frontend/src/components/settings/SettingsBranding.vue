<template>
  <div class="settings-branding">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="intro"
    >
      <template #title>
        白标不 fork：这里改的 logo / 名称 / 主色会即时应用到侧栏、登录页、注册与分享页，
        标题栏与按钮主色一并跟着变。安装包名称与图标是构建期产物，运行期改不了。
      </template>
    </el-alert>

    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>基本文案</span>
          <el-button
            type="primary"
            size="small"
            :loading="saving"
            @click="save"
          >
            保存
          </el-button>
        </div>
      </template>

      <el-form
        label-width="140px"
        class="settings-form"
      >
        <el-form-item label="门户名称">
          <el-input
            v-model="form.app_name"
            maxlength="40"
            show-word-limit
            :placeholder="data?.defaults.app_name"
            class="wide"
          />
          <div class="note">
            登录页大标题、浏览器标签页标题都用它；留空恢复默认「{{ data?.defaults.app_name }}」。
          </div>
        </el-form-item>

        <el-form-item label="侧栏简称">
          <el-input
            v-model="form.short_name"
            maxlength="8"
            show-word-limit
            :placeholder="data?.defaults.short_name"
            class="narrow"
          />
          <div class="note">
            侧栏 logo 右侧那行字，建议 2–4 字；未上传 logo 时方块里显示它的首字。
          </div>
        </el-form-item>

        <el-form-item label="登录页标语">
          <el-input
            v-model="form.login_tagline"
            maxlength="40"
            show-word-limit
            :placeholder="data?.defaults.login_tagline"
            class="wide"
          />
        </el-form-item>

        <el-form-item label="分享页落款">
          <el-input
            v-model="form.footer_text"
            maxlength="80"
            show-word-limit
            :placeholder="data?.defaults.footer_text"
            class="wide"
          />
          <div class="note">
            分享出去的页面底部那行「由 … 生成」，用于对外露出单位名称。
          </div>
        </el-form-item>

        <el-form-item label="主色">
          <div class="color-row">
            <el-color-picker v-model="colorPick" />
            <el-input
              v-model="form.primary_color"
              maxlength="7"
              placeholder="#6366F1"
              class="color-input"
            />
            <el-button
              size="small"
              text
              @click="resetColor"
            >
              恢复默认
            </el-button>
          </div>
          <div class="note">
            支持 #RRGGBB 或 #RGB；会同时改按钮、链接与选中态。留空恢复默认色。
          </div>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card class="section">
      <template #header>
        <span>门户 logo</span>
      </template>

      <div class="logo-row">
        <div class="logo-preview light">
          <img
            v-if="store.logoUrl"
            :src="store.logoUrl"
            alt="当前 logo"
          >
          <span
            v-else
            class="logo-fallback"
          >{{ (form.short_name || data?.defaults.short_name || '科').slice(0, 1) }}</span>
        </div>
        <div class="logo-preview dark">
          <img
            v-if="store.logoUrl"
            :src="store.logoUrl"
            alt="当前 logo"
          >
          <span
            v-else
            class="logo-fallback"
          >{{ (form.short_name || data?.defaults.short_name || '科').slice(0, 1) }}</span>
        </div>
        <div class="logo-actions">
          <input
            ref="fileRef"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            hidden
            @change="onPick"
          >
          <el-button
            :loading="uploading"
            @click="fileRef?.click()"
          >
            {{ store.logoUrl ? '更换 logo' : '上传 logo' }}
          </el-button>
          <el-button
            v-if="store.logoUrl"
            text
            type="danger"
            :disabled="uploading"
            @click="removeLogo"
          >
            移除
          </el-button>
          <div class="note">
            PNG / JPG / WEBP，≤{{ data?.logo_max_mb ?? 1 }}MB，建议正方形。
            不收 SVG（可内嵌脚本，登录页展示有 XSS 风险）。
          </div>
        </div>
      </div>
    </el-card>

    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>门户域名</span>
          <el-button
            type="primary"
            size="small"
            :loading="saving"
            @click="save"
          >
            保存
          </el-button>
        </div>
      </template>

      <el-form label-width="140px">
        <el-form-item label="对外域名">
          <el-select
            v-model="form.domains"
            multiple
            filterable
            allow-create
            default-first-option
            :reserve-keyword="false"
            :multiple-limit="data?.max_domains ?? 5"
            placeholder="如 portal.example.edu.cn，输入后回车添加"
            class="wide"
          />
          <div class="note">
            只记 host（自动去掉 https:// 与路径），第一个视为主域名。用于生成分享链接与
            「非官方入口」提示；<strong>不代配 DNS 与证书</strong>。
          </div>
        </el-form-item>
      </el-form>

      <el-alert
        type="warning"
        :closable="false"
        show-icon
      >
        <template #title>
          上线检查：① 域名 A/CNAME 指向本服务；② 反向代理透传 Host 头；
          ③ 证书就位后把 http 跳 https。以上都在服务器侧完成，系统只负责记录与识别。
        </template>
      </el-alert>
    </el-card>

    <el-card
      v-if="isSuperAdmin"
      class="section"
    >
      <template #header>
        <div class="card-header">
          <span>品牌管理授权</span>
          <span class="note">仅平台超级管理员可见</span>
        </div>
      </template>

      <p class="note block">
        被授权的管理员能改上面的 logo、文案与域名，但不能再转授给他人。
        超级管理员默认拥有该权限，不需要在此勾选。
      </p>

      <el-table
        :data="grants"
        size="small"
        v-loading="grantsLoading"
        max-height="320"
      >
        <el-table-column
          prop="display_name"
          label="成员"
          min-width="180"
        >
          <template #default="{ row }">
            {{ row.display_name }}
            <span class="muted">@{{ row.username }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="可管理品牌"
          width="140"
        >
          <template #default="{ row }">
            <el-switch
              :model-value="row.grants.includes('branding:manage')"
              :loading="grantingId === row.user_id"
              @change="(val: string | number | boolean) => toggleGrant(row, Boolean(val))"
            />
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { brandingApi, type BrandingAdmin, type PlatformGrantTarget } from '@/api/branding'
  import { useAuthStore } from '@/stores/auth'
  import { useBrandingStore } from '@/stores/branding'

  /** 品牌与门户（v3.67）：超管或被授权的管理员可改，改完即时生效 */
  const auth = useAuthStore()
  const store = useBrandingStore()

  const data = ref<BrandingAdmin | null>(null)
  const saving = ref(false)
  const uploading = ref(false)
  const fileRef = ref<HTMLInputElement | null>(null)

  const form = reactive({
    app_name: '',
    short_name: '',
    login_tagline: '',
    footer_text: '',
    primary_color: '',
    domains: [] as string[],
  })

  /** 色板与输入框双向同步：色板清空 = 恢复默认色 */
  const colorPick = computed({
    get: () => form.primary_color || data.value?.defaults.primary_color || '#6366F1',
    set: (value: string | null) => {
      form.primary_color = value ?? ''
    },
  })

  const isSuperAdmin = computed(() => auth.roles.includes('platform_admin'))

  const grants = ref<PlatformGrantTarget[]>([])
  const grantsLoading = ref(false)
  const grantingId = ref('')

  function hydrate(payload: BrandingAdmin): void {
    data.value = payload
    form.app_name = payload.raw.app_name
    form.short_name = payload.raw.short_name
    form.login_tagline = payload.raw.login_tagline
    form.footer_text = payload.raw.footer_text
    form.primary_color = payload.raw.primary_color
    form.domains = [...payload.domains]
    // 让侧栏/标题/主色立刻反映最新品牌（含别人改过的情况）
    store.applyAdmin(payload)
  }

  async function load(): Promise<void> {
    try {
      hydrate(await brandingApi.admin())
    } catch {
      /* toast 已统一处理 */
    }
  }

  async function save(): Promise<void> {
    saving.value = true
    try {
      const payload = await brandingApi.update({
        app_name: form.app_name.trim(),
        short_name: form.short_name.trim(),
        login_tagline: form.login_tagline.trim(),
        footer_text: form.footer_text.trim(),
        primary_color: form.primary_color.trim(),
        domains: form.domains.map(item => String(item).trim()).filter(Boolean),
      })
      hydrate(payload)
      ElMessage.success('品牌设置已保存')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }

  function resetColor(): void {
    form.primary_color = ''
  }

  async function onPick(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file) return
    uploading.value = true
    try {
      hydrate(await brandingApi.uploadLogo(file))
      ElMessage.success('logo 已更新')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      uploading.value = false
    }
  }

  async function removeLogo(): Promise<void> {
    uploading.value = true
    try {
      hydrate(await brandingApi.clearLogo())
      ElMessage.success('已恢复文字 logo')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      uploading.value = false
    }
  }

  async function loadGrants(): Promise<void> {
    if (!isSuperAdmin.value) return
    grantsLoading.value = true
    try {
      const res = await brandingApi.grants()
      grants.value = res.items
    } catch {
      /* toast 已统一处理 */
    } finally {
      grantsLoading.value = false
    }
  }

  async function toggleGrant(row: unknown, enabled: boolean): Promise<void> {
    const target = row as PlatformGrantTarget
    grantingId.value = target.user_id
    try {
      const next = enabled
        ? [...new Set([...target.grants, 'branding:manage'])]
        : target.grants.filter(item => item !== 'branding:manage')
      const res = await brandingApi.setGrants(target.user_id, next)
      target.grants = res.grants
      ElMessage.success(res.grants.length ? '已授权' : '已撤销授权')
    } catch {
      /* 错误 toast 已统一处理 */
    } finally {
      grantingId.value = ''
    }
  }

  onMounted(() => {
    void load()
    void loadGrants()
  })
</script>

<style scoped>
  .section {
    margin-bottom: 16px;
  }
  .intro {
    margin-bottom: 16px;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .note {
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.6;
    margin-top: 4px;
  }
  .note.block {
    margin: 0 0 12px;
  }
  .muted {
    color: var(--ds-text-3);
    font-size: 12px;
    margin-left: 6px;
  }
  .wide {
    width: min(420px, 100%);
  }
  .narrow {
    width: 160px;
  }
  .color-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .color-input {
    width: 130px;
  }
  .logo-row {
    display: flex;
    align-items: flex-start;
    gap: 16px;
    flex-wrap: wrap;
  }
  .logo-preview {
    width: 72px;
    height: 72px;
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    border: 1px solid var(--ds-border);
  }
  .logo-preview.light {
    background: #fff;
    color: #1f2937;
  }
  .logo-preview.dark {
    background: #14161f;
    color: #fff;
  }
  .logo-preview img {
    width: 100%;
    height: 100%;
    object-fit: contain;
  }
  .logo-fallback {
    font-size: 26px;
    font-weight: 600;
  }
  .logo-actions {
    flex: 1;
    min-width: 220px;
  }
</style>

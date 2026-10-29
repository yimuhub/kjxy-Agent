<template>
  <el-card class="section">
    <template #header>
      <div class="card-header">
        <span>工作台视图</span>
        <div class="page-actions">
          <el-button
            size="small"
            :loading="loading"
            @click="load"
          >
            刷新
          </el-button>
          <el-button
            size="small"
            type="primary"
            :loading="saving"
            @click="save"
          >
            保存默认视图
          </el-button>
        </div>
      </div>
    </template>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="view-alert"
      title="优先级：成员本地自定义 > 角色默认视图 > 全局默认。成员未自定义时按角色取默认；管理员仍可在侧栏 DIY 中随时切换。"
    />

    <el-form
      label-width="120px"
      class="view-form"
    >
      <el-form-item label="全局默认视图">
        <el-radio-group v-model="form.default_mode">
          <el-radio-button
            v-for="mode in MODES"
            :key="mode.value"
            :value="mode.value"
          >
            {{ mode.label }}
          </el-radio-button>
        </el-radio-group>
        <div class="note">
          {{ modeHint(form.default_mode) }}
        </div>
      </el-form-item>

      <el-form-item
        v-for="role in ROLE_ROWS"
        :key="role.code"
        :label="role.label"
      >
        <el-select
          v-model="form.role_modes[role.code]"
          style="width: 220px"
        >
          <el-option
            v-for="mode in MODES"
            :key="mode.value"
            :label="mode.label"
            :value="mode.value"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="允许成员自定义">
        <el-switch v-model="form.allow_member_customize" />
        <span class="note">关闭后成员仍可取用角色默认视图，但侧栏 DIY 的本地布局会被忽略</span>
      </el-form-item>

      <el-form-item label="内置视图模板">
        <div class="preset-list">
          <div
            v-for="preset in MENU_PRESETS"
            :key="preset.id"
            class="preset-item"
          >
            <span class="preset-name">{{ preset.name }}</span>
            <span class="preset-desc">{{ preset.description }}</span>
            <el-tag
              size="small"
              effect="plain"
            >
              {{ MENU_MODE_LABELS[preset.mode] }}
            </el-tag>
          </div>
        </div>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup lang="ts">
  import { onMounted, reactive, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { menuApi, type MenuConfig, type MenuMode } from '@/api/v3'
  import { MENU_MODE_LABELS, MENU_PRESETS } from '@/stores/menu'

  const MODES: Array<{ value: MenuMode; label: string }> = [
    { value: 'minimal', label: '极简模式' },
    { value: 'standard', label: '标准模式' },
    { value: 'full', label: '全功能模式' },
  ]

  const ROLE_ROWS = [
    { code: 'yard_owner', label: '小院负责人' },
    { code: 'yard_admin', label: '小院管理员' },
    { code: 'expert', label: '专家' },
    { code: 'operator', label: '科研操作员' },
    { code: 'viewer', label: '普通成员' },
  ]

  const DEFAULT_CONFIG: MenuConfig = {
    default_mode: 'standard',
    role_modes: {
      yard_owner: 'standard',
      yard_admin: 'standard',
      expert: 'standard',
      operator: 'minimal',
      viewer: 'minimal',
    },
    profiles: [],
    allow_member_customize: true,
  }

  const loading = ref(false)
  const saving = ref(false)
  const form = reactive<MenuConfig>(structuredClone(DEFAULT_CONFIG))

  function modeHint(mode: MenuMode): string {
    if (mode === 'minimal') return '仅保留概览、对话、知识库、任务与管理入口'
    if (mode === 'full') return '全部功能平铺展示，适合负责人巡检'
    return '工作台 / 能力协作 / 知识数据 / 管理与统计四组'
  }

  async function load(): Promise<void> {
    loading.value = true
    try {
      const data = await menuApi.get()
      form.default_mode = data.default_mode ?? 'standard'
      form.role_modes = { ...DEFAULT_CONFIG.role_modes, ...(data.role_modes ?? {}) }
      form.profiles = data.profiles ?? []
      form.allow_member_customize = data.allow_member_customize !== false
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      loading.value = false
    }
  }

  async function save(): Promise<void> {
    saving.value = true
    try {
      await menuApi.update({
        default_mode: form.default_mode,
        role_modes: { ...form.role_modes },
        profiles: form.profiles,
        allow_member_customize: form.allow_member_customize,
      })
      ElMessage.success('默认工作台视图已保存')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }

  onMounted(() => {
    void load()
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
  .view-alert {
    margin-bottom: 14px;
  }
  .view-form {
    max-width: 760px;
  }
  .note {
    margin-left: 10px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .preset-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: 100%;
  }
  .preset-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
  }
  .preset-name {
    font-size: 13px;
    font-weight: 600;
  }
  .preset-desc {
    flex: 1;
    font-size: 12px;
    color: var(--ds-text-3);
  }
</style>

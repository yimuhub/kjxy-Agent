<template>
  <div class="settings-yard-profile">
    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>小院画像（官方技能库推荐依据）</span>
          <el-button
            size="small"
            type="primary"
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
        <el-form-item label="小院产业类型">
          <el-checkbox-group v-model="types">
            <el-checkbox
              v-for="t in TYPE_OPTIONS"
              :key="t.key"
              :value="t.key"
            >
              {{ t.label }}
            </el-checkbox>
          </el-checkbox-group>
          <div class="note">
            按调研（江苏 89 家科技小院）聚为八大产业族：勾选后，官方技能库在
            「插件与技能库 → 官方技能包」里针对性推荐并一键启用对应技能包；全选
            「通用」类包恒定推荐。
          </div>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { governanceApi } from '@/api/v3'

  /** 小院画像（v3.58）：官方技能包按产业类型推荐 */
  const saving = ref(false)
  const types = ref<string[]>([])

  const TYPE_OPTIONS = [
    { key: 'rice', label: '水稻/稻麦' },
    { key: 'aquaculture', label: '水产（蟹虾鱼）' },
    { key: 'fruit', label: '应时鲜果' },
    { key: 'vegetable', label: '蔬菜瓜果' },
    { key: 'tea', label: '茶叶' },
    { key: 'livestock', label: '畜禽' },
    { key: 'smart_agri', label: '智慧农业/农文旅' },
    { key: 'general', label: '通用' },
  ]

  async function loadSettings(): Promise<void> {
    try {
      const data = await governanceApi.settings()
      types.value = [...(data.yard_profile?.types ?? [])]
    } catch {
      /* toast 已统一处理 */
    }
  }

  async function save(): Promise<void> {
    saving.value = true
    try {
      await governanceApi.updateSetting('yard_profile', { types: types.value })
      ElMessage.success('小院画像已保存')
      await loadSettings()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }

  onMounted(() => {
    void loadSettings()
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
  .note {
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.6;
    margin-top: 4px;
  }
</style>

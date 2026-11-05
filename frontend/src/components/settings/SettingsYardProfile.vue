<template>
  <div class="settings-yard-profile">
    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>小院档案（技能推荐 + 枢纽分组依据）</span>
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

        <el-divider content-position="left">
          组织归属（枢纽工作台分包用）
        </el-divider>

        <el-form-item label="培养单位">
          <el-input
            v-model="form.unit"
            maxlength="64"
            show-word-limit
            placeholder="如：南京农业大学"
            class="wide"
          />
          <div class="note">
            省级科协模式按此分组，看各高校所辖小院的达标进度。
          </div>
        </el-form-item>

        <el-form-item label="所在县域">
          <el-input
            v-model="form.county"
            maxlength="32"
            show-word-limit
            placeholder="如：如皋市"
            class="wide"
          />
          <div class="note">
            市级专班模式按此分组，看县域覆盖率与断更清单；留空则回退到小院城市。
          </div>
        </el-form-item>

        <el-form-item label="主导产业">
          <el-input
            v-model="form.industry"
            maxlength="32"
            show-word-limit
            placeholder="如：水稻 / 设施蔬菜"
            class="wide"
          />
          <div class="note">
            实验站模式缺「小院类型」时用产业类型兜底；留空则回退到产业类型勾选。
          </div>
        </el-form-item>

        <el-form-item label="一院一策标签">
          <el-select
            v-model="form.tags"
            multiple
            filterable
            allow-create
            default-first-option
            :reserve-keyword="false"
            placeholder="输入后回车添加，最多 12 个"
            class="wide"
          >
            <el-option
              v-for="t in TAG_SUGGESTIONS"
              :key="t"
              :label="t"
              :value="t"
            />
          </el-select>
          <div class="note">
            用于实验站模式的横向对比与督办标注，如「盐碱地改良」「稻虾共作」。
          </div>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, reactive, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { governanceApi } from '@/api/v3'

  /** 小院档案（v3.58 起为技能推荐依据；v3.67 扩展为枢纽三模式的分组依据） */
  const saving = ref(false)
  const types = ref<string[]>([])
  const form = reactive({
    unit: '',
    county: '',
    industry: '',
    tags: [] as string[],
  })

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

  const TAG_SUGGESTIONS = ['一院一策', '盐碱地改良', '稻虾共作', '设施栽培', '示范基地']

  async function loadSettings(): Promise<void> {
    try {
      const data = await governanceApi.settings()
      types.value = [...(data.yard_profile?.types ?? [])]
      form.unit = data.yard_profile?.unit ?? ''
      form.county = data.yard_profile?.county ?? ''
      form.industry = data.yard_profile?.industry ?? ''
      form.tags = [...(data.yard_profile?.tags ?? [])]
    } catch {
      /* toast 已统一处理 */
    }
  }

  async function save(): Promise<void> {
    saving.value = true
    try {
      await governanceApi.updateSetting('yard_profile', {
        types: types.value,
        unit: form.unit.trim(),
        county: form.county.trim(),
        industry: form.industry.trim(),
        tags: form.tags.map(t => String(t).trim()).filter(Boolean),
      })
      ElMessage.success('小院档案已保存')
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
  .wide {
    width: min(420px, 100%);
  }
</style>

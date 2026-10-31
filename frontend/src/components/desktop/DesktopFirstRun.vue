<template>
  <el-dialog
    v-model="visible"
    title="欢迎使用科技小院"
    width="580px"
    :close-on-click-modal="false"
    append-to-body
    @closed="onClosed"
  >
    <p class="lead">
      本机引擎已经就绪，数据只保存在这台电脑。接入一个模型后就能开始对话；
      也可以先跳过，之后在「模型中心」再配置。
    </p>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="mb"
    />
    <el-form
      label-width="96px"
    >
      <el-form-item label="模型服务">
        <el-select
          v-model="form.provider_type"
          placeholder="选择常用服务"
          style="width: 100%"
          @change="applyTemplate"
        >
          <el-option
            v-for="t in templates"
            :key="t.provider_type"
            :label="t.label"
            :value="t.provider_type"
          />
          <el-option
            label="自定义（OpenAI 兼容）"
            value="openai_compatible"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="名称">
        <el-input
          v-model="form.name"
          maxlength="40"
          placeholder="如 deepseek-main"
        />
      </el-form-item>
      <el-form-item label="Base URL">
        <el-input
          v-model="form.base_url"
          placeholder="https://api.deepseek.com"
        />
      </el-form-item>
      <el-form-item label="API Key">
        <el-input
          v-model="form.api_key"
          type="password"
          show-password
          placeholder="加密保存在本机，不会上传"
        />
      </el-form-item>
      <el-form-item label="模型名">
        <el-input
          v-model="form.model_id"
          placeholder="如 deepseek-chat"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="skip">
        稍后配置
      </el-button>
      <el-button @click="goAdvanced">
        高级配置
      </el-button>
      <el-button
        type="primary"
        :loading="saving"
        :disabled="!canSave"
        @click="save"
      >
        保存并开始
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage } from 'element-plus'
  import { llmSupplierApi, type LlmProviderTemplate } from '@/api/modules'

  const DONE_KEY = 'ky_desktop_welcome_done'

  const router = useRouter()
  const visible = ref(false)
  const saving = ref(false)
  const error = ref('')
  const templates = ref<LlmProviderTemplate[]>([])

  const form = reactive({
    provider_type: '',
    name: '',
    base_url: '',
    api_key: '',
    model_id: '',
  })

  const canSave = computed(
    () =>
      Boolean(form.provider_type && form.name && form.base_url && form.api_key && form.model_id),
  )

  function isDesktopRuntime() {
    const port = Number(window.location.port || '0')
    return window.location.hostname === '127.0.0.1' && port >= 18700 && port <= 18710
  }

  function applyTemplate(type: string) {
    const tpl = templates.value.find(t => t.provider_type === type)
    if (!tpl) return
    form.base_url = tpl.base_url
    form.model_id = tpl.model_id
    if (!form.name) form.name = `${tpl.label}-main`
  }

  async function loadTemplates() {
    try {
      templates.value = await llmSupplierApi.templates()
      if (templates.value.length) {
        form.provider_type = templates.value[0].provider_type
        applyTemplate(form.provider_type)
      }
    } catch {
      // 模板拉取失败不阻塞手工填写
    }
  }

  function markDone() {
    localStorage.setItem(DONE_KEY, '1')
    visible.value = false
  }

  function skip() {
    markDone()
    ElMessage.info('已跳过，可在「模型中心」随时配置模型')
  }

  function goAdvanced() {
    markDone()
    void router.push('/model-center')
  }

  function onClosed() {
    if (!localStorage.getItem(DONE_KEY)) markDone()
  }

  async function save() {
    saving.value = true
    error.value = ''
    try {
      const created = await llmSupplierApi.create({
        name: form.name,
        provider_type: form.provider_type,
        base_url: form.base_url,
        model_id: form.model_id,
        api_key: form.api_key,
        scope: 'yard',
      })
      await llmSupplierApi.setActive(created.id)
      markDone()
      ElMessage.success('模型已接入，可以开始对话了')
    } catch (e) {
      error.value = e instanceof Error ? e.message : '保存失败，请检查地址与密钥'
    } finally {
      saving.value = false
    }
  }

  onMounted(async () => {
    if (!isDesktopRuntime()) return
    if (localStorage.getItem(DONE_KEY) === '1') return
    try {
      const existing = await llmSupplierApi.list()
      if (existing.length) {
        localStorage.setItem(DONE_KEY, '1')
        return
      }
    } catch {
      return
    }
    await loadTemplates()
    visible.value = true
  })
</script>

<style scoped>
  .lead {
    margin: 0 0 16px;
    color: var(--ds-text-2);
    line-height: 1.7;
    font-size: 14px;
  }
  .mb {
    margin-bottom: 16px;
  }
</style>

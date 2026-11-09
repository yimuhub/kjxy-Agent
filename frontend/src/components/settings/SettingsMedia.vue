<template>
  <AsyncState
    :state="state"
    empty-text="暂无媒体生成配置"
    @retry="loadAll"
  >
    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>对话内媒体</span>
          <el-button
            v-if="canManage"
            size="small"
            type="primary"
            :loading="saving"
            @click="save"
          >
            保存
          </el-button>
        </div>
      </template>

      <el-alert
        v-if="!canManage"
        type="info"
        :closable="false"
        show-icon
        title="仅小院管理员可修改媒体生成设置；成员可在对话里查看自己当日剩余额度。"
        style="margin-bottom: 12px"
      />

      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="route-hint"
      >
        <template #title>
          图片和视频都使用「模型中心」里已启用的图像/视频供应商。未配置供应商时，对话会明确提示去配置，
          不会假装已生成。
        </template>
      </el-alert>

      <div class="usage-row">
        <span>今日用量：</span>
        <el-tag
          size="small"
          effect="plain"
        >
          {{ imageQuotaText }}
        </el-tag>
        <el-tag
          size="small"
          effect="plain"
        >
          {{ videoQuotaText }}
        </el-tag>
        <span class="note">额度按北京时间的自然日重置；0 表示不限。</span>
      </div>

      <el-divider content-position="left">
        图片
      </el-divider>
      <el-form
        label-width="170px"
        class="settings-form"
      >
        <el-form-item label="启用对话内图片">
          <el-switch
            v-model="media.image.enabled"
            :disabled="!canManage"
          />
          <span class="note">关闭后对话里不再直接生成图片，其余对话能力不受影响</span>
        </el-form-item>
        <el-form-item label="允许普通成员使用">
          <el-switch
            v-model="media.image.member_access"
            :disabled="!canManage"
          />
          <span class="note">关闭后仅管理员与具备图片技能的助手可用</span>
        </el-form-item>
        <el-form-item label="每人每日张数">
          <el-input-number
            v-model="media.image.daily_limit"
            :min="0"
            :max="200"
            :step="5"
            :disabled="!canManage"
          />
          <span class="note">0 表示不限；管理员不受此上限约束</span>
        </el-form-item>
        <el-form-item label="单次最多生成">
          <el-input-number
            v-model="media.image.max_per_request"
            :min="1"
            :max="4"
            :disabled="!canManage"
          />
          <span class="note">张（1–4）</span>
        </el-form-item>
        <el-form-item label="默认尺寸">
          <el-select
            v-model="media.image.default_size"
            :disabled="!canManage"
            style="width: 200px"
          >
            <el-option
              label="方形 1024×1024"
              value="1024x1024"
            />
            <el-option
              label="竖版 1024×1536"
              value="1024x1536"
            />
            <el-option
              label="横版 1536×1024"
              value="1536x1024"
            />
          </el-select>
          <span class="note">对话里说「竖版/横版」会临时覆盖</span>
        </el-form-item>
      </el-form>

      <el-divider content-position="left">
        视频
      </el-divider>
      <el-form
        label-width="170px"
        class="settings-form"
      >
        <el-form-item label="启用对话内视频">
          <el-switch
            v-model="media.video.enabled"
            :disabled="!canManage"
          />
        </el-form-item>
        <el-form-item label="允许普通成员使用">
          <el-switch
            v-model="media.video.member_access"
            :disabled="!canManage"
          />
          <span class="note">默认关闭：只有管理员、指挥官和绑定了视频生成技能的助手可用</span>
        </el-form-item>
        <el-form-item label="每人每日条数">
          <el-input-number
            v-model="media.video.daily_limit"
            :min="0"
            :max="50"
            :disabled="!canManage"
          />
          <span class="note">0 表示不限；管理员不受此上限约束</span>
        </el-form-item>
        <el-form-item label="成员单次时长">
          <el-input-number
            v-model="media.video.max_seconds"
            :min="1"
            :max="30"
            :disabled="!canManage"
          />
          <span class="note">秒，不能超过管理员上限</span>
        </el-form-item>
        <el-form-item label="管理员单次时长">
          <el-input-number
            v-model="media.video.admin_max_seconds"
            :min="1"
            :max="30"
            :disabled="!canManage"
          />
          <span class="note">秒（第三方视频通道通常上限 30 秒）</span>
        </el-form-item>
        <el-form-item label="成品保留天数">
          <el-input-number
            v-model="media.video.retention_days"
            :min="1"
            :max="365"
            :disabled="!canManage"
          />
          <span class="note">视频会转存到本机，到期自动清理；需要长期留存请先下载</span>
        </el-form-item>
      </el-form>

      <p class="note boundary">
        边界：生成结果会保存在本机并只对发起人（与管理员）可见；海报文字由系统叠加，避免模型把中文写错。
        视频需要轮询上游任务，完成后卡片自动出现成品，不要求用户留在当前页面等待。
      </p>
    </el-card>
  </AsyncState>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { governanceApi } from '@/api/v3'
  import { mediaApi, type MediaUsage } from '@/api/media'
  import { useAuthStore } from '@/stores/auth'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'

  const auth = useAuthStore()
  const canManage = computed(() => auth.isYardAdmin())
  const state = ref<ViewState>('loading')
  const saving = ref(false)

  const media = reactive({
    image: {
      enabled: true,
      member_access: true,
      daily_limit: 20,
      max_per_request: 4,
      default_size: '1024x1024' as '1024x1024' | '1024x1536' | '1536x1024',
    },
    video: {
      enabled: true,
      member_access: false,
      daily_limit: 3,
      max_seconds: 10,
      admin_max_seconds: 30,
      retention_days: 30,
    },
  })

  const emptyUsage = (kind: 'image' | 'video'): MediaUsage['image'] => ({
    kind,
    limit: 0,
    used: 0,
    remaining: null,
    seconds_used: 0,
    seconds_limit: null,
    reset_at: '',
  })
  const usage = reactive<MediaUsage>({ image: emptyUsage('image'), video: emptyUsage('video') })

  const imageQuotaText = computed(() =>
    usage.image.limit > 0
      ? `图片 ${usage.image.used} / ${usage.image.limit}`
      : `图片 ${usage.image.used}`,
  )
  const videoQuotaText = computed(() => {
    const base = usage.video.limit > 0
      ? `视频 ${usage.video.used} / ${usage.video.limit}`
      : `视频 ${usage.video.used}`
    return usage.video.seconds_limit ? `${base}（${usage.video.seconds_used} 秒）` : base
  })

  async function loadAll(): Promise<void> {
    state.value = 'loading'
    try {
      const bundle = await governanceApi.settings()
      const current = bundle.media
      if (current) {
        media.image.enabled = Boolean(current.image?.enabled)
        media.image.member_access = Boolean(current.image?.member_access)
        media.image.daily_limit = Number(current.image?.daily_limit ?? 20)
        media.image.max_per_request = Number(current.image?.max_per_request ?? 4)
        media.image.default_size = current.image?.default_size ?? '1024x1024'
        media.video.enabled = Boolean(current.video?.enabled)
        media.video.member_access = Boolean(current.video?.member_access)
        media.video.daily_limit = Number(current.video?.daily_limit ?? 3)
        media.video.max_seconds = Number(current.video?.max_seconds ?? 10)
        media.video.admin_max_seconds = Number(current.video?.admin_max_seconds ?? 30)
        media.video.retention_days = Number(current.video?.retention_days ?? 30)
      }
      await loadUsage()
      state.value = 'ready'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  /** 用量是辅助信息，失败不拖垮整页设置 */
  async function loadUsage(): Promise<void> {
    try {
      const next = await mediaApi.usage()
      usage.image = next.image ?? emptyUsage('image')
      usage.video = next.video ?? emptyUsage('video')
    } catch {
      usage.image = emptyUsage('image')
      usage.video = emptyUsage('video')
    }
  }

  async function save(): Promise<void> {
    if (media.video.max_seconds > media.video.admin_max_seconds) {
      ElMessage.warning('成员单次时长不能超过管理员上限')
      return
    }
    saving.value = true
    try {
      await governanceApi.updateSetting('media', {
        image: { ...media.image },
        video: { ...media.video },
      })
      ElMessage.success('媒体生成设置已保存')
      await loadAll()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }

  onMounted(loadAll)
</script>

<style scoped>
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .route-hint {
    margin-bottom: 12px;
  }
  .usage-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }
  .note {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .boundary {
    margin: 12px 0 0;
    line-height: 1.7;
  }
</style>

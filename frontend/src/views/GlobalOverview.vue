<template>
  <div class="global-overview">
    <h2 class="page-title">
      全局总览
    </h2>
    <el-alert
      type="info"
      show-icon
      class="mb"
      :title="`平台管理员：${auth.user?.display_name || auth.user?.username}`"
      :description="
        '平台管理员为全局只读（不读取租户业务正文）。要体验租户业务流程（问答、知识库、任务、政策），请使用租户账号登录。'
      "
    />

    <el-row :gutter="16" class="stat-row">
      <el-col :span="6">
        <el-card>
          <template #header>平台概况</template>
          <AsyncState :state="statsState" empty-text="暂无统计数据" @retry="loadAll">
            <div class="stat-number">{{ stats.tasks?.total ?? 0 }}</div>
            <div class="stat-desc">
              平台任务总数 · 成员 {{ stats.members?.total ?? 0 }} 人
            </div>
            <el-table :data="llmByProvider" size="small" class="llm-table">
              <el-table-column prop="provider" label="模型提供方" />
              <el-table-column prop="count" label="调用次数" />
            </el-table>
            <div v-if="stats.llm" class="stat-desc">
              累计成本 ≈ {{ stats.llm.total_estimated_cost }} 元
            </div>
          </AsyncState>
        </el-card>
      </el-col>

      <el-col :span="6">
        <el-card>
          <template #header>任务状态</template>
          <AsyncState :state="statsState" empty-text="暂无任务" @retry="loadAll">
            <ul class="mini-list">
              <li v-for="(count, status) in stats.tasks?.by_status || {}" :key="status">
                <el-tag
                  :type="status === 'failed' ? 'danger' : status === 'succeeded' ? 'success' : status === 'running' ? 'primary' : 'info'"
                  size="small"
                >
                  {{ taskStatusLabel(String(status)) }}
                </el-tag>
                <span class="mini-title">{{ count }} 个</span>
              </li>
            </ul>
          </AsyncState>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card>
          <template #header>
            <span>租户列表（{{ tenantList.count ?? 0 }}）</span>
          </template>
          <AsyncState :state="tenantsState" empty-text="暂无租户" @retry="loadAll">
            <el-table :data="tenantList.tenants ?? []" size="small">
              <el-table-column prop="name" label="租户" min-width="140" />
              <el-table-column prop="province" label="地区" width="90" />
              <el-table-column prop="active_members" label="成员" width="70" />
              <el-table-column prop="task_total" label="任务" width="70" />
              <el-table-column label="状态" width="80">
                <template #default="{ row }">
                  <el-tag :type="row.status === 'active' ? 'success' : 'warning'" size="small">
                    {{ row.status }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
          </AsyncState>
        </el-card>
      </el-col>
    </el-row>

    <el-card v-if="showDemoAccounts" class="switch-card">
      <template #header>快速切换体验租户功能</template>
      <div class="switch-row">
        <el-tag>租户管理员</el-tag>
        <code>tenant_admin / tenant123</code>
        <el-button link type="primary" @click="switchToTenant">
          退出后用该账号登录 →
        </el-button>
      </div>
      <div class="switch-row">
        <el-tag>农业专家</el-tag>
        <code>expert / expert123</code>
        <el-button link type="primary" @click="switchToTenant">退出后用该账号登录 →</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { adminApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
import AsyncState from '@/components/AsyncState.vue'
import { classifyError, type ViewState } from '@/utils/status'
import type { AdminStats, AdminTenantList } from '@/types/api'

const router = useRouter()
const auth = useAuthStore()
/** 演示账号仅在开发/演示构建展示，生产环境避免客户截图泄露 */
const showDemoAccounts = !import.meta.env.PROD

const statsState = ref<ViewState>('loading')
const tenantsState = ref<ViewState>('loading')
const stats = reactive<AdminStats>({})
const tenantList = reactive<AdminTenantList>({ count: 0, tenants: [] })

const llmByProvider = computed(() =>
  Object.entries(stats.llm?.by_provider ?? {}).map(([provider, count]) => ({
    provider,
    count,
  })),
)

function taskStatusLabel(status: string): string {
  const map: Record<string, string> = {
    queued: '排队中',
    running: '运行中',
    succeeded: '成功',
    failed: '失败',
    awaiting_review: '待审核',
    cancelled: '已取消',
  }
  return map[status] ?? status
}

async function loadAll() {
  statsState.value = 'loading'
  tenantsState.value = 'loading'
  try {
    Object.assign(stats, await adminApi.stats())
    statsState.value = 'ready'
  } catch (e) {
    statsState.value = classifyError(e)
  }
  try {
    const t = await adminApi.tenants()
    tenantList.count = t.count
    tenantList.tenants = t.tenants
    tenantsState.value = t.tenants.length ? 'ready' : 'empty'
  } catch (e) {
    tenantsState.value = classifyError(e)
  }
}

async function switchToTenant() {
  await ElMessageBox.confirm('退出当前账号后用租户账号登录？', '切换账号', {
    confirmButtonText: '退出登录',
    cancelButtonText: '取消',
  })
  await auth.logout()
  router.push('/login')
}

onMounted(loadAll)
</script>

<style scoped>
/* 设计系统 v2：颜色一律走 --ds-* token */
.global-overview {
  padding: 4px;
}
.mb {
  margin-bottom: 16px;
}
.stat-row {
  margin-bottom: 16px;
}
.stat-number {
  font-size: 36px;
  font-weight: 700;
  color: var(--ds-accent);
  font-variant-numeric: tabular-nums;
}
.stat-desc {
  color: var(--ds-text-3);
  font-size: 13px;
  margin-top: 4px;
}
.llm-table {
  margin-top: 12px;
}
.mini-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mini-title {
  margin-left: 8px;
  font-size: 13px;
  color: var(--ds-text-2);
  font-variant-numeric: tabular-nums;
}
.switch-card {
  margin-top: 4px;
}
.switch-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
}
.switch-row code {
  padding: 2px 8px;
  border-radius: var(--ds-radius-control);
  background: var(--ds-glass-strong);
  border: 1px solid var(--ds-border);
  color: var(--ds-text-1);
}
</style>

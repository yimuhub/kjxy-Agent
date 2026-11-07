<template>
  <div class="oversight-page">
    <div class="page-head">
      <div>
        <h3>小院监管</h3>
        <p class="sub">
          全部小院的工作日志聚合（仅数字，不含内容）：{{ month }} · 断更阈值 {{ staleDays }} 天
        </p>
      </div>
      <el-button
        :loading="loading"
        @click="load"
      >
        刷新
      </el-button>
    </div>

    <el-alert
      v-if="staleItems.length"
      :title="`${staleItems.length} 家小院本月日志断更（超过 ${staleDays} 天无更新或零填报），建议跟进`"
      type="warning"
      :closable="false"
      show-icon
    />

    <el-table
      v-col-resize
      :data="items"
      v-loading="loading"
      :default-sort="{ prop: 'journal_count', order: 'descending' }"
      empty-text="暂无数据"
    >
      <el-table-column
        prop="yard_name"
        label="小院"
        min-width="180"
      />
      <el-table-column
        prop="journal_count"
        label="本月日志篇数"
        width="140"
        sortable
      />
      <el-table-column
        prop="active_authors"
        label="活跃作者"
        width="110"
        sortable
      />
      <el-table-column
        label="最近更新"
        width="180"
      >
        <template #default="{ row }">
          {{ row.last_updated_at ? formatTime(row.last_updated_at) : '—' }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.stale ? 'danger' : 'success'" effect="plain">
            {{ row.stale ? '断更' : '正常' }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { journalApi, type OversightItem } from '@/api/journal'
  import { formatTime } from '@/utils/format'

  /** 跨小院监管看板（P1，platform_admin）：只出数字与断更预警，不出内容。 */
  const items = ref<OversightItem[]>([])
  const loading = ref(false)
  const month = new Date().toISOString().slice(0, 7)
  const staleDays = ref(7)

  const staleItems = computed(() => items.value.filter(i => i.stale))

  async function load(): Promise<void> {
    loading.value = true
    try {
      const data = await journalApi.oversight(month)
      items.value = data.items
      staleDays.value = data.stale_days
    } catch {
      /* toast 已统一处理 */
    } finally {
      loading.value = false
    }
  }

  onMounted(() => {
    void load()
  })
</script>

<style scoped>
  .oversight-page {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .page-head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 12px;
  }
  .page-head h3 {
    margin: 0 0 4px;
  }
  .sub {
    margin: 0;
    font-size: 12.5px;
    color: var(--ds-text-3);
  }
</style>

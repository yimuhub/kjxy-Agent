<template>
  <div class="evolution">
    <div class="page-header">
      <h2 class="page-title">
        自生长管理
      </h2>
      <div class="page-actions">
        <el-button
          :loading="scanning"
          @click="scanPlugins"
        >
          重新扫描
        </el-button>
      </div>
    </div>
    <el-tabs v-model="activeTab">
      <el-tab-pane
        label="Skill 列表"
        name="skills"
      >
        <AsyncState
          :state="skillsState"
          empty-text="暂无 Skill"
          @retry="loadSkills"
        >
          <el-table
            :data="skills"
            stripe
          >
            <el-table-column
              prop="skill_code"
              label="编码"
              width="180"
            />
            <el-table-column
              prop="version"
              label="版本"
              width="100"
            />
            <el-table-column
              prop="status"
              label="状态"
              width="120"
            >
              <template #default="{ row }">
                <el-tag
                  :type="statusType(row.status)"
                  size="small"
                >
                  {{
                    row.status
                  }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              label="灰度"
              width="120"
            >
              <template #default="{ row }">
                <el-tag
                  v-if="row.rollout"
                  type="warning"
                  size="small"
                >
                  灰度中
                </el-tag><span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column
              label="操作"
              width="280"
            >
              <template #default="{ row }">
                <el-button
                  size="small"
                  @click="viewEvalRuns(row)"
                >
                  门禁记录
                </el-button>
                <el-button
                  size="small"
                  @click="openRollout(row)"
                >
                  灰度
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </AsyncState>
      </el-tab-pane>
      <el-tab-pane
        label="插件"
        name="plugins"
      >
        <AsyncState
          :state="pluginsState"
          empty-text="暂未发现插件"
          @retry="loadPlugins"
        >
          <el-table
            :data="plugins"
            stripe
          >
            <el-table-column
              prop="plugin_id"
              label="插件 ID"
              width="200"
            />
            <el-table-column
              prop="name"
              label="名称"
            />
            <el-table-column
              prop="version"
              label="版本"
              width="100"
            />
            <el-table-column
              prop="type"
              label="类型"
              width="140"
            />
            <el-table-column
              prop="status"
              label="状态"
              width="100"
            >
              <template #default="{ row }">
                <el-tag
                  :type="row.status === 'enabled' ? 'success' : 'info'"
                  size="small"
                >
                  {{
                    row.status
                  }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              label="操作"
              width="180"
            >
              <template #default="{ row }">
                <el-button
                  v-if="row.status !== 'enabled'"
                  size="small"
                  type="primary"
                  @click="togglePlugin(row, true)"
                >
                  启用
                </el-button>
                <el-button
                  v-else
                  size="small"
                  @click="togglePlugin(row, false)"
                >
                  禁用
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </AsyncState>
      </el-tab-pane>
    </el-tabs>
    <el-dialog
      v-model="rolloutVisible"
      title="Skill 灰度发布"
      width="520px"
    >
      <el-form
        :model="rolloutForm"
        label-width="110px"
      >
        <el-form-item label="新版本">
          <el-input
            v-model="rolloutForm.rollout_version"
            placeholder="如 1.1.0"
          />
        </el-form-item>
        <el-form-item label="策略">
          <el-select v-model="rolloutForm.strategy">
            <el-option
              label="使用空间名单"
              value="tenant_allowlist"
            />
            <el-option
              label="按比例"
              value="percentage"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          v-if="rolloutForm.strategy === 'percentage'"
          label="比例 %"
        >
          <el-input-number
            v-model="rolloutForm.percentage"
            :min="1"
            :max="100"
          />
        </el-form-item>
        <el-form-item
          v-else
          label="使用空间名单"
        >
          <el-input
            v-model="rolloutForm.tenants"
            placeholder="逗号分隔的使用空间 ID"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rolloutVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          @click="submitRollout"
        >
          启动灰度
        </el-button>
        <el-button
          type="success"
          @click="promoteRollout"
        >
          全量
        </el-button>
        <el-button
          type="warning"
          @click="haltRollout"
        >
          暂停
        </el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="evalVisible"
      title="评测门禁记录"
      width="720px"
    >
      <el-table
        :data="evalRuns"
        stripe
        size="small"
      >
        <el-table-column
          prop="test_set_id"
          label="评估集"
          width="140"
        />
        <el-table-column
          prop="skill_version"
          label="版本"
          width="90"
        />
        <el-table-column
          prop="pass_rate"
          label="通过率"
          width="90"
        >
          <template #default="{ row }">
            {{ (row.pass_rate * 100).toFixed(1) }}%
          </template>
        </el-table-column>
        <el-table-column
          prop="passed"
          label="通过"
          width="70"
        />
        <el-table-column
          prop="failed"
          label="失败"
          width="70"
        />
        <el-table-column
          prop="created_at"
          label="时间"
        />
      </el-table>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
  import { onMounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { evolutionApi } from '@/api/modules'
  import { extractErrorMessage } from '@/api/client'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, friendlyErrorMessage, type ViewState } from '@/utils/status'
  const activeTab = ref('skills')
  const scanning = ref(false)
  const skills = ref<any[]>([])
  const plugins = ref<any[]>([])
  const skillsState = ref<ViewState>('loading')
  const pluginsState = ref<ViewState>('loading')
  const rolloutVisible = ref(false)
  const evalVisible = ref(false)
  const evalRuns = ref<any[]>([])
  const currentSkill = ref<any>(null)
  const rolloutForm = ref({
    rollout_version: '',
    strategy: 'percentage',
    percentage: 10,
    tenants: '',
  })
  function statusType(s: string) {
    return s === 'published' ? 'success' : s === 'deprecated' ? 'info' : 'warning'
  }
  async function loadSkills() {
    skillsState.value = 'loading'
    try {
      skills.value = await evolutionApi.listSkills()
      skillsState.value = skills.value.length ? 'ready' : 'empty'
    } catch (e) {
      skills.value = []
      skillsState.value = classifyError(e)
    }
  }
  async function loadPlugins() {
    pluginsState.value = 'loading'
    try {
      plugins.value = await evolutionApi.listPlugins()
      pluginsState.value = plugins.value.length ? 'ready' : 'empty'
    } catch (e) {
      plugins.value = []
      pluginsState.value = classifyError(e)
    }
  }
  async function scanPlugins() {
    scanning.value = true
    try {
      await evolutionApi.scanPlugins()
      await loadPlugins()
      ElMessage.success('扫描完成')
    } catch {
      /* toast 已统一 */
    } finally {
      scanning.value = false
    }
  }
  async function togglePlugin(p: any, enable: boolean) {
    try {
      if (enable) await evolutionApi.enablePlugin(p.plugin_id)
      else await evolutionApi.disablePlugin(p.plugin_id)
      await loadPlugins()
    } catch {
      /* toast 已统一 */
    }
  }
  function openRollout(s: any) {
    currentSkill.value = s
    rolloutVisible.value = true
  }
  async function submitRollout() {
    try {
      await evolutionApi.setRollout(currentSkill.value.id, {
        rollout_version: rolloutForm.value.rollout_version,
        strategy: rolloutForm.value.strategy,
        percentage:
          rolloutForm.value.strategy === 'percentage' ? rolloutForm.value.percentage : undefined,
        tenant_allowlist:
          rolloutForm.value.strategy === 'tenant_allowlist'
            ? rolloutForm.value.tenants
                .split(',')
                .map(t => t.trim())
                .filter(Boolean)
            : undefined,
      })
      ElMessage.success('灰度已启动')
      rolloutVisible.value = false
      await loadSkills()
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message))
    }
  }
  async function promoteRollout() {
    try {
      await evolutionApi.promoteRollout(currentSkill.value.id)
      ElMessage.success('已全量')
      rolloutVisible.value = false
      await loadSkills()
    } catch {
      /* toast 已统一 */
    }
  }
  async function haltRollout() {
    try {
      await evolutionApi.haltRollout(currentSkill.value.id)
      ElMessage.success('已暂停')
      rolloutVisible.value = false
      await loadSkills()
    } catch {
      /* toast 已统一 */
    }
  }
  async function viewEvalRuns(s: any) {
    try {
      evalRuns.value = await evolutionApi.listEvalRuns(s.id)
      evalVisible.value = true
    } catch {
      /* toast 已统一 */
    }
  }
  onMounted(() => {
    loadSkills()
    loadPlugins()
  })
</script>
<style scoped>
  /* 设计系统 v2：.page-title/.page-actions 由全局提供，这里只做头部排布微调 */
  .page-header {
    display: flex;
    align-items: center;
    margin-bottom: 16px;
  }
  .page-header .page-title {
    margin-bottom: 0;
  }
</style>

<template>
  <div class="custom-agents">
    <div class="page-head">
      <div>
        <h2 class="page-title">
          我的智能体
        </h2>
        <p class="page-sub">
          自由管理你与本小院创建的专属智能体：支持卡片/列表双视图、独立绑定大模型与多项技能，随时与单个或多个智能体发起对话与群聊协同。
        </p>
      </div>
      <div class="page-actions">
        <el-button
          v-if="auth.roles.includes('platform_admin')"
          size="small"
          @click="openReviewQueue"
        >
          平台审核队列<template v-if="reviewQueue.length">
            （{{ reviewQueue.length }}）
          </template>
        </el-button>
        <el-button
          size="small"
          @click="router.push('/agents/factory')"
        >
          智能体工厂批量生成
        </el-button>
        <el-button
          type="primary"
          size="small"
          @click="openDaCreate"
        >
          + 新建智能体
        </el-button>
      </div>
    </div>

    <!-- 顶部统计指标条 -->
    <div class="stats-bar">
      <div class="stat-card">
        <span class="stat-label">智能体总数</span>
        <strong class="stat-val">{{ daList.length }}</strong>
      </div>
      <div class="stat-card">
        <span class="stat-label">已发布可用</span>
        <strong class="stat-val ok">{{ publishedCount }}</strong>
      </div>
      <div class="stat-card">
        <span class="stat-label">我创建的 / 私有</span>
        <strong class="stat-val">{{ privateOrMineCount }}</strong>
      </div>
      <div class="stat-card">
        <span class="stat-label">累计调用次数</span>
        <strong class="stat-val">{{ totalCalls.toLocaleString() }}</strong>
      </div>
      <div class="stat-card">
        <span class="stat-label">累计 Token 消耗</span>
        <strong class="stat-val token">⚡ {{ totalTokens.toLocaleString() }}</strong>
      </div>
    </div>

    <!-- 筛选与视图切换栏 -->
    <div class="toolbar-card">
      <div class="toolbar-left">
        <el-input
          v-model="searchKeyword"
          placeholder="搜索智能体名称、编码、职责或技能…"
          clearable
          size="small"
          style="width: 260px"
        />
        <el-segmented
          v-model="statusFilter"
          size="small"
          :options="STATUS_FILTER_OPTIONS"
        />
        <el-segmented
          v-model="visibilityFilter"
          size="small"
          :options="VISIBILITY_FILTER_OPTIONS"
        />
      </div>
      <div class="toolbar-right">
        <el-button
          v-if="selectedIds.length"
          size="small"
          link
          @click="selectedIds = []"
        >
          清空已选（{{ selectedIds.length }}）
        </el-button>
        <div
          ref="viewSwitchRef"
          class="da-view-switch"
        >
          <el-segmented
            v-model="daView"
            size="small"
            :options="DA_VIEW_OPTIONS"
            aria-label="切换我的智能体视图"
          />
        </div>
      </div>
    </div>

    <!-- 多智能体协同悬浮动作条 -->
    <div
      v-if="selectedAgents.length > 0"
      class="batch-bar"
    >
      <div class="batch-info">
        <span class="batch-badge">已选 {{ selectedAgents.length }} 个智能体</span>
        <div class="batch-chips">
          <el-tag
            v-for="a in selectedAgents"
            :key="a.id"
            size="small"
            closable
            @close="toggleSelectAgent(a)"
          >
            {{ a.emoji || '🤖' }} {{ a.display_name }}
          </el-tag>
        </div>
      </div>
      <div class="batch-actions">
        <el-button
          v-if="selectedAgents.length === 1"
          type="primary"
          size="small"
          @click="openDaUse(selectedAgents[0])"
        >
          与「{{ selectedAgents[0].display_name }}」单独对话
        </el-button>
        <template v-else>
          <el-button
            type="primary"
            size="small"
            @click="openMultiGroupChat"
          >
            💬 发起多智能体群聊（{{ selectedAgents.length }}）
          </el-button>
          <el-button
            size="small"
            type="success"
            plain
            @click="openMainGroupChat"
          >
            在主对话页开启群聊 ↗
          </el-button>
          <el-button
            v-if="auth.isYardAdmin()"
            size="small"
            @click="quickBuildTeam"
          >
            🤝 一键组建协作班组
          </el-button>
        </template>
      </div>
    </div>

    <!-- 卡片视图 -->
    <el-row
      v-if="daView === 'card'"
      :gutter="16"
      class="da-grid"
    >
      <el-col
        v-for="a in filteredList"
        :key="a.id"
        :xs="24"
        :sm="12"
        :md="8"
        :lg="6"
        class="da-grid-item"
      >
        <DeclarativeAgentCard
          :agent="a"
          :model-text="modelLabel(a)"
          :skill-names="skillLabels(a)"
          :focused="a.id === daFocusId"
          selectable
          show-duplicate
          :selected="selectedIds.includes(a.id)"
          @toggle-select="toggleSelectAgent"
          @use="openDaUse"
          @edit="openDaEdit"
          @duplicate="daDuplicate"
          @publish="daPublish"
          @deprecate="daDeprecate"
          @remove="daRemove"
        />
      </el-col>
      <el-col
        v-if="!filteredList.length"
        :span="24"
      >
        <el-empty description="暂无匹配的智能体，点击右上角「+ 新建智能体」创建你的第一个专属智能体">
          <el-button
            type="primary"
            @click="openDaCreate"
          >
            立即创建智能体
          </el-button>
        </el-empty>
      </el-col>
    </el-row>

    <!-- 列表视图 -->
    <el-card
      v-else
      class="list-card"
      shadow="never"
    >
      <el-table
        :data="filteredList"
        size="small"
        :row-class-name="daRowClass"
        empty-text="暂无匹配的智能体，点「+ 新建智能体」或用智能体工厂生成"
      >
        <el-table-column
          width="48"
          align="center"
        >
          <template #header>
            <el-checkbox
              :model-value="allFilteredSelected"
              :indeterminate="someFilteredSelected && !allFilteredSelected"
              @change="toggleSelectAllFiltered"
            />
          </template>
          <template #default="{ row }">
            <el-checkbox
              :model-value="selectedIds.includes(row.id)"
              @change="toggleSelectAgent(row as DeclarativeAgent)"
            />
          </template>
        </el-table-column>

        <el-table-column
          label="智能体"
          min-width="210"
        >
          <template #default="{ row }">
            <div class="da-row-title">
              <span class="da-row-name">
                <span
                  class="da-row-emoji"
                  aria-hidden="true"
                >{{ row.emoji || '🤖' }}</span>
                {{ row.display_name }}
              </span>
              <el-tag
                v-if="row.visibility === 'private'"
                size="small"
                type="warning"
              >
                私有
              </el-tag>
            </div>
            <span class="da-code">{{ row.agent_code }}</span>
          </template>
        </el-table-column>

        <el-table-column
          label="功能职责"
          min-width="200"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            {{ row.description || '未填写功能介绍' }}
          </template>
        </el-table-column>

        <el-table-column
          label="绑定大模型"
          min-width="150"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              effect="plain"
            >
              {{ modelLabel(row as DeclarativeAgent) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column
          label="已绑技能（1对多）"
          min-width="210"
        >
          <template #default="{ row }">
            <div class="table-skills">
              <template v-if="skillLabels(row as DeclarativeAgent).length">
                <el-tag
                  v-for="sk in skillLabels(row as DeclarativeAgent)"
                  :key="sk"
                  size="small"
                  type="info"
                >
                  {{ sk }}
                </el-tag>
              </template>
              <span
                v-else
                class="da-note"
              >未绑定</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column
          label="Token 统计"
          width="145"
        >
          <template #default="{ row }">
            <div class="token-cell">
              <span class="token-num">⚡ {{ (row.token_stats?.total_tokens ?? 0).toLocaleString() }}</span>
              <span class="token-calls">{{ row.token_stats?.calls ?? 0 }} 次调用</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column
          label="状态"
          width="105"
        >
          <template #default="{ row }">
            <el-tag
              :type="daStatusType(row.status)"
              size="small"
            >
              {{ daStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column
          label="来源"
          width="76"
        >
          <template #default="{ row }">
            {{ daFromLabel(row.created_from) }}
          </template>
        </el-table-column>

        <el-table-column
          label="操作"
          min-width="280"
          fixed="right"
        >
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              @click="openDaUse(row as DeclarativeAgent)"
            >
              对话
            </el-button>
            <el-button
              v-if="row.status === 'draft'"
              size="small"
              type="success"
              @click="daPublish(row as DeclarativeAgent)"
            >
              发布
            </el-button>
            <el-button
              size="small"
              @click="openDaEdit(row as DeclarativeAgent)"
            >
              编辑
            </el-button>
            <el-button
              size="small"
              @click="daDuplicate(row as DeclarativeAgent)"
            >
              复制
            </el-button>
            <el-button
              v-if="row.status === 'published'"
              size="small"
              type="warning"
              @click="daDeprecate(row as DeclarativeAgent)"
            >
              停用
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :aria-label="`删除助手${row.display_name}`"
              @click="daRemove(row as DeclarativeAgent)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建/编辑智能体弹窗（支持独立大模型+子模型指定、1对多工具与技能绑定） -->
    <el-dialog
      v-model="daDialogVisible"
      :title="daEditingId ? '编辑智能体' : '新建智能体'"
      width="700px"
      top="5vh"
    >
      <el-form label-width="115px">
        <el-form-item label="快速模板">
          <el-radio-group
            v-model="daTemplate"
            size="small"
            @change="applyDaTemplate"
          >
            <el-radio-button value="">
              自定义
            </el-radio-button>
            <el-radio-button
              v-for="t in DA_TEMPLATES"
              :key="t.key"
              :value="t.key"
            >
              {{ t.name }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item
          label="智能体编码"
          required
        >
          <el-input
            v-model="decForm.agent_code"
            :disabled="!!daEditingId"
            placeholder="小写字母数字下划线，如 miaoqing_reporter"
          />
          <span class="da-note">编码创建后不可改，支持 @ 点名调用</span>
        </el-form-item>

        <el-form-item
          label="智能体名称"
          required
        >
          <el-input
            ref="daNameInputRef"
            v-model="decForm.name"
            placeholder="如：水稻苗情诊断师"
          />
        </el-form-item>

        <el-form-item label="可见范围">
          <el-radio-group v-model="decForm.visibility">
            <el-radio
              value="public"
              :disabled="!auth.isYardAdmin()"
            >
              公用（本小院全员可用）
            </el-radio>
            <el-radio value="private">
              私有（仅自己可见可用）
            </el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="表情图标">
          <el-input
            v-model="decForm.emoji"
            style="width: 120px"
            placeholder="🌱"
          />
        </el-form-item>

        <el-form-item
          v-if="daEditingId"
          label="卡片首图"
        >
          <div class="da-cover-editor">
            <img
              v-if="daCoverSrc"
              class="da-cover-preview"
              :src="daCoverSrc"
              alt="首图预览"
            />
            <span
              v-else
              class="da-cover-preview da-cover-preview-empty"
              aria-hidden="true"
            >{{ decForm.emoji || '🤖' }}</span>
            <div class="da-cover-actions">
              <el-button
                size="small"
                :loading="daCoverSaving"
                @click="pickDaCover"
              >
                {{ daCoverSrc ? '更换首图' : '上传首图' }}
              </el-button>
              <el-button
                v-if="daCoverSrc"
                size="small"
                type="danger"
                plain
                @click="clearDaCover"
              >
                移除
              </el-button>
            </div>
            <input
              ref="daCoverInputRef"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              class="da-cover-input"
              @change="onDaCoverChange"
            />
          </div>
          <span class="da-note">PNG/JPG/WEBP，≤2MB，展示在卡片顶部</span>
        </el-form-item>

        <el-form-item label="一句话职责">
          <el-input
            v-model="decForm.description"
            placeholder="解读苗情资料并给出田间管理建议"
          />
        </el-form-item>

        <el-form-item
          label="角色人设"
          required
        >
          <el-input
            v-model="decForm.persona_prompt"
            type="textarea"
            :rows="3"
            placeholder="你是一名……（角色、专业领域、语气、合规边界）"
          />
        </el-form-item>

        <el-form-item
          label="任务提示模板"
          required
        >
          <el-input
            v-model="decForm.task_prompt_template"
            type="textarea"
            :rows="3"
            placeholder="用 {{question}} 占位放用户问题"
          />
        </el-form-item>

        <el-form-item label="绑定大模型">
          <div class="model-bind-row">
            <el-select
              v-model="decForm.supplier_id"
              clearable
              placeholder="跟随小院默认供应商"
              style="width: 240px"
              @change="onSupplierChange"
            >
              <el-option
                v-for="s in supplierList"
                :key="s.id"
                :label="`${s.name}（默认: ${s.model_id}）`"
                :value="s.id"
              />
            </el-select>
            <el-select
              v-model="decForm.model_name"
              filterable
              allow-create
              default-first-option
              clearable
              placeholder="可选：指定具体子模型（可手输）"
              style="width: 240px"
            >
              <el-option
                v-for="m in candidateSubModels"
                :key="m"
                :label="m"
                :value="m"
              />
            </el-select>
          </div>
          <span class="da-note">每个智能体可绑定独立供应商与具体模型型号（如 deepseek-reasoner、qwen-max）</span>
        </el-form-item>

        <el-form-item label="内置与MCP工具">
          <div class="skill-bind-box">
            <el-checkbox-group v-model="decForm.allowed_tools">
              <el-checkbox
                v-for="t in toolList"
                :key="t.code"
                :value="t.code"
              >
                {{ t.name }}
                <span class="da-note">{{ t.code }}</span>
              </el-checkbox>
            </el-checkbox-group>
            <span
              v-if="!toolList.length"
              class="da-note"
            >暂无已启用工具</span>
          </div>
        </el-form-item>

        <el-form-item label="扩展技能">
          <BindableExtensionPicker
            v-model="decForm.bound_skills"
            kind="skill"
            :options="bindableSkillOptions"
            :stale-codes="staleSkillCodes"
            :error="extensionsError"
            :loading="extensionsLoading"
            empty-text="暂无可绑定的扩展技能，可在「插件与技能库」安装或导入后重试"
            @retry="loadBindableExtensions"
          />
        </el-form-item>

        <el-form-item label="扩展插件">
          <BindableExtensionPicker
            v-model="decForm.bound_skills"
            kind="plugin"
            :options="bindablePluginOptions"
            :stale-codes="stalePluginCodes"
            :error="extensionsError"
            :loading="extensionsLoading"
            empty-text="暂无可绑定的扩展插件，可在「插件与技能库」安装或导入后重试"
            @retry="loadBindableExtensions"
          />
        </el-form-item>

        <el-form-item label="知识库范围">
          <el-checkbox-group v-model="decForm.knowledge_scopes">
            <el-checkbox value="yard">
              本小院知识库
            </el-checkbox>
            <el-checkbox value="crop_public">
              公开作物知识
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="daDialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="daSaving"
          @click="saveDa(false)"
        >
          保存
        </el-button>
        <el-button
          v-if="!daEditingId"
          type="success"
          :loading="daSaving"
          @click="saveDa(true)"
        >
          保存并立即发布
        </el-button>
      </template>
    </el-dialog>

    <!-- 单聊 / 多智能体群聊弹窗 -->
    <AgentUseDialog
      v-if="daUseTarget"
      :key="daUseDialogKey"
      v-model="daUseVisible"
      :agent="daUseTarget"
      :agents="daUseGroupTargets"
      :model-text="modelLabel(daUseTarget)"
      @card-published="loadDa"
    />

    <!-- 重名三选：复用 / 复制 / 重命名 -->
    <DuplicateNameDialog
      v-model="dupVisible"
      :name="dupName"
      :existing="dupExisting"
      :duplicating="dupDuplicating"
      @reuse="onDupReuse"
      @duplicate="onDupDuplicate"
      @rename="onDupRename"
    />

    <!-- 平台审核队列 -->
    <el-dialog
      v-model="reviewVisible"
      title="高风险智能体 · 平台审核队列"
      width="720px"
    >
      <el-table
        :data="reviewQueue"
        size="small"
        empty-text="队列为空"
      >
        <el-table-column
          label="智能体"
          min-width="200"
        >
          <template #default="{ row }">
            {{ row.emoji || '🤖' }} {{ row.display_name }}
            <span class="da-code">{{ row.agent_code }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="description"
          label="职责"
          min-width="180"
        />
        <el-table-column
          label="操作"
          width="170"
        >
          <template #default="{ row }">
            <el-button
              size="small"
              type="success"
              @click="reviewAction(row as DeclarativeAgent, 'approve')"
            >
              批准
            </el-button>
            <el-button
              size="small"
              type="danger"
              @click="reviewAction(row as DeclarativeAgent, 'reject')"
            >
              驳回
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import type { AxiosRequestConfig } from 'axios'
  import {
    declarativeApi,
    llmSupplierApi,
    teamApi,
    toolApi,
    type DeclarativeAgent,
    type DeclarativeAgentUpdate,
    type DuplicateNameDetails,
    type LlmSupplier,
  } from '@/api/modules'
  import { agentsApi, type BindablePlugin, type BindableSkill } from '@/api/v3'
  import { chatApi } from '@/api/chat'
  import { api, extractErrorDetails, extractErrorMessage } from '@/api/client'
  import { useAuthStore } from '@/stores/auth'
  import AgentUseDialog from '@/components/agents/AgentUseDialog.vue'
  import BindableExtensionPicker from '@/components/agents/BindableExtensionPicker.vue'
  import DeclarativeAgentCard from '@/components/agents/DeclarativeAgentCard.vue'
  import DuplicateNameDialog from '@/components/agents/DuplicateNameDialog.vue'
  import { friendlyErrorMessage } from '@/utils/status'
  import type { ToolDefinition } from '@/types/api'

  const router = useRouter()
  const auth = useAuthStore()

  const daList = ref<DeclarativeAgent[]>([])
  const searchKeyword = ref('')
  const statusFilter = ref<'all' | 'published' | 'draft' | 'review'>('all')
  const visibilityFilter = ref<'all' | 'public' | 'private'>('all')
  const selectedIds = ref<string[]>([])

  const STATUS_FILTER_OPTIONS = [
    { label: '全部状态', value: 'all' },
    { label: '已发布', value: 'published' },
    { label: '草稿', value: 'draft' },
    { label: '待审核', value: 'review' },
  ]
  const VISIBILITY_FILTER_OPTIONS = [
    { label: '全部范围', value: 'all' },
    { label: '小院公用', value: 'public' },
    { label: '仅我私有', value: 'private' },
  ]

  type DaView = 'card' | 'list'
  const DA_VIEW_KEY = 'ky_custom_agents_view'
  const DA_VIEW_OPTIONS: { label: string; value: DaView }[] = [
    { label: '卡片视图', value: 'card' },
    { label: '列表视图', value: 'list' },
  ]
  const daView = ref<DaView>(
    (() => {
      try {
        return localStorage.getItem(DA_VIEW_KEY) === 'list' ? 'list' : 'card'
      } catch {
        return 'card'
      }
    })(),
  )
  const viewSwitchRef = ref<HTMLElement | null>(null)

  function syncViewAriaCurrent() {
    const root = viewSwitchRef.value
    if (!root) return
    const order: DaView[] = ['card', 'list']
    root.querySelectorAll<HTMLElement>('.el-segmented__item').forEach((el, i) => {
      if (order[i] === daView.value) el.setAttribute('aria-current', 'true')
      else el.removeAttribute('aria-current')
    })
  }
  watch(daView, v => {
    try {
      localStorage.setItem(DA_VIEW_KEY, v)
    } catch {
      /* ignore */
    }
    nextTick(syncViewAriaCurrent)
  })

  /* ---------- 统计与筛选 ---------- */
  const publishedCount = computed(() => daList.value.filter(a => a.status === 'published').length)
  const privateOrMineCount = computed(
    () => daList.value.filter(a => a.visibility === 'private' || a.created_by === auth.user?.id).length,
  )
  const totalCalls = computed(() =>
    daList.value.reduce((acc, a) => acc + (a.token_stats?.calls ?? 0), 0),
  )
  const totalTokens = computed(() =>
    daList.value.reduce((acc, a) => acc + (a.token_stats?.total_tokens ?? 0), 0),
  )

  const filteredList = computed(() => {
    const kw = searchKeyword.value.trim().toLowerCase()
    return daList.value.filter(a => {
      if (statusFilter.value === 'published' && a.status !== 'published') return false
      if (statusFilter.value === 'draft' && a.status !== 'draft') return false
      if (statusFilter.value === 'review' && a.status !== 'pending_platform_review') return false
      if (visibilityFilter.value !== 'all' && a.visibility !== visibilityFilter.value) return false
      if (!kw) return true
      const skillsText = skillLabels(a).join(' ').toLowerCase()
      return (
        a.display_name.toLowerCase().includes(kw) ||
        a.agent_code.toLowerCase().includes(kw) ||
        (a.description ?? '').toLowerCase().includes(kw) ||
        skillsText.includes(kw)
      )
    })
  })

  const selectedAgents = computed(() =>
    selectedIds.value
      .map(id => daList.value.find(a => a.id === id))
      .filter((a): a is DeclarativeAgent => !!a),
  )

  const allFilteredSelected = computed(
    () =>
      filteredList.value.length > 0 &&
      filteredList.value.every(a => selectedIds.value.includes(a.id)),
  )
  const someFilteredSelected = computed(() =>
    filteredList.value.some(a => selectedIds.value.includes(a.id)),
  )

  function toggleSelectAgent(agent: DeclarativeAgent) {
    const idx = selectedIds.value.indexOf(agent.id)
    if (idx >= 0) {
      selectedIds.value.splice(idx, 1)
    } else {
      if (selectedIds.value.length >= 8) {
        ElMessage.warning('多智能体群聊或班组单次最多选择 8 个智能体')
        return
      }
      selectedIds.value.push(agent.id)
    }
  }

  function toggleSelectAllFiltered(val: unknown) {
    if (val) {
      const ids = new Set(selectedIds.value)
      for (const a of filteredList.value) {
        if (ids.size >= 8) break
        ids.add(a.id)
      }
      selectedIds.value = Array.from(ids)
    } else {
      const removeSet = new Set(filteredList.value.map(a => a.id))
      selectedIds.value = selectedIds.value.filter(id => !removeSet.has(id))
    }
  }

  /* ---------- 绑模型 / 绑多技能数据源 ---------- */
  const supplierList = ref<LlmSupplier[]>([])
  const toolList = ref<ToolDefinition[]>([])

  interface BindableOption {
    code: string
    name: string
    summary: string
    source: string
    risk: 'low' | 'medium' | 'high'
  }

  const bindableSkills = ref<BindableSkill[]>([])
  const bindablePlugins = ref<BindablePlugin[]>([])
  const extensionsLoading = ref(false)
  const extensionsError = ref(false)

  const supplierMap = computed(() => {
    const map: Record<string, LlmSupplier> = {}
    for (const s of supplierList.value) map[s.id] = s
    return map
  })
  const toolNames = computed(() => {
    const map: Record<string, string> = {}
    for (const t of toolList.value) map[t.code] = t.name
    for (const o of [...bindableSkillOptions.value, ...bindablePluginOptions.value]) {
      map[o.code] = o.name
    }
    return map
  })

  const bindableSkillOptions = computed<BindableOption[]>(() =>
    bindableSkills.value.map(s => ({
      code: s.code,
      name: s.name || s.code,
      summary: s.summary || '',
      source: s.source,
      risk: s.risk_level,
    })),
  )
  const bindablePluginOptions = computed<BindableOption[]>(() =>
    bindablePlugins.value.map(p => ({
      code: p.code,
      name: p.name || p.code,
      summary: p.summary || '',
      source: p.source,
      risk: p.risk_level,
    })),
  )

  function modelLabel(a: DeclarativeAgent): string {
    const sub = a.model_name?.trim()
    if (!a.supplier_id) {
      return sub ? `默认供应商 · ${sub}` : '跟随小院默认'
    }
    const sup = supplierMap.value[a.supplier_id]
    if (!sup) return sub || '已绑自定义模型'
    return sub ? `${sup.name} · ${sub}` : `${sup.model_id || sup.name}（${sup.name}）`
  }

  function skillLabels(a: DeclarativeAgent): string[] {
    const tools = (a.allowed_tools ?? []).map(code => toolNames.value[code] ?? code)
    const bound = (a.bound_skills ?? []).map(code => toolNames.value[code] ?? code)
    return Array.from(new Set([...tools, ...bound]))
  }

  async function loadBindOptions() {
    const [sup, tools] = await Promise.allSettled([llmSupplierApi.list(), toolApi.list()])
    if (sup.status === 'fulfilled') supplierList.value = sup.value
    if (tools.status === 'fulfilled') toolList.value = tools.value
  }

  /** 可绑定扩展（技能/插件分区候选）：失败只影响两个区块，不影响整页与已有绑定值 */
  async function loadBindableExtensions() {
    extensionsLoading.value = true
    extensionsError.value = false
    try {
      const data = await agentsApi.bindableExtensions()
      bindableSkills.value = data.skills ?? []
      bindablePlugins.value = data.plugins ?? []
    } catch {
      bindableSkills.value = []
      bindablePlugins.value = []
      extensionsError.value = true
    } finally {
      extensionsLoading.value = false
    }
  }

  /* ---------- 新建 / 编辑智能体表单 ---------- */
  const daDialogVisible = ref(false)
  const daEditingId = ref('')
  const daSaving = ref(false)
  const daTemplate = ref('')
  const daEditingVisibility = ref<'public' | 'private'>('public')
  const daNameInputRef = ref<{ focus: () => void; select: () => void } | null>(null)
  const daFocusId = ref('')

  const decForm = ref({
    agent_code: '',
    name: '',
    visibility: 'public' as 'public' | 'private',
    emoji: '',
    description: '',
    persona_prompt: '',
    task_prompt_template: '用户问题：{{question}}\n请依据检索到的资料作答，没有依据时如实说明。',
    knowledge_scopes: ['yard'] as string[],
    allowed_tools: [] as string[],
    bound_skills: [] as string[],
    supplier_id: '' as string,
    model_name: '' as string,
  })

  const candidateSubModels = computed<string[]>(() => {
    const sup = supplierMap.value[decForm.value.supplier_id]
    if (!sup) return ['deepseek-chat', 'deepseek-reasoner', 'qwen-plus', 'qwen-max', 'gpt-4o-mini']
    const list = new Set<string>()
    if (sup.model_id) list.add(sup.model_id)
    if (sup.models) {
      sup.models
        .split(/[,，\s]+/)
        .map(s => s.trim())
        .filter(Boolean)
        .forEach(m => list.add(m))
    }
    return Array.from(list)
  })

  /**
   * 失效绑定：已绑定但不在当前可选集合（已下架/无权限）。
   * 加载中/失败时集合未知，不标记失效，避免误伤；绑定值始终保留在 bound_skills 中。
   */
  const staleSkillCodes = computed(() => {
    if (extensionsLoading.value || extensionsError.value) return []
    const known = new Set(bindableSkillOptions.value.map(o => o.code))
    return decForm.value.bound_skills.filter(c => c.startsWith('skill:') && !known.has(c))
  })
  const stalePluginCodes = computed(() => {
    if (extensionsLoading.value || extensionsError.value) return []
    const known = new Set(bindablePluginOptions.value.map(o => o.code))
    return decForm.value.bound_skills.filter(c => c.startsWith('plugin:') && !known.has(c))
  })

  function onSupplierChange() {
    const sup = supplierMap.value[decForm.value.supplier_id]
    if (sup && !decForm.value.model_name) {
      decForm.value.model_name = sup.model_id || ''
    }
  }

  const DA_TEMPLATES = [
    {
      key: 'adviser',
      name: '农技顾问',
      emoji: '🌾',
      persona:
        '你是一名扎根一线的农技顾问，回答基于提供的资料，语言朴实，不确定时如实说明并建议咨询当地农技站。',
      desc: '回答本领域的生产技术与病虫害防治问题',
    },
    {
      key: 'policy',
      name: '政策解读员',
      emoji: '📜',
      persona:
        '你是一名政策解读员，只依据提供的政策文本作答，注明政策出处，不臆测补贴金额与资格。',
      desc: '解读强农惠农政策与申报材料',
    },
    {
      key: 'trainer',
      name: '培训讲师',
      emoji: '🎓',
      persona: '你是一名农民培训讲师，把专业知识拆成步骤化、可操作的要点，多用田间实例。',
      desc: '生成培训提纲与田间实操手册',
    },
    {
      key: 'reporter',
      name: '数据助理',
      emoji: '📊',
      persona: '你是一名农业数据助理，基于提供的数据整理结论，标注统计口径与假设。',
      desc: '汇总试验与物联网数据并生成汇报材料',
    },
  ]

  function applyDaTemplate(key: string | number | boolean | undefined) {
    const t = DA_TEMPLATES.find(x => x.key === String(key ?? ''))
    if (!t) return
    decForm.value.persona_prompt = t.persona
    decForm.value.description = t.desc
    decForm.value.emoji = t.emoji
    if (!decForm.value.name) decForm.value.name = t.name
  }

  function daStatusType(s: string): 'info' | 'warning' | 'success' {
    const map: Record<string, 'info' | 'warning' | 'success'> = {
      draft: 'info',
      pending_platform_review: 'warning',
      published: 'success',
      deprecated: 'info',
    }
    return map[s] ?? 'info'
  }
  function daStatusLabel(s: string) {
    return (
      {
        draft: '草稿',
        pending_platform_review: '平台审核中',
        published: '已发布',
        deprecated: '已停用',
      }[s] || s
    )
  }
  function daFromLabel(f: string) {
    return { manual: '手工', template: '模板', factory: '工厂', chat: '对话', market: '市场' }[f] || f
  }
  function daRowClass({ row }: { row: DeclarativeAgent }): string {
    return row.id === daFocusId.value ? 'is-focus-row' : ''
  }

  async function loadDa() {
    try {
      daList.value = await declarativeApi.list()
    } catch {
      daList.value = []
    }
  }

  function openDaCreate() {
    daEditingId.value = ''
    daTemplate.value = ''
    daFocusId.value = ''
    daEditingVisibility.value = 'public'
    decForm.value = {
      agent_code: '',
      name: '',
      visibility: auth.isYardAdmin() ? 'public' : 'private',
      emoji: '🤖',
      description: '',
      persona_prompt: '',
      task_prompt_template: '用户问题：{{question}}\n请依据检索到的资料作答，没有依据时如实说明。',
      knowledge_scopes: ['yard'],
      allowed_tools: [],
      bound_skills: [],
      supplier_id: '',
      model_name: '',
    }
    daDialogVisible.value = true
  }

  function openDaEdit(row: DeclarativeAgent) {
    daEditingId.value = row.id
    daTemplate.value = ''
    daFocusId.value = ''
    daEditingVisibility.value = row.visibility
    decForm.value = {
      agent_code: row.agent_code,
      name: row.name,
      visibility: row.visibility,
      emoji: row.emoji || '🤖',
      description: row.description || '',
      persona_prompt: row.persona_prompt,
      task_prompt_template: row.task_prompt_template,
      knowledge_scopes: [...row.knowledge_scopes],
      allowed_tools: [...(row.allowed_tools ?? [])],
      bound_skills: [...(row.bound_skills ?? [])],
      supplier_id: row.supplier_id ?? '',
      model_name: row.model_name ?? '',
    }
    daDialogVisible.value = true
  }

  /* ---------- 首图上传 ---------- */
  const daCoverInputRef = ref<HTMLInputElement | null>(null)
  const daCoverSrc = ref<string | null>(null)
  const daCoverSaving = ref(false)
  let daCoverObjectUrl: string | null = null

  function revokeDaCover(): void {
    if (daCoverObjectUrl) {
      URL.revokeObjectURL(daCoverObjectUrl)
      daCoverObjectUrl = null
    }
    daCoverSrc.value = null
  }

  async function refreshDaCover(url: string | null | undefined): Promise<void> {
    revokeDaCover()
    if (!url) return
    try {
      const res = await api.get<Blob>(url, {
        responseType: 'blob',
        skipErrorToast: true,
      } as AxiosRequestConfig & { skipErrorToast?: boolean })
      if (res.data.type.startsWith('image/')) {
        daCoverObjectUrl = URL.createObjectURL(res.data)
        daCoverSrc.value = daCoverObjectUrl
      }
    } catch {
      /* ignore */
    }
  }

  function pickDaCover(): void {
    daCoverInputRef.value?.click()
  }

  async function onDaCoverChange(ev: Event): Promise<void> {
    const input = ev.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file || !daEditingId.value) return
    daCoverSaving.value = true
    try {
      const updated = await declarativeApi.uploadCover(daEditingId.value, file)
      await refreshDaCover(updated.cover_image_url)
      ElMessage.success('首图已更新')
      await loadDa()
    } catch {
      /* toast handled */
    } finally {
      daCoverSaving.value = false
    }
  }

  async function clearDaCover(): Promise<void> {
    if (!daEditingId.value) return
    try {
      await ElMessageBox.confirm('移除这张首图？卡片将回落为表情图标。', '移除首图', {
        type: 'warning',
        confirmButtonText: '移除',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
    try {
      await declarativeApi.clearCover(daEditingId.value)
      revokeDaCover()
      ElMessage.success('首图已移除')
      await loadDa()
    } catch {
      /* toast handled */
    }
  }

  watch(daDialogVisible, vis => {
    if (vis && daEditingId.value) {
      const row = daList.value.find(a => a.id === daEditingId.value)
      void refreshDaCover(row?.cover_image_url)
    } else {
      revokeDaCover()
    }
  })
  onUnmounted(revokeDaCover)

  function duplicateDetails(err: unknown): DuplicateNameDetails | null {
    if (extractErrorMessage(err).code !== 'DUPLICATE_NAME') return null
    const d = extractErrorDetails(err)
    if (!d || typeof d.id !== 'string' || typeof d.name !== 'string') return null
    return {
      id: d.id,
      agent_code: typeof d.agent_code === 'string' ? d.agent_code : '',
      name: d.name,
      status: typeof d.status === 'string' ? d.status : '',
    }
  }

  async function saveDa(publishImmediately = false) {
    const f = decForm.value
    if (!f.agent_code || !f.name || !f.persona_prompt || !f.task_prompt_template) {
      ElMessage.warning('编码、名称、角色人设、任务模板为必填')
      return
    }
    if (!f.task_prompt_template.includes('{{question}}')) {
      ElMessage.warning('任务模板必须包含 {{question}} 占位')
      return
    }
    const shared = {
      name: f.name.trim(),
      description: f.description,
      emoji: f.emoji,
      persona_prompt: f.persona_prompt,
      task_prompt_template: f.task_prompt_template,
      allowed_tools: f.allowed_tools,
      bound_skills: f.bound_skills,
      knowledge_scopes: f.knowledge_scopes,
      supplier_id: f.supplier_id || null,
      model_name: f.model_name.trim() || null,
    }
    daSaving.value = true
    try {
      if (daEditingId.value) {
        const body: DeclarativeAgentUpdate = { ...shared }
        if (f.visibility !== daEditingVisibility.value) body.visibility = f.visibility
        await declarativeApi.update(daEditingId.value, body)
        ElMessage.success('智能体配置已保存')
      } else {
        const created = await declarativeApi.create({
          ...shared,
          agent_code: f.agent_code.trim(),
          visibility: f.visibility,
        })
        if (publishImmediately) {
          const pub = await declarativeApi.publish(created.id)
          ElMessage.success(
            pub.status === 'published'
              ? `「${created.display_name}」已创建并发布上线`
              : `「${created.display_name}」已创建并提交平台审核`,
          )
        } else {
          ElMessage.success('智能体草稿已创建，可随时点「发布」或直接对话测试')
        }
      }
      daDialogVisible.value = false
      await loadDa()
    } catch (e) {
      const dup = duplicateDetails(e)
      if (dup) {
        dupExisting.value = dup
        dupName.value = shared.name
        dupVisible.value = true
      } else {
        const { code, message } = extractErrorMessage(e)
        ElMessage.error(friendlyErrorMessage(code, message || '保存失败'))
      }
    } finally {
      daSaving.value = false
    }
  }

  async function daDuplicate(row: DeclarativeAgent) {
    try {
      const copy = await declarativeApi.duplicate(row.id)
      await loadDa()
      daFocusId.value = copy.id
      ElMessage.success(`已复制为「${copy.display_name}」`)
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message || '复制失败'))
    }
  }

  async function daPublish(row: DeclarativeAgent) {
    try {
      const r = await declarativeApi.publish(row.id)
      ElMessage.success(
        r.status === 'published'
          ? `「${row.display_name}」已发布`
          : `「${row.display_name}」属高风险，已提交平台审核`,
      )
      await loadDa()
    } catch (e) {
      ElMessage.error(extractErrorMessage(e).message || '发布失败')
    }
  }

  async function daDeprecate(row: DeclarativeAgent) {
    try {
      await declarativeApi.deprecate(row.id)
      ElMessage.success(`「${row.display_name}」已停用`)
      await loadDa()
    } catch (e) {
      ElMessage.error(extractErrorMessage(e).message || '停用失败')
    }
  }

  async function daRemove(row: DeclarativeAgent) {
    try {
      await ElMessageBox.confirm(
        `删除「${row.display_name}」？删除后该智能体从列表退出，已有历史对话仍会保留。`,
        '删除智能体',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    try {
      await declarativeApi.remove(row.id)
      ElMessage.success(`已删除「${row.display_name}」`)
      selectedIds.value = selectedIds.value.filter(id => id !== row.id)
      await loadDa()
    } catch {
      /* handled */
    }
  }

  /* ---------- 单聊 / 多智能体群聊 / 一键组建班组 ---------- */
  const daUseVisible = ref(false)
  const daUseTarget = ref<DeclarativeAgent | null>(null)
  const daUseGroupTargets = ref<DeclarativeAgent[]>([])
  const daUseDialogKey = ref('')

  async function ensurePublishedForChat(agent: DeclarativeAgent): Promise<DeclarativeAgent> {
    if (agent.status === 'published') return agent
    if (agent.status === 'draft') {
      try {
        const res = await declarativeApi.publish(agent.id)
        await loadDa()
        const refreshed = daList.value.find(a => a.id === agent.id) ?? agent
        if (res.status === 'published') {
          ElMessage.success(`已自动发布「${agent.display_name}」并开启对话`)
        }
        return refreshed
      } catch {
        return agent
      }
    }
    return agent
  }

  async function openDaUse(row: DeclarativeAgent) {
    const ready = await ensurePublishedForChat(row)
    daUseGroupTargets.value = []
    daUseTarget.value = ready
    daUseDialogKey.value = `single-${ready.id}-${Date.now()}`
    daUseVisible.value = true
  }

  async function openMultiGroupChat() {
    if (selectedAgents.value.length < 2) {
      ElMessage.warning('请至少勾选 2 个智能体发起群聊')
      return
    }
    const list: DeclarativeAgent[] = []
    for (const a of selectedAgents.value) {
      list.push(await ensurePublishedForChat(a))
    }
    daUseTarget.value = list[0]
    daUseGroupTargets.value = list
    daUseDialogKey.value = `group-${list.map(a => a.id).join('-')}-${Date.now()}`
    daUseVisible.value = true
  }

  async function openMainGroupChat() {
    if (selectedAgents.value.length < 2) return
    try {
      const list: DeclarativeAgent[] = []
      for (const a of selectedAgents.value) {
        list.push(await ensurePublishedForChat(a))
      }
      const names = list.map(a => a.display_name).join('、')
      const session = await chatApi.createSession({ title: `群聊：${names.slice(0, 40)}` })
      await chatApi.saveParticipants(
        session.id,
        list.map(a => a.id),
      )
      router.push({ path: '/chat', query: { session_id: session.id } })
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message))
    }
  }

  async function quickBuildTeam() {
    if (selectedAgents.value.length < 2) return
    try {
      const { value: teamName } = await ElMessageBox.prompt(
        '请输入协作班组名称（将按已选智能体顺序组建接力流水线）',
        '一键组建协作班组',
        {
          inputValue: `${selectedAgents.value[0].name}等${selectedAgents.value.length}人协同班组`,
          confirmButtonText: '创建并前往',
          cancelButtonText: '取消',
        },
      )
      if (!teamName?.trim()) return
      const members = []
      for (let i = 0; i < selectedAgents.value.length; i++) {
        const a = await ensurePublishedForChat(selectedAgents.value[i])
        const isLast = i === selectedAgents.value.length - 1
        members.push({
          agent_id: a.id,
          role_name: a.name.slice(0, 20),
          instruction: isLast
            ? `结合前序成员输出，发挥「${a.description || a.name}」专长进行综合把关与总结建议。`
            : `发挥「${a.description || a.name}」专长，针对用户问题给出本环节的专业分析。`,
          is_critic: isLast,
        })
      }
      await teamApi.create({
        name: teamName.trim(),
        description: `由「我的智能体」多选一键组建：${selectedAgents.value.map(a => a.display_name).join(' → ')}`,
        members,
      })
      ElMessage.success('协作班组已创建')
      router.push('/agents/teams')
    } catch {
      /* cancelled or toasted */
    }
  }

  /* ---------- 重名三选 ---------- */
  const dupVisible = ref(false)
  const dupName = ref('')
  const dupExisting = ref<DuplicateNameDetails | null>(null)
  const dupDuplicating = ref(false)

  async function onDupReuse() {
    const ex = dupExisting.value
    dupVisible.value = false
    daDialogVisible.value = false
    if (!ex) return
    const inList = daList.value.find(a => a.id === ex.id)
    if (inList) {
      await openDaUse(inList)
    }
  }

  async function onDupDuplicate() {
    const ex = dupExisting.value
    if (!ex || dupDuplicating.value) return
    dupDuplicating.value = true
    try {
      const copy = await declarativeApi.duplicate(ex.id)
      dupVisible.value = false
      daDialogVisible.value = false
      await loadDa()
      daFocusId.value = copy.id
      ElMessage.success(`已创建副本「${copy.display_name}」`)
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message || '复制失败'))
    } finally {
      dupDuplicating.value = false
    }
  }

  function onDupRename() {
    dupVisible.value = false
    nextTick(() => {
      daNameInputRef.value?.focus()
      daNameInputRef.value?.select?.()
    })
  }

  /* ---------- 平台审核队列 ---------- */
  const reviewVisible = ref(false)
  const reviewQueue = ref<DeclarativeAgent[]>([])

  async function loadReviewQueue() {
    if (!auth.roles.includes('platform_admin')) return
    try {
      reviewQueue.value = await declarativeApi.reviewQueue()
    } catch {
      reviewQueue.value = []
    }
  }

  async function openReviewQueue() {
    await loadReviewQueue()
    reviewVisible.value = true
  }

  async function reviewAction(row: DeclarativeAgent, decision: 'approve' | 'reject') {
    try {
      await declarativeApi.review(row.id, decision)
      ElMessage.success(decision === 'approve' ? '已批准发布' : '已驳回为草稿')
      await loadReviewQueue()
      await loadDa()
    } catch (e) {
      ElMessage.error(extractErrorMessage(e).message || '操作失败')
    }
  }

  onMounted(() => {
    loadDa()
    loadBindOptions()
    loadBindableExtensions()
    loadReviewQueue()
    nextTick(syncViewAriaCurrent)
  })
</script>

<style scoped>
  .custom-agents {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .page-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
  }
  .page-title {
    margin: 0 0 4px;
  }
  .page-sub {
    margin: 0;
    font-size: 13px;
    color: var(--ds-text-3);
  }
  .page-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .stats-bar {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
  }
  .stat-card {
    padding: 12px 16px;
    border-radius: 12px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .stat-label {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .stat-val {
    font-size: 20px;
    font-weight: 700;
    color: var(--ds-text-1);
    font-variant-numeric: tabular-nums;
  }
  .stat-val.ok {
    color: var(--ds-success);
  }
  .stat-val.token {
    color: #34d399;
  }

  .toolbar-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
    padding: 10px 14px;
    border-radius: 12px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
  }
  .toolbar-left,
  .toolbar-right {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }

  .batch-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
    padding: 10px 16px;
    border-radius: 12px;
    background: color-mix(in srgb, var(--ds-accent) 14%, var(--ds-bg-1));
    border: 1px solid color-mix(in srgb, var(--ds-accent) 45%, transparent);
  }
  .batch-info {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  .batch-badge {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-accent-text);
  }
  .batch-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .batch-actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }

  .da-grid {
    row-gap: 16px;
  }
  .da-grid-item {
    display: flex;
  }
  .da-grid-item > * {
    width: 100%;
  }

  .list-card {
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
  }
  .da-row-title {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .da-row-name {
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .da-row-emoji {
    margin-right: 4px;
  }
  .da-code {
    display: block;
    font-size: 11.5px;
    font-family: var(--ds-font-mono);
    color: var(--ds-text-3);
  }
  .table-skills {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .token-cell {
    display: flex;
    flex-direction: column;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
  .token-num {
    color: #34d399;
    font-weight: 600;
  }
  .token-calls {
    color: var(--ds-text-3);
    font-size: 11px;
  }

  .model-bind-row {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    width: 100%;
  }
  .skill-bind-box {
    width: 100%;
    max-height: 150px;
    overflow-y: auto;
    padding: 8px 12px;
    border-radius: 8px;
    border: 1px solid var(--ds-border);
    background: var(--ds-bg-2);
  }
  .da-note {
    display: block;
    font-size: 12px;
    color: var(--ds-text-3);
    margin-top: 2px;
  }
  .da-cover-editor {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .da-cover-preview {
    width: 88px;
    height: 56px;
    border-radius: 8px;
    object-fit: cover;
    border: 1px solid var(--ds-border);
    background: var(--ds-bg-2);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .da-cover-preview-empty {
    font-size: 28px;
  }
  .da-cover-actions {
    display: flex;
    gap: 8px;
  }
  .da-cover-input {
    display: none;
  }
</style>

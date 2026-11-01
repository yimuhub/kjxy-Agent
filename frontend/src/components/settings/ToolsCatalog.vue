<template>
  <el-card class="section">
    <template #header>
      <div class="card-header">
        <span>
          可用工具（{{ items.length }}）
          <span class="note">
            内置 {{ counts.builtin || 0 }} · 本小院 {{ counts.custom || 0 }} · MCP {{ counts.mcp || 0 }}
          </span>
        </span>
        <div class="page-actions">
          <el-button
            size="small"
            @click="load"
          >
            刷新
          </el-button>
          <el-dropdown
            trigger="click"
            @command="onAddCommand"
          >
            <el-button
              size="small"
              type="primary"
            >
              添加可用工具
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="ai">
                  一句话添加（模型生成草稿）
                </el-dropdown-item>
                <el-dropdown-item command="manual">
                  手动填写
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
    </template>

    <AsyncState
      :state="state"
      empty-text="暂无可用工具"
      @retry="load"
    >
      <el-table
        v-col-resize
        :data="items"
        stripe
      >
        <el-table-column
          label="名称"
          min-width="180"
        >
          <template #default="{ row }">
            <button
              type="button"
              class="tool-link"
              @click="openDetail(row as ToolCatalogItem)"
            >
              {{ row.name }}
            </button>
          </template>
        </el-table-column>
        <el-table-column
          prop="code"
          label="编码"
          min-width="160"
          show-overflow-tooltip
        />
        <el-table-column
          label="来源"
          width="130"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="sourceTag(row.source)"
              effect="plain"
            >
              {{ sourceLabels[row.source] || row.source }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="风险"
          width="120"
        >
          <template #default="{ row }">
            <el-tooltip
              :content="riskLabels[row.risk_level] || row.risk_level"
              placement="top"
            >
              <el-tag
                size="small"
                :type="riskTag(row.risk_level)"
              >
                {{ RISK_SHORT[row.risk_level] || row.risk_level }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column
          label="状态"
          width="90"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="row.enabled ? 'success' : 'info'"
            >
              {{ row.enabled ? '已启用' : '草稿/停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          width="230"
        >
          <template #default="{ row }">
            <el-button
              size="small"
              text
              @click="openDetail(row as ToolCatalogItem)"
            >
              详情
            </el-button>
            <el-button
              v-if="!row.readonly"
              size="small"
              text
              :type="row.enabled ? 'info' : 'success'"
              @click="toggle(row as ToolCatalogItem)"
            >
              {{ row.enabled ? '停用' : '启用' }}
            </el-button>
            <el-button
              v-if="row.source === 'custom'"
              size="small"
              text
              @click="openEdit(row as ToolCatalogItem)"
            >
              编辑
            </el-button>
            <el-button
              v-if="row.source === 'custom'"
              size="small"
              text
              type="danger"
              @click="remove(row as ToolCatalogItem)"
            >
              删除
            </el-button>
            <el-tooltip
              v-if="row.source === 'mcp'"
              content="MCP 工具只读：启停在「MCP 工具服务器」里按服务器整体控制"
              placement="top"
            >
              <el-button
                size="small"
                text
                disabled
              >
                只读
              </el-button>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
    </AsyncState>
    <div class="note foot-note">
      内置工具为平台级开关，停用后对所有小院生效；自定义工具需人工确认后才启用，模型生成的草稿不会自动对模型可见。
    </div>

    <!-- 工具详情：统一改成居中弹窗（原右侧抽屉） -->
    <el-dialog
      v-model="detailVisible"
      :title="detail ? `${detail.name}（${detail.code}）` : '工具详情'"
      width="720px"
      append-to-body
    >
      <div
        v-if="detail"
        class="detail"
      >
        <div class="detail-tags">
          <el-tag
            size="small"
            :type="sourceTag(detail.source)"
            effect="plain"
          >
            {{ sourceLabels[detail.source] }}
          </el-tag>
          <el-tag
            size="small"
            :type="riskTag(detail.risk_level)"
          >
            {{ RISK_SHORT[detail.risk_level] || detail.risk_level }}
          </el-tag>
          <el-tag
            size="small"
            :type="detail.enabled ? 'success' : 'info'"
          >
            {{ detail.enabled ? '已启用' : '草稿/停用' }}
          </el-tag>
          <span class="note">v{{ detail.version }}</span>
        </div>

        <h4>能做什么</h4>
        <p class="detail-text">
          {{ detail.description || '（未填写描述）' }}
        </p>

        <h4>风险与权限</h4>
        <p class="detail-text">
          {{ riskLabels[detail.risk_level] || detail.risk_level }}
          <template v-if="detail.required_permissions.length">
            ；所需权限：
            <code
              v-for="perm in detail.required_permissions"
              :key="perm"
              class="perm-code"
            >{{ permissionLabel(perm) }}</code>
          </template>
        </p>

        <h4>参数</h4>
        <el-table
          v-col-resize
          v-if="paramRows.length"
          :data="paramRows"
          size="small"
        >
          <el-table-column
            prop="name"
            label="参数"
            width="130"
          />
          <el-table-column
            prop="type"
            label="类型"
            width="90"
          />
          <el-table-column
            prop="required"
            label="必填"
            width="70"
          />
          <el-table-column
            prop="desc"
            label="说明"
            show-overflow-tooltip
          />
        </el-table>
        <p
          v-else
          class="note"
        >该工具不需要参数。</p>

        <h4>调用示例</h4>
        <pre class="detail-pre">{{ detail.call_example }}</pre>

        <h4>执行方式</h4>
        <p class="detail-text">
          {{ implementationText }}
        </p>
        <p
          v-if="detail.source === 'mcp'"
          class="note"
        >
          来自 MCP 服务器：工具清单以服务器连接测试刷新的缓存快照为准，服务器停用后这里看不到也调不到。
        </p>

        <h4>最近调用</h4>
        <el-table
          v-col-resize
          v-if="detail.recent_calls?.length"
          :data="detail.recent_calls"
          size="small"
        >
          <el-table-column
            prop="at"
            label="时间"
            width="150"
          />
          <el-table-column
            prop="result"
            label="结局"
            width="80"
          />
          <el-table-column
            prop="latency_ms"
            label="耗时(ms)"
            width="90"
          />
          <el-table-column
            prop="stage"
            label="阶段"
            show-overflow-tooltip
          />
        </el-table>
        <p
          v-else
          class="note"
        >还没有调用记录。</p>
      </div>
    </el-dialog>

    <!-- 一句话添加 -->
    <el-dialog
      v-model="aiDialog"
      append-to-body
      title="一句话添加可用工具"
      width="640px"
    >
      <el-input
        v-model="utterance"
        type="textarea"
        :rows="3"
        maxlength="200"
        show-word-limit
        placeholder="例如：把今天的设备告警总结成三句话"
      />
      <el-button
        class="gen-btn"
        type="primary"
        plain
        :loading="generating"
        @click="generate"
      >
        {{ generating ? '生成中…' : '生成草稿' }}
      </el-button>

      <div
        v-if="generated"
        class="preview"
      >
        <el-alert
          :type="generated.source === 'model' ? 'success' : 'warning'"
          :closable="false"
          show-icon
          :title="generated.source === 'model' ? `模型已产出草稿（${generated.model || '未知模型'}）` : '模型输出不可用，已给出保守草稿'"
          :description="generated.note"
        />
        <el-form
          label-width="90px"
          class="preview-form"
        >
          <el-form-item label="工具名称">
            <el-input v-model="draft.name" />
          </el-form-item>
          <el-form-item label="工具编码">
            <el-input
              v-model="draft.code"
              maxlength="60"
            />
            <span class="note">创建后不可修改；小写字母开头，可含数字/下划线/点号</span>
          </el-form-item>
          <el-form-item label="说明">
            <el-input
              v-model="draft.description"
              type="textarea"
              :rows="2"
            />
          </el-form-item>
          <el-form-item label="执行方式">
            <el-radio-group v-model="draft.impl_kind">
              <el-radio value="prompt">
                复用小院模型推理
              </el-radio>
              <el-radio value="http">
                调用外部接口
              </el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item
            v-if="draft.impl_kind === 'prompt'"
            label="提示词"
          >
            <el-input
              v-model="draft.prompt_template"
              type="textarea"
              :rows="3"
              placeholder="用 {参数名} 引用入参"
            />
          </el-form-item>
          <el-form-item
            v-else
            label="接口地址"
          >
            <el-input v-model="draft.url" />
          </el-form-item>
          <el-form-item label="参数">
            <el-input
              v-model="draft.paramsText"
              type="textarea"
              :rows="3"
              placeholder='每行一个：参数名:类型:说明:是否必填，如 crop:string:作物名:必填'
            />
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="aiDialog = false">
          取消
        </el-button>
        <el-button
          :disabled="!generated"
          @click="createDraft(false)"
        >
          存为草稿
        </el-button>
        <el-button
          type="primary"
          :disabled="!generated"
          :loading="creating"
          @click="createDraft(true)"
        >
          确认并启用
        </el-button>
      </template>
    </el-dialog>

    <!-- 手动填写 / 编辑 -->
    <el-dialog
      v-model="manualDialog"
      append-to-body
      :title="editingCode ? `编辑工具：${editingCode}` : '手动添加工具'"
      width="640px"
    >
      <el-form
        label-width="90px"
      >
        <el-form-item
          label="工具名称"
          required
        >
          <el-input
            v-model="manual.name"
            maxlength="100"
          />
        </el-form-item>
        <el-form-item
          label="工具编码"
          required
        >
          <el-input
            v-model="manual.code"
            maxlength="60"
            :disabled="!!editingCode"
            placeholder="如 pest.alert_read"
          />
        </el-form-item>
        <el-form-item label="说明">
          <el-input
            v-model="manual.description"
            type="textarea"
            :rows="2"
          />
        </el-form-item>
        <el-form-item label="执行方式">
          <el-radio-group v-model="manual.impl_kind">
            <el-radio value="prompt">
              复用小院模型推理
            </el-radio>
            <el-radio value="http">
              调用外部接口
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item
          v-if="manual.impl_kind === 'prompt'"
          label="提示词"
          required
        >
          <el-input
            v-model="manual.prompt_template"
            type="textarea"
            :rows="3"
            placeholder="用 {参数名} 引用入参，例如：请用三句话总结 {crop} 的今日告警"
          />
        </el-form-item>
        <template v-else>
          <el-form-item
            label="接口地址"
            required
          >
            <el-input
              v-model="manual.url"
              placeholder="https://api.example.com/v1/sync"
            />
          </el-form-item>
          <el-form-item label="方法">
            <el-select v-model="manual.method">
              <el-option
                v-for="m in ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']"
                :key="m"
                :label="m"
                :value="m"
              />
            </el-select>
            <span class="note">非 GET 会被升级为「外部副作用」，调用需人工审批</span>
          </el-form-item>
          <el-form-item label="鉴权头">
            <el-input
              v-model="manual.headersText"
              type="textarea"
              :rows="2"
              placeholder='每行一个：头名: 头值，如 X-Token: abc'
            />
            <span class="note">头值加密存储，界面只回显打码形式</span>
          </el-form-item>
        </template>
        <el-form-item label="参数">
          <el-input
            v-model="manual.paramsText"
            type="textarea"
            :rows="3"
            placeholder='每行一个：参数名:类型:说明:是否必填，如 crop:string:作物名:必填'
          />
        </el-form-item>
        <el-form-item label="风险等级">
          <el-select v-model="manual.risk_level">
            <el-option
              v-for="(label, key) in riskLabels"
              :key="key"
              :label="label"
              :value="key"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="manualDialog = false">
          取消
        </el-button>
        <el-button
          v-if="!editingCode"
          @click="saveManual(false)"
        >
          存为草稿
        </el-button>
        <el-button
          type="primary"
          :loading="creating"
          @click="editingCode ? saveEdit() : saveManual(true)"
        >
          {{ editingCode ? '保存' : '确认并启用' }}
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
  /** 可用工具目录（v3.25）：三源统一列表、详情抽屉、手动/一句话添加工具。 */
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ArrowDown } from '@element-plus/icons-vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { toolApi } from '@/api/modules'
  import type { ToolCatalogItem, ToolCatalogOut, ToolDetailOut, ToolGenerateOut } from '@/api/modules'
  import AsyncState from '@/components/AsyncState.vue'
  import { PERMISSION_LABELS } from '@/components/settings/permissions'
  import { classifyError, type ViewState } from '@/utils/status'

  const RISK_SHORT: Record<string, string> = {
    read_only: '只读',
    draft: '起草',
    write_reversible: '可回滚',
    external_side_effect: '外部副作用',
  }

  const items = ref<ToolCatalogItem[]>([])
  const counts = ref<Record<string, number>>({})
  const riskLabels = ref<Record<string, string>>({})
  const sourceLabels = ref<Record<string, string>>({})
  const state = ref<ViewState>('loading')

  const detailVisible = ref(false)
  const detail = ref<ToolDetailOut | null>(null)

  const aiDialog = ref(false)
  const manualDialog = ref(false)
  const utterance = ref('')
  const generating = ref(false)
  const creating = ref(false)
  const generated = ref<ToolGenerateOut | null>(null)
  const editingCode = ref('')

  const draft = reactive({
    name: '',
    code: '',
    description: '',
    impl_kind: 'prompt',
    prompt_template: '',
    url: '',
    method: 'POST',
    paramsText: '',
    risk_level: 'read_only',
  })

  const manual = reactive({
    name: '',
    code: '',
    description: '',
    impl_kind: 'prompt',
    prompt_template: '',
    url: '',
    method: 'POST',
    headersText: '',
    paramsText: '',
    risk_level: 'read_only',
  })

  const paramRows = computed(() => {
    const schema = (detail.value?.input_schema ?? {}) as Record<string, unknown>
    const props = schema.properties as Record<string, Record<string, unknown>> | undefined
    if (!props) return []
    const required = (schema.required as string[]) ?? []
    return Object.entries(props).map(([name, spec]) => ({
      name,
      type: String(spec?.type ?? 'string'),
      required: required.includes(name) ? '必填' : '可选',
      desc: String(spec?.description ?? ''),
    }))
  })

  const implementationText = computed(() => {
    const impl = detail.value?.implementation as Record<string, string> | undefined
    if (!impl) return '内置实现（由平台代码提供）'
    if (impl.kind === 'prompt') return `复用小院已配置模型推理：${impl.prompt_template || '（未填写模板）'}`
    if (impl.kind === 'http') return `${impl.method} ${impl.url}`
    if (impl.kind === 'mcp') return `来自 MCP 服务器「${impl.server}」`
    return '内置实现（由平台代码提供）'
  })

  function permissionLabel(perm: string): string {
    return PERMISSION_LABELS[perm] ?? perm
  }

  function sourceTag(source: string): 'info' | 'primary' | 'success' {
    if (source === 'mcp') return 'primary'
    if (source === 'custom') return 'success'
    return 'info'
  }

  function riskTag(risk: string): 'success' | 'warning' | 'danger' | 'info' {
    if (risk === 'read_only') return 'success'
    if (risk === 'draft') return 'info'
    if (risk === 'write_reversible') return 'warning'
    if (risk === 'external_side_effect') return 'danger'
    return 'info'
  }

  async function load(): Promise<void> {
    state.value = 'loading'
    try {
      const res: ToolCatalogOut = await toolApi.catalog()
      items.value = res.items
      counts.value = res.counts
      riskLabels.value = res.risk_labels
      sourceLabels.value = res.source_labels
      state.value = res.items.length ? 'ready' : 'empty'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  async function openDetail(row: ToolCatalogItem): Promise<void> {
    detail.value = null
    detailVisible.value = true
    try {
      detail.value = await toolApi.detail(row.code)
    } catch {
      /* toast 由 client 统一处理 */
    }
  }

  async function toggle(row: ToolCatalogItem): Promise<void> {
    try {
      if (row.enabled) await toolApi.disable(row.code)
      else await toolApi.enable(row.code)
      ElMessage.success(row.enabled ? '已停用' : '已启用，下一次对话即可被调用')
      await load()
    } catch {
      /* toast 由 client 统一处理 */
    }
  }

  async function remove(row: ToolCatalogItem): Promise<void> {
    try {
      await ElMessageBox.confirm(
        `确定删除自定义工具「${row.name}」？助手上的引用会一并失效。`,
        '删除工具',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    try {
      await toolApi.remove(row.code)
      ElMessage.success('已删除')
      await load()
    } catch {
      /* toast 由 client 统一处理 */
    }
  }

  // ---------- 添加 ----------

  function onAddCommand(command: string): void {
    if (command === 'ai') {
      generated.value = null
      utterance.value = ''
      resetDraft()
      aiDialog.value = true
      return
    }
    editingCode.value = ''
    Object.assign(manual, {
      name: '',
      code: '',
      description: '',
      impl_kind: 'prompt',
      prompt_template: '',
      url: '',
      method: 'POST',
      headersText: '',
      paramsText: '',
      risk_level: 'read_only',
    })
    manualDialog.value = true
  }

  function resetDraft(): void {
    Object.assign(draft, {
      name: '',
      code: '',
      description: '',
      impl_kind: 'prompt',
      prompt_template: '',
      url: '',
      method: 'POST',
      paramsText: '',
      risk_level: 'read_only',
    })
  }

  /** 「参数名:类型:说明:是否必填」逐行解析为 JSON Schema */
  function parseParams(text: string): Record<string, unknown> {
    const properties: Record<string, unknown> = {}
    const required: string[] = []
    text
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean)
      .forEach(line => {
        const [name, type = 'string', desc = '', flag = ''] = line.split(/[:：]/).map(part => part.trim())
        if (!name) return
        properties[name] = { type, description: desc }
        if (flag && flag !== '可选') required.push(name)
      })
    return {
      type: 'object',
      properties,
      required,
      additionalProperties: false,
    }
  }

  function parseHeaders(text: string): Record<string, string> {
    const out: Record<string, string> = {}
    text
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean)
      .forEach(line => {
        const idx = line.search(/[:：]/)
        if (idx <= 0) return
        out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
      })
    return out
  }

  async function generate(): Promise<void> {
    if (!utterance.value.trim()) {
      ElMessage.warning('先用一句话说清这个工具要做什么')
      return
    }
    generating.value = true
    try {
      generated.value = await toolApi.generate(utterance.value.trim())
      const spec = generated.value.spec as Record<string, any>
      const impl = (spec.impl ?? {}) as Record<string, any>
      Object.assign(draft, {
        name: String(spec.name ?? ''),
        code: String(spec.code ?? ''),
        description: String(spec.description ?? ''),
        impl_kind: String(spec.impl_kind ?? 'prompt'),
        prompt_template: String(impl.prompt_template ?? ''),
        url: String(impl.url ?? ''),
        method: String(impl.method ?? 'POST'),
        risk_level: String(spec.risk_level ?? 'read_only'),
        paramsText: Object.entries((spec.input_schema as Record<string, any>)?.properties ?? {})
          .map(
            ([name, p]: [string, any]) =>
              `${name}:${p?.type ?? 'string'}:${p?.description ?? ''}:${
                ((spec.input_schema as Record<string, any>)?.required ?? []).includes(name) ? '必填' : '可选'
              }`,
          )
          .join('\n'),
      })
    } catch {
      generated.value = null
    } finally {
      generating.value = false
    }
  }

  function specFrom(target: typeof draft | typeof manual, headersText = ''): Record<string, unknown> {
    const impl: Record<string, unknown> =
      target.impl_kind === 'http'
        ? {
            url: target.url.trim(),
            method: target.method,
            headers: headersText ? parseHeaders(headersText) : {},
          }
        : { prompt_template: target.prompt_template }
    return {
      code: target.code.trim(),
      name: target.name.trim(),
      description: target.description,
      input_schema: parseParams(target.paramsText),
      impl_kind: target.impl_kind,
      impl,
      risk_level: target.risk_level,
      side_effect: target.risk_level === 'external_side_effect',
      required_permissions: ['tools:execute'],
    }
  }

  async function createDraft(enable: boolean): Promise<void> {
    if (!draft.name.trim() || !draft.code.trim()) {
      ElMessage.warning('名称与编码不能为空')
      return
    }
    creating.value = true
    try {
      await toolApi.create(specFrom(draft), enable)
      ElMessage.success(enable ? '工具已创建并启用' : '草稿已保存，确认后点「启用」')
      aiDialog.value = false
      await load()
    } catch {
      /* toast 由 client 统一处理 */
    } finally {
      creating.value = false
    }
  }

  async function saveManual(enable: boolean): Promise<void> {
    if (!manual.name.trim() || !manual.code.trim()) {
      ElMessage.warning('名称与编码不能为空')
      return
    }
    creating.value = true
    try {
      await toolApi.create(specFrom(manual, manual.headersText), enable)
      ElMessage.success(enable ? '工具已创建并启用' : '草稿已保存')
      manualDialog.value = false
      await load()
    } catch {
      /* toast 由 client 统一处理 */
    } finally {
      creating.value = false
    }
  }

  function openEdit(row: ToolCatalogItem): void {
    editingCode.value = row.code
    const impl = (row.implementation ?? {}) as Record<string, string>
    Object.assign(manual, {
      name: row.name,
      code: row.code,
      description: row.description,
      impl_kind: row.impl_kind,
      prompt_template: impl.prompt_template ?? '',
      url: impl.url ?? '',
      method: impl.method ?? 'POST',
      // 头值只回显打码：留空即保持库里已存密文不变
      headersText: '',
      paramsText: Object.entries(
        ((row.input_schema as Record<string, any>)?.properties ?? {}) as Record<string, any>,
      )
        .map(
          ([name, p]) =>
            `${name}:${p?.type ?? 'string'}:${p?.description ?? ''}:${
              ((row.input_schema as Record<string, any>)?.required ?? []).includes(name) ? '必填' : '可选'
            }`,
        )
        .join('\n'),
      risk_level: row.risk_level,
    })
    generated.value = null
    manualDialog.value = true
  }

  async function saveEdit(): Promise<void> {
    creating.value = true
    try {
      await toolApi.update(editingCode.value, specFrom(manual, manual.headersText))
      ElMessage.success('工具定义已保存')
      manualDialog.value = false
      await load()
    } catch {
      /* toast 由 client 统一处理 */
    } finally {
      creating.value = false
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
  .note {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .foot-note {
    margin: 10px 0 0;
  }
  .tool-link {
    padding: 0;
    border: none;
    background: none;
    color: var(--el-color-primary);
    font-size: 13px;
    cursor: pointer;
    text-align: left;
  }
  .detail h4 {
    margin: 18px 0 6px;
    font-size: 13px;
    color: var(--ds-text-1);
  }
  .detail-tags {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .detail-text {
    margin: 0;
    font-size: 13px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }
  .detail-pre {
    margin: 0;
    padding: 8px 10px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: 8px;
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .perm-code {
    margin-right: 6px;
    font-family: ui-monospace, monospace;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .gen-btn {
    margin-top: 10px;
  }
  .preview {
    margin-top: 12px;
  }
  .preview-form {
    margin-top: 12px;
  }
</style>

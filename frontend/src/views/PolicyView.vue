<template>
  <div class="policy">
    <h2 class="page-title">政策服务</h2>

    <el-tabs v-model="activeTab">
      <!-- ============ Tab1：政策列表 ============ -->
      <el-tab-pane label="政策列表" name="list">
        <el-card class="mb">
          <el-form :inline="true" :model="searchForm">
            <el-form-item label="关键词">
              <el-input v-model="searchForm.keyword" placeholder="政策名称关键词" clearable />
            </el-form-item>
            <el-form-item label="省份">
              <el-input v-model="searchForm.province" placeholder="如：江苏" clearable />
            </el-form-item>
            <el-form-item label="类型">
              <el-input v-model="searchForm.policy_type" placeholder="如：补贴/贴息" clearable />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="onSearch">查询</el-button>
              <el-button @click="onResetSearch">重置</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card>
          <AsyncState :state="listState" empty-text="暂无政策数据" @retry="loadPolicies">
            <el-table :data="policies" stripe style="width: 100%" @row-click="openDetail">
              <el-table-column prop="policy_name" label="政策名称" min-width="220" />
              <el-table-column prop="publish_department" label="发布机构" width="160" />
              <el-table-column prop="province" label="省份" width="100" />
              <el-table-column prop="policy_type" label="类型" width="110" />
              <el-table-column label="申报截止" width="130">
                <template #default="{ row }">{{ fmtDate(row.apply_end_at) }}</template>
              </el-table-column>
              <el-table-column label="状态" width="110">
                <template #default="{ row }">
                  <el-tag :type="statusTag(row.status).type" size="small">
                    {{ statusTag(row.status).label }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
            <el-pagination
              class="mt"
              background
              layout="total, prev, pager, next"
              :total="listTotal"
              :page-size="pageSize"
              :current-page="page"
              @current-change="onPageChange"
            />
          </AsyncState>
        </el-card>

        <el-dialog
          v-model="drawerVisible"
          :title="detail?.policy_name"
          width="72%"
          align-center
        >
          <div v-if="detail">
            <el-descriptions :column="1" border size="small" class="mb">
              <el-descriptions-item label="发布机构">{{ detail.publish_department || '-' }}</el-descriptions-item>
              <el-descriptions-item label="发布时间">{{ fmtDate(detail.published_at) }}</el-descriptions-item>
              <el-descriptions-item label="适用范围">
                {{ detail.scope }}{{ detail.province ? ` · ${detail.province}` : '' }}{{ detail.city ? ` · ${detail.city}` : '' }}
              </el-descriptions-item>
              <el-descriptions-item label="申报起止">
                {{ fmtDate(detail.apply_start_at) }} ~ {{ fmtDate(detail.apply_end_at) }}
              </el-descriptions-item>
              <el-descriptions-item label="来源">
                <el-link v-if="detail.source_url" :href="detail.source_url" target="_blank" type="primary">
                  原文链接
                </el-link>
                <span v-else>-</span>
              </el-descriptions-item>
            </el-descriptions>
            <h4>原文</h4>
            <p class="pre">{{ detail.content || '（无）' }}</p>
            <h4>申报条件</h4>
            <pre class="json">{{ pretty(detail.apply_conditions_json) }}</pre>
            <h4>材料要求</h4>
            <pre class="json">{{ pretty(detail.material_requirements_json) }}</pre>
          </div>
        </el-dialog>
      </el-tab-pane>

      <!-- ============ Tab2：政策采集 ============ -->
      <el-tab-pane label="政策采集" name="ingest">
        <el-card>
          <template #header><span>手动采集入库（去重：重复标题+机构会返回 409）</span></template>
          <el-form :model="ingestForm" label-width="130px" class="form-narrow">
            <el-form-item label="政策标题" required>
              <el-input v-model="ingestForm.policy_name" placeholder="如：2026年高标准农田建设补贴政策" />
            </el-form-item>
            <el-form-item label="发布机构" required>
              <el-input v-model="ingestForm.publish_department" placeholder="如：农业农村局" />
            </el-form-item>
            <el-form-item label="发布时间">
              <el-date-picker v-model="ingestForm.published_at" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" />
            </el-form-item>
            <el-form-item label="适用范围">
              <el-select v-model="ingestForm.scope" placeholder="选择">
                <el-option label="全国" value="national" />
                <el-option label="省级" value="province" />
                <el-option label="市级" value="city" />
              </el-select>
            </el-form-item>
            <el-form-item label="省份 / 城市">
              <el-input v-model="ingestForm.province" placeholder="省份（可选）" style="width: 45%" />
              <el-input v-model="ingestForm.city" placeholder="城市（可选）" style="width: 45%; margin-left: 8px" />
            </el-form-item>
            <el-form-item label="政策类型">
              <el-input v-model="ingestForm.policy_type" placeholder="如：补贴、贴息、保险" />
            </el-form-item>
            <el-form-item label="申报起止">
              <el-date-picker
                v-model="ingestRange"
                type="daterange"
                range-separator="至"
                start-placeholder="开始日期"
                end-placeholder="结束日期"
                value-format="YYYY-MM-DD"
              />
            </el-form-item>
            <el-form-item label="申报条件（JSON）">
              <el-input
                v-model="ingestForm.conditionsText"
                type="textarea"
                :rows="3"
                placeholder='如：{"crops": ["水稻"], "min_area_mu": 50, "applicant_types": ["合作社"]}'
              />
            </el-form-item>
            <el-form-item label="材料要求（JSON）">
              <el-input
                v-model="ingestForm.materialsText"
                type="textarea"
                :rows="3"
                placeholder='如：{"materials": [{"name": "身份证复印件", "required": true}]}'
              />
            </el-form-item>
            <el-form-item label="政策原文">
              <el-input v-model="ingestForm.content" type="textarea" :rows="5" placeholder="粘贴政策原文" />
            </el-form-item>
            <el-form-item label="来源链接">
              <el-input v-model="ingestForm.source_url" placeholder="https://…" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="ingesting" @click="submitIngest">采集入库</el-button>
              <el-button @click="resetIngest">重置</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>

      <!-- ============ Tab3：政策匹配 ============ -->
      <el-tab-pane label="政策匹配" name="match">
        <el-card class="mb">
          <template #header><span>经营画像（用于规则比对，不做成功率预测）</span></template>
          <el-form :model="profileForm" label-width="130px" class="form-narrow">
            <el-form-item label="种植作物">
              <el-checkbox-group v-model="profileForm.crops">
                <el-checkbox label="水稻">水稻</el-checkbox>
                <el-checkbox label="小麦">小麦</el-checkbox>
                <el-checkbox label="玉米">玉米</el-checkbox>
                <el-checkbox label="油菜">油菜</el-checkbox>
                <el-checkbox label="蔬菜">蔬菜</el-checkbox>
                <el-checkbox label="果树">果树</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="经营面积（亩）">
              <el-input-number v-model="profileForm.area_mu" :min="0" placeholder="亩数" />
            </el-form-item>
            <el-form-item label="省份 / 城市">
              <el-input v-model="profileForm.province" placeholder="省份" style="width: 45%" />
              <el-input v-model="profileForm.city" placeholder="城市（可选）" style="width: 45%; margin-left: 8px" />
            </el-form-item>
            <el-form-item label="身份类型">
              <el-select v-model="profileForm.applicant_type" placeholder="选择" clearable>
                <el-option label="个人" value="个人" />
                <el-option label="家庭农场" value="家庭农场" />
                <el-option label="合作社" value="合作社" />
                <el-option label="企业" value="企业" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="matching" @click="runMatch">开始匹配</el-button>
              <el-button @click="resetProfile">重置</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card>
          <template #header><span>匹配结果</span></template>
          <AsyncState :state="matchState" empty-text="尚未匹配，点击上方「开始匹配」" @retry="runMatch">
            <el-table :data="matchResults" stripe>
              <el-table-column prop="policy_name" label="政策名称" min-width="220" />
              <el-table-column label="匹配档位" width="120">
                <template #default="{ row }">
                  <el-tag :type="matchLevelMeta(row.match_level).type" size="small">
                    {{ matchLevelMeta(row.match_level).label }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="命中条件" min-width="200">
                <template #default="{ row }">{{ row.matched_conditions?.join('；') || '-' }}</template>
              </el-table-column>
              <el-table-column label="未命中条件" min-width="200">
                <template #default="{ row }">
                  <span class="danger">{{ row.unmet_conditions?.join('；') || '-' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="未知条件" min-width="160">
                <template #default="{ row }">{{ row.unknown_conditions?.join('；') || '-' }}</template>
              </el-table-column>
            </el-table>
          </AsyncState>
        </el-card>
      </el-tab-pane>

      <!-- ============ Tab4：申报材料 ============ -->
      <el-tab-pane label="申报材料" name="draft">
        <el-card class="mb">
          <template #header><span>生成申报材料草案（仅草案，不自动提交）</span></template>
          <el-form :model="draftForm" label-width="130px" class="form-narrow">
            <el-form-item label="选择政策" required>
              <el-select v-model="draftForm.policy_id" placeholder="请选择政策" filterable style="width: 100%">
                <el-option
                  v-for="p in policyOptions"
                  :key="p.id"
                  :label="p.policy_name"
                  :value="p.id"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="申请人姓名" required>
              <el-input v-model="draftForm.applicant_name" placeholder="如：张三" />
            </el-form-item>
            <el-form-item label="申请人类型">
              <el-select v-model="draftForm.applicant_type" placeholder="选择" clearable>
                <el-option label="个人" value="个人" />
                <el-option label="家庭农场" value="家庭农场" />
                <el-option label="合作社" value="合作社" />
                <el-option label="企业" value="企业" />
              </el-select>
            </el-form-item>
            <el-form-item label="联系电话">
              <el-input v-model="draftForm.phone" placeholder="如：13800001111" />
            </el-form-item>
            <el-form-item label="联系地址">
              <el-input v-model="draftForm.address" placeholder="如：某县某镇" />
            </el-form-item>
            <el-form-item label="申请表字段（JSON）">
              <el-input
                v-model="draftForm.formDataText"
                type="textarea"
                :rows="3"
                placeholder='如：{"身份证复印件": "已准备"}（键为材料名称）'
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="drafting" @click="generateDraft">生成草案</el-button>
              <el-button @click="resetDraft">重置</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <AsyncState :state="draftState" empty-text="尚未生成草案" @retry="generateDraft">
          <el-card v-if="draft" class="draft-card">
            <template #header>
              <div class="draft-header">
                <span>{{ draftPolicyName }}</span>
                <el-tag type="info" size="large" class="draft-watermark">草案</el-tag>
              </div>
            </template>
            <el-alert :title="draft.verification_reminder" type="warning" show-icon class="mb" />
            <el-descriptions :column="2" border size="small" class="mb">
              <el-descriptions-item label="申请人">{{ draft.applicant_name }}</el-descriptions-item>
              <el-descriptions-item label="身份类型">{{ draft.applicant_type || '-' }}</el-descriptions-item>
              <el-descriptions-item label="联系方式">
                {{ contactLine }}
              </el-descriptions-item>
              <el-descriptions-item label="草案编号">{{ draft.draft_id }}</el-descriptions-item>
            </el-descriptions>
            <h4>材料清单</h4>
            <el-table :data="checklistRows" stripe size="small">
              <el-table-column label="已备" width="70">
                <template #default="{ row }">
                  <el-checkbox v-model="row.checked" />
                </template>
              </el-table-column>
              <el-table-column prop="name" label="材料名称" min-width="200">
                <template #default="{ row }">
                  <span :class="{ danger: isMissing(row.name) }">{{ row.name }}</span>
                  <el-tag v-if="isMissing(row.name)" type="danger" size="small" class="ml">缺失</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="必填" width="90">
                <template #default="{ row }">
                  {{ row.required ? '是' : '否' }}
                </template>
              </el-table-column>
              <el-table-column prop="source" label="来源" width="120" />
            </el-table>
            <el-alert
              v-if="draft.missing_materials?.length"
              :title="`缺失 ${draft.missing_materials.length} 项必填材料：${draft.missing_materials.join('、')}`"
              type="error"
              show-icon
              class="mb mt"
            />
            <h4>注意事项</h4>
            <el-alert type="info" show-icon :closable="false" class="mb">
              <p class="pre">{{ draft.notes }}</p>
            </el-alert>
          </el-card>
        </AsyncState>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import AsyncState from '@/components/AsyncState.vue'
import { policyApi } from '@/api/modules'
import { extractErrorMessage } from '@/api/client'
import { classifyError, type ViewState } from '@/utils/status'
import type {
  ApplicationDraft,
  Policy,
  PolicyMatchLevel,
  PolicyMatchResult,
} from '@/types/api'

const activeTab = ref('list')

function pretty(o: unknown): string {
  return o == null ? '（无）' : JSON.stringify(o, null, 2)
}
function fmtDate(s: string | null | undefined): string {
  if (!s) return '-'
  return s.slice(0, 10)
}

// ================= Tab1：政策列表 =================
const policies = ref<Policy[]>([])
const listState = ref<ViewState>('loading')
const page = ref(1)
const pageSize = ref(10)
const listTotal = ref(0)
const searchForm = ref({ keyword: '', province: '', policy_type: '' })
const detail = ref<Policy | null>(null)
const drawerVisible = ref(false)

async function loadPolicies() {
  listState.value = 'loading'
  try {
    const res = await policyApi.list({
      page: page.value,
      page_size: pageSize.value,
      keyword: searchForm.value.keyword || undefined,
      province: searchForm.value.province || undefined,
      policy_type: searchForm.value.policy_type || undefined,
    })
    policies.value = res.items
    listTotal.value = res.meta.total
    listState.value = res.items.length ? 'ready' : 'empty'
  } catch (e) {
    listState.value = classifyError(e)
  }
}
function onSearch() {
  page.value = 1
  loadPolicies()
}
function onResetSearch() {
  searchForm.value = { keyword: '', province: '', policy_type: '' }
  onSearch()
}
function onPageChange(p: number) {
  page.value = p
  loadPolicies()
}
function openDetail(row: Policy) {
  detail.value = row
  drawerVisible.value = true
}
function statusTag(s: string): { label: string; type: 'success' | 'warning' | 'info' } {
  if (s === 'active') return { label: '申报中', type: 'success' }
  if (s === 'expired') return { label: '已过期', type: 'warning' }
  return { label: s || '未知', type: 'info' }
}

// ================= Tab2：政策采集 =================
const ingesting = ref(false)
const ingestForm = ref({
  policy_name: '',
  publish_department: '',
  published_at: '' as string | null,
  scope: 'national' as 'national' | 'province' | 'city',
  province: '',
  city: '',
  policy_type: '',
  conditionsText: '',
  materialsText: '',
  content: '',
  source_url: '',
})
const ingestRange = ref<[string, string] | null>(null)

function parseJsonOrNull(text: string, field: string): Record<string, unknown> | null {
  const t = text.trim()
  if (!t) return null
  try {
    const v = JSON.parse(t)
    if (typeof v !== 'object' || v === null) throw new Error('not object')
    return v as Record<string, unknown>
  } catch {
    throw new Error(`${field}不是合法的 JSON 对象`)
  }
}
function resetIngest() {
  ingestForm.value = {
    policy_name: '',
    publish_department: '',
    published_at: null,
    scope: 'national',
    province: '',
    city: '',
    policy_type: '',
    conditionsText: '',
    materialsText: '',
    content: '',
    source_url: '',
  }
  ingestRange.value = null
}
async function submitIngest() {
  if (!ingestForm.value.policy_name.trim() || !ingestForm.value.publish_department.trim()) {
    ElMessage.error('请填写政策标题与发布机构')
    return
  }
  let conditions: Record<string, unknown> | null
  let materials: Record<string, unknown> | null
  try {
    conditions = parseJsonOrNull(ingestForm.value.conditionsText, '申报条件')
    materials = parseJsonOrNull(ingestForm.value.materialsText, '材料要求')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : 'JSON 格式错误')
    return
  }
  ingesting.value = true
  try {
    await policyApi.ingest({
      policy_name: ingestForm.value.policy_name.trim(),
      publish_department: ingestForm.value.publish_department.trim(),
      published_at: ingestForm.value.published_at || null,
      scope: ingestForm.value.scope,
      province: ingestForm.value.province.trim() || null,
      city: ingestForm.value.city.trim() || null,
      policy_type: ingestForm.value.policy_type.trim() || null,
      apply_start_at: ingestRange.value?.[0] || null,
      apply_end_at: ingestRange.value?.[1] || null,
      apply_conditions_json: conditions,
      material_requirements_json: materials,
      content: ingestForm.value.content.trim() || null,
      source_url: ingestForm.value.source_url.trim() || null,
    })
    ElMessage.success('采集入库成功')
    resetIngest()
    if (activeTab.value === 'list') loadPolicies()
  } catch (e) {
    const { code, message } = extractErrorMessage(e)
    if (code === 'POLICY_DUPLICATE') {
      ElMessage.warning('该政策已采集过（重复），未重复入库')
    } else {
      ElMessage.error(message)
    }
  } finally {
    ingesting.value = false
  }
}

// ================= Tab3：政策匹配 =================
const profileForm = ref({
  crops: [] as string[],
  area_mu: null as number | null,
  province: '',
  city: '',
  applicant_type: '' as string,
})
const matching = ref(false)
const matchResults = ref<PolicyMatchResult[]>([])
const matchState = ref<ViewState>('empty')

function matchLevelMeta(l: PolicyMatchLevel): { label: string; type: 'success' | 'warning' | 'info' | 'danger' } {
  if (l === 'eligible') return { label: '符合条件', type: 'success' }
  if (l === 'partial') return { label: '部分符合', type: 'warning' }
  if (l === 'insufficient') return { label: '信息不足', type: 'info' }
  return { label: '不符合', type: 'danger' }
}
function resetProfile() {
  profileForm.value = { crops: [], area_mu: null, province: '', city: '', applicant_type: '' }
}
async function runMatch() {
  matchState.value = 'loading'
  matching.value = true
  try {
    matchResults.value = await policyApi.match({
      crops: profileForm.value.crops.length ? profileForm.value.crops : null,
      area_mu: profileForm.value.area_mu,
      province: profileForm.value.province.trim() || null,
      city: profileForm.value.city.trim() || null,
      applicant_type: profileForm.value.applicant_type || null,
    })
    matchState.value = matchResults.value.length ? 'ready' : 'empty'
  } catch (e) {
    matchState.value = classifyError(e)
  } finally {
    matching.value = false
  }
}

// ================= Tab4：申报材料 =================
const policyOptions = ref<Policy[]>([])
const drafting = ref(false)
const draft = ref<ApplicationDraft | null>(null)
const draftState = ref<ViewState>('empty')
const draftForm = ref({
  policy_id: '',
  applicant_name: '',
  applicant_type: '',
  phone: '',
  address: '',
  formDataText: '',
})
const checklistRows = ref<{ name: string; required: boolean; source: string; checked: boolean }[]>([])

const draftPolicyName = computed(() => {
  const p = policyOptions.value.find(x => x.id === draftForm.value.policy_id)
  return p?.policy_name || '申报材料草案'
})
const contactLine = computed(() => {
  if (!draft.value?.contact_info) return '-'
  const c = draft.value.contact_info as Record<string, unknown>
  return [c.phone, c.address].filter(Boolean).join(' / ') || '-'
})
const missingSet = computed(() => new Set(draft.value?.missing_materials ?? []))
function isMissing(name: string): boolean {
  return missingSet.value.has(name)
}

async function loadPolicyOptions() {
  try {
    const res = await policyApi.list({ page: 1, page_size: 100 })
    policyOptions.value = res.items
  } catch (e) {
    const { message } = extractErrorMessage(e)
    ElMessage.error(`加载政策列表失败：${message}`)
  }
}
function resetDraft() {
  draftForm.value = { policy_id: '', applicant_name: '', applicant_type: '', phone: '', address: '', formDataText: '' }
  draft.value = null
  checklistRows.value = []
  draftState.value = 'empty'
}
async function generateDraft() {
  if (!draftForm.value.policy_id) {
    ElMessage.error('请选择政策')
    return
  }
  if (!draftForm.value.applicant_name.trim()) {
    ElMessage.error('请填写申请人姓名')
    return
  }
  let formData: Record<string, unknown> | null
  try {
    formData = parseJsonOrNull(draftForm.value.formDataText, '申请表字段')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : 'JSON 格式错误')
    return
  }
  draftState.value = 'loading'
  drafting.value = true
  try {
    const res = await policyApi.createDraft(draftForm.value.policy_id, {
      applicant_name: draftForm.value.applicant_name.trim(),
      applicant_type: draftForm.value.applicant_type || null,
      contact_info: {
        ...(draftForm.value.phone.trim() ? { phone: draftForm.value.phone.trim() } : {}),
        ...(draftForm.value.address.trim() ? { address: draftForm.value.address.trim() } : {}),
      },
      form_data: formData,
    })
    draft.value = res
    checklistRows.value = (res.material_checklist ?? []).map(c => ({
      name: c.name,
      required: c.required,
      source: c.source,
      checked: !missingSet.value.has(c.name),
    }))
    draftState.value = 'ready'
    ElMessage.success('草案已生成（草案状态，未提交）')
  } catch (e) {
    draftState.value = classifyError(e)
    const { message } = extractErrorMessage(e)
    ElMessage.error(message)
  } finally {
    drafting.value = false
  }
}

onMounted(() => {
  loadPolicies()
  loadPolicyOptions()
})
watch(activeTab, tab => {
  if (tab === 'list' && !policies.value.length) loadPolicies()
})
</script>

<style scoped>
.mb {
  margin-bottom: 16px;
}
.mt {
  margin-top: 16px;
}
.ml {
  margin-left: 8px;
}
.form-narrow {
  max-width: 720px;
}
.pre {
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ds-text-1);
}
.json {
  background: var(--ds-glass);
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-control);
  padding: 12px;
  font-size: 12px;
  color: var(--ds-text-2);
  overflow-x: auto;
}
.danger {
  color: var(--ds-danger);
}
h4 {
  margin: 16px 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--ds-text-1);
}
.draft-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.draft-watermark {
  letter-spacing: 4px;
}
</style>

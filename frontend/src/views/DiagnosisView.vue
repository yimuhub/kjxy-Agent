<template>
  <div class="diagnosis">
    <div class="page-head">
      <div>
        <h2 class="page-title">
          作物诊断
        </h2>
        <p class="page-sub">
          症状描述生成诊断辅助并留档；涉及用药必须专家复核。图片仅保存引用，当前不做图像识别。
        </p>
      </div>
      <el-button
        v-if="canWrite"
        type="primary"
        @click="openCreate"
      >
        新建诊断
      </el-button>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane
        label="诊断记录"
        name="records"
      >
        <div class="filters">
          <el-select
            v-model="statusFilter"
            clearable
            placeholder="全部状态"
            class="w160"
            @change="loadDiagnoses(1)"
          >
            <el-option
              v-for="s in STATUS_OPTIONS"
              :key="s.value"
              :label="s.label"
              :value="s.value"
            />
          </el-select>
          <el-button @click="loadDiagnoses(1)">
            刷新
          </el-button>
        </div>

        <el-empty
          v-if="!loading && !diagnoses.length"
          description="还没有诊断记录。描述症状后生成一份可追溯的诊断辅助。"
        >
          <el-button
            v-if="canWrite"
            type="primary"
            @click="openCreate"
          >
            新建诊断
          </el-button>
        </el-empty>

        <el-table
          v-else
          v-loading="loading"
          :data="diagnoses"
          size="small"
        >
          <el-table-column
            label="时间"
            width="150"
          >
            <template #default="{ row }">
              {{ formatTime(row.created_at) }}
            </template>
          </el-table-column>
          <el-table-column
            prop="crop"
            label="作物"
            width="100"
          />
          <el-table-column
            prop="plot_name"
            label="地块"
            width="120"
          >
            <template #default="{ row }">
              {{ row.plot_name || '—' }}
            </template>
          </el-table-column>
          <el-table-column
            label="首要候选"
            min-width="180"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ row.top_candidate || '未识别' }}
            </template>
          </el-table-column>
          <el-table-column
            label="置信度"
            width="90"
          >
            <template #default="{ row }">
              {{ Math.round((row.confidence ?? 0) * 100) }}%
            </template>
          </el-table-column>
          <el-table-column
            label="状态"
            width="110"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="STATUS_TAG[row.status]"
              >
                {{ STATUS_LABEL[row.status] }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="90"
          >
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                @click="openDetail(row.diagnosis_id)"
              >
                查看
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <el-pagination
          v-if="total > pageSize"
          class="pager"
          layout="total, prev, pager, next"
          :total="total"
          :page-size="pageSize"
          :current-page="page"
          @current-change="loadDiagnoses"
        />
      </el-tab-pane>

      <el-tab-pane
        label="药剂登记"
        name="pesticides"
      >
        <div class="filters">
          <span class="hint">只登记标签信息与安全间隔期，不替代当地农药登记查询。</span>
          <el-button
            v-if="canWrite"
            type="primary"
            size="small"
            @click="openPesticide()"
          >
            登记药剂
          </el-button>
        </div>
        <el-table
          v-loading="pesticideLoading"
          :data="pesticides"
          size="small"
        >
          <el-table-column
            prop="name"
            label="药剂名称"
            min-width="160"
          />
          <el-table-column
            prop="active_ingredient"
            label="有效成分"
            min-width="140"
          >
            <template #default="{ row }">
              {{ row.active_ingredient || '—' }}
            </template>
          </el-table-column>
          <el-table-column
            label="登记作物"
            min-width="140"
          >
            <template #default="{ row }">
              {{ (row.registered_crops || []).join('、') || '—' }}
            </template>
          </el-table-column>
          <el-table-column
            label="安全间隔期"
            width="110"
          >
            <template #default="{ row }">
              {{ row.pre_harvest_interval_days }} 天
            </template>
          </el-table-column>
          <el-table-column
            label="状态"
            width="90"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="row.status === 'active' ? 'success' : 'info'"
              >
                {{ row.status === 'active' ? '在用' : '归档' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="200"
          >
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                @click="openSafeHarvest(row as PesticideProduct)"
              >
                算收获日
              </el-button>
              <template v-if="canWrite">
                <el-button
                  link
                  @click="openPesticide(row as PesticideProduct)"
                >
                  编辑
                </el-button>
                <el-button
                  link
                  type="danger"
                  @click="removePesticide(row as PesticideProduct)"
                >
                  删除
                </el-button>
              </template>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 新建诊断 -->
    <el-dialog
      v-model="createDialog"
      title="新建作物诊断"
      width="640px"
    >
      <el-form label-width="90px">
        <el-form-item label="作物">
          <el-input
            v-model="createForm.crop"
            maxlength="100"
            placeholder="如：水稻"
          />
        </el-form-item>
        <el-form-item label="地块">
          <el-select
            v-model="createForm.plot_id"
            clearable
            placeholder="可选，关联台账地块"
            class="full"
          >
            <el-option
              v-for="p in plots"
              :key="p.plot_id"
              :label="p.name"
              :value="p.plot_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="生育期">
          <el-input
            v-model="createForm.growth_stage"
            maxlength="100"
            placeholder="如：分蘖期"
          />
        </el-form-item>
        <el-form-item label="症状">
          <el-input
            v-model="createForm.symptoms"
            type="textarea"
            :rows="5"
            maxlength="4000"
            placeholder="描述发病部位、颜色变化、发生范围、近期天气与用药情况"
          />
        </el-form-item>
        <el-form-item label="图片引用">
          <el-input
            v-model="createForm.imageRefs"
            type="textarea"
            :rows="2"
            placeholder="每行一个图片地址或存储键；当前只留档，不做图像识别"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="creating"
          @click="submitCreate"
        >
          生成并留档
        </el-button>
      </template>
    </el-dialog>

    <!-- 转专家会诊 -->
    <el-dialog
      v-model="consultDialog"
      title="转专家会诊"
      width="480px"
    >
      <el-form label-width="84px">
        <el-form-item label="指派专家">
          <el-select
            v-model="consultForm.expert_id"
            clearable
            filterable
            class="full"
            placeholder="可暂不指派"
          >
            <el-option
              v-for="m in members"
              :key="m.user_id"
              :label="m.display_name"
              :value="m.user_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="补充说明">
          <el-input
            v-model="consultForm.note"
            type="textarea"
            :rows="3"
            maxlength="2000"
            placeholder="希望专家重点确认的问题"
          />
        </el-form-item>
      </el-form>
      <p class="muted">
        会创建一个高优先级工单（3 天截止），处置结论走工单回执，全程留痕。
      </p>
      <template #footer>
        <el-button @click="consultDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="consulting"
          @click="submitConsult"
        >
          发起会诊
        </el-button>
      </template>
    </el-dialog>

    <!-- 诊断分享链接 -->
    <el-dialog
      v-model="shareDialog"
      title="只读分享链接"
      width="520px"
    >
      <p class="muted">
        分享内容为诊断摘要快照：不含地块、图片与人员信息；可随时在「农业台账 → 对外分享」撤销。
      </p>
      <el-input
        v-if="sharePath"
        :model-value="shareUrl"
        readonly
      >
        <template #append>
          <el-button @click="copyShare">
            复制
          </el-button>
        </template>
      </el-input>
    </el-dialog>

    <!-- 详情 -->
    <el-dialog
      v-model="detailDrawer"
      :title="detail ? `${detail.crop}诊断 · ${STATUS_LABEL[detail.status]}` : '诊断详情'"
      width="640px"
      align-center
    >
      <template v-if="detail">
        <div class="detail-meta">
          <span>{{ formatTime(detail.created_at) }}</span>
          <span>置信度 {{ Math.round(detail.confidence * 100) }}%</span>
          <el-tag
            v-if="detail.needs_expert_review"
            type="warning"
            size="small"
          >
            需专家复核
          </el-tag>
          <el-tag
            v-if="detail.vision_status === 'not_configured'"
            type="info"
            size="small"
          >
            未启用图像识别
          </el-tag>
        </div>

        <div
          v-if="canWrite"
          class="detail-actions"
        >
          <el-button
            v-if="detail.status !== 'closed'"
            size="small"
            type="warning"
            plain
            @click="openConsult"
          >
            转专家会诊
          </el-button>
          <el-button
            size="small"
            :loading="sharing"
            @click="createShare"
          >
            生成只读分享
          </el-button>
          <span
            v-if="consultOrderId"
            class="muted"
          >已生成会诊工单，可在「告警与工单」跟进。</span>
        </div>

        <section class="block">
          <h4>症状描述</h4>
          <p class="text">
            {{ detail.symptoms }}
          </p>
          <p
            v-if="detail.image_refs?.length"
            class="text muted"
          >
            图片引用：{{ detail.image_refs.join('、') }}
          </p>
        </section>

        <section
          v-if="detail.medication_guard"
          class="block"
        >
          <el-alert
            type="warning"
            :closable="false"
            show-icon
            :title="detail.medication_guard.note"
          />
          <div
            v-if="detail.medication_guard.registered_products.length"
            class="guard-list"
          >
            <div
              v-for="p in detail.medication_guard.registered_products"
              :key="p.pesticide_id"
              class="guard-item"
            >
              <strong>{{ p.name }}</strong>
              <span>{{ p.active_ingredient || '成分未填' }}</span>
              <span>安全间隔 {{ p.pre_harvest_interval_days }} 天</span>
            </div>
          </div>
          <p
            v-else
            class="text muted"
          >
            本院尚未登记该作物的可用药剂。
          </p>
        </section>

        <section
          v-if="detail.output"
          class="block"
        >
          <h4>候选问题</h4>
          <div
            v-for="c in detail.output.candidates"
            :key="c.name"
            class="candidate"
          >
            <div class="candidate-head">
              <strong>{{ c.name }}</strong>
              <span>{{ Math.round(c.confidence * 100) }}%</span>
            </div>
            <p class="text">
              {{ c.notes }}
            </p>
          </div>

          <h4>证据</h4>
          <el-empty
            v-if="!detail.output.evidence.length"
            description="知识库未检索到直接证据。"
          />
          <div
            v-for="e in detail.output.evidence"
            :key="e.chunk_id"
            class="evidence"
          >
            <strong>{{ e.title }}</strong>
            <p class="text">
              {{ e.quote }}
            </p>
          </div>

          <h4>风险与下一步</h4>
          <ul class="notes">
            <li
              v-for="note in detail.output.risk_notes"
              :key="note"
            >
              {{ note }}
            </li>
            <li
              v-for="step in detail.output.next_steps"
              :key="step"
            >
              {{ step }}
            </li>
          </ul>
          <p class="disclaimer">
            {{ detail.output.disclaimer }}
          </p>
        </section>

        <section class="block">
          <h4>专家复核</h4>
          <div
            v-if="detail.expert_reviewed_at"
            class="review-result"
          >
            <p><strong>结论：</strong>{{ detail.final_diagnosis || '—' }}</p>
            <p><strong>意见：</strong>{{ detail.expert_note }}</p>
            <p class="muted">
              复核时间 {{ formatTime(detail.expert_reviewed_at) }}
            </p>
          </div>
          <el-form
            v-if="canReview"
            label-width="80px"
          >
            <el-form-item label="结论">
              <el-input
                v-model="reviewForm.final_diagnosis"
                maxlength="4000"
                placeholder="专家最终判断（可选）"
              />
            </el-form-item>
            <el-form-item label="意见">
              <el-input
                v-model="reviewForm.expert_note"
                type="textarea"
                :rows="3"
                maxlength="4000"
                placeholder="处置建议与注意事项"
              />
            </el-form-item>
            <el-form-item label="状态">
              <el-select
                v-model="reviewForm.status"
                class="w160"
              >
                <el-option
                  label="已确认"
                  value="confirmed"
                />
                <el-option
                  label="已关闭"
                  value="closed"
                />
              </el-select>
            </el-form-item>
            <el-button
              type="primary"
              :loading="reviewing"
              @click="submitReview"
            >
              提交复核
            </el-button>
          </el-form>
        </section>

        <section class="block">
          <h4>跟进记录</h4>
          <el-empty
            v-if="!detail.follow_ups.length"
            description="还没有跟进记录。"
          />
          <div
            v-for="f in detail.follow_ups"
            :key="f.followup_id"
            class="follow-up"
          >
            <div class="follow-head">
              <strong>{{ f.occurred_on }}</strong>
              <span class="muted">{{ formatTime(f.created_at) }}</span>
            </div>
            <p class="text">
              {{ f.note }}
            </p>
          </div>
          <el-form
            v-if="canWrite"
            label-width="80px"
            class="follow-form"
          >
            <el-form-item label="日期">
              <el-date-picker
                v-model="followForm.occurred_on"
                type="date"
                value-format="YYYY-MM-DD"
              />
            </el-form-item>
            <el-form-item label="观察">
              <el-input
                v-model="followForm.note"
                type="textarea"
                :rows="3"
                maxlength="4000"
                placeholder="症状变化、处置动作与效果"
              />
            </el-form-item>
            <el-button
              :loading="following"
              @click="submitFollowUp"
            >
              添加跟进
            </el-button>
          </el-form>
        </section>
      </template>
    </el-dialog>

    <!-- 药剂编辑 -->
    <el-dialog
      v-model="pesticideDialog"
      :title="pesticideForm.pesticide_id ? '编辑药剂' : '登记药剂'"
      width="560px"
    >
      <el-form label-width="100px">
        <el-form-item label="名称">
          <el-input
            v-model="pesticideForm.name"
            maxlength="120"
          />
        </el-form-item>
        <el-form-item label="有效成分">
          <el-input
            v-model="pesticideForm.active_ingredient"
            maxlength="120"
          />
        </el-form-item>
        <el-form-item label="登记作物">
          <el-input
            v-model="pesticideForm.cropsText"
            placeholder="逗号分隔，如：水稻,小麦"
          />
        </el-form-item>
        <el-form-item label="安全间隔期">
          <el-input-number
            v-model="pesticideForm.pre_harvest_interval_days"
            :min="0"
            :max="365"
            controls-position="right"
          />
          <span class="unit-hint">天</span>
        </el-form-item>
        <el-form-item label="标签备注">
          <el-input
            v-model="pesticideForm.label_notes"
            type="textarea"
            :rows="3"
            maxlength="2000"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select
            v-model="pesticideForm.status"
            class="w160"
          >
            <el-option
              label="在用"
              value="active"
            />
            <el-option
              label="归档"
              value="archived"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pesticideDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="savingPesticide"
          @click="savePesticide"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 安全间隔计算 -->
    <el-dialog
      v-model="harvestDialog"
      title="安全间隔期计算"
      width="440px"
    >
      <p class="text">
        {{ harvestProduct?.name }} · 安全间隔 {{ harvestProduct?.pre_harvest_interval_days }} 天
      </p>
      <el-date-picker
        v-model="appliedOn"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="选择施药日期"
      />
      <p
        v-if="harvestResult"
        class="harvest-result"
      >
        最早可采收：<strong>{{ harvestResult.earliest_harvest_on }}</strong>
      </p>
      <p
        v-if="harvestResult"
        class="muted"
      >
        {{ harvestResult.note }}
      </p>
      <template #footer>
        <el-button @click="harvestDialog = false">
          关闭
        </el-button>
        <el-button
          type="primary"
          :disabled="!appliedOn"
          @click="calcHarvest"
        >
          计算
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import {
    diagnosisApi,
    farmApi,
    shareApi,
    workOrderApi,
    type AssignableMember,
    type FarmDiagnosis,
    type FarmDiagnosisSummary,
    type FarmPlot,
    type PesticideProduct,
  } from '@/api/modules'
  import { useAuthStore } from '@/stores/auth'
  import { formatDateTime as formatTime } from '@/utils/format'

  const STATUS_OPTIONS = [
    { value: 'open', label: '待跟进' },
    { value: 'expert_review', label: '待专家复核' },
    { value: 'confirmed', label: '已确认' },
    { value: 'closed', label: '已关闭' },
  ]
  const STATUS_LABEL: Record<string, string> = Object.fromEntries(
    STATUS_OPTIONS.map(s => [s.value, s.label]),
  )
  const STATUS_TAG: Record<string, 'success' | 'warning' | 'info' | 'danger'> = {
    open: 'info',
    expert_review: 'warning',
    confirmed: 'success',
    closed: 'info',
  }

  const auth = useAuthStore()
  const canWrite = computed(() =>
    auth.hasAnyRole(['yard_owner', 'yard_admin', 'platform_admin', 'expert', 'operator']),
  )
  const canReview = computed(() =>
    auth.hasAnyRole(['yard_owner', 'yard_admin', 'platform_admin', 'expert']),
  )

  const tab = ref('records')
  const loading = ref(false)
  const statusFilter = ref('')
  const diagnoses = ref<FarmDiagnosisSummary[]>([])
  const page = ref(1)
  const pageSize = 20
  const total = ref(0)

  const plots = ref<FarmPlot[]>([])

  const createDialog = ref(false)
  const creating = ref(false)
  const createForm = reactive({
    crop: '',
    plot_id: '',
    growth_stage: '',
    symptoms: '',
    imageRefs: '',
  })

  const detailDrawer = ref(false)
  const detail = ref<FarmDiagnosis | null>(null)
  const reviewing = ref(false)
  const reviewForm = reactive({
    final_diagnosis: '',
    expert_note: '',
    status: 'confirmed' as 'confirmed' | 'closed',
  })
  const following = ref(false)
  const followForm = reactive({ occurred_on: '', note: '' })

  const pesticideLoading = ref(false)
  const pesticides = ref<PesticideProduct[]>([])
  const pesticideDialog = ref(false)
  const savingPesticide = ref(false)
  const pesticideForm = reactive({
    pesticide_id: '',
    name: '',
    active_ingredient: '',
    cropsText: '',
    pre_harvest_interval_days: 0,
    label_notes: '',
    status: 'active' as 'active' | 'archived',
  })

  const harvestDialog = ref(false)
  const harvestProduct = ref<PesticideProduct | null>(null)
  const appliedOn = ref('')
  const harvestResult = ref<{ earliest_harvest_on: string; note: string } | null>(null)

  async function loadDiagnoses(nextPage = page.value) {
    page.value = nextPage
    loading.value = true
    try {
      const res = await diagnosisApi.list({
        status: statusFilter.value || undefined,
        page: page.value,
        page_size: pageSize,
      })
      diagnoses.value = res.items
      total.value = res.meta.total
    } catch {
      diagnoses.value = []
      total.value = 0
    } finally {
      loading.value = false
    }
  }

  async function loadPlots() {
    try {
      plots.value = await farmApi.plots()
    } catch {
      plots.value = []
    }
  }

  async function loadPesticides() {
    pesticideLoading.value = true
    try {
      pesticides.value = await diagnosisApi.pesticides()
    } catch {
      pesticides.value = []
    } finally {
      pesticideLoading.value = false
    }
  }

  function openCreate() {
    Object.assign(createForm, {
      crop: plots.value[0]?.crop ?? '',
      plot_id: '',
      growth_stage: '',
      symptoms: '',
      imageRefs: '',
    })
    createDialog.value = true
  }

  async function submitCreate() {
    if (!createForm.crop.trim() || !createForm.symptoms.trim()) {
      return ElMessage.warning('请填写作物与症状描述')
    }
    creating.value = true
    try {
      const imageRefs = createForm.imageRefs
        .split('\n')
        .map(s => s.trim())
        .filter(Boolean)
      const created = await diagnosisApi.create({
        crop: createForm.crop.trim(),
        symptoms: createForm.symptoms.trim(),
        plot_id: createForm.plot_id || undefined,
        growth_stage: createForm.growth_stage || undefined,
        image_refs: imageRefs,
      })
      createDialog.value = false
      ElMessage.success(
        created.needs_expert_review ? '诊断已生成，等待专家复核' : '诊断已生成并留档',
      )
      await loadDiagnoses(1)
      await openDetail(created.diagnosis_id)
    } finally {
      creating.value = false
    }
  }

  async function openDetail(diagnosisId: string) {
    detail.value = await diagnosisApi.detail(diagnosisId)
    reviewForm.final_diagnosis = detail.value.final_diagnosis ?? ''
    reviewForm.expert_note = detail.value.expert_note ?? ''
    reviewForm.status = detail.value.status === 'closed' ? 'closed' : 'confirmed'
    followForm.occurred_on = new Date().toISOString().slice(0, 10)
    followForm.note = ''
    consultOrderId.value = ''
    sharePath.value = ''
    detailDrawer.value = true
  }

  /** ---- 转专家会诊（v3.12）：复用告警与工单闭环 ---- */
  const members = ref<AssignableMember[]>([])
  const consultDialog = ref(false)
  const consulting = ref(false)
  const consultOrderId = ref('')
  const consultForm = reactive({ expert_id: '', note: '' })

  async function openConsult() {
    consultForm.expert_id = ''
    consultForm.note = ''
    consultDialog.value = true
    try {
      members.value = await workOrderApi.assignable()
    } catch {
      members.value = []
    }
  }

  async function submitConsult() {
    if (!detail.value) return
    consulting.value = true
    try {
      const res = await diagnosisApi.consult(detail.value.diagnosis_id, {
        expert_id: consultForm.expert_id || undefined,
        note: consultForm.note.trim() || undefined,
      })
      detail.value = res.diagnosis
      consultOrderId.value = res.work_order.work_order_id
      consultDialog.value = false
      ElMessage.success('已生成专家会诊工单')
      await loadDiagnoses()
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error
          ?.message || '发起会诊失败，请稍后重试'
      ElMessage.warning(message)
    } finally {
      consulting.value = false
    }
  }

  /** ---- 诊断只读分享（v3.12）：脱敏快照 ---- */
  const shareDialog = ref(false)
  const sharing = ref(false)
  const sharePath = ref('')
  const shareUrl = computed(() =>
    sharePath.value ? `${window.location.origin}${sharePath.value}` : '',
  )

  async function createShare() {
    if (!detail.value) return
    sharing.value = true
    try {
      const res = await shareApi.create({
        resource_type: 'diagnosis',
        diagnosis_id: detail.value.diagnosis_id,
        expires_days: 7,
      })
      sharePath.value = res.path
      shareDialog.value = true
    } finally {
      sharing.value = false
    }
  }

  async function copyShare() {
    try {
      await navigator.clipboard.writeText(shareUrl.value)
      ElMessage.success('链接已复制')
    } catch {
      ElMessage.warning('复制失败，请手动选中复制')
    }
  }

  async function submitReview() {
    if (!detail.value) return
    if (!reviewForm.expert_note.trim()) return ElMessage.warning('请填写复核意见')
    reviewing.value = true
    try {
      detail.value = await diagnosisApi.review(detail.value.diagnosis_id, {
        expert_note: reviewForm.expert_note.trim(),
        final_diagnosis: reviewForm.final_diagnosis,
        status: reviewForm.status,
      })
      ElMessage.success('复核已提交')
      await loadDiagnoses()
    } finally {
      reviewing.value = false
    }
  }

  async function submitFollowUp() {
    if (!detail.value) return
    if (!followForm.occurred_on || !followForm.note.trim()) {
      return ElMessage.warning('请填写跟进日期与观察内容')
    }
    following.value = true
    try {
      await diagnosisApi.followUp(detail.value.diagnosis_id, {
        occurred_on: followForm.occurred_on,
        note: followForm.note.trim(),
      })
      detail.value = await diagnosisApi.detail(detail.value.diagnosis_id)
      followForm.note = ''
      ElMessage.success('跟进已添加')
    } finally {
      following.value = false
    }
  }

  function openPesticide(row?: PesticideProduct) {
    Object.assign(pesticideForm, {
      pesticide_id: row?.pesticide_id ?? '',
      name: row?.name ?? '',
      active_ingredient: row?.active_ingredient ?? '',
      cropsText: (row?.registered_crops ?? []).join(','),
      pre_harvest_interval_days: row?.pre_harvest_interval_days ?? 0,
      label_notes: row?.label_notes ?? '',
      status: row?.status ?? 'active',
    })
    pesticideDialog.value = true
  }

  async function savePesticide() {
    if (!pesticideForm.name.trim()) return ElMessage.warning('请填写药剂名称')
    savingPesticide.value = true
    try {
      const payload = {
        name: pesticideForm.name.trim(),
        active_ingredient: pesticideForm.active_ingredient,
        registered_crops: pesticideForm.cropsText
          .split(',')
          .map(s => s.trim())
          .filter(Boolean),
        pre_harvest_interval_days: pesticideForm.pre_harvest_interval_days,
        label_notes: pesticideForm.label_notes,
        status: pesticideForm.status,
      }
      if (pesticideForm.pesticide_id) {
        await diagnosisApi.updatePesticide(pesticideForm.pesticide_id, payload)
        ElMessage.success('药剂已更新')
      } else {
        await diagnosisApi.createPesticide(payload)
        ElMessage.success('药剂已登记')
      }
      pesticideDialog.value = false
      await loadPesticides()
    } finally {
      savingPesticide.value = false
    }
  }

  async function removePesticide(row: PesticideProduct) {
    await ElMessageBox.confirm(`删除药剂「${row.name}」？`, '删除药剂', { type: 'warning' })
    await diagnosisApi.deletePesticide(row.pesticide_id)
    ElMessage.success('已删除')
    await loadPesticides()
  }

  function openSafeHarvest(row: PesticideProduct) {
    harvestProduct.value = row
    appliedOn.value = new Date().toISOString().slice(0, 10)
    harvestResult.value = null
    harvestDialog.value = true
  }

  async function calcHarvest() {
    if (!harvestProduct.value || !appliedOn.value) return
    harvestResult.value = await diagnosisApi.safeHarvest(
      harvestProduct.value.pesticide_id,
      appliedOn.value,
    )
  }

  onMounted(async () => {
    await Promise.all([loadPlots(), loadDiagnoses(1), loadPesticides()])
  })
</script>

<style scoped>
  .diagnosis {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .page-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
  }
  .page-title {
    margin: 0;
    font-size: 20px;
  }
  .page-sub {
    margin: 6px 0 0;
    color: var(--ds-text-3);
    font-size: 13px;
    max-width: 720px;
  }
  .filters {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
  }
  .w160 {
    width: 160px;
  }
  .full {
    width: 100%;
  }
  .pager {
    margin-top: 12px;
    justify-content: flex-end;
  }
  .hint {
    font-size: 12px;
    color: var(--ds-text-3);
    margin-right: auto;
  }
  .detail-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    font-size: 12px;
    color: var(--ds-text-3);
    margin-bottom: 12px;
  }
  .detail-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
    margin-bottom: 12px;
    border-radius: 8px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
  }
  .detail-actions .muted {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .block {
    padding: 14px 0;
    border-top: 1px solid var(--ds-border);
  }
  .block h4 {
    margin: 0 0 8px;
    font-size: 14px;
  }
  .text {
    margin: 4px 0;
    font-size: 13px;
    line-height: 1.65;
    white-space: pre-wrap;
  }
  .muted {
    color: var(--ds-text-3);
  }
  .candidate,
  .evidence,
  .follow-up {
    padding: 8px 0;
    border-bottom: 1px dashed var(--ds-border);
  }
  .candidate-head,
  .follow-head {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    font-size: 13px;
  }
  .notes {
    margin: 6px 0;
    padding-left: 18px;
    font-size: 13px;
    line-height: 1.7;
  }
  .disclaimer {
    margin-top: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .guard-list {
    display: grid;
    gap: 6px;
    margin-top: 8px;
  }
  .guard-item {
    display: flex;
    gap: 12px;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .review-result {
    margin-bottom: 10px;
    font-size: 13px;
  }
  .follow-form {
    margin-top: 12px;
  }
  .unit-hint {
    margin-left: 8px;
    color: var(--ds-text-3);
    font-size: 12px;
  }
  .harvest-result {
    margin-top: 10px;
    font-size: 14px;
  }
</style>

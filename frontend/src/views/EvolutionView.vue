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
        <div class="section-toolbar">
          <el-button @click="openSkillCreate">
            新建 Skill 草稿
          </el-button>
          <el-button
            :loading="skillUploading"
            aria-label="上传 SKILL.md 技能文件"
            @click="pickSkillFile"
          >
            上传 SKILL.md
          </el-button>
          <el-button
            aria-label="前往插件与技能库导入 GitHub / 市场技能"
            @click="router.push('/library')"
          >
            从技能库导入
          </el-button>
          <el-button
            type="primary"
            :loading="suggesting"
            @click="suggestSkills"
          >
            AI 总结候选
          </el-button>
          <input
            ref="skillFileInput"
            class="hidden-file"
            type="file"
            accept=".md,.markdown,.txt"
            aria-label="选择 Skill 文件"
            @change="handleSkillUpload"
          >
        </div>
        <AsyncState
          :state="skillsState"
          empty-text="暂无 Skill，可新建草稿、上传 SKILL.md 或从技能库导入"
          @retry="loadSkills"
        >
          <el-table
            v-col-resize
            :data="skills"
            stripe
          >
            <el-table-column
              label="技能"
              min-width="260"
            >
              <template #default="{ row }">
                <div
                  class="skill-cell"
                  role="button"
                  tabindex="0"
                  :aria-label="`查看技能详情 ${skillName(row)}`"
                  @click="openSkillDetail(row)"
                  @keyup.enter="openSkillDetail(row)"
                >
                  <div class="skill-name">
                    {{ skillName(row) }}
                    <el-tag
                      v-if="isAiCreated(row.created_from)"
                      class="ai-badge"
                      type="warning"
                      size="small"
                      title="该 Skill 由 AI 从近期运行证据总结生成，需审核后生效"
                    >
                      AI 生成
                    </el-tag>
                    <el-tag
                      v-if="row.rollout"
                      class="ai-badge"
                      type="warning"
                      size="small"
                    >
                      灰度中
                    </el-tag>
                  </div>
                  <div class="skill-code">
                    {{ row.skill_code }}
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              prop="version"
              label="版本"
              width="90"
            />
            <el-table-column
              label="状态"
              width="100"
            >
              <template #default="{ row }">
                <el-tag
                  :type="statusType(row.status)"
                  size="small"
                >
                  {{ statusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              label="近 30 天调用"
              width="120"
            >
              <template #default="{ row }">
                <div>{{ usageOf(row).calls ? `${usageOf(row).calls} 次` : '未调用' }}</div>
                <div class="skill-sub">
                  {{ usageOf(row).sessions }} 个会话
                </div>
              </template>
            </el-table-column>
            <el-table-column
              label="自动升级"
              width="130"
            >
              <template #default="{ row }">
                <div class="auto-cell">
                  <el-switch
                    :model-value="upgradeOf(row).enabled"
                    :loading="upgradeBusy === row.skill_code"
                    :aria-label="`切换 ${skillName(row)} 的自动升级`"
                    @change="v => toggleAutoUpgrade(row, v)"
                  />
                  <el-button
                    link
                    size="small"
                    @click="openUpgradeConfig(row)"
                  >
                    设置
                  </el-button>
                </div>
                <div
                  v-if="upgradeOf(row).last_status"
                  class="skill-sub"
                  :title="upgradeOf(row).last_error || ''"
                >
                  {{ runStatusLabel(upgradeOf(row).last_status) }}
                </div>
              </template>
            </el-table-column>
            <el-table-column
              label="操作"
              width="330"
            >
              <template #default="{ row }">
                <el-button
                  size="small"
                  @click="openSkillDetail(row)"
                >
                  详情
                </el-button>
                <el-button
                  v-if="row.status === 'draft'"
                  size="small"
                  type="primary"
                  :loading="reviewingSkillId === row.id"
                  @click="reviewSkill(row, 'submit')"
                >
                  提交审核
                </el-button>
                <el-button
                  v-if="row.status === 'pending_review'"
                  size="small"
                  type="success"
                  :loading="reviewingSkillId === row.id"
                  @click="reviewSkill(row, 'approve')"
                >
                  发布
                </el-button>
                <el-button
                  v-if="canReject(row)"
                  size="small"
                  type="danger"
                  link
                  @click="openRejectSkill(row)"
                >
                  驳回
                </el-button>
                <el-button
                  v-if="row.status === 'published'"
                  size="small"
                  type="warning"
                  link
                  @click="reviewSkill(row, 'rollback')"
                >
                  回滚
                </el-button>
                <el-button
                  size="small"
                  @click="viewEvalRuns(row)"
                >
                  门禁
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
        <div class="section-toolbar">
          <el-button
            :loading="mining"
            @click="minePlugins"
          >
            从历史挖掘插件
          </el-button>
          <el-button
            type="primary"
            @click="openRegister"
          >
            注册插件
          </el-button>
        </div>
        <AsyncState
          :state="pluginsState"
          empty-text="暂未发现插件"
          @retry="loadPlugins"
        >
          <el-table
            v-col-resize
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
                  :type="pluginStatusType(row.status)"
                  size="small"
                >
                  {{
                    row.status
                  }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              label="来源"
              width="100"
            >
              <template #default="{ row }">
                <el-tag
                  v-if="pluginSource(row) === 'auto'"
                  type="warning"
                  size="small"
                >
                  AI 生成
                </el-tag>
                <el-tag
                  v-else-if="pluginSource(row) === 'manual'"
                  type="info"
                  size="small"
                >
                  手动
                </el-tag>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column
              label="操作"
              width="260"
            >
              <template #default="{ row }">
                <template v-if="row.status === 'pending_review'">
                  <el-popconfirm
                    title="批准并发布该插件？"
                    confirm-button-text="批准"
                    cancel-button-text="取消"
                    @confirm="submitReview(row, 'approve')"
                  >
                    <template #reference>
                      <el-button
                        size="small"
                        type="success"
                        :loading="reviewing === row.plugin_id"
                      >
                        批准
                      </el-button>
                    </template>
                  </el-popconfirm>
                  <el-popconfirm
                    title="驳回该插件？"
                    confirm-button-text="驳回"
                    cancel-button-text="取消"
                    @confirm="submitReview(row, 'reject')"
                  >
                    <template #reference>
                      <el-button
                        size="small"
                        type="danger"
                        :loading="reviewing === row.plugin_id"
                      >
                        驳回
                      </el-button>
                    </template>
                  </el-popconfirm>
                </template>
                <template v-else-if="row.status === 'enabled'">
                  <el-button
                    size="small"
                    type="primary"
                    @click="openRun(row)"
                  >
                    运行
                  </el-button>
                  <el-button
                    size="small"
                    @click="togglePlugin(row, false)"
                  >
                    禁用
                  </el-button>
                </template>
                <el-button
                  v-else-if="row.status !== 'rejected'"
                  size="small"
                  type="primary"
                  @click="togglePlugin(row, true)"
                >
                  启用
                </el-button>
                <span v-else>-</span>
              </template>
            </el-table-column>
          </el-table>
        </AsyncState>
      </el-tab-pane>
    </el-tabs>
    <el-dialog
      v-model="rolloutVisible"
      append-to-body
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
              label="小院名单"
              value="yard_allowlist"
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
          label="小院名单"
        >
          <el-input
            v-model="rolloutForm.yards"
            placeholder="逗号分隔的小院 ID"
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
      append-to-body
      title="评测门禁记录"
      width="720px"
    >
      <el-table
        v-col-resize
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
    <el-dialog
      v-model="registerVisible"
      append-to-body
      title="注册插件"
      width="720px"
    >
      <el-form
        :model="regForm"
        label-position="top"
      >
        <el-form-item
          label="名称"
          required
        >
          <el-input
            v-model="regForm.name"
            maxlength="60"
            placeholder="如：传感器数据日报"
          />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="regForm.description"
            type="textarea"
            :rows="2"
            maxlength="200"
            placeholder="这个插件做什么（展示在插件列表）"
          />
        </el-form-item>
        <el-form-item label="输入参数">
          <div class="row-list">
            <div
              v-for="(p, i) in regForm.params"
              :key="i"
              class="row-line"
            >
              <el-input
                v-model="p.key"
                class="w-key"
                placeholder="参数 key（步骤里 {{key}} 引用）"
              />
              <el-input
                v-model="p.label"
                class="w-label"
                placeholder="显示名称"
              />
              <el-checkbox v-model="p.required">
                必填
              </el-checkbox>
              <el-button
                size="small"
                type="danger"
                link
                @click="regForm.params.splice(i, 1)"
              >
                删除
              </el-button>
            </div>
            <div>
              <el-button
                size="small"
                @click="addRegParam"
              >
                + 添加参数
              </el-button>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="步骤">
          <div class="row-list">
            <div
              v-for="(s, i) in regForm.steps"
              :key="i"
              class="step-card"
            >
              <div class="step-head">
                <span class="step-title">
                  步骤 {{ i + 1 }}
                </span>
                <el-select
                  v-model="s.kind"
                  class="w-kind"
                >
                  <el-option
                    label="工具步骤"
                    value="tool"
                  />
                  <el-option
                    label="LLM 步骤"
                    value="llm"
                  />
                </el-select>
                <el-button
                  size="small"
                  type="danger"
                  link
                  @click="removeRegStep(i)"
                >
                  删除
                </el-button>
              </div>
              <template v-if="s.kind === 'tool'">
                <el-select
                  v-model="s.tool"
                  class="step-field"
                  filterable
                  allow-create
                  default-first-option
                  :loading="toolsLoading"
                  placeholder="从已注册工具中选择，或直接输入工具编码"
                >
                  <el-option
                    v-for="t in tools"
                    :key="t.code"
                    :label="toolLabel(t)"
                    :value="t.code"
                  />
                </el-select>
                <div class="hint">
                  工具需在工具注册表中且本小院可用（如 sensor.query）；列表加载失败时可直接输入完整编码。
                </div>
                <el-input
                  v-model="s.argsText"
                  class="step-field"
                  type="textarea"
                  :rows="2"
                  :placeholder="ARGS_PLACEHOLDER"
                />
                <el-input
                  v-model="s.save_as"
                  class="step-field-sm"
                  placeholder="save_as：以此名保存结果（可选）"
                />
              </template>
              <template v-else>
                <el-input
                  v-model="s.prompt"
                  class="step-field"
                  type="textarea"
                  :rows="3"
                  placeholder="提示词，支持 {{变量}} 插值，如：基于 {{r1}} 给出建议 {{question}}"
                />
                <div class="hint">
                  变量来自上方「输入参数」或前面步骤的 save_as。
                </div>
              </template>
            </div>
            <div class="step-add">
              <el-button
                size="small"
                :disabled="regForm.steps.length >= 8"
                @click="addRegStep('tool')"
              >
                + 工具步骤
              </el-button>
              <el-button
                size="small"
                :disabled="regForm.steps.length >= 8"
                @click="addRegStep('llm')"
              >
                + LLM 步骤
              </el-button>
              <span class="hint">
                最多 8 步（{{ regForm.steps.length }}/8）
              </span>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="registerVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="registering"
          @click="submitRegister"
        >
          提交审核
        </el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="runVisible"
      append-to-body
      :title="runDialogTitle"
      width="460px"
    >
      <el-form
        v-if="runParamDefs.length"
        label-position="top"
      >
        <el-form-item
          v-for="p in runParamDefs"
          :key="p.key"
          :label="p.label || p.key"
          :required="!!p.required"
        >
          <el-input
            v-model="runParams[p.key]"
            :placeholder="`请输入${p.label || p.key}`"
          />
        </el-form-item>
      </el-form>
      <div
        v-else
        class="hint"
      >
        该插件未声明输入参数，点击「运行」直接执行。
      </div>
      <template #footer>
        <el-button @click="runVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="running"
          @click="submitRun"
        >
          运行
        </el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="runResultVisible"
      append-to-body
      title="插件运行结果"
      width="480px"
      align-center
    >
      <div class="run-body">
        <div class="run-section-title">
          最终结果
        </div>
        <pre>{{ runFinalText }}</pre>
        <div class="run-section-title">
          输出（outputs）
        </div>
        <pre>{{ runOutputsText }}</pre>
      </div>
    </el-dialog>

    <!-- Skill 详情：点名称/编码打开，说明这个技能能做什么（统一改成居中弹窗） -->
    <el-dialog
      v-model="detailVisible"
      :title="detailSkill ? skillName(detailSkill) : 'Skill 详情'"
      width="560px"
      append-to-body
    >
      <div
        v-if="detailSkill"
        class="skill-detail"
      >
        <div class="detail-head">
          <div>
            <div class="detail-name">
              {{ skillName(detailSkill) }}
            </div>
            <div class="skill-code">
              {{ detailSkill.skill_code }} · v{{ detailSkill.version }}
            </div>
          </div>
          <el-tag :type="statusType(detailSkill.status)">
            {{ statusLabel(detailSkill.status) }}
          </el-tag>
        </div>
        <el-descriptions
          :column="2"
          border
          size="small"
        >
          <el-descriptions-item label="来源">
            {{ skillSourceLabel(detailSkill) }}
          </el-descriptions-item>
          <el-descriptions-item label="范围">
            {{ visibilityLabel(detailSkill.visibility) }}
          </el-descriptions-item>
          <el-descriptions-item label="许可证">
            {{ skillLicense(detailSkill) }}
          </el-descriptions-item>
          <el-descriptions-item label="风险">
            <el-tag
              size="small"
              :type="riskTagType(skillRisk(detailSkill))"
              effect="plain"
            >
              {{ riskLabel(skillRisk(detailSkill)) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">
            {{ formatTime(detailSkill.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="审核备注">
            {{ detailSkill.review_note || '—' }}
          </el-descriptions-item>
        </el-descriptions>
        <h4>能做什么</h4>
        <p class="detail-text">
          {{ skillDescription(detailSkill) }}
        </p>
        <template v-if="skillPrompt(detailSkill)">
          <h4>执行提示词</h4>
          <pre class="detail-pre">{{ skillPrompt(detailSkill) }}</pre>
        </template>
        <template v-if="skillSteps(detailSkill).length">
          <h4>步骤</h4>
          <ol class="detail-steps">
            <li
              v-for="(step, i) in skillSteps(detailSkill)"
              :key="i"
            >
              {{ step }}
            </li>
          </ol>
        </template>
        <h4>签名</h4>
        <p class="detail-text">
          {{ signatureOf(detailSkill) }}
        </p>
        <h4>自动升级</h4>
        <p class="detail-text">
          {{ upgradeOf(detailSkill).enabled ? '已开启' : '未开启' }}；
          门槛：近 30 天调用 ≥ {{ upgradeOf(detailSkill).min_calls }} 次；
          冷却：{{ upgradeOf(detailSkill).cooldown_hours }} 小时。
        </p>
        <div class="detail-actions">
          <el-button
            size="small"
            @click="openUpgradeConfig(detailSkill)"
          >
            自动升级设置
          </el-button>
          <el-button
            size="small"
            @click="openSkillEval(detailSkill)"
          >
            评测报告
          </el-button>
        </div>
      </div>
    </el-dialog>

    <!-- 自动升级设置：只产待审核草稿，不自动发布 -->
    <el-dialog
      v-model="upgradeVisible"
      append-to-body
      :title="upgradeTarget ? `自动升级：${skillName(upgradeTarget)}` : '自动升级'"
      width="600px"
      align-center
    >
      <div class="hint">
        开启后，系统会按调用次数与近期会话/反馈证据定期让模型改进该技能；只会生成「待审核」草稿，仍需人工发布。
      </div>
      <el-form label-width="110px">
        <el-form-item label="自动升级">
          <el-switch v-model="upgradeForm.enabled" />
        </el-form-item>
        <el-form-item label="调用门槛">
          <el-input-number
            v-model="upgradeForm.min_calls"
            :min="1"
            :max="1000"
          />
          <span class="hint-inline">近 30 天调用达到该次数才升级</span>
        </el-form-item>
        <el-form-item label="冷却时间">
          <el-input-number
            v-model="upgradeForm.cooldown_hours"
            :min="1"
            :max="8760"
          />
          <span class="hint-inline">小时</span>
        </el-form-item>
      </el-form>
      <div class="run-head">
        <span class="run-title">最近升级记录</span>
        <el-button
          size="small"
          type="primary"
          :loading="upgradeRunning"
          @click="triggerUpgrade"
        >
          立即尝试升级
        </el-button>
      </div>
      <el-table
        v-col-resize
        :data="upgradeRuns"
        size="small"
        empty-text="暂无升级记录"
      >
        <el-table-column
          label="时间"
          width="170"
        >
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column
          label="触发"
          width="80"
        >
          <template #default="{ row }">
            {{ triggerLabel(row.trigger) }}
          </template>
        </el-table-column>
        <el-table-column
          label="状态"
          width="110"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="runTagType(row.status)"
            >
              {{ runStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="结果"
          min-width="160"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            {{ row.error || row.summary || '—' }}
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="upgradeVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="upgradeSaving"
          @click="saveUpgradeConfig"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 新建 Skill 草稿（从专家审核并入） -->
    <el-dialog
      v-model="createDialog"
      append-to-body
      title="新建 Skill 草稿"
      width="560px"
      align-center
    >
      <el-form
        :model="skillForm"
        label-width="80px"
      >
        <el-form-item
          label="名称"
          required
        >
          <el-input v-model="skillForm.name" />
        </el-form-item>
        <el-form-item
          label="编码"
          required
        >
          <el-input
            v-model="skillForm.code"
            placeholder="如 crop.rice.tillering"
          />
        </el-form-item>
        <el-form-item label="版本">
          <el-input
            v-model="skillForm.version"
            placeholder="如 0.1.0"
          />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="skillForm.description"
            type="textarea"
            :rows="3"
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
          @click="doCreateSkill"
        >
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 驳回 Skill -->
    <el-dialog
      v-model="skillRejectDialog"
      append-to-body
      title="驳回 Skill"
      width="480px"
      align-center
    >
      <el-form label-width="60px">
        <el-form-item label="备注">
          <el-input
            v-model="skillRejectNote"
            type="textarea"
            :rows="3"
            placeholder="请输入驳回原因"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="skillRejectDialog = false">
          取消
        </el-button>
        <el-button
          type="danger"
          :loading="!!reviewingSkillId"
          @click="confirmRejectSkill"
        >
          确认驳回
        </el-button>
      </template>
    </el-dialog>

    <SkillEvalDialog
      v-model="skillEvalDialog"
      :skill="skillEvalTarget"
    />
  </div>
</template>
<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import {
    evolutionApi,
    toolApi,
    type PluginRunResult,
    type SkillAutoUpgradeInfo,
    type SkillUpgradeRunRecord,
    type SkillUsageStats,
    type WorkflowManifest,
    type WorkflowParamDef,
    type WorkflowStepDef,
  } from '@/api/modules'
  import { libraryApi, skillApi } from '@/api/v3'
  import { extractErrorMessage } from '@/api/client'
  import AsyncState from '@/components/AsyncState.vue'
  import {
    definitionOf,
    importSkillMessage,
    riskLabel,
    riskTagType,
    type ImportResultLike,
  } from '@/components/library/libraryMeta'
  import SkillEvalDialog from '@/components/library/SkillEvalDialog.vue'
  import { formatDateTime as formatTime } from '@/utils/format'
  import type { Skill, SkillReviewAction } from '@/types/api'
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
    yards: '',
  })
  // ----- Skill 列表、详情与自动升级（v3.21） -----
  const router = useRouter()
  const SKILL_STATUS_LABELS: Record<string, string> = {
    draft: '草稿',
    testing: '测试中',
    pending_review: '待审核',
    published: '已发布',
    deprecated: '已下线',
    rejected: '已驳回',
  }
  const RUN_STATUS_LABELS: Record<string, string> = {
    running: '进行中',
    success: '已生成草稿',
    skipped: '无需更新',
    failed: '失败',
  }
  const SKILL_SOURCE_LABELS: Record<string, string> = {
    market: '市场',
    market_import: '本地导入',
    seed: '内置',
    manual: '手工',
    ai: 'AI 生成',
    'auto:upgrade': 'AI 自动升级',
  }
  const FALLBACK_UPGRADE: SkillAutoUpgradeInfo = {
    enabled: false,
    min_calls: 10,
    cooldown_hours: 168,
    last_run_at: null,
    last_status: null,
    last_error: null,
    usage: { calls: 0, sessions: 0 },
  }
  const autoUpgradeOverview = ref<Record<string, SkillAutoUpgradeInfo>>({})
  const upgradeBusy = ref('')
  function statusLabel(s: string) {
    return SKILL_STATUS_LABELS[s] ?? s
  }
  function runStatusLabel(s: string | null) {
    return s ? (RUN_STATUS_LABELS[s] ?? s) : ''
  }
  function runTagType(s: string) {
    if (s === 'success') return 'success'
    if (s === 'skipped') return 'info'
    if (s === 'failed') return 'danger'
    return 'warning'
  }
  function triggerLabel(t: string) {
    return t === 'schedule' ? '定时' : t === 'manual' ? '手动' : t || '—'
  }
  function statusType(s: string) {
    if (s === 'published') return 'success'
    if (s === 'pending_review') return 'warning'
    if (s === 'rejected') return 'danger'
    if (s === 'testing') return 'primary'
    return 'info'
  }
  function canReject(row: any): boolean {
    return row?.status === 'draft' || row?.status === 'pending_review'
  }
  function skillName(row: any): string {
    const definition = definitionOf(row)
    const candidates = [definition.name, definition.title, row?.name, row?.skill_code]
    const hit = candidates.find(v => typeof v === 'string' && v.trim())
    return typeof hit === 'string' ? hit : '—'
  }
  function skillDescription(row: any): string {
    const definition = definitionOf(row)
    const value = definition.description ?? definition.summary ?? row?.description
    return typeof value === 'string' && value.trim() ? value : '暂无描述'
  }
  function skillPrompt(row: any): string {
    const definition = definitionOf(row)
    const value = definition.prompt ?? definition.content
    return typeof value === 'string' ? value : ''
  }
  function skillSteps(row: any): string[] {
    const definition = definitionOf(row)
    const steps = definition.steps
    return Array.isArray(steps) ? steps.map(s => String(s)).filter(Boolean) : []
  }
  function skillSourceLabel(row: any): string {
    const source = row?.created_from || row?.source
    if (!source) return '—'
    return SKILL_SOURCE_LABELS[source] ?? source
  }
  function skillLicense(row: any): string {
    const definition = definitionOf(row)
    const license = row?.license || definition.license
    if (typeof license !== 'string' || !license || license === 'unknown') return '未声明'
    return license
  }
  function skillRisk(row: any): string {
    const definition = definitionOf(row)
    const risk = row?.risk_level || definition.risk_level
    return typeof risk === 'string' ? risk : ''
  }
  function visibilityLabel(v: unknown): string {
    if (v === 'private') return '私有'
    if (v === 'platform') return '平台'
    return '小院'
  }
  function signatureOf(row: any): string {
    if (!row?.signature_alg) return '未签名'
    const digest = typeof row.definition_sha256 === 'string' ? row.definition_sha256.slice(0, 12) : ''
    return `${row.signature_alg}${row.key_id ? ` · ${row.key_id}` : ''}${digest ? ` · ${digest}…` : ''}`
  }
  function upgradeOf(row: any): SkillAutoUpgradeInfo {
    const code = String(row?.skill_code ?? '')
    return autoUpgradeOverview.value[code] ?? FALLBACK_UPGRADE
  }
  function usageOf(row: any): SkillUsageStats {
    return upgradeOf(row).usage ?? { calls: 0, sessions: 0 }
  }
  async function loadOverview() {
    try {
      autoUpgradeOverview.value = await evolutionApi.autoUpgradeOverview()
    } catch {
      // 总览失败不阻塞列表：开关回落为关、调用量显示 0
      autoUpgradeOverview.value = {}
    }
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
    await loadOverview()
  }
  async function toggleAutoUpgrade(row: any, value: string | number | boolean) {
    const code = String(row?.skill_code ?? '')
    const enabled = Boolean(value)
    upgradeBusy.value = code
    try {
      const info = await evolutionApi.setAutoUpgrade(row.id, { enabled })
      autoUpgradeOverview.value = {
        ...autoUpgradeOverview.value,
        [code]: { ...info, runs: autoUpgradeOverview.value[code]?.runs },
      }
      ElMessage.success(enabled ? '已开启自动升级：只生成待审核草稿' : '已关闭自动升级')
    } catch {
      /* toast 已统一 */
    } finally {
      upgradeBusy.value = ''
    }
  }

  // ----- 详情抽屉 -----
  const detailVisible = ref(false)
  const detailSkill = ref<any>(null)
  function openSkillDetail(row: any) {
    detailSkill.value = row
    detailVisible.value = true
  }

  // ----- 自动升级设置 -----
  const upgradeVisible = ref(false)
  const upgradeSaving = ref(false)
  const upgradeRunning = ref(false)
  const upgradeTarget = ref<any>(null)
  const upgradeRuns = ref<SkillUpgradeRunRecord[]>([])
  const upgradeForm = ref({ enabled: false, min_calls: 10, cooldown_hours: 168 })
  async function openUpgradeConfig(row: any) {
    upgradeTarget.value = row
    const base = upgradeOf(row)
    upgradeForm.value = {
      enabled: base.enabled,
      min_calls: base.min_calls,
      cooldown_hours: base.cooldown_hours,
    }
    upgradeRuns.value = []
    upgradeVisible.value = true
    try {
      const info = await evolutionApi.getAutoUpgrade(row.id)
      if (upgradeTarget.value?.id !== row.id) return
      upgradeForm.value = {
        enabled: info.enabled,
        min_calls: info.min_calls,
        cooldown_hours: info.cooldown_hours,
      }
      upgradeRuns.value = info.runs ?? []
    } catch {
      /* 设置读取失败时保留列表值，不阻塞 */
    }
  }
  async function saveUpgradeConfig() {
    const row = upgradeTarget.value
    if (!row) return
    upgradeSaving.value = true
    try {
      const info = await evolutionApi.setAutoUpgrade(row.id, { ...upgradeForm.value })
      autoUpgradeOverview.value = {
        ...autoUpgradeOverview.value,
        [String(row.skill_code)]: { ...info, runs: upgradeRuns.value },
      }
      ElMessage.success('自动升级设置已保存')
      upgradeVisible.value = false
    } catch {
      /* toast 已统一 */
    } finally {
      upgradeSaving.value = false
    }
  }
  async function triggerUpgrade() {
    const row = upgradeTarget.value
    if (!row) return
    upgradeRunning.value = true
    try {
      const run = await evolutionApi.runAutoUpgrade(row.id)
      upgradeRuns.value = [run, ...upgradeRuns.value].slice(0, 10)
      const text = run.summary || run.error || '已执行'
      if (run.status === 'success') ElMessage.success(text)
      else if (run.status === 'skipped') ElMessage.info(text)
      else ElMessage.warning(text)
      await loadSkills()
    } catch {
      /* toast 已统一 */
    } finally {
      upgradeRunning.value = false
    }
  }

  // ----- 新建 / 上传 / 审核（从专家审核并入） -----
  const creating = ref(false)
  const createDialog = ref(false)
  const skillForm = reactive({ name: '', code: '', version: '0.1.0', description: '' })
  const skillUploading = ref(false)
  const skillFileInput = ref<HTMLInputElement | null>(null)
  const reviewingSkillId = ref('')
  const skillRejectDialog = ref(false)
  const skillRejectNote = ref('')
  const skillRejectTarget = ref<Skill | null>(null)
  const skillEvalDialog = ref(false)
  const skillEvalTarget = ref<Skill | null>(null)
  function openSkillCreate() {
    skillForm.name = ''
    skillForm.code = ''
    skillForm.version = '0.1.0'
    skillForm.description = ''
    createDialog.value = true
  }
  async function doCreateSkill() {
    if (!skillForm.name.trim()) {
      ElMessage.warning('请填写 Skill 名称')
      return
    }
    if (!skillForm.code.trim()) {
      ElMessage.warning('请填写 Skill 编码')
      return
    }
    creating.value = true
    try {
      const description = skillForm.description.trim()
      // 后端 SkillCreate 只接受 { skill_code, version, definition }；
      // 名称/描述必须放进 definition，否则列表无法展示且字段会被忽略。
      await skillApi.create({
        skill_code: skillForm.code.trim(),
        version: skillForm.version.trim() || '0.1.0',
        definition: {
          name: skillForm.name.trim(),
          description,
          prompt: description,
          steps: [],
        },
      })
      ElMessage.success('草稿已创建')
      createDialog.value = false
      await loadSkills()
    } catch {
      /* toast 已统一 */
    } finally {
      creating.value = false
    }
  }
  function pickSkillFile() {
    skillFileInput.value?.click()
  }
  async function handleSkillUpload(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file) return
    if (!/\.(md|markdown|txt)$/i.test(file.name)) {
      ElMessage.warning('技能文件仅支持 .md/.markdown/.txt')
      return
    }
    if (file.size > 256 * 1024) {
      ElMessage.warning('技能文件不能超过 256KB')
      return
    }
    skillUploading.value = true
    try {
      const result = (await libraryApi.importSkill(file)) as unknown as ImportResultLike
      ElMessage.success(importSkillMessage(result))
      await loadSkills()
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message))
    } finally {
      skillUploading.value = false
    }
  }
  async function reviewSkill(row: any, action: SkillReviewAction) {
    if (action === 'approve' || action === 'rollback') {
      const verb = action === 'approve' ? '发布' : '回滚'
      try {
        await ElMessageBox.confirm(`确定${verb} Skill「${skillName(row)}」吗？`, '二次确认', {
          type: 'warning',
        })
      } catch {
        return
      }
    }
    reviewingSkillId.value = row.id
    try {
      await skillApi.review(row.id, action)
      ElMessage.success(
        action === 'submit' ? '已提交审核' : action === 'approve' ? '已发布' : '已回滚',
      )
      await loadSkills()
    } catch {
      /* toast 已统一 */
    } finally {
      reviewingSkillId.value = ''
    }
  }
  function openRejectSkill(row: any) {
    skillRejectTarget.value = row as Skill
    skillRejectNote.value = ''
    skillRejectDialog.value = true
  }
  async function confirmRejectSkill() {
    const row = skillRejectTarget.value
    if (!row) return
    reviewingSkillId.value = row.id
    try {
      await skillApi.review(row.id, 'reject', skillRejectNote.value || undefined)
      ElMessage.success('已驳回')
      skillRejectDialog.value = false
      await loadSkills()
    } catch {
      /* toast 已统一 */
    } finally {
      reviewingSkillId.value = ''
    }
  }
  function openSkillEval(row: any) {
    skillEvalTarget.value = row as Skill
    skillEvalDialog.value = true
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
        // 后端 RolloutCreate 的入参字段是 yard_ids（extra=ignore），传 yard_allowlist 会被丢弃
        yard_ids:
          rolloutForm.value.strategy === 'yard_allowlist'
            ? rolloutForm.value.yards
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
  // ===== v1.3 进化域（DESIGN-v3 C）：AI 总结候选 + 声明式插件闭环 =====
  /** 进化域专用错误码中文文案（优先于全局映射表） */
  const EVOLUTION_ERROR_MESSAGES: Record<string, string> = {
    EVOLUTION_INSUFFICIENT_EVIDENCE:
      '近 7 天运行证据不足（至少需要 5 次运行），暂不生成技能候选',
    EVOLUTION_LLM_UNAVAILABLE: '模型服务暂时不可用，AI 总结失败，请稍后重试',
  }
  const suggesting = ref(false)
  const mining = ref(false)
  const reviewing = ref<string | null>(null)
  function isAiCreated(v: unknown): boolean {
    return typeof v === 'string' && v.startsWith('auto:')
  }
  function pluginStatusType(s: string) {
    if (s === 'enabled') return 'success'
    if (s === 'pending_review') return 'warning'
    if (s === 'rejected') return 'danger'
    return 'info'
  }
  /** 插件来源：created_from=auto/manual；缺字段时按 manifest._auto 兜底识别 */
  function pluginSource(row: any): '' | 'auto' | 'manual' {
    const v = typeof row?.created_from === 'string' ? row.created_from : ''
    if (v === 'auto' || v.startsWith('auto:')) return 'auto'
    if (v === 'manual') return 'manual'
    const m = row?.manifest_json ?? row?.manifest
    if (m && typeof m === 'object' && m._auto) return 'auto'
    return ''
  }
  /** 从多种结果体里取候选/草稿数量（契约外字段一律容错） */
  function pickCount(res: unknown): number {
    if (Array.isArray(res)) return res.length
    if (!res || typeof res !== 'object') return 0
    const r = res as Record<string, unknown>
    if (typeof r.count === 'number') return r.count
    if (typeof r.created === 'number') return r.created
    for (const k of ['candidates', 'skills', 'plugins', 'items']) {
      if (Array.isArray(r[k])) return (r[k] as unknown[]).length
    }
    return 0
  }
  /** 专用错误码走专用文案，其余汇入全局友好映射（不外泄英文细节） */
  function showFriendlyError(e: unknown) {
    const { code, message } = extractErrorMessage(e)
    ElMessage.error(EVOLUTION_ERROR_MESSAGES[code] ?? friendlyErrorMessage(code, message))
  }
  async function suggestSkills() {
    suggesting.value = true
    try {
      const res = await evolutionApi.suggestSkills({}, { skipErrorToast: true })
      ElMessage.success(`已生成 ${pickCount(res)} 个 AI 技能候选，审核后方可灰度`)
      await loadSkills()
    } catch (e) {
      showFriendlyError(e)
    } finally {
      suggesting.value = false
    }
  }
  async function minePlugins() {
    mining.value = true
    try {
      const res = await evolutionApi.suggestPlugins({ skipErrorToast: true })
      const n = pickCount(res)
      if (n > 0) ElMessage.success(`挖掘出 ${n} 个插件草稿，批准后方可使用`)
      else ElMessage.info('近期未发现重复的工具调用序列，暂无可挖掘的插件')
      await loadPlugins()
    } catch (e) {
      showFriendlyError(e)
    } finally {
      mining.value = false
    }
  }
  async function submitReview(row: any, decision: 'approve' | 'reject') {
    reviewing.value = row?.plugin_id ?? null
    try {
      await evolutionApi.reviewPlugin(row.plugin_id, decision)
      ElMessage.success(decision === 'approve' ? '已批准，插件可以运行了' : '已驳回')
      await loadPlugins()
    } catch {
      /* toast 已统一 */
    } finally {
      reviewing.value = null
    }
  }

  // ----- 注册插件：workflow manifest 编辑器 -----
  interface RegParamRow {
    key: string
    label: string
    required: boolean
  }
  interface RegStepRow {
    kind: 'tool' | 'llm'
    tool: string
    argsText: string
    save_as: string
    prompt: string
  }
  const registerVisible = ref(false)
  const registering = ref(false)
  const tools = ref<{ code: string; name: string }[]>([])
  const toolsLoading = ref(false)
  const toolsLoaded = ref(false)
  const ARGS_PLACEHOLDER = '工具参数 args：JSON 对象，如 {"limit": 10}'
  const regForm = ref({
    name: '',
    description: '',
    params: [] as RegParamRow[],
    steps: [] as RegStepRow[],
  })
  function newStep(kind: 'tool' | 'llm'): RegStepRow {
    return { kind, tool: '', argsText: '{}', save_as: '', prompt: '' }
  }
  function addRegParam() {
    regForm.value.params.push({ key: '', label: '', required: false })
  }
  function addRegStep(kind: 'tool' | 'llm') {
    if (regForm.value.steps.length >= 8) return
    regForm.value.steps.push(newStep(kind))
  }
  function removeRegStep(i: number) {
    regForm.value.steps.splice(i, 1)
  }
  function toolLabel(t: { code: string; name: string }) {
    return t.name && t.name !== t.code ? `${t.name} (${t.code})` : t.code
  }
  function openRegister() {
    regForm.value = { name: '', description: '', params: [], steps: [newStep('tool')] }
    registerVisible.value = true
    loadToolsOnce()
  }
  /** 工具下拉取既有 /tools 列表端点；失败时回落为手动输入（allow-create） */
  async function loadToolsOnce() {
    if (toolsLoaded.value || toolsLoading.value) return
    toolsLoading.value = true
    try {
      const list = await toolApi.list()
      tools.value = (list ?? []).map(t => ({ code: t.code, name: t.name }))
      toolsLoaded.value = true
    } catch {
      tools.value = []
    } finally {
      toolsLoading.value = false
    }
  }
  async function submitRegister() {
    const f = regForm.value
    if (!f.name.trim()) {
      ElMessage.warning('请填写插件名称')
      return
    }
    const seen = new Set<string>()
    for (const p of f.params) {
      const k = p.key.trim()
      if (!k) {
        ElMessage.warning('参数 key 不能为空')
        return
      }
      if (seen.has(k)) {
        ElMessage.warning(`参数 key 重复：${k}`)
        return
      }
      seen.add(k)
    }
    if (!f.steps.length) {
      ElMessage.warning('请至少添加一个步骤')
      return
    }
    const steps: WorkflowStepDef[] = []
    for (let i = 0; i < f.steps.length; i++) {
      const s = f.steps[i]
      if (s.kind === 'tool') {
        const tool = s.tool.trim()
        if (!tool) {
          ElMessage.warning(`步骤 ${i + 1}：请选择或输入工具`)
          return
        }
        let args: Record<string, unknown> = {}
        const txt = s.argsText.trim()
        if (txt) {
          try {
            const parsed: unknown = JSON.parse(txt)
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
              throw new Error('args must be a JSON object')
            }
            args = parsed as Record<string, unknown>
          } catch {
            ElMessage.warning(`步骤 ${i + 1}：args 必须是 JSON 对象`)
            return
          }
        }
        const saveAs = s.save_as.trim()
        steps.push(saveAs ? { kind: 'tool', tool, args, save_as: saveAs } : { kind: 'tool', tool, args })
      } else {
        if (!s.prompt.trim()) {
          ElMessage.warning(`步骤 ${i + 1}：请填写提示词`)
          return
        }
        steps.push({ kind: 'llm', prompt: s.prompt })
      }
    }
    const manifest: WorkflowManifest = {
      name: f.name.trim(),
      description: f.description.trim() || undefined,
      params: f.params.map(p => ({
        key: p.key.trim(),
        label: p.label.trim() || p.key.trim(),
        required: !!p.required,
      })),
      steps,
    }
    registering.value = true
    try {
      await evolutionApi.registerPlugin(manifest)
      ElMessage.success('已提交，等待审核')
      registerVisible.value = false
      await loadPlugins()
    } catch {
      /* toast 已统一 */
    } finally {
      registering.value = false
    }
  }

  // ----- 运行插件：参数小对话框 + 结果抽屉 -----
  const runVisible = ref(false)
  const running = ref(false)
  const runRow = ref<any>(null)
  const runParamDefs = ref<WorkflowParamDef[]>([])
  const runParams = ref<Record<string, string>>({})
  const runResultVisible = ref(false)
  const runResult = ref<PluginRunResult | null>(null)
  const runDialogTitle = computed(() =>
    runRow.value?.name ? `运行插件：${runRow.value.name}` : '运行插件',
  )
  const runFinalText = computed(() => {
    const f = runResult.value?.final
    return typeof f === 'string' && f ? f : '（无最终文本）'
  })
  const runOutputsText = computed(() => {
    const o = runResult.value?.outputs
    if (o === undefined || o === null) return '（无输出）'
    try {
      return JSON.stringify(o, null, 2)
    } catch {
      return String(o)
    }
  })
  function openRun(row: any) {
    runRow.value = row ?? null
    // 列表行一般不带 manifest：先用行内字段快路径，再拉详情接口补全参数定义
    fillRunParams(row?.manifest_json ?? row?.manifest)
    runVisible.value = true
    void fetchRunParams(row?.plugin_id)
  }
  function fillRunParams(manifest: unknown) {
    const m = (manifest ?? {}) as Record<string, unknown>
    const defs = Array.isArray(m.params) ? m.params : []
    runParamDefs.value = defs.filter((d: any) => d && typeof d.key === 'string' && d.key)
    const init: Record<string, string> = {}
    runParamDefs.value.forEach(d => {
      init[d.key] = runParams.value[d.key] ?? ''
    })
    runParams.value = init
  }
  /** 参数声明在 manifest 里，需插件详情端点取；失败时保持「直接运行」兜底 */
  async function fetchRunParams(pluginId?: string) {
    if (!pluginId) return
    try {
      const detail = await evolutionApi.getPlugin(pluginId)
      if (runRow.value?.plugin_id !== pluginId) return
      fillRunParams(detail?.manifest ?? detail?.manifest_json)
    } catch {
      /* 详情失败不阻塞运行：空参数直接跑，由后端校验 */
    }
  }
  async function submitRun() {
    const row = runRow.value
    if (!row) return
    const missing = runParamDefs.value.filter(
      d => d.required && !String(runParams.value[d.key] ?? '').trim(),
    )
    if (missing.length) {
      ElMessage.warning(`请填写必填参数：${missing.map(d => d.label || d.key).join('、')}`)
      return
    }
    const params: Record<string, unknown> = {}
    Object.entries(runParams.value).forEach(([k, v]) => {
      const s = String(v ?? '').trim()
      if (s) params[k] = s
    })
    running.value = true
    try {
      runResult.value = await evolutionApi.runPlugin(row.plugin_id, params)
      runVisible.value = false
      runResultVisible.value = true
    } catch {
      /* toast 已统一 */
    } finally {
      running.value = false
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

  /* v1.3 进化域：分区工具条 + 插件编辑器（全部 --ds-* token） */
  .section-toolbar {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-bottom: 12px;
  }
  .ai-badge {
    margin-left: 6px;
  }
  .row-list {
    width: 100%;
  }
  .row-line {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }
  .row-line .w-key {
    width: 150px;
  }
  .row-line .w-label {
    flex: 1;
  }
  .step-card {
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
    padding: 12px;
    margin-bottom: 10px;
  }
  .step-head {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }
  .step-head .step-title {
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .step-head .w-kind {
    width: 120px;
  }
  .step-field {
    width: 100%;
    margin-bottom: 8px;
  }
  .step-field-sm {
    width: 260px;
    margin-top: 8px;
  }
  .step-add {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .hint {
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-3);
    margin-bottom: 8px;
  }
  .run-body .run-section-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-2);
    margin: 14px 0 6px;
  }
  .run-body .run-section-title:first-child {
    margin-top: 0;
  }
  .run-body pre {
    white-space: pre-wrap;
    word-break: break-all;
    margin: 0;
    padding: 12px;
    max-height: 300px;
    overflow: auto;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    color: var(--ds-text-1);
    font-size: 13px;
    line-height: 1.6;
  }

  /* v3.21：技能列表（名称大/编码小、详情、自动升级） */
  .hidden-file {
    display: none;
  }
  .skill-cell {
    cursor: pointer;
    padding: 2px 0;
  }
  .skill-cell:focus-visible {
    outline: 2px solid var(--ds-accent);
    outline-offset: 2px;
    border-radius: var(--ds-radius-control);
  }
  .skill-cell .skill-name {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 14px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .skill-cell .skill-code {
    margin-top: 2px;
    font-size: 12px;
    color: var(--ds-text-3);
    font-family: var(--ds-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  }
  .skill-sub {
    margin-top: 2px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .auto-cell {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .skill-detail .detail-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
  }
  .skill-detail .detail-name {
    font-size: 17px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .skill-detail h4 {
    margin: 16px 0 6px;
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-2);
  }
  .skill-detail .detail-text {
    margin: 0;
    font-size: 13px;
    line-height: 1.7;
    color: var(--ds-text-1);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .skill-detail .detail-pre {
    margin: 0;
    padding: 12px;
    max-height: 240px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    background: var(--ds-field);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }
  .skill-detail .detail-steps {
    margin: 0;
    padding-left: 20px;
    font-size: 13px;
    line-height: 1.7;
    color: var(--ds-text-1);
  }
  .skill-detail .detail-actions {
    display: flex;
    gap: 8px;
    margin-top: 18px;
  }
  .run-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 8px 0 8px;
  }
  .run-head .run-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-2);
  }
  .hint-inline {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
</style>

<template>
  <div
    class="chat-panel"
    :data-panel="panelId"
  >
    <div
      ref="streamRef"
      v-loading="store.messagesLoading"
      class="chat-stream"
      role="log"
      aria-live="polite"
      :aria-label="`${label}消息`"
    >
      <!-- 空态：大标题 + 建议卡（点击填入输入框） -->
      <div
        v-if="!store.messages.length && !store.sending"
        class="chat-empty"
      >
        <h3 class="empty-title">
          {{ emptyTitle }}
        </h3>
        <p
          v-if="emptySub"
          class="empty-sub"
        >
          {{ emptySub }}
        </p>
        <div
          v-if="suggestions.length"
          class="suggest-grid"
        >
          <button
            v-for="(tip, i) in suggestions"
            :key="i"
            class="suggest-card"
            :aria-label="`填入示例问题：${tip}`"
            @click="useSuggestion(tip)"
          >
            {{ tip }}
          </button>
        </div>
      </div>

      <div
        v-else
        class="stream-inner"
      >
        <div
          v-for="row in messageRows"
          :key="row.m.id"
          class="msg-row"
          :class="row.m.role"
        >
          <!-- 群聊署名头像：一位助手一条气泡，谁答的一眼可辨 -->
          <span
            v-if="showAvatar && row.signer"
            class="msg-avatar"
            aria-hidden="true"
          >{{ row.avatar }}</span>
          <div class="msg-col">
            <!-- 助手诚实徽章：优先后端文案，老数据回落本地表（tooltip=reason） -->
            <div
              v-if="row.badge || row.signer"
              class="msg-badge"
            >
              <span
                v-if="row.signer"
                class="badge-from"
              >↳ 由 {{ row.signer }} 回答</span>
              <el-tooltip
                v-if="row.badge"
                :content="row.badge.tip"
                placement="top"
              >
                <el-tag
                  size="small"
                  :type="row.badge.tone"
                >
                  {{ row.badge.label }}
                </el-tag>
              </el-tooltip>
            </div>
            <!-- 用户随消息上传的附件（文件名可点开查看要点） -->
            <div
              v-if="row.m.attachments?.length"
              class="msg-attachments"
            >
              <span
                v-for="a in row.m.attachments"
                :key="a.id"
                class="att-tag"
              >
                <el-icon>
                  <component :is="a.kind === 'image' ? Picture : Document" />
                </el-icon>
                {{ a.filename }}
              </span>
            </div>
            <!-- 历史过程步骤（可折叠展开） -->
            <ChatSteps
              v-if="row.m.role === 'assistant' && row.m.steps?.length"
              :steps="row.m.steps"
              collapsible
            />
            <!-- 纯文本 pre-wrap；方案正文是 Markdown 成稿，用无脚本安全渲染器分节显示 -->
            <div
              class="bubble"
              :class="row.m.role"
            >
              <SafeMarkdown
                v-if="isPlanMessage(row.m)"
                :content="row.m.content"
              />
              <template v-else>
                {{ row.m.content }}
              </template>
            </div>
            <!-- 引用来源（v3.7）：知识库片段与网页链接可点开溯源 -->
            <details
              v-if="row.m.role === 'assistant' && row.m.citations?.length"
              class="msg-citations"
            >
              <summary>依据来源（{{ row.m.citations.length }}）</summary>
              <ul>
                <li
                  v-for="(c, i) in row.m.citations"
                  :key="i"
                >
                  <!-- 编号与正文里的 [K1]/[W1] 对应，方案与问答都能顺着查到出处 -->
                  <span
                    v-if="c.reference"
                    class="citation-ref"
                  >{{ c.reference }}</span>
                  <a
                    v-if="safeHttpUrl(c.url)"
                    :href="safeHttpUrl(c.url)"
                    target="_blank"
                    rel="noopener noreferrer"
                  >{{ c.title || c.url }}</a>
                  <span v-else>{{ c.title || c.source || '知识库片段' }}</span>
                  <span
                    v-if="c.quote"
                    class="citation-quote"
                  >{{ c.quote }}</span>
                </li>
              </ul>
            </details>
            <!-- 结构化载荷：技能卡片 / 插件卡片 / 方案卡片 / 能力草稿 / 能力市场卡片 -->
            <SkillCard
              v-if="row.m.role === 'assistant' && row.m.payload?.type === 'skill_card'"
              :card="row.m.payload as ChatSkillCard"
              :store="store"
              :message-id="row.m.id"
            />
            <PluginCard
              v-else-if="row.m.role === 'assistant' && row.m.payload?.type === 'plugin_card'"
              :card="row.m.payload as ChatPluginCard"
              :store="store"
              :message-id="row.m.id"
            />
            <PlanCard
              v-else-if="row.m.role === 'assistant' && row.m.payload?.type === 'plan'"
              :card="row.m.payload as ChatPlanCard"
              :exporting="planExportingId === row.m.id"
              @export="exportPlan(row.m)"
              @escalate="openEscalate(row.m)"
            />
            <!-- 高风险操作待批准：给出审批单号与去批准入口，不含糊地说「已经做了」 -->
            <div
              v-else-if="row.m.role === 'assistant' && row.m.payload?.type === 'approval'"
              class="msg-approval"
              role="status"
            >
              <div class="apv-head">
                <el-icon><Lock /></el-icon>
                <span>{{
                  (row.m.payload as ChatApprovalCard).title || '高风险操作待批准'
                }}</span>
              </div>
              <p
                v-if="(row.m.payload as ChatApprovalCard).hint"
                class="apv-hint"
              >
                {{ (row.m.payload as ChatApprovalCard).hint }}
              </p>
              <ul class="apv-list">
                <li
                  v-for="item in (row.m.payload as ChatApprovalCard).items || []"
                  :key="item.approval_id"
                >
                  <span class="apv-tool">{{ item.tool_code }}</span>
                  <span class="apv-id">审批单 {{ item.approval_id.slice(0, 8) }}</span>
                </li>
              </ul>
              <button
                class="apv-go"
                type="button"
                title="前往任务中心的待审批列表处理"
                @click="goApprovals"
              >
                去批准
              </button>
            </div>
            <ChatCard
              v-else-if="row.m.role === 'assistant' && row.m.payload"
              :card="row.m.payload as ChatStandardCard"
              @published="onCardPublished(row.m.id, $event)"
              @preview="openDocPreview"
              @download="downloadDoc"
            />
            <!--
              本轮既产出了别的卡片、又拦下了高风险调用：审批项附在原卡片下面，
              而不是把方案/文档卡顶掉——两张信息用户都需要，且审批单号必须可点。
            -->
            <div
              v-if="pendingApprovals(row.m).length"
              class="msg-approval is-strip"
              role="status"
            >
              <ul class="apv-list">
                <li
                  v-for="item in pendingApprovals(row.m)"
                  :key="item.approval_id"
                >
                  <span class="apv-tool">{{ item.tool_code }}</span>
                  <span class="apv-id">审批单 {{ item.approval_id.slice(0, 8) }}</span>
                </li>
              </ul>
              <button
                class="apv-go"
                type="button"
                title="前往任务中心的待审批列表处理"
                @click="goApprovals"
              >
                去批准
              </button>
            </div>
            <!-- 一键复制：提问与回答都可复制，优先 Clipboard API，失败回退 execCommand -->
            <div
              v-if="row.m.content"
              class="msg-actions"
            >
              <button
                class="msg-copy"
                type="button"
                :aria-label="`复制${row.m.role === 'user' ? '提问' : '回答'}`"
                @click="copyMessage(row.m)"
              >
                <el-icon><CopyDocument /></el-icon>
                <span>{{ copiedId === row.m.id ? '已复制' : '复制' }}</span>
              </button>
              <!-- 回答反馈回流（v3.13）：有用/有问题进反馈队列，纠错进专家审核后合入知识库 -->
              <template v-if="row.m.role === 'assistant'">
                <button
                  class="msg-fb"
                  type="button"
                  :class="{ done: fbState[row.m.id] === 'up' || fbState[row.m.id] === 'corrected' }"
                  :disabled="fbBusy === row.m.id"
                  :aria-label="fbState[row.m.id] === 'up' ? '已反馈：这条回答有用' : '反馈这条回答有用'"
                  :aria-pressed="fbState[row.m.id] === 'up'"
                  @click="submitRating(row.m, 'up')"
                >
                  <el-icon><Goods /></el-icon>
                  <span>{{ fbState[row.m.id] === 'up' ? '已反馈' : '有用' }}</span>
                </button>
                <button
                  class="msg-fb"
                  type="button"
                  :class="{ done: fbState[row.m.id] === 'down' || fbState[row.m.id] === 'corrected' }"
                  :disabled="fbBusy === row.m.id"
                  :aria-label="'反馈这条回答有问题并可补充正确说法'"
                  @click="markWrong(row.m)"
                >
                  <el-icon><EditPen /></el-icon>
                  <span>{{
                    fbState[row.m.id] === 'corrected'
                      ? '已提交纠错'
                      : fbState[row.m.id] === 'down'
                        ? '补充正确说法'
                        : '有问题'
                  }}</span>
                </button>
                <!-- 分支：从这条回答拉出一个全新对话（原对话不受影响） -->
                <button
                  v-if="store.activeId"
                  class="msg-act"
                  type="button"
                  :disabled="forkBusy === row.m.id"
                  title="以这条回答为止另开一个分支对话，继续往别的方向问"
                  :aria-label="forkBusy === row.m.id ? '分支创建中' : '从这条回答分支'"
                  @click="onFork(row.m)"
                >
                  <el-icon>
                    <Loading
                      v-if="forkBusy === row.m.id"
                      class="is-spin"
                    />
                    <Share v-else />
                  </el-icon>
                  <span>{{ forkBusy === row.m.id ? '分支中' : '分支' }}</span>
                </button>
              </template>
              <!-- 引用：把这段原文贴进输入框，接着问它 -->
              <button
                class="msg-act"
                type="button"
                title="引用这段内容继续提问"
                :aria-label="`引用这条${row.m.role === 'user' ? '提问' : '回答'}继续提问`"
                @click="onQuote(row.m)"
              >
                <el-icon><ChatLineSquare /></el-icon>
                <span>引用</span>
              </button>
            </div>
            <!-- 纠错输入：提交后进入「专家审核」，通过后合入知识库，不再只是聊天里抱怨一句 -->
            <div
              v-if="fbOpenId === row.m.id"
              class="msg-correct"
            >
              <el-input
                v-model="fbDraft[row.m.id]"
                type="textarea"
                :rows="3"
                maxlength="2000"
                placeholder="写下正确说法（至少 10 个字）。提交后由小院专家审核，通过才进知识库。"
                :aria-label="'这条回答的纠错内容'"
              />
              <div class="correct-foot">
                <span class="correct-hint">{{ (fbDraft[row.m.id] ?? '').trim().length }} / 10 字起</span>
                <button
                  class="correct-btn"
                  type="button"
                  @click="closeCorrection"
                >
                  取消
                </button>
                <button
                  class="correct-btn primary"
                  type="button"
                  :disabled="fbBusy === row.m.id || (fbDraft[row.m.id] ?? '').trim().length < 10"
                  @click="submitCorrection(row.m)"
                >
                  提交纠错
                </button>
              </div>
            </div>
            <!-- 消息级 Token 消耗统计（每条回答及每次输入均透明可查） -->
            <div
              v-if="row.m.token_usage"
              class="msg-token-bar"
            >
              <span class="tok-main">⚡ {{ row.m.token_usage.total_tokens ?? ((row.m.token_usage.input_tokens ?? 0) + (row.m.token_usage.output_tokens ?? 0)) }} tokens</span>
              <template v-if="row.m.role === 'assistant'">
                <span class="tok-detail">（输入 {{ row.m.token_usage.input_tokens ?? 0 }} / 输出 {{ row.m.token_usage.output_tokens ?? 0 }}</span>
                <span
                  v-if="row.m.token_usage.latency_ms != null"
                  class="tok-detail"
                >· {{ row.m.token_usage.latency_ms }} ms</span>
                <span
                  v-if="row.m.token_usage.model"
                  class="tok-detail"
                >· {{ row.m.token_usage.model }}</span>
                <span class="tok-detail">）</span>
              </template>
              <template v-else>
                <span class="tok-detail">（本条输入估算）</span>
              </template>
            </div>
          </div>
        </div>

        <!-- 等待回答：实时步骤条（未收到首步时回落到三点波浪） -->
        <div
          v-if="store.sending"
          class="msg-row assistant"
        >
          <div class="msg-col">
            <div
              v-if="store.liveSteps.length"
              class="bubble assistant live-steps"
            >
              <ChatSteps
                :steps="store.liveSteps"
                live
              />
            </div>
            <div
              v-else
              class="bubble assistant typing"
            >
              <span class="dot" /><span class="dot" /><span class="dot" />
            </div>
            <!-- 群聊本轮实况：谁在答、谁没答，随 member_event 实时更新 -->
            <div
              v-if="store.isGroup"
              class="live-members"
            >
              <span
                v-for="m in memberLiveRows"
                :key="m.key"
                class="live-member"
                :class="m.status"
              >{{ m.name }} · {{ m.note }}</span>
            </div>
          </div>
        </div>

        <!-- 流中断提示（诚实告知，不假装已回答） -->
        <div
          v-if="store.streamError"
          class="stream-warn"
          role="alert"
        >
          <el-icon><Warning /></el-icon>
          {{ store.streamError }}
        </div>
      </div>
    </div>

    <footer class="chat-input">
      <!-- 待发附件：上传后先落这里，随下一条消息一起发出 -->
      <div
        v-if="store.attachments.length"
        class="att-row"
      >
        <span
          v-for="a in store.attachments"
          :key="a.id"
          class="att-chip"
          :class="{ bad: a.status === 'failed' }"
        >
          <el-icon>
            <component :is="a.kind === 'image' ? Picture : Document" />
          </el-icon>
          <span class="nm">{{ a.filename }}</span>
          <el-tooltip
            v-if="a.status === 'failed'"
            :content="a.error || '解析失败'"
            placement="top"
          >
            <span class="err">解析失败</span>
          </el-tooltip>
          <button
            v-else-if="a.kind === 'image'"
            class="vision"
            type="button"
            :disabled="visionId === a.id"
            :title="a.chars > 0 ? '已识别，点击重新识别' : '识别图片内容（会消耗模型额度）'"
            :aria-label="`识别图片 ${a.filename}`"
            @click="onVision(a)"
          >
            <el-icon>
              <Loading
                v-if="visionId === a.id"
                class="is-spin"
              />
              <View v-else />
            </el-icon>
            {{ a.chars > 0 ? '已识别' : '识别' }}
          </button>
          <button
            class="x"
            type="button"
            aria-label="移除附件"
            @click="onRemoveAttachment(a.id)"
          >
            <el-icon><Close /></el-icon>
          </button>
        </span>
      </div>
      <div
        v-if="groupable && !qaMode"
        class="group-band"
        :style="{ maxWidth }"
      >
        <ChatMemberBar
          :store="store"
          :disabled="store.sending"
          :label="label"
        />
        <ChatPolicyPicker
          v-if="store.isGroup"
          v-model="policyModel"
          :disabled="store.sending"
          :block-reason="blockedReason"
        />
      </div>

      <!-- 本轮点名提示条：比把 @ 文本硬塞进正文更好回收 -->
      <div
        v-if="mentionable && !qaMode && store.mention"
        class="mention-bar"
        :style="{ maxWidth }"
        role="status"
      >
        <span class="mb-label">本轮点名：</span>
        <span class="mb-name">
          <span aria-hidden="true">{{ store.mention.emoji || '🤖' }}</span>
          {{ mentionDisplayName(store.mention) }}
        </span>
        <button
          class="mb-x"
          type="button"
          :aria-label="`取消点名 ${mentionDisplayName(store.mention)}，下一条回到自动路由`"
          @click="store.clearMention()"
        >
          <el-icon><Close /></el-icon>
          取消
        </button>
      </div>

      <!-- 模式切换（v3.13）：通用对话自动路由助手；知识问答只依据知识库且必带引用 -->
      <div
        v-if="qaToggle"
        class="qa-bar"
        :style="{ maxWidth }"
      >
        <div
          class="qa-tabs"
          role="group"
          aria-label="对话模式"
        >
          <button
            type="button"
            class="qa-tab"
            :class="{ active: !qaMode }"
            :aria-pressed="!qaMode"
            @click="setQaMode(false)"
          >
            通用对话
          </button>
          <button
            type="button"
            class="qa-tab"
            :class="{ active: qaMode }"
            :aria-pressed="qaMode"
            @click="setQaMode(true)"
          >
            知识问答
          </button>
        </div>
        <span
          v-if="qaMode"
          class="qa-hint"
        >只依据知识库资料，回答带引用；不支持附件与 @ 点名</span>
        <label
          v-if="qaMode"
          class="qa-topk"
        >
          检索条数
          <el-input-number
            v-model="qaTopK"
            :min="1"
            :max="20"
            size="small"
            controls-position="right"
            aria-label="知识库检索条数"
          />
        </label>
      </div>

      <!--
        @ 候选浮层的锚点就是输入行本身：ElPopover 的 reference 只透传唯一子元素、
        不额外包 DOM，所以不传 mentionable 时（open 恒为 false）渲染结果与改造前一致。
      -->
      <ChatMentionLayer
        ref="mentionLayerRef"
        v-model:open="mentionOpen"
        :candidates="store.mentionCandidates"
        :keyword="mentionKeyword"
        :loading="store.mentionLoading"
        :label="label"
        @select="onMentionSelect"
      >
        <template #reference>
          <div class="input-inner">
            <!--
              输入框行左端：本对话的高风险操作权限（每次询问 / 会话内自动放行 / 完全访问）。
              知识问答只读知识库、不调用工具，所以不在该模式下出现。
            -->
            <ChatApprovalPicker
              v-if="!qaMode"
              :model-value="approvalMode"
              :can-escalate="canEscalateApproval"
              :has-session="!!store.activeId"
              :disabled="store.sending || approvalBusy"
              @update:model-value="onApprovalMode"
            />
            <!-- 输入框左侧：上传附件（照片/文档） -->
            <button
              class="attach-btn"
              type="button"
              :disabled="store.sending || store.uploading || qaMode"
              :title="qaMode ? '知识问答只依据知识库资料，不支持附件' : '上传附件（图片或文档）'"
              aria-label="上传附件"
              @click="onPickFile"
            >
              <el-icon>
                <Loading
                  v-if="store.uploading"
                  class="is-spin"
                />
                <Paperclip v-else />
              </el-icon>
            </button>
            <input
              ref="fileRef"
              class="file-input"
              type="file"
              accept="image/png,image/jpeg,image/webp,.pdf,.docx,.xlsx,.pptx,.txt,.md,.csv,.html"
              :aria-label="`为${label}选择附件`"
              @change="onFilePicked"
            >
            <!-- 语音输入：录音 → 后端转写 → 文本落入草稿（不支持时明确提示） -->
            <button
              class="attach-btn"
              type="button"
              :class="{ recording }"
              :disabled="store.sending || transcribing"
              :title="recording ? '停止录音并转写' : '语音输入'"
              :aria-label="recording ? '停止录音并转写' : '语音输入'"
              :aria-pressed="recording"
              @click="onToggleRecord"
            >
              <el-icon>
                <Loading
                  v-if="transcribing"
                  class="is-spin"
                />
                <VideoPause v-else-if="recording" />
                <Microphone v-else />
              </el-icon>
            </button>
            <span
              v-if="recording"
              class="rec-hint"
              role="status"
            >录音中 {{ recordSeconds }}s，再点一次结束</span>
            <el-input
              ref="inputRef"
              v-model="draft"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 6 }"
              :maxlength="2000"
              resize="none"
              :placeholder="placeholderText"
              :aria-label="`${label}输入`"
              :role="mentionable ? 'combobox' : undefined"
              :aria-expanded="mentionable ? mentionOpen : undefined"
              :aria-controls="mentionable ? mentionListboxId : undefined"
              :aria-activedescendant="mentionable ? mentionActiveDescendant : undefined"
              @keydown="onKeydown"
              @keyup="onKeyup"
            />
            <!-- 发送中仅样式变为「停止」并禁用（v1 不支持中断） -->
            <button
              class="send-btn"
              :class="{ stop: store.sending }"
              :disabled="store.sending || !draft.trim() || !!blockedReason"
              :title="blockedReason || (store.sending ? '回答生成中' : '发送')"
              :aria-label="blockedReason || (store.sending ? '回答生成中' : '发送')"
              @click="onSend"
            >
              <el-icon>
                <VideoPause v-if="store.sending" />
                <Promotion v-else />
              </el-icon>
            </button>
          </div>
        </template>
      </ChatMentionLayer>
      <p
        v-if="blockedReason"
        class="send-block"
        :style="{ maxWidth }"
        role="alert"
      >
        {{ blockedReason }}
      </p>
    </footer>

    <!-- 生成文档预览：后端渲染自包含 HTML，装进无脚本沙箱 iframe -->
    <el-dialog
      v-model="previewOpen"
      append-to-body
      :title="previewCard?.filename || '文档预览'"
      width="min(920px, 92vw)"
      class="doc-preview-dialog"
      destroy-on-close
      @closed="previewHtml = ''"
    >
      <div
        v-loading="previewLoading"
        class="doc-preview-body"
      >
        <iframe
          v-if="previewHtml"
          :srcdoc="previewHtml"
          class="doc-preview-frame"
          sandbox=""
          title="文档预览"
        />
        <el-empty
          v-else-if="!previewLoading"
          description="预览暂不可用，请下载后查看"
        />
      </div>
      <template #footer>
        <el-button @click="previewOpen = false">
          关闭
        </el-button>
        <el-button
          type="primary"
          :loading="downloadingId === previewCard?.attachment_id"
          @click="previewCard && downloadDoc(previewCard)"
        >
          下载
        </el-button>
      </template>
    </el-dialog>
    <!-- 转会诊（v3.14）：同一问题交给班组接力，多成员+检索融合的结果在班组页查看 -->
    <el-dialog
      v-model="escalateOpen"
      append-to-body
      title="转班组会诊"
      width="min(520px, 92vw)"
    >
      <p class="escalate-question">
        {{ escalateQuestion }}
      </p>
      <el-select
        v-model="escalateTeamId"
        placeholder="选择班组"
      >
        <el-option
          v-for="t in escalateTeams"
          :key="t.id"
          :label="t.name"
          :value="t.id"
        />
      </el-select>
      <p
        v-if="!escalateTeams.length"
        class="escalate-empty"
      >
        本小院还没有启用的班组，可先到「班组」页创建一个再转。
      </p>
      <template #footer>
        <el-button @click="escalateOpen = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :disabled="!escalateTeamId"
          :loading="escalateBusy"
          @click="confirmEscalate"
        >
          提交会诊
        </el-button>
      </template>
    </el-dialog>

    <QuotaRequestDialog
      v-model="quotaDialogVisible"
      :block="store.quotaBlock"
      @submitted="store.clearQuotaBlock()"
    />
  </div>
</template>

<script setup lang="ts">
  import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue'
  import { useRouter } from 'vue-router'
  import { ElInput, ElMessage, ElMessageBox } from 'element-plus'
  import {
    ChatLineSquare,
    Close,
    CopyDocument,
    Document,
    EditPen,
    Goods,
    Loading,
    Lock,
    Microphone,
    Paperclip,
    Picture,
    Promotion,
    Share,
    VideoPause,
    View,
    Warning,
  } from '@element-plus/icons-vue'
  import ChatApprovalPicker from '@/components/chat/ChatApprovalPicker.vue'
  import ChatCard from '@/components/chat/ChatCard.vue'
  import ChatMemberBar from '@/components/chat/ChatMemberBar.vue'
  import ChatMentionLayer from '@/components/chat/ChatMentionLayer.vue'
  import ChatPolicyPicker from '@/components/chat/ChatPolicyPicker.vue'
  import QuotaRequestDialog from '@/components/quota/QuotaRequestDialog.vue'
  import type { QuotaBlockInfo } from '@/api/quota'
  import ChatSteps from '@/components/chat/ChatSteps.vue'
  import { safeHttpUrl } from '@/components/teams/presentation'
  import PluginCard from '@/components/chat/PluginCard.vue'
  import PlanCard from '@/components/chat/PlanCard.vue'
  import SafeMarkdown from '@/components/teams/SafeMarkdown.vue'
  import SkillCard from '@/components/chat/SkillCard.vue'
  import {
    APPROVAL_MODE_META,
    chatApi,
    mentionDisplayName,
    normalizeRoutedTone,
    planDocModel,
  } from '@/api/chat'
  import { feedbackApi, teamApi } from '@/api/modules'
  import { useAuthStore } from '@/stores/auth'
  import type {
    ChatApprovalCard,
    ChatApprovalItem,
    ChatApprovalMode,
    ChatAttachment,
    ChatDocumentCard,
    ChatGroupPolicy,
    ChatMessage,
    ChatPlanCard,
    ChatPluginCard,
    ChatRoutedBy,
    ChatSkillCard,
    ChatStandardCard,
    MentionCandidate,
    RoutedTone,
  } from '@/api/chat'
  import type { TeamDefinition } from '@/api/modules'
  import type { MemberRoundRow } from '@/stores/chat'
  import type { ChatStoreInstance } from '@/stores/chat'
  import { canRecord, WavRecorder } from '@/utils/wavRecorder'

  /** 路由来源徽章（仅作老数据兜底：后端下发 routed_label 后不再走这里） */
  const ROUTED_META: Record<
    ChatRoutedBy,
    { label: string; type: 'success' | 'primary' | 'warning' | 'info' | 'danger'; tip: string }
  > = {
    rules: { label: '规则命中', type: 'success', tip: '按关键词规则直接命中该对象' },
    llm: { label: '智能分类', type: 'primary', tip: '由模型分类挑选该对象' },
    fallback: { label: '兜底', type: 'warning', tip: '未命中规则与分类，按近期频率兜底' },
    manual: { label: '手动指定', type: 'info', tip: '用户手动指定的执行对象' },
    local: { label: '对话直办', type: 'info', tip: '由对话直接处理，未走助手路由' },
    qa: { label: '知识问答', type: 'primary', tip: '只依据知识库资料回答，附引用来源' },
    plan: {
      label: '多源方案',
      type: 'success',
      tip: '合并知识库、联网公开资料、技能与模型知识合成完整方案',
    },
    error: { label: '执行失败', type: 'danger', tip: '执行遇到问题，可换个说法再问' },
  }

  /** 后端新增路由来源时兜底，避免未知取值让整条消息渲染中断 */
  const ROUTED_FALLBACK = {
    label: '已处理',
    type: 'info' as const,
    tip: '由对话直接处理',
  }

  /**
   * 对话面板（展示层）：消息流 + 实时步骤 + 输入区 + 点名/群聊外壳。
   *
   * 状态与动作全部来自 store prop（父级用 useChatStore(scope) 取实例后传入），
   * 组件自身不持有任何对话状态，因此同一页面可以并存多个互不干扰的面板实例。
   * mentionable / groupable 是 v2.0 新增的可选开关，默认关闭以保持既有调用方行为不变。
   */
  const props = withDefaults(
    defineProps<{
      /** 该面板绑定的对话实例状态；不同 scope 的实例互不可见 */
      store: ChatStoreInstance
      /** 实例标识，落在 data-panel 上（弹出面板/群聊各自取名，也供 E2E 定位） */
      panelId?: string
      /** 无障碍名称：区域与输入控件的 aria-label 前缀 */
      label?: string
      /** 空态建议卡文案，传空数组则不显示建议卡 */
      suggestions?: string[]
      emptyTitle?: string
      emptySub?: string
      placeholder?: string
      /** 消息流/输入区最大宽度：页面用 820px，弹卡可收窄 */
      maxWidth?: string
      /** 开启 @ 点名（输入 @ 弹候选浮层） */
      mentionable?: boolean
      /** 开启多助手群聊（成员条 + 本轮策略） */
      groupable?: boolean
      /** 显示「通用对话 / 知识问答」模式切换（v3.13） */
      qaToggle?: boolean
      /** 当前是否知识问答模式（v-model:qa-mode） */
      qaMode?: boolean
    }>(),
    {
      panelId: 'main',
      label: '对话',
      // defineProps 会被提到 setup 之外，默认值只能就地写字面量（不能引用 DEFAULT_SUGGESTIONS）
      suggestions: () => [
        '帮我诊断水稻叶片发黄，可能是什么问题？',
        '整理一下本周设备上报的异常数据',
        '写一份小麦赤霉病的防治方案',
        '创建一个「农机维修问答」助手，以后专门用它',
      ],
      emptyTitle: '直接说要办的事',
      emptySub: '助手与班组会自动接住你的问题，回答标注实际来源，不确定可转专家。',
      placeholder: '直接说要办的事，Enter 发送，Shift+Enter 换行；可先上传照片或文档',
      maxWidth: '820px',
      mentionable: false,
      groupable: false,
      qaToggle: false,
      qaMode: false,
    },
  )

  const emit = defineEmits<{
    /** 一次发送结束（ok=false 表示服务端未收到，草稿已保留可重发） */
    (e: 'sent', ok: boolean): void
    /** 卡片内发布动作成功：父级可据此刷新外部列表 */
    (e: 'card-published', messageId: string, status: string): void
    /** 切换通用对话 / 知识问答模式 */
    (e: 'update:qaMode', value: boolean): void
  }>()

  const draft = ref('')
  /** 额度用尽时弹「申请提额」：store 收到 QUOTA_EXCEEDED(requestable) 就会置位 */
  const quotaDialogVisible = ref(false)
  watch(
    () => props.store.quotaBlock,
    (value: QuotaBlockInfo | null) => {
      if (value) quotaDialogVisible.value = true
    },
  )
  /** 角色用于决定「自动放行 / 完全访问」是否可选；最终裁决在服务端 */
  const auth = useAuthStore()
  /** 待批准卡片跳任务中心审批列表 */
  const router = useRouter()
  /** 知识问答模式的检索条数（1–20，默认 5；只在问答模式生效） */
  const qaTopK = ref(5)
  /** 刚复制过的消息 id（按钮短暂显示「已复制」） */
  const copiedId = ref('')
  /**
   * 回答反馈（v3.13）：按消息 id 记录本次会话内的已反馈状态。
   * 服务端反馈队列不回填到消息上，所以刷新后不回显，避免谎称已反馈。
   */
  const fbState = ref<Record<string, 'up' | 'down' | 'corrected'>>({})
  const fbDraft = ref<Record<string, string>>({})
  const fbOpenId = ref('')
  const fbBusy = ref('')
  /** 分支进行中的消息 id（同一时刻只让点一个，避免连点建出多个分支会话） */
  const forkBusy = ref('')
  /** 授权模式提交中：请求在途时禁用选择器，防止连续点成与显示不一致的状态 */
  const approvalBusy = ref(false)
  const streamRef = ref<HTMLElement | null>(null)
  const inputRef = ref<InstanceType<typeof ElInput> | null>(null)
  const fileRef = ref<HTMLInputElement | null>(null)
  const mentionLayerRef = ref<InstanceType<typeof ChatMentionLayer> | null>(null)

  /** @ 浮层状态：关键词是 @ 之后已输入的片段，prefix/suffix 用于选中后把 @ 片段收回 */
  const mentionOpen = ref(false)
  const mentionKeyword = ref('')
  const mentionPrefix = ref('')
  const mentionSuffix = ref('')

  /** 徽章：优先后端 routed_label/routed_tone，老数据回落本地表，未知值一律 FALLBACK */
  interface RoutedBadge {
    label: string
    tone: RoutedTone
    tip: string
  }

  function routedBadge(m: ChatMessage): RoutedBadge {
    const backendLabel = m.routed_label?.trim()
    if (backendLabel) {
      return {
        label: backendLabel,
        tone: normalizeRoutedTone(m.routed_tone),
        tip: m.reason?.trim() || backendLabel,
      }
    }
    const meta = m.routed_by ? ROUTED_META[m.routed_by] : undefined
    return {
      label: meta?.label ?? ROUTED_FALLBACK.label,
      tone: meta?.type ?? ROUTED_FALLBACK.type,
      tip: m.reason?.trim() || meta?.tip || ROUTED_FALLBACK.tip,
    }
  }

  /** 助手消息的署名：用消息自带的助手名，不从前端的成员表反推 */
  function signerOf(m: ChatMessage): string {
    return m.role === 'assistant' ? m.choice?.name?.trim() || '' : ''
  }

  /** 头像位：能对上候选就用自己的 emoji，否则取助手名首字 */
  function avatarOf(m: ChatMessage, signer: string): string {
    const hit = props.store.mentionCandidates.find(
      c => (m.choice?.code && c.code === m.choice.code) || mentionDisplayName(c) === signer,
    )
    return hit?.emoji || signer.slice(0, 1) || 'A'
  }

  /** 消息行：徽章/署名一次算好，模板里不做重复查找 */
  const messageRows = computed(() =>
    props.store.messages.map(m => {
      const signer = signerOf(m)
      const hasBadge = m.role === 'assistant' && !!(m.routed_by || m.routed_label)
      return {
        m,
        signer,
        avatar: avatarOf(m, signer),
        badge: hasBadge ? routedBadge(m) : null,
      }
    }),
  )

  const showAvatar = computed(() => props.groupable)
  const blockedReason = computed(() => props.store.sendBlockReason)
  const placeholderText = computed(() =>
    props.qaMode
      ? '向知识库提问：只依据已上传资料回答，必附引用'
      : props.placeholder,
  )

  function setQaMode(value: boolean) {
    if (props.qaMode === value) return
    if (props.store.sending) return
    mentionOpen.value = false
    emit('update:qaMode', value)
  }
  const memberLiveRows = computed<MemberRoundRow[]>(() => props.store.memberRounds)
  const mentionListboxId = computed(() => mentionLayerRef.value?.listboxId)
  const mentionActiveDescendant = computed(() => mentionLayerRef.value?.activeOptionId ?? undefined)

  /** 策略选择器直接代理到 store（store 是群聊状态的唯一事实源） */
  const policyModel = computed<ChatGroupPolicy>({
    get: () => props.store.groupPolicy,
    set: value => props.store.setGroupPolicy(value),
  })

  function scrollToBottom() {
    nextTick(() => {
      const el = streamRef.value
      if (el) el.scrollTop = el.scrollHeight
    })
  }

  function useSuggestion(tip: string) {
    draft.value = tip
    inputRef.value?.focus()
  }

  /** 一键复制消息正文：Clipboard API 不可用（非安全上下文/桌面壳）时回退 execCommand */
  async function copyMessage(m: ChatMessage): Promise<void> {
    const text = m.content ?? ''
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    copiedId.value = m.id
    ElMessage.success('已复制')
    window.setTimeout(() => {
      if (copiedId.value === m.id) copiedId.value = ''
    }, 1500)
  }

  /**
   * 引用这段原文进输入框：带来源标记，用户接着写自己的问题。
   * 按行加 `> ` 前缀——正文按纯文本入库，Markdown 渲染时正是标准引用块。
   * 输入框 maxlength=2000，超长时保住引用块、截掉尾部草稿，不做静默丢弃。
   */
  const QUOTE_CAP = 600
  const DRAFT_CAP = 2000

  function onQuote(m: ChatMessage) {
    const raw = (m.content ?? '').trim()
    if (!raw) return
    const clipped = raw.length > QUOTE_CAP ? `${raw.slice(0, QUOTE_CAP)}…` : raw
    const body = clipped
      .split('\n')
      .map(line => (line ? `> ${line}` : '>'))
      .join('\n')
    const who =
      m.role === 'user'
        ? '我自己的上一句'
        : `这条回答${m.routed_label ? `（${m.routed_label}）` : ''}`
    const block = `引用${who}：\n${body}\n\n`
    const tail = draft.value.trim()
    const merged = tail ? `${tail}\n\n${block}` : block
    draft.value = merged.length > DRAFT_CAP ? merged.slice(0, DRAFT_CAP) : merged
    void nextTick(() => {
      const el = inputRef.value?.ref as HTMLTextAreaElement | undefined
      el?.focus()
      const end = el?.value.length ?? 0
      el?.setSelectionRange(end, end)
    })
  }

  /** 分支：把「到这条回答为止」的历史复制成一个全新会话并立刻切过去 */
  async function onFork(m: ChatMessage) {
    if (forkBusy.value) return
    forkBusy.value = m.id
    try {
      const branch = await props.store.forkFromMessage(m.id)
      if (branch) {
        ElMessage.success(`已分支为「${branch.title || '新对话'}」，原对话不受影响`)
        void nextTick(() => scrollToBottom())
      }
    } catch {
      ElMessage.error('分支创建失败，请稍后重试')
    } finally {
      forkBusy.value = ''
    }
  }

  /** 本对话当前生效的授权模式：只认服务端回包，被降级时显示的也是降级后的值 */
  const approvalMode = computed<ChatApprovalMode>(
    () => props.store.activeSession?.approval_mode ?? 'ask',
  )

  /** 与后端 APPROVAL_ESCALATION_ROLES 同口径：能批准工具的人才有权免批准 */
  const canEscalateApproval = computed(() => auth.isYardAdmin() || auth.roles.includes('expert'))

  async function onApprovalMode(mode: ChatApprovalMode) {
    if (approvalBusy.value) return
    approvalBusy.value = true
    try {
      const next = await props.store.setApprovalMode(mode)
      const effective = (next?.approval_mode ?? mode) as ChatApprovalMode
      if (effective !== mode) {
        ElMessage.warning('当前角色开不了这一档，已回到「每次询问」')
      } else {
        ElMessage.success(`本对话的操作权限已设为「${APPROVAL_MODE_META[effective].label}」`)
      }
    } catch {
      ElMessage.error('权限设置失败，请稍后重试')
    } finally {
      approvalBusy.value = false
    }
  }

  /**
   * 本轮被风险闸拦下的调用（附在别的卡片上的那份）。
   *
   * 单独成卡的审批（payload.type=approval）不走这里，避免同一条审批渲染两遍。
   */
  function pendingApprovals(m: ChatMessage): ChatApprovalItem[] {
    if (m.role !== 'assistant') return []
    const payload = m.payload as { type?: string; approvals?: ChatApprovalItem[] } | null
    if (!payload || payload.type === 'approval') return []
    return payload.approvals ?? []
  }

  /** 审批单在任务中心的「待审批的高风险操作」里处理 */
  function goApprovals() {
    router.push('/tasks')
  }

  /**
   * 反馈正文带上下文：队列里只有反馈本身，专家需要知道是哪一问的回答、来源是谁。
   * 首行会被后端截成候选标题，所以首行放用户原问题，审核列表一眼可对号。
   */
  function feedbackContext(m: ChatMessage): string {
    const list = props.store.messages
    const idx = list.findIndex(x => x.id === m.id)
    const question = [...list.slice(0, idx < 0 ? 0 : idx)].reverse().find(x => x.role === 'user')
    return [
      question?.content?.trim() || '对话回答反馈',
      `会话 ${props.store.activeId || '未保存'}${m.routed_label ? ` · 来源 ${m.routed_label}` : ''}`,
      `原回答摘要：${(m.content ?? '').slice(0, 300)}`,
    ].join('\n')
  }

  /** 点赞/点踩：先落评分，点踩顺带打开纠错框，愿意写就写、不写也算有效反馈 */
  async function submitRating(m: ChatMessage, rating: 'up' | 'down'): Promise<void> {
    if (fbBusy.value === m.id || fbState.value[m.id] === rating) return
    fbBusy.value = m.id
    try {
      await feedbackApi.submit({ rating, feedback_text: feedbackContext(m) })
      fbState.value = { ...fbState.value, [m.id]: rating }
      if (rating === 'down') openCorrection(m)
      else ElMessage.success('已记录，感谢反馈')
    } catch {
      ElMessage.warning('反馈提交失败，请稍后重试')
    } finally {
      fbBusy.value = ''
    }
  }

  /** 「有问题」：还没评分时先补一条 down，再进入纠错输入 */
  async function markWrong(m: ChatMessage): Promise<void> {
    if (fbState.value[m.id] !== 'down' && fbState.value[m.id] !== 'corrected') {
      await submitRating(m, 'down')
      return
    }
    openCorrection(m)
  }

  function openCorrection(m: ChatMessage): void {
    if (!(m.id in fbDraft.value)) fbDraft.value = { ...fbDraft.value, [m.id]: '' }
    fbOpenId.value = m.id
  }

  function closeCorrection(): void {
    fbOpenId.value = ''
  }

  /** 纠错提交后自动成为知识候选，走「专家审核」才能进知识库（后端已保证不直写） */
  async function submitCorrection(m: ChatMessage): Promise<void> {
    const text = (fbDraft.value[m.id] ?? '').trim()
    if (text.length < 10) {
      ElMessage.warning('纠错内容至少 10 个字，便于专家判断')
      return
    }
    fbBusy.value = m.id
    try {
      await feedbackApi.submit({
        rating: fbState.value[m.id] === 'up' ? undefined : 'down',
        corrected_answer: text,
        feedback_text: feedbackContext(m),
      })
      fbState.value = { ...fbState.value, [m.id]: 'corrected' }
      closeCorrection()
      ElMessage.success('纠错已提交，已生成待审核候选')
    } catch {
      ElMessage.warning('纠错提交失败，请稍后重试')
    } finally {
      fbBusy.value = ''
    }
  }

  function onPickFile() {
    fileRef.value?.click()
  }

  async function onFilePicked(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = '' // 允许连续选同一个文件
    if (!file) return
    const ok = await props.store.uploadAttachment(file)
    if (ok) scrollToBottom()
  }

  async function onRemoveAttachment(id: string) {
    await props.store.removeAttachment(id)
  }

  /** ---- 图片识别（v3.11，手动触发，明确消耗模型额度） ---- */
  const visionId = ref('')

  async function onVision(a: ChatAttachment) {
    if (visionId.value) return
    visionId.value = a.id
    try {
      await props.store.recognizeAttachment(a.id)
      ElMessage.success('图片已识别，将随下一条消息进入上下文')
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error
          ?.message || '图片识别失败，请稍后重试'
      ElMessage.warning(msg)
    } finally {
      visionId.value = ''
    }
  }

  /** ---- 语音输入（v3.30）：前端编 16k WAV → 后端本机/在线转写 → 文本填入草稿 ---- */
  const recording = ref(false)
  const transcribing = ref(false)
  const recordSeconds = ref(0)
  let recorder: WavRecorder | null = null
  let recordTimer: ReturnType<typeof setInterval> | undefined

  function stopRecordTimer() {
    if (recordTimer) clearInterval(recordTimer)
    recordTimer = undefined
  }

  async function onToggleRecord() {
    if (recording.value) {
      finishRecord()
      return
    }
    if (!canRecord()) {
      ElMessage.warning('当前环境不支持录音，请改用文字输入')
      return
    }
    const instance = new WavRecorder()
    try {
      await instance.start(finishRecord)
    } catch (err) {
      instance.abort()
      ElMessage.warning(
        (err as Error)?.message === 'UNSUPPORTED'
          ? '当前环境不支持录音，请改用文字输入'
          : '无法访问麦克风，请在系统或浏览器设置中允许后重试',
      )
      return
    }
    recorder = instance
    recording.value = true
    recordSeconds.value = 0
    stopRecordTimer()
    recordTimer = setInterval(() => {
      recordSeconds.value = Math.floor((recorder?.durationMs ?? 0) / 1000)
    }, 500)
  }

  function finishRecord() {
    if (!recorder) return
    const blob = recorder.stop()
    recorder = null
    recording.value = false
    recordSeconds.value = 0
    stopRecordTimer()
    if (!blob.size) return
    void transcribeBlob(new File([blob], `voice-${Date.now()}.wav`, { type: 'audio/wav' }))
  }

  /** 转写不可用时的统一引导：说明原因 + 一键跳到「语音转写」设置 */
  async function guideToAsrSettings(message: string) {
    try {
      await ElMessageBox.confirm(message, '语音转写尚未就绪', {
        confirmButtonText: '去设置',
        cancelButtonText: '改用文字',
        type: 'warning',
      })
      void router.push({ path: '/settings', query: { tab: 'asr' } })
    } catch {
      /* 用户选择继续用文字输入 */
    }
  }

  async function transcribeBlob(file: File) {
    transcribing.value = true
    try {
      const res = await chatApi.transcribeAudio(file)
      draft.value = draft.value.trim() ? `${draft.value.trim()} ${res.text}` : res.text
      await nextTick()
      inputRef.value?.focus()
    } catch (err: unknown) {
      const error = (
        err as { response?: { data?: { error?: { code?: string; message?: string } } } }
      )?.response?.data?.error
      const code = error?.code ?? ''
      const message = error?.message || '语音转写失败，请稍后重试或改用文字输入'
      if (
        code === 'ASR_DISABLED' ||
        code === 'ASR_NOT_CONFIGURED' ||
        code === 'ASR_LOCAL_NOT_READY' ||
        code === 'ASR_UPSTREAM_ERROR'
      ) {
        await guideToAsrSettings(message)
      } else {
        ElMessage.warning(message)
      }
    } finally {
      transcribing.value = false
    }
  }

  onBeforeUnmount(() => {
    recorder?.abort()
    recorder = null
    recording.value = false
    stopRecordTimer()
  })

  /** ---- 生成文档：预览（沙箱 iframe + srcdoc）与下载（带登录态的 blob） ---- */
  const previewOpen = ref(false)
  const previewCard = ref<ChatDocumentCard | null>(null)
  const previewHtml = ref('')
  const previewLoading = ref(false)
  const downloadingId = ref('')

  async function openDocPreview(card: ChatDocumentCard) {
    previewCard.value = card
    previewOpen.value = true
    previewLoading.value = true
    previewHtml.value = ''
    try {
      previewHtml.value = await chatApi.previewDocument(card.session_id, card.attachment_id)
    } catch {
      previewHtml.value = ''
    } finally {
      previewLoading.value = false
    }
  }

  async function downloadDoc(card: ChatDocumentCard) {
    if (downloadingId.value) return
    downloadingId.value = card.attachment_id
    try {
      const blob = await chatApi.downloadDocument(card.session_id, card.attachment_id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = card.filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      ElMessage.error('下载失败，请稍后重试')
    } finally {
      downloadingId.value = ''
    }
  }

  /** ---- 方案（v3.14）：正文按 Markdown 渲染，卡片负责导出与会诊 ---- */
  function isPlanMessage(m: ChatMessage): boolean {
    return m.role === 'assistant' && m.payload?.type === 'plan'
  }

  /** 方案对应的原始需求：往上找最近一条用户消息（转会诊要带着它跑班组） */
  function questionOf(m: ChatMessage): string {
    const rows = props.store.messages
    const index = rows.findIndex(row => row.id === m.id)
    for (let i = index - 1; i >= 0; i -= 1) {
      if (rows[i].role === 'user' && rows[i].content.trim()) return rows[i].content.trim()
    }
    return (m.payload?.type === 'plan' && m.payload.title) || ''
  }

  const planExportingId = ref('')

  /** 导出：分节正文 → DocModel → 既有文档端点（docx），拿到卡片后直接下载 */
  async function exportPlan(m: ChatMessage) {
    const card = m.payload
    const sessionId = props.store.activeId
    if (card?.type !== 'plan') return
    if (!sessionId) {
      ElMessage.warning('会话还没保存，先发一条消息再导出')
      return
    }
    if (planExportingId.value) return
    planExportingId.value = m.id
    try {
      const doc = await chatApi.createDocument(
        sessionId,
        'docx',
        planDocModel(card, questionOf(m) || '方案', m.citations || []),
      )
      await downloadDoc(doc)
    } catch {
      ElMessage.error('导出失败，请稍后重试')
    } finally {
      planExportingId.value = ''
    }
  }

  const escalateOpen = ref(false)
  const escalateBusy = ref(false)
  const escalateQuestion = ref('')
  const escalateTeamId = ref('')
  const escalateTeams = ref<TeamDefinition[]>([])

  async function openEscalate(m: ChatMessage) {
    escalateQuestion.value = questionOf(m) || m.content.slice(0, 500)
    escalateTeamId.value = ''
    escalateOpen.value = true
    if (escalateTeams.value.length) return
    try {
      escalateTeams.value = (await teamApi.list()).filter(t => t.enabled)
    } catch {
      escalateTeams.value = []
    }
  }

  /** 转会诊：同一问题交给班组接力（多成员+检索融合），结果在班组页查看 */
  async function confirmEscalate() {
    const team = escalateTeams.value.find(t => t.id === escalateTeamId.value)
    if (!team || escalateBusy.value) return
    escalateBusy.value = true
    try {
      const run = await teamApi.run(team.id, escalateQuestion.value, 'md')
      escalateOpen.value = false
      ElMessage.success(`已提交「${team.name}」会诊，可在班组页查看运行 ${run.id.slice(0, 8)}`)
    } catch {
      ElMessage.error('提交会诊失败，请稍后重试')
    } finally {
      escalateBusy.value = false
    }
  }

  /** 卡片发布成功：把新状态回写到消息载荷，刷新后仍然一致 */
  function onCardPublished(messageId: string, status: string) {    const msg = props.store.messages.find(m => m.id === messageId)
    if (msg?.payload && msg.payload.type === 'capability') {
      msg.payload.status = status
    }
    emit('card-published', messageId, status)
  }

  /**
   * 光标前是否有未完成的 @ 片段：有则开浮层并记下要收回的区间。
   * 按 selectionStart 取而不是正则匹配整串——用户在中间插入 @ 也要能点名。
   */
  function syncMentionFrom(el: HTMLTextAreaElement) {
    if (!props.mentionable || props.qaMode) return
    const caret = el.selectionStart ?? el.value.length
    const before = el.value.slice(0, caret)
    if (!/(^|\s)@[^@\s]*$/.test(before)) {
      mentionOpen.value = false
      return
    }
    const at = before.lastIndexOf('@')
    mentionPrefix.value = before.slice(0, at)
    mentionSuffix.value = el.value.slice(caret)
    mentionKeyword.value = before.slice(at + 1)
    mentionOpen.value = true
    if (!props.store.mentionCandidates.length) void props.store.loadMentionCandidates()
  }

  /** 选中候选：把 @片段从正文收回（不硬塞进消息体），改为输入区上方的点名提示条 */
  function onMentionSelect(candidate: MentionCandidate) {
    props.store.setMention(candidate)
    draft.value = `${mentionPrefix.value}${mentionSuffix.value}`
    mentionOpen.value = false
    mentionKeyword.value = ''
    nextTick(() => {
      const el = inputRef.value?.ref
      if (!el) return
      el.focus()
      const pos = mentionPrefix.value.length
      el.setSelectionRange(pos, pos)
    })
  }

  /** 键盘：浮层优先消费（上下/Enter/Esc），否则 Enter 发送、Shift+Enter 换行 */
  function onKeydown(event: Event | KeyboardEvent) {
    if (!(event instanceof KeyboardEvent)) return
    if (mentionLayerRef.value?.handleKeydown(event)) {
      event.preventDefault()
      return
    }
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault()
      void onSend()
    }
  }

  /** keyup 再看一次光标：字符已落进 textarea，判断“当前是不是在 @ 之后”更准 */
  function onKeyup(event: Event | KeyboardEvent) {
    const el = event instanceof KeyboardEvent ? event.target : null
    if (el instanceof HTMLTextAreaElement) syncMentionFrom(el)
  }

  async function onSend() {
    const raw = draft.value
    const text = raw.trim()
    if (!text || props.store.sending) return
    // named 策略没点名：按钮已置灰，键盘 Enter 这条路也要拦下并说明原因
    if (blockedReason.value) return
    mentionOpen.value = false
    if (props.qaMode && props.store.attachments.length) {
      ElMessage.warning('知识问答模式不支持附件，请先移除附件或切回通用对话')
      return
    }
    // 发送失败保留草稿便于重发；成功才清空。
    // 一轮对话的收尾（改标题、刷会话列表）是异步的，可能在回答已上屏之后才结束；
    // 期间用户新打的字不能被这一轮的收尾清掉，故只在草稿没被动过时清空。
    const ok = props.qaMode
      ? await props.store.sendQa(text, qaTopK.value)
      : await props.store.send(text)
    if (ok && draft.value === raw) draft.value = ''
    scrollToBottom()
    emit('sent', ok)
  }

  onMounted(() => {
    scrollToBottom()
    if (props.mentionable) void props.store.loadMentionCandidates()
  })

  defineExpose({
    /** 父级切换会话后请求滚到底（保持改造前的滚动时机） */
    scrollToBottom,
    /** 把文本填入输入框并聚焦（建议卡、外部快捷入口复用） */
    fillDraft(text: string) {
      useSuggestion(text)
    },
  })
</script>

<style scoped>
  /* 面板自身占满父容器：页面里是 chat-main，弹卡里是对话框内容区 */
  .chat-panel {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .chat-stream {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
  }
  .chat-stream :deep(.el-loading-mask) {
    background-color: var(--ds-bg-1);
  }

  .stream-inner {
    max-width: v-bind(maxWidth);
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .msg-row {
    display: flex;
  }
  .msg-row.user {
    justify-content: flex-end;
  }
  .msg-col {
    max-width: 78%;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
  }
  .msg-row.user .msg-col {
    align-items: flex-end;
  }

  .bubble {
    padding: 10px 14px;
    border-radius: 14px;
    font-size: 14px;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
  }
  /* 用户气泡：accent 深底右对齐 */
  .bubble.user {
    background: color-mix(in srgb, var(--ds-accent) 30%, var(--ds-bg-1));
    border: 1px solid color-mix(in srgb, var(--ds-accent) 45%, transparent);
    color: var(--ds-text-1);
  }
  /* 助手气泡：玻璃底左对齐 */
  .bubble.assistant {
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    color: var(--ds-text-1);
  }

  /* 引用来源：默认折叠，展开后可点开原文 */
  .msg-citations {
    margin: 6px 4px 0;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .msg-citations summary {
    cursor: pointer;
    color: var(--ds-text-3);
    user-select: none;
  }
  .msg-citations ul {
    margin: 6px 0 0;
    padding-left: 18px;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .msg-citations a {
    color: var(--ds-accent-2, #7dd3fc);
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .citation-ref {
    margin-right: 6px;
    padding: 0 4px;
    border-radius: 3px;
    background: var(--ds-glass, rgba(255, 255, 255, 0.06));
    color: var(--ds-text-2);
    font-variant-numeric: tabular-nums;
  }
  .citation-quote {
    display: block;
    margin-top: 2px;
    color: var(--ds-text-3);
    line-height: 1.5;
  }

  .msg-badge {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0 2px 6px;
  }
  .badge-from {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .msg-token-bar {
    display: inline-flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px;
    margin: 4px 4px 0;
    font-size: 11.5px;
    font-variant-numeric: tabular-nums;
    color: var(--ds-text-3);
  }
  .tok-main {
    color: #34d399;
    font-weight: 600;
  }
  .tok-detail {
    color: var(--ds-text-3);
  }

  /* 一键复制：低调常驻，hover/聚焦才提亮（不抢气泡视觉） */
  .msg-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 4px 4px 0;
  }
  .msg-copy,
  .msg-fb,
  .msg-act {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 1px 8px;
    font-size: 11.5px;
    line-height: 18px;
    color: var(--ds-text-3);
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--ds-radius-pill);
    cursor: pointer;
    transition:
      color 0.15s,
      border-color 0.15s,
      background 0.15s;
  }
  .msg-copy:hover,
  .msg-copy:focus-visible,
  .msg-fb:hover,
  .msg-fb:focus-visible,
  .msg-act:hover,
  .msg-act:focus-visible {
    color: var(--ds-text-1);
    background: var(--ds-glass-strong);
    border-color: var(--ds-border);
  }
  .msg-copy:active,
  .msg-fb:active,
  .msg-act:active {
    color: var(--ds-accent-text);
  }
  /* 已反馈：让「说过一次」在会话内可见，避免重复提交 */
  .msg-copy.done,
  .msg-fb.done {
    color: var(--el-color-success);
    border-color: var(--ds-border);
    background: var(--ds-glass-strong);
  }
  .msg-copy:disabled,
  .msg-fb:disabled,
  .msg-act:disabled {
    cursor: default;
    opacity: 0.6;
  }

  /* 高风险操作待批准：把「被拦下了、拦在哪张单上、去哪儿批」说清楚 */
  .msg-approval {
    margin: 8px 4px 0;
    padding: 10px 12px;
    border: 1px solid color-mix(in srgb, var(--ds-warning) 45%, transparent);
    border-radius: 10px;
    background: color-mix(in srgb, var(--ds-warning) 8%, transparent);
  }
  /* 附在其他卡片下面的紧凑审批条：一行放得下，不再占一整块卡片的高度 */
  .msg-approval.is-strip {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 6px 4px 0;
    padding: 6px 10px;
  }
  .msg-approval.is-strip .apv-list {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
  }
  .apv-head {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--ds-warning);
  }
  .apv-hint {
    margin: 6px 0 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }
  .apv-list {
    margin: 6px 0 0;
    padding-left: 18px;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .apv-id {
    margin-left: 6px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }
  .apv-go {
    margin-top: 8px;
    padding: 3px 12px;
    font-size: 12px;
    color: var(--ds-warning);
    background: transparent;
    border: 1px solid color-mix(in srgb, var(--ds-warning) 55%, transparent);
    border-radius: var(--ds-radius-pill);
    cursor: pointer;
  }
  .apv-go:hover,
  .apv-go:focus-visible {
    background: color-mix(in srgb, var(--ds-warning) 16%, transparent);
  }

  /* 纠错输入区：紧贴被纠正的那条回答，写清去向是专家审核 */
  .msg-correct {
    margin: 6px 4px 0;
    padding: 10px 12px;
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    background: var(--ds-glass);
  }
  .correct-foot {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
  }
  .correct-hint {
    flex: 1;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
  .correct-btn {
    padding: 2px 12px;
    font-size: 12px;
    border-radius: var(--ds-radius-pill);
    border: 1px solid var(--ds-border);
    background: transparent;
    color: var(--ds-text-2);
    cursor: pointer;
  }
  .correct-btn.primary {
    color: var(--ds-accent-text);
    border-color: var(--ds-border-strong);
  }
  .correct-btn:disabled {
    cursor: default;
    opacity: 0.55;
  }

  /* 群聊署名头像：每条 assistant 气泡各带一位，多助手同屏不混淆 */
  .msg-avatar {
    flex: none;
    width: 26px;
    height: 26px;
    margin: 16px 8px 0 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    color: var(--ds-text-1);
    background: var(--ds-glass-strong);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
  }

  /* 群聊本轮实况（发送中的成员进度，与成员条同一口径） */
  .live-members {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 8px;
  }
  .live-member {
    font-size: 11.5px;
    line-height: 1.5;
    color: var(--ds-text-3);
    padding: 1px 8px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
  }
  .live-member.running {
    color: var(--ds-accent-text);
    border-color: color-mix(in srgb, var(--ds-accent) 40%, transparent);
  }
  .live-member.done {
    color: var(--ds-success);
  }
  .live-member.error,
  .live-member.unknown,
  .live-member.idle {
    color: var(--ds-warning);
  }

  /* 用户消息上的附件标签（文件名即事实，不做多余解释） */
  .msg-attachments {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 0 2px 6px;
  }
  .att-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    max-width: 220px;
    padding: 2px 8px;
    font-size: 11.5px;
    color: var(--ds-text-2);
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: 999px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* 实时步骤条容器：与气泡同底，宽度自适应内容 */
  .bubble.live-steps {
    padding: 10px 14px;
  }

  /* 流中断提示：诚实告知，不假装已回答 */
  .stream-warn {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 12px;
    font-size: 12.5px;
    color: var(--ds-danger);
    background: color-mix(in srgb, var(--ds-danger) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--ds-danger) 35%, transparent);
    border-radius: 10px;
  }

  /* 待发附件行：输入框上方的可移除小卡 */
  .att-row {
    max-width: v-bind(maxWidth);
    margin: 0 auto 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  /* 群聊带：成员条 + 策略选择器，与输入行同宽并在窄屏自身换行 */
  .group-band {
    margin: 0 auto 8px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
  }

  /* 本轮点名提示条：一键移除，正文里不留 @ 文本 */
  .mention-bar {
    margin: 0 auto 8px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
    padding: 4px 6px 4px 10px;
    width: fit-content;
    max-width: 100%;
    font-size: 12px;
    color: var(--ds-text-2);
    background: var(--ds-glass-strong);
    border: 1px solid color-mix(in srgb, var(--ds-accent) 35%, transparent);
    border-radius: var(--ds-radius-pill);
  }
  .mb-label {
    color: var(--ds-text-3);
  }
  .mb-name {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    min-width: 0;
    max-width: 240px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--ds-text-1);
  }
  .mb-x {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 1px 8px;
    font-size: 11.5px;
    color: var(--ds-text-3);
    background: transparent;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
    cursor: pointer;
  }
  .mb-x:hover {
    color: var(--ds-danger);
    border-color: color-mix(in srgb, var(--ds-danger) 40%, transparent);
  }

  /* named 策略缺点名对象时的说明（不允许静默置灰） */
  .send-block {
    margin: 8px auto 0;
    font-size: 11.5px;
    line-height: 1.5;
    color: var(--ds-warning);
  }
  .qa-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    margin: 0 auto 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .qa-tabs {
    display: inline-flex;
    padding: 2px;
    border-radius: 999px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
  }
  .qa-tab {
    padding: 4px 12px;
    border: none;
    border-radius: 999px;
    background: transparent;
    color: var(--ds-text-2);
    font-size: 12px;
    cursor: pointer;
  }
  .qa-tab.active {
    background: var(--ds-accent);
    color: #fff;
  }
  .qa-hint {
    color: var(--ds-text-3);
  }
  .qa-topk {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .qa-topk :deep(.el-input-number) {
    width: 92px;
  }
  .att-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    max-width: 240px;
    padding: 3px 6px 3px 9px;
    font-size: 12px;
    color: var(--ds-text-2);
    background: var(--ds-glass-strong);
    border: 1px solid var(--ds-border);
    border-radius: 999px;
  }
  .att-chip.bad {
    border-color: color-mix(in srgb, var(--ds-danger) 45%, transparent);
  }
  .att-chip .nm {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .att-chip .err {
    font-size: 11px;
    color: var(--ds-danger);
  }
  .att-chip .vision {
    flex: none;
    padding: 0 6px;
    height: 18px;
    border: 1px solid var(--ds-border);
    border-radius: 999px;
    background: transparent;
    color: var(--ds-text-3);
    font-size: 11px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 2px;
  }
  .att-chip .vision:hover:not(:disabled) {
    color: var(--ds-accent);
    border-color: var(--ds-border-strong);
  }
  .att-chip .vision:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .att-chip .x {
    flex: none;
    width: 18px;
    height: 18px;
    padding: 0;
    border: none;
    border-radius: 999px;
    background: transparent;
    color: var(--ds-text-3);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .att-chip .x:hover {
    color: var(--ds-danger);
    background: color-mix(in srgb, var(--ds-danger) 12%, transparent);
  }

  /* 等待回答：三点波浪 */
  .bubble.typing {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 14px 16px;
  }
  .bubble.typing .dot {
    width: 6px;
    height: 6px;
    border-radius: 999px;
    background: var(--ds-text-3);
    animation: typing-wave 0.9s ease-in-out infinite;
  }
  .bubble.typing .dot:nth-child(2) {
    animation-delay: 0.15s;
  }
  .bubble.typing .dot:nth-child(3) {
    animation-delay: 0.3s;
  }
  @keyframes typing-wave {
    0%,
    60%,
    100% {
      transform: translateY(0);
      opacity: 0.5;
    }
    30% {
      transform: translateY(-4px);
      opacity: 1;
    }
  }

  /* 空态 */
  .chat-empty {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 24px;
  }
  .empty-title {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ds-text-1);
  }
  .empty-sub {
    margin: 0 0 10px;
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .suggest-grid {
    width: 100%;
    max-width: 720px;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  /* 建议卡：可点玻璃卡 hover 配方（§3） */
  .suggest-card {
    text-align: left;
    padding: 14px 16px;
    font-size: 13px;
    line-height: 1.6;
    color: var(--ds-text-2);
    cursor: pointer;
    background: var(--ds-glass);
    backdrop-filter: blur(18px) saturate(1.2);
    border: 1px solid var(--ds-border);
    border-radius: 14px;
    transition:
      border-color 0.2s,
      background 0.2s,
      transform 0.2s;
  }
  .suggest-card:hover {
    border-color: var(--ds-border-strong);
    background: var(--ds-glass-strong);
    color: var(--ds-text-1);
    transform: translateY(-2px);
  }

  /* 底部输入区：粘性玻璃，随消息流同宽 */
  .chat-input {
    flex: none;
    border-top: 1px solid var(--ds-border);
    background: var(--ds-glass);
    backdrop-filter: blur(18px) saturate(1.2);
    padding: 12px 20px 16px;
  }
  .input-inner {
    max-width: v-bind(maxWidth);
    margin: 0 auto;
    display: flex;
    align-items: flex-end;
    gap: 10px;
  }

  /* 附件按钮：输入框左侧，玻璃圆形动作位 */
  .attach-btn {
    flex: none;
    width: 34px;
    height: 34px;
    border: 1px solid var(--ds-border);
    border-radius: 999px;
    background: var(--ds-glass-strong);
    color: var(--ds-text-2);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition:
      color 0.2s,
      border-color 0.2s;
  }
  .attach-btn:hover:not(:disabled) {
    color: var(--ds-accent);
    border-color: var(--ds-border-strong);
  }
  .attach-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .attach-btn.recording {
    color: var(--ds-danger);
    border-color: color-mix(in srgb, var(--ds-danger) 55%, transparent);
    animation: attach-rec 1.2s ease-in-out infinite;
  }
  .rec-hint {
    flex: none;
    align-self: center;
    font-size: 12px;
    color: var(--ds-danger);
    white-space: nowrap;
  }
  @keyframes attach-rec {
    50% {
      background: color-mix(in srgb, var(--ds-danger) 14%, transparent);
    }
  }
  .is-spin {
    animation: attach-spin 0.9s linear infinite;
  }
  @keyframes attach-spin {
    to {
      transform: rotate(360deg);
    }
  }

  /* 隐藏的文件选择框（由左侧按钮触发） */
  .file-input {
    display: none;
  }
  .input-inner :deep(.el-textarea__inner) {
    box-shadow: 0 0 0 1px var(--ds-border) inset;
    background: var(--ds-bg-1);
    color: var(--ds-text-1);
    border-radius: 10px;
    padding: 8px 12px;
    line-height: 22px;
  }
  .input-inner :deep(.el-textarea__inner:focus) {
    box-shadow:
      0 0 0 1px color-mix(in srgb, var(--ds-accent) 30%, transparent) inset,
      0 0 0 3px color-mix(in srgb, var(--ds-accent) 30%, transparent);
  }

  /* 发送圆钮：品牌渐变动作位；发送中仅禁用 */
  .send-btn {
    flex: none;
    width: 34px;
    height: 34px;
    border: none;
    border-radius: 999px;
    background: var(--ds-grad-brand);
    color: #fff;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: filter 0.2s;
  }
  .send-btn:hover:not(:disabled) {
    filter: brightness(1.1);
  }
  .send-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  /* 滚动条 8px 圆头（§6） */
  .chat-stream::-webkit-scrollbar {
    width: 8px;
  }
  .chat-stream::-webkit-scrollbar-thumb {
    background: var(--ds-border-strong);
    border-radius: 999px;
  }
  .chat-stream::-webkit-scrollbar-track {
    background: transparent;
  }

  @media (max-width: 900px) {
    .suggest-grid {
      grid-template-columns: 1fr;
    }
    .chat-stream {
      padding: 14px;
    }
    .chat-input {
      padding: 10px 12px 14px;
    }
    /* 窄屏：气泡与成员卡都不许溢出 */
    .msg-col {
      max-width: 86%;
    }
    .msg-avatar {
      width: 22px;
      height: 22px;
      margin-right: 6px;
    }
    .mention-bar {
      width: 100%;
    }
  }

  /* 生成文档预览：iframe 铺满弹窗正文，深色底与预览 HTML 一致 */
  .doc-preview-body {
    height: 66vh;
    min-height: 320px;
    border-radius: 10px;
    overflow: hidden;
    background: #0b0e14;
  }
  .doc-preview-frame {
    width: 100%;
    height: 100%;
    border: 0;
    display: block;
    background: #0b0e14;
  }
  .escalate-question {
    margin: 0 0 12px;
    padding: 8px 10px;
    border-radius: var(--ds-radius-sm, 6px);
    background: var(--ds-glass, rgba(255, 255, 255, 0.05));
    color: var(--ds-text-2);
    font-size: 13px;
    line-height: 1.6;
    max-height: 140px;
    overflow: auto;
    white-space: pre-wrap;
  }
  .escalate-empty {
    margin: 10px 0 0;
    color: var(--ds-text-3);
    font-size: 12px;
  }

  :deep(.doc-preview-dialog .el-dialog__body) {
    padding: 10px 14px;
  }
</style>

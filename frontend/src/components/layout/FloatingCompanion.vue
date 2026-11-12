<template>
  <div
    v-if="visible"
    class="companion"
    :class="[`mood-${mood}`, { 'is-dragging': dragging, 'is-open': expanded }]"
    :style="anchorStyle"
    data-companion
  >
    <el-config-provider :z-index="POPPER_BASE_IN_COMPANION">
      <div
        v-if="wide && !expanded && bubbleText"
        class="companion-bubble"
        :class="{ 'bubble-below': bubbleBelow, 'is-dimmed': !bubbleVisible }"
        data-companion-bubble
      >
        {{ bubbleText }}
      </div>

      <button
        ref="handleRef"
        class="companion-avatar"
        type="button"
        :aria-label="`${skin.name}指挥官：点击交流，拖动换位置`"
        aria-haspopup="dialog"
        :aria-expanded="expanded"
        data-companion-handle
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
        @contextmenu.prevent="cycleSkin"
      >
        <CommanderFigure
          v-if="skin.figure === 'human'"
          class="avatar-figure"
          :skin="skin"
          :mood="mood"
          :size="wide ? 86 : 64"
        />
        <span
          v-else
          class="avatar-face"
          :style="{ background: skin.body, borderColor: skin.ear }"
        >
          {{ skin.emoji }}
        </span>
        <span class="avatar-state">{{ MOOD_LABEL[mood] }}</span>
      </button>

      <section
        v-if="expanded"
        class="companion-panel"
        :style="panelStyle"
        role="dialog"
        :aria-label="`${skin.name}指挥官`"
        data-companion-panel
      >
        <header
          class="panel-head"
          data-companion-panel-drag
          @pointerdown="onPanelDragStart"
        >
          <div class="panel-id">
            <strong>{{ skin.name }}</strong>
            <span class="panel-role">指挥官</span>
            <small>{{ MOOD_LABEL[mood] }} · 历史 {{ historyCount }} 条</small>
          </div>
          <div class="panel-tools">
            <button
              type="button"
              title="选大模型、挂技能与插件"
              :aria-pressed="abilitiesOpen"
              @click="abilitiesOpen = !abilitiesOpen"
            >
              能力
            </button>
            <button
              type="button"
              title="换一套形象（也可右键头像）"
              @click="cycleSkin"
            >
              换肤
            </button>
            <button
              type="button"
              title="在指挥官工作台打开"
              @click="goWorkbench"
            >
              工作台
            </button>
            <button
              type="button"
              aria-label="收起伙伴"
              @click="expanded = false"
            >
              ×
            </button>
          </div>
        </header>

        <div
          v-if="abilitiesOpen"
          class="panel-abilities"
          data-companion-abilities
        >
          <CommanderAbilities
            compact
            :can-manage="canManage"
          />
        </div>

        <div class="panel-body">
          <ChatPanel
            :store="store"
            panel-id="companion"
            :label="`${skin.name}指挥官`"
            :suggestions="SUGGESTIONS"
            :max-width="'100%'"
            empty-title="说要办的事，我来安排"
            empty-sub="直接说目标：我会拆任务、找助手、调工具，做完把成品放回这里。历史一直在，随时回来接着说。"
            placeholder="说一句目标，Enter 交给指挥官"
            commander-only
            :commander-enabled="commanderEnabled"
            :default-preview-plan="previewPlan"
          />
        </div>

        <span
          class="panel-edge panel-edge-e"
          data-companion-resize="e"
          @pointerdown="onResizeStart('e', $event)"
        />
        <span
          class="panel-edge panel-edge-s"
          data-companion-resize="s"
          @pointerdown="onResizeStart('s', $event)"
        />
        <span
          class="panel-corner"
          data-companion-resize="se"
          aria-hidden="true"
          @pointerdown="onResizeStart('se', $event)"
        />
      </section>
    </el-config-provider>
  </div>
</template>

<script setup lang="ts">
  /**
   * 页面内悬浮指挥官伙伴（v3.50 首发，v3.51 重做）：
   * - 任意页面常驻，默认形象是戴草帽会擦汗的农民小人；
   * - 头像可拖，面板也能整体拖走、拖边/拖角改大小，位置与尺寸都记住；
   * - 只有「指挥官」一个身份：不出现普通/指挥官切换，每轮都走指挥官链路；
   * - 每个用户固定一条专属会话，历史一直往下长，刷新换页都不丢。
   */
  import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
  import { useRouter } from 'vue-router'
  import ChatPanel from '@/components/chat/ChatPanel.vue'
  import CommanderAbilities from '@/components/commander/CommanderAbilities.vue'
  import CommanderFigure from '@/components/pet/CommanderFigure.vue'
  import { useChatStore } from '@/stores/chat'
  import { useAuthStore } from '@/stores/auth'
  import { commanderApi } from '@/api/commander'
  import {
    COMPANION_SESSION_TITLE,
    clearCompanionSessionId,
    readCompanionSessionId,
    writeCompanionSessionId,
  } from '@/utils/companionChat'
  import { companionCoversCorner } from '@/utils/companionDock'
  import { collectPetLines } from '@/utils/petBrief'
  import {
    MOOD_LABEL,
    greetingFor,
    petMuted,
    readStoredPetScene,
    sceneTip,
    subscribePetEvents,
    type PetEvent,
    type PetMood,
    type PetScene,
  } from '@/utils/petCopilot'
  import {
    PET_OVERLAY_EVENT,
    clampPetPos,
    clampPetRect,
    defaultPetPanelRect,
    defaultPetPos,
    overlapsComposerDock,
    petOverlayEnabled,
    readPetPanelRect,
    readPetPos,
    writePetPanelRect,
    writePetPos,
  } from '@/utils/petOverlay'
  import {
    PET_SKINS,
    normalizePetSkin,
    petSkinById,
    readStoredPetSkin,
    writeStoredPetSkin,
    type PetSkinId,
  } from '@/utils/petSkins'

  const COMPANION_SCOPE = 'floating-companion'
  /**
   * 伙伴本体是 z-index 2601 的常驻浮层，比 Element Plus 弹层默认基数（2000）还高。
   * 不抬高就会「点开产物没反应、下拉展开了点在面板上」——弹窗与下拉其实开在面板背后。
   */
  const POPPER_BASE_IN_COMPANION = 2700
  const SUGGESTIONS = [
    '组织一次本周经营复盘，把结论写成文档给我',
    '给水稻叶黄问题安排诊断并出防治方案',
    '把待处理工单按紧急程度排一遍并派给助手',
  ]

  const router = useRouter()
  const store = useChatStore(COMPANION_SCOPE)
  const auth = useAuthStore()

  /** 能力抽屉：非管理员也看得见，但只有管理员能改 */
  const canManage = computed(() => auth.isYardAdmin())
  const abilitiesOpen = ref(false)

  const enabled = ref(true)
  const wide = ref(true)
  /** 窄屏只留头像：气泡会挡住输入区，所以不弹；指挥官本体任何宽度都要在。 */
  const visible = computed(() => enabled.value)
  const expanded = ref(false)
  const dragging = ref(false)
  const skinId = ref<PetSkinId>(readStoredPetSkin())
  const skin = computed(() => petSkinById(skinId.value))
  const mood = ref<PetMood>('idle')
  const scene = ref<PetScene>(readStoredPetScene())
  const speech = ref('')
  const pos = ref({ x: 24, y: 24 })
  const panel = ref(defaultPetPanelRect({ x: 24, y: 24 }, 1280, 800))

  const commanderEnabled = ref(true)
  const previewPlan = ref(true)

  const handleRef = ref<HTMLElement | null>(null)
  let pointerId: number | null = null
  let dragOffset = { x: 0, y: 0 }
  let moved = 0
  let speechTimer: number | null = null
  let unlisten: (() => void) | null = null

  /** 面板拖拽/缩放进行中：detach 函数，组件卸载时也要摘掉 */
  let stopPanelDrag: (() => void) | null = null
  let stopResize: (() => void) | null = null

  const historyCount = computed(() => store.messages.length)

  /**
   * 气泡轮播（v3.70）：固定两句（时段问候 + 场景提示）打底，
   * 再叠加周期拉取的小院实况（天气/工单/警报/后台任务），每 10 秒换一句；
   * 事件短句（任务结果等）随时插队，说完接着轮播。
   */
  const dataLines = ref<string[]>([])
  const tickerIndex = ref(0)
  let briefTimer: number | null = null
  let tickerTimer: number | null = null
  /** 闲聊气泡是否在「露出期」。2026-11-13 交付走查修正:此前的气泡 24 小时常驻,
   * 在表格/按钮密度的页面上持续遮挡内容(全站 8+ 页复现)。现在按大厂陪伴件
   * 惯例改成「露出 6s → 淡出 8s」的值守周期:陪伴感在,内容不被挡。 */
  const bubbleVisible = ref(true)
  let bubbleHideTimer: number | null = null

  const ambientLines = computed(() => [greetingFor(), sceneTip(scene.value), ...dataLines.value])

  const bubbleText = computed(() => {
    if (speech.value) return speech.value
    if (!bubbleVisible.value) return ''
    const lines = ambientLines.value
    if (lines.length === 0) return ''
    return lines[tickerIndex.value % lines.length] ?? lines[0]
  })

  /** 小院实况拉取失败就只说固定两句，不打断陪伴 */
  async function refreshBriefs(): Promise<void> {
    try {
      dataLines.value = await collectPetLines(permission => auth.hasPermission(permission))
    } catch {
      dataLines.value = []
    }
  }

  /** 小人贴着屏幕顶时气泡翻到脚下，避免探出视口 */
  const bubbleBelow = computed(() => pos.value.y < 150)

  const anchorStyle = computed(() => ({ left: `${pos.value.x}px`, top: `${pos.value.y}px` }))

  const panelStyle = computed(() => ({
    left: `${panel.value.x}px`,
    top: `${panel.value.y}px`,
    width: `${panel.value.width}px`,
    height: `${panel.value.height}px`,
  }))

  function viewport(): { w: number; h: number } {
    return { w: window.innerWidth, h: window.innerHeight }
  }

  function place(at: { x: number; y: number }): void {
    const { w, h } = viewport()
    pos.value = clampPetPos(at.x, at.y, w, h)
  }

  /**
   * 告诉页面「小人此刻正停在右下角」：对话输入条据此让开右侧，
   * 否则 86px 的头像会把 30px 的发送圆钮整颗盖住，用户点不下去。
   */
  function syncCornerCoverage(): void {
    const el = handleRef.value
    if (!visible.value || !el) {
      companionCoversCorner.value = false
      return
    }
    const rect = el.getBoundingClientRect()
    const { w, h } = viewport()
    companionCoversCorner.value = overlapsComposerDock(rect, w, h)
  }

  function fitPanel(next: typeof panel.value): void {
    const { w, h } = viewport()
    panel.value = clampPetRect(next, w, h)
  }

  /** 打开面板：优先回到上次的位置与大小，首次贴在头像左上方 */
  function openPanel(): void {
    const { w, h } = viewport()
    const stored = readPetPanelRect()
    fitPanel(stored ?? defaultPetPanelRect(pos.value, w, h))
    expanded.value = true
  }

  function onPointerDown(event: PointerEvent): void {
    if (event.button !== 0) return
    pointerId = event.pointerId
    moved = 0
    dragOffset = { x: event.clientX - pos.value.x, y: event.clientY - pos.value.y }
    handleRef.value?.setPointerCapture?.(event.pointerId)
  }

  function onPointerMove(event: PointerEvent): void {
    if (pointerId === null || event.pointerId !== pointerId) return
    const nextX = event.clientX - dragOffset.x
    const nextY = event.clientY - dragOffset.y
    moved += Math.abs(nextX - pos.value.x) + Math.abs(nextY - pos.value.y)
    if (moved > 4) {
      dragging.value = true
      place({ x: nextX, y: nextY })
    }
  }

  function onPointerUp(event: PointerEvent): void {
    if (pointerId === null || event.pointerId !== pointerId) return
    handleRef.value?.releasePointerCapture?.(event.pointerId)
    pointerId = null
    const wasDragging = dragging.value
    dragging.value = false
    if (wasDragging) {
      writePetPos(pos.value)
      return
    }
    if (expanded.value) expanded.value = false
    else openPanel()
  }

  function onPanelDragStart(event: PointerEvent): void {
    if (event.button !== 0) return
    // 头部按钮不参与拖拽
    if ((event.target as HTMLElement).closest('button')) return
    const dx = event.clientX - panel.value.x
    const dy = event.clientY - panel.value.y
    stopPanelDrag?.()
    stopPanelDrag = attachPointerDrag(
      move => fitPanel({ ...panel.value, x: move.clientX - dx, y: move.clientY - dy }),
      () => {
        stopPanelDrag = null
        writePetPanelRect(panel.value)
      },
    )
  }

  function onResizeStart(dir: 'e' | 's' | 'se', event: PointerEvent): void {
    if (event.button !== 0) return
    event.preventDefault()
    const startX = event.clientX
    const startY = event.clientY
    const startWidth = panel.value.width
    const startHeight = panel.value.height
    stopResize?.()
    stopResize = attachPointerDrag(
      move =>
        fitPanel({
          ...panel.value,
          width: dir === 's' ? startWidth : startWidth + (move.clientX - startX),
          height: dir === 'e' ? startHeight : startHeight + (move.clientY - startY),
        }),
      () => {
        stopResize = null
        writePetPanelRect(panel.value)
      },
    )
  }

  /**
   * pointerdown 后把 move/up 挂到 window：
   * 把手只有 18px，指针滑出把手也要继续拖，松手才落盘。
   */
  function attachPointerDrag(
    move: (event: PointerEvent) => void,
    done: () => void,
  ): () => void {
    // 捕获阶段监听：面板里有组件会拦截 pointermove 冒泡，冒泡阶段挂 window 收不到
    const detach = (): void => {
      window.removeEventListener('pointermove', onMove, true)
      window.removeEventListener('pointerup', onUp, true)
      window.removeEventListener('pointercancel', onUp, true)
    }
    const onMove = (event: PointerEvent): void => move(event)
    const onUp = (): void => {
      detach()
      done()
    }
    window.addEventListener('pointermove', onMove, true)
    window.addEventListener('pointerup', onUp, true)
    window.addEventListener('pointercancel', onUp, true)
    return detach
  }

  function cycleSkin(): void {
    const index = PET_SKINS.findIndex(item => item.id === skinId.value)
    const next = PET_SKINS[(index + 1) % PET_SKINS.length]
    skinId.value = next.id
    writeStoredPetSkin(next.id)
  }

  function goWorkbench(): void {
    expanded.value = false
    void router.push('/commander')
  }

  function showSpeech(text: string): void {
    if (petMuted() || !text.trim()) return
    speech.value = text.trim().slice(0, 60)
    bubbleVisible.value = true
    if (speechTimer !== null) window.clearTimeout(speechTimer)
    speechTimer = window.setTimeout(() => {
      speech.value = ''
      speechTimer = null
    }, 7000)
  }

  function handleEvent(event: PetEvent): void {
    if (event.type === 'scene' && event.scene) {
      scene.value = event.scene
      // 切页立刻说这个页面的提示（轮播第 2 句是场景提示）
      tickerIndex.value = 1
      return
    }
    if (event.type === 'say' && event.text) {
      showSpeech(event.text)
      return
    }
    if (event.type === 'mood' && event.mood) {
      mood.value = event.mood
      if (event.text) showSpeech(event.text)
    }
  }

  function onStorage(event: StorageEvent): void {
    if (event.key === 'ky_pet_skin') {
      skinId.value = normalizePetSkin(event.newValue)
    }
    if (event.key === 'ky_pet_overlay') enabled.value = petOverlayEnabled()
  }

  function onOverlayEvent(event: Event): void {
    const detail = (event as CustomEvent<{ enabled?: boolean }>).detail
    enabled.value = detail?.enabled ?? petOverlayEnabled()
  }

  function onResize(): void {
    wide.value = window.innerWidth >= 900
    place(pos.value)
    if (expanded.value) fitPanel(panel.value)
    syncCornerCoverage()
  }

  watch(visible, value => {
    if (!value) expanded.value = false
  })

  /** 头像每挪动一步都重算一次让位（拖动过程实时生效，拖离角落立刻恢复满宽）；post 保证读到移动后的真实矩形 */
  watch([pos, visible], syncCornerCoverage, { flush: 'post' })

  async function loadCommanderSettings(): Promise<void> {
    // 后端读写分离（v3.65）：commander 设置单查放宽到 chat:use，全员可读
    try {
      const settings = await commanderApi.getSettings()
      commanderEnabled.value = settings.enabled
      previewPlan.value = settings.preview_plan
    } catch {
      /* 读不到设置时保持指挥官可用，不阻塞伙伴 */
    }
  }

  /**
   * 续接专属会话：本地记着会话 id 就切回去（历史一直在），
   * 会话被删或换了账号（列表里没有）才新开一条并重新锚定。
   */
  async function ensureCompanionSession(): Promise<void> {
    const anchor = readCompanionSessionId()
    await store.fetchSessions().catch(() => undefined)
    if (anchor && store.sessions.some(item => item.id === anchor)) {
      await store.selectSession(anchor).catch(() => undefined)
      return
    }
    if (anchor) clearCompanionSessionId()
    const created = await store
      .newSession({ title: COMPANION_SESSION_TITLE, execution_mode: 'commander' })
      .catch(() => undefined)
    if (created) writeCompanionSessionId(created.id)
  }

  onMounted(() => {
    enabled.value = petOverlayEnabled()
    wide.value = window.innerWidth >= 900
    const { w, h } = viewport()
    const anchor = readPetPos() ?? defaultPetPos(w, h)
    pos.value = clampPetPos(anchor.x, anchor.y, w, h)
    panel.value = clampPetRect(readPetPanelRect() ?? defaultPetPanelRect(pos.value, w, h), w, h)
    syncCornerCoverage()
    unlisten = subscribePetEvents(handleEvent)
    window.addEventListener('storage', onStorage)
    window.addEventListener('resize', onResize)
    window.addEventListener(PET_OVERLAY_EVENT, onOverlayEvent)
    void loadCommanderSettings()
    void ensureCompanionSession()
    void refreshBriefs()
    briefTimer = window.setInterval(() => void refreshBriefs(), 5 * 60 * 1000)
    tickerTimer = window.setInterval(() => {
      if (speech.value) return
      if (bubbleVisible.value) {
        // 露出满 6 秒 → 淡出,让内容喘口气
        bubbleVisible.value = false
      } else {
        // 淡出满 8 秒 → 换一句再露出
        tickerIndex.value += 1
        bubbleVisible.value = true
        if (bubbleHideTimer !== null) window.clearTimeout(bubbleHideTimer)
        bubbleHideTimer = window.setTimeout(() => {
          bubbleVisible.value = false
          bubbleHideTimer = null
        }, 6 * 1000)
      }
    }, 8 * 1000)
  })

  onBeforeUnmount(() => {
    companionCoversCorner.value = false
    unlisten?.()
    stopPanelDrag?.()
    stopResize?.()
    if (briefTimer !== null) window.clearInterval(briefTimer)
    if (tickerTimer !== null) window.clearInterval(tickerTimer)
    if (speechTimer !== null) window.clearTimeout(speechTimer)
    if (bubbleHideTimer !== null) window.clearTimeout(bubbleHideTimer)
    window.removeEventListener('storage', onStorage)
    window.removeEventListener('resize', onResize)
    window.removeEventListener(PET_OVERLAY_EVENT, onOverlayEvent)
  })
</script>

<style scoped>
  .companion {
    position: fixed;
    z-index: 2600;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    /* 容器只占头像大小，气泡与面板用定位浮出，不会把头像顶出视口 */
    width: 76px;
    user-select: none;
  }

  /*
   * 气泡挂在头像正上方（v3.70 修复）：以前 bottom: 82px 会压在农民小人的
   * 脸和草帽上；现在整颗抬到头顶以上，加个小尾巴指回去。小人贴着屏幕顶时
   * 翻到脚下（bubble-below），保证任何落位都能读全。
   */
  .companion-bubble {
    position: absolute;
    right: 0;
    bottom: calc(100% + 10px);
    z-index: 2;
    width: max-content;
    max-width: 240px;
    padding: 7px 11px;
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    background: var(--ds-bg-1);
    color: var(--ds-text-2);
    font-size: 12.5px;
    line-height: 1.5;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.22);

    /* 气泡只是提示，不能替头像吃掉点击：否则正好压在对话输入区右下角的
       「普通/指挥官」切换上，用户点下去没反应 */
    pointer-events: none;
    opacity: 1;
    transition: opacity 0.6s ease;
  }

  .companion-bubble.is-dimmed {
    opacity: 0;
    visibility: hidden;
  }

  .companion-bubble::after {
    content: '';
    position: absolute;
    right: 26px;
    bottom: -5px;
    width: 9px;
    height: 9px;
    border-right: 1px solid var(--ds-border);
    border-bottom: 1px solid var(--ds-border);
    border-radius: 0 0 3px 0;
    background: var(--ds-bg-1);
    transform: rotate(45deg);
  }

  .companion-bubble.bubble-below {
    bottom: auto;
    top: calc(100% + 10px);
  }

  .companion-bubble.bubble-below::after {
    bottom: auto;
    top: -5px;
    border: none;
    border-left: 1px solid var(--ds-border);
    border-top: 1px solid var(--ds-border);
    border-radius: 3px 0 0 0;
  }

  .companion-avatar {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    padding: 0;
    border: none;
    background: none;
    cursor: grab;
    touch-action: none;
  }

  .companion.is-dragging .companion-avatar {
    cursor: grabbing;
  }

  .avatar-figure {
    filter: drop-shadow(0 8px 14px rgba(0, 0, 0, 0.32));
  }

  .avatar-face {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 54px;
    height: 54px;
    border: 3px solid transparent;
    border-radius: 50%;
    font-size: 24px;
    box-shadow: 0 8px 20px rgba(0, 0, 0, 0.28);
    animation: companion-float 3.6s ease-in-out infinite;
  }

  .avatar-state {
    padding: 1px 7px;
    border: 1px solid var(--ds-border);
    border-radius: 999px;
    background: var(--ds-bg-1);
    color: var(--ds-text-2);
    font-size: 10.5px;
    letter-spacing: 0.02em;
  }

  .companion.mood-sleepy .avatar-face {
    filter: brightness(0.82) saturate(0.7);
  }

  .companion-panel {
    position: fixed;
    z-index: 2601;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    border: 1px solid var(--ds-border);
    border-radius: 14px;
    background: var(--ds-bg-1);
    box-shadow: 0 18px 44px rgba(0, 0, 0, 0.36);
  }

  .panel-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 8px 10px;
    border-bottom: 1px solid var(--ds-border);
    background: var(--ds-field);
    cursor: grab;
    touch-action: none;
  }

  .panel-id {
    display: flex;
    align-items: baseline;
    gap: 6px;
    min-width: 0;
    /* 最小宽度下也要一行读完：名字与徽章不许折行，状态段自己截断 */
    overflow: hidden;
    white-space: nowrap;
  }

  .panel-id strong {
    font-size: 13px;
    color: var(--ds-text-1);
    flex-shrink: 0;
  }

  .panel-role {
    padding: 1px 6px;
    border: 1px solid var(--ds-border);
    border-radius: 999px;
    color: var(--ds-accent);
    font-size: 10.5px;
    flex-shrink: 0;
  }

  .panel-id small {
    color: var(--ds-text-3);
    font-size: 11px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
  }

  .panel-tools {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }

  .panel-tools button {
    padding: 3px 8px;
    border: 1px solid var(--ds-border);
    border-radius: 7px;
    background: transparent;
    color: var(--ds-text-2);
    font-size: 11.5px;
    cursor: pointer;
  }

  .panel-tools button:hover {
    border-color: var(--ds-accent);
    color: var(--ds-accent);
  }

  .panel-body {
    flex: 1;
    min-height: 0;
    display: flex;
    overflow: hidden;
  }

  /* 能力抽屉盖在对话上方：可滚动，不挤掉聊天区的最小可视高度 */
  .panel-abilities {
    flex: none;
    max-height: 52%;
    overflow: auto;
    padding: 10px 12px;
    border-bottom: 1px solid var(--ds-border);
    background: var(--ds-field);
  }

  .panel-body > * {
    flex: 1;
    min-height: 0;
  }

  /* 拖边改宽、拖边改高、拖角同时改 */
  .panel-edge,
  .panel-corner {
    position: absolute;
    touch-action: none;
  }

  .panel-edge-e {
    top: 24px;
    right: 0;
    width: 6px;
    height: calc(100% - 40px);
    cursor: ew-resize;
  }

  .panel-edge-s {
    bottom: 0;
    left: 24px;
    height: 6px;
    width: calc(100% - 40px);
    cursor: ns-resize;
  }

  .panel-corner {
    right: 2px;
    bottom: 2px;
    width: 18px;
    height: 18px;
    cursor: nwse-resize;
    background:
      linear-gradient(135deg, transparent 45%, var(--ds-border) 45%, var(--ds-border) 55%, transparent 55%),
      linear-gradient(135deg, transparent 68%, var(--ds-border) 68%, var(--ds-border) 78%, transparent 78%);
  }

  @keyframes companion-float {
    0%,
    100% {
      transform: translateY(0);
    }
    50% {
      transform: translateY(-3px);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .avatar-face {
      animation: none;
    }
  }
</style>

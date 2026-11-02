<template>
  <div
    v-if="visible"
    class="companion"
    :class="[`mood-${mood}`, { 'is-dragging': dragging, 'is-open': expanded }]"
    :style="anchorStyle"
    data-companion
  >
    <div
      v-if="!expanded && bubbleText"
      class="companion-bubble"
      data-companion-bubble
    >
      {{ bubbleText }}
    </div>

    <button
      ref="handleRef"
      class="companion-avatar"
      type="button"
      :aria-label="`${skin.name}伙伴：点击交流，拖动换位置`"
      aria-haspopup="dialog"
      :aria-expanded="expanded"
      data-companion-handle
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
      @contextmenu.prevent="cycleSkin"
    >
      <span
        class="avatar-face"
        :style="{ background: skin.body, borderColor: skin.ear }"
      >
        <span class="avatar-hat">{{ skin.emoji }}</span>
      </span>
      <span class="avatar-state">{{ MOOD_LABEL[mood] }}</span>
    </button>

    <section
      v-if="expanded"
      class="companion-panel"
      :style="panelStyle"
      role="dialog"
      :aria-label="`${skin.name}指挥官伙伴`"
      data-companion-panel
    >
      <header class="panel-head">
        <div class="panel-title">
          <strong>{{ skin.name }}</strong>
          <small>{{ SCENE_LABEL[scene] }} · {{ MOOD_LABEL[mood] }}</small>
        </div>
        <div class="panel-tools">
          <button
            type="button"
            title="换一套皮肤（也可右键头像）"
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

      <div class="panel-body">
        <ChatPanel
          :store="store"
          panel-id="companion"
          :label="`${skin.name}指挥官伙伴`"
          :suggestions="SUGGESTIONS"
          :max-width="'100%'"
          empty-title="直接说要办的事"
          empty-sub="伙伴会把目标拆给助手与班组，进度在这里实时回显。"
          placeholder="说一句目标，Enter 交给指挥官"
          commander-toggle
          :commander-enabled="commanderEnabled"
          :default-execution-mode="defaultMode"
          :default-preview-plan="previewPlan"
        />
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
  /**
   * 页面内悬浮指挥官伙伴（v3.50）：
   * - 任意页面右下角常驻，可拖到屏幕任何位置，位置存 localStorage；
   * - 状态/气泡来自 petCopilot 总线，与桌面伙伴窗口共用同一套事件；
   * - 点头像即开即聊，默认指挥官模式，可直接下指令指挥全部内容。
   */
  import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
  import { useRouter } from 'vue-router'
  import ChatPanel from '@/components/chat/ChatPanel.vue'
  import { useChatStore } from '@/stores/chat'
  import { commanderApi, type ExecutionMode } from '@/api/commander'
  import {
    MOOD_LABEL,
    SCENE_LABEL,
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
    PET_OVERLAY_MARGIN,
    PET_OVERLAY_SIZE,
    clampPetPos,
    defaultPetPos,
    petOverlayEnabled,
    readPetPos,
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
  const PANEL_SIZE = { width: 360, height: 520 }
  const SUGGESTIONS = [
    '组织一次本周经营复盘，把结论写成文档给我',
    '给水稻叶黄问题安排诊断并出防治方案',
    '把待处理工单按紧急程度排一遍并派给助手',
  ]

  const router = useRouter()
  const store = useChatStore(COMPANION_SCOPE)

  const enabled = ref(true)
  const wide = ref(true)
  /** 窄屏（手机/分屏）不悬浮，避免挡住输入区 */
  const visible = computed(() => enabled.value && wide.value)
  const expanded = ref(false)
  const dragging = ref(false)
  const skinId = ref<PetSkinId>(readStoredPetSkin())
  const skin = computed(() => petSkinById(skinId.value))
  const mood = ref<PetMood>('idle')
  const scene = ref<PetScene>(readStoredPetScene())
  const speech = ref('')
  const pos = ref({ x: 24, y: 24 })

  const commanderEnabled = ref(true)
  const defaultMode = ref<ExecutionMode>('commander')
  const previewPlan = ref(true)

  const handleRef = ref<HTMLElement | null>(null)
  let pointerId: number | null = null
  let dragOffset = { x: 0, y: 0 }
  let moved = 0
  let speechTimer: number | null = null
  let unlisten: (() => void) | null = null

  const bubbleText = computed(() => {
    if (speech.value) return speech.value
    if (scene.value !== 'general') return sceneTip(scene.value)
    return greetingFor()
  })

  const anchorStyle = computed(() => ({ left: `${pos.value.x}px`, top: `${pos.value.y}px` }))

  /** 面板固定在头像左上方，并按视口夹取，贴边时不会被裁掉 */
  const panelStyle = computed(() => {
    const w = window.innerWidth
    const h = window.innerHeight
    const left = Math.min(
      Math.max(pos.value.x + PET_OVERLAY_SIZE.width - PANEL_SIZE.width, PET_OVERLAY_MARGIN),
      Math.max(PET_OVERLAY_MARGIN, w - PANEL_SIZE.width - PET_OVERLAY_MARGIN),
    )
    const bottom = Math.min(
      Math.max(h - pos.value.y - 6, PET_OVERLAY_MARGIN),
      Math.max(PET_OVERLAY_MARGIN, h - PANEL_SIZE.height - PET_OVERLAY_MARGIN),
    )
    return { left: `${left}px`, bottom: `${bottom}px` }
  })

  function viewport(): { w: number; h: number } {
    return { w: window.innerWidth, h: window.innerHeight }
  }

  function place(posInput: { x: number; y: number }): void {
    const { w, h } = viewport()
    pos.value = clampPetPos(posInput.x, posInput.y, w, h)
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
    expanded.value = !expanded.value
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
    if (speechTimer !== null) window.clearTimeout(speechTimer)
    speechTimer = window.setTimeout(() => {
      speech.value = ''
      speechTimer = null
    }, 7000)
  }

  function handleEvent(event: PetEvent): void {
    if (event.type === 'scene' && event.scene) {
      scene.value = event.scene
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
  }

  watch(visible, value => {
    if (!value) expanded.value = false
  })

  async function loadCommanderSettings(): Promise<void> {
    try {
      const settings = await commanderApi.getSettings()
      commanderEnabled.value = settings.enabled
      // 伙伴的定位是「随时能指挥」：指挥官开着就默认进入指挥模式，关了才回落到普通对话
      defaultMode.value = settings.enabled ? 'commander' : 'standard'
      previewPlan.value = settings.preview_plan
    } catch {
      /* 读不到设置时保持指挥官可用，不阻塞伙伴 */
    }
  }

  onMounted(() => {
    enabled.value = petOverlayEnabled()
    wide.value = window.innerWidth >= 900
    const stored = readPetPos()
    pos.value = stored ?? defaultPetPos(window.innerWidth, window.innerHeight, PET_OVERLAY_SIZE)
    if (stored) place(stored)
    unlisten = subscribePetEvents(handleEvent)
    window.addEventListener('storage', onStorage)
    window.addEventListener('resize', onResize)
    window.addEventListener(PET_OVERLAY_EVENT, onOverlayEvent)
    void loadCommanderSettings()
  })

  onBeforeUnmount(() => {
    unlisten?.()
    if (speechTimer !== null) window.clearTimeout(speechTimer)
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

  .companion-bubble {
    position: absolute;
    right: 0;
    bottom: 82px;
    width: max-content;
    max-width: 220px;
    padding: 6px 10px;
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    background: var(--ds-surface, #161b26);
    color: var(--ds-text-2, #cfd8e6);
    font-size: 12px;
    line-height: 1.5;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.22);
  }

  .companion-avatar {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    padding: 0;
    border: none;
    background: none;
    cursor: grab;
    touch-action: none;
  }

  .companion.is-dragging .companion-avatar {
    cursor: grabbing;
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

  .avatar-hat {
    line-height: 1;
  }

  .avatar-state {
    padding: 1px 7px;
    border-radius: 999px;
    background: rgba(10, 14, 22, 0.62);
    color: #b9c6da;
    font-size: 10.5px;
    letter-spacing: 0.02em;
  }

  .companion.mood-thinking .avatar-face,
  .companion.mood-busy .avatar-face {
    animation: companion-work 1.1s ease-in-out infinite;
  }

  .companion.mood-happy .avatar-face {
    animation: companion-hop 0.7s ease-in-out 3;
  }

  .companion.mood-worried .avatar-face {
    animation: companion-shake 0.9s ease-in-out 2;
  }

  .companion.mood-sleepy .avatar-face {
    animation: none;
    filter: brightness(0.82) saturate(0.7);
  }

  .companion-panel {
    position: fixed;
    z-index: 2601;
    display: flex;
    flex-direction: column;
    width: 360px;
    height: 520px;
    overflow: hidden;
    border: 1px solid var(--ds-border);
    border-radius: 14px;
    background: var(--ds-surface, #121722);
    box-shadow: 0 18px 44px rgba(0, 0, 0, 0.36);
  }

  .panel-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 8px 10px;
    border-bottom: 1px solid var(--ds-border);
    background: var(--ds-field, #171d29);
  }

  .panel-title {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .panel-title strong {
    font-size: 13px;
    color: var(--ds-text-1, #e8eefb);
  }

  .panel-title small {
    font-size: 11px;
    color: var(--ds-text-3, #8b98ad);
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
    color: var(--ds-text-2, #cfd8e6);
    font-size: 11.5px;
    cursor: pointer;
  }

  .panel-tools button:hover {
    border-color: var(--ds-accent, #4c9aff);
    color: var(--ds-accent, #4c9aff);
  }

  .panel-body {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    display: flex;
  }

  .panel-body > * {
    flex: 1;
    min-height: 0;
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

  @keyframes companion-work {
    0%,
    100% {
      transform: translateY(0) rotate(-2deg);
    }
    50% {
      transform: translateY(-4px) rotate(2deg);
    }
  }

  @keyframes companion-hop {
    0%,
    100% {
      transform: translateY(0) scale(1);
    }
    40% {
      transform: translateY(-7px) scale(1.04);
    }
  }

  @keyframes companion-shake {
    0%,
    100% {
      transform: translateX(0);
    }
    25% {
      transform: translateX(-3px);
    }
    75% {
      transform: translateX(3px);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .avatar-face,
    .companion.mood-thinking .avatar-face,
    .companion.mood-busy .avatar-face,
    .companion.mood-happy .avatar-face,
    .companion.mood-worried .avatar-face {
      animation: none;
    }
  }
</style>

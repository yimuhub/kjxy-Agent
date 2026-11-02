<template>
  <div
    class="pet-page"
    :class="`mood-${mood}`"
    @contextmenu.prevent="togglePanel"
  >
    <div class="pet-greeting">
      {{ greeting }}
    </div>
    <div class="pet-bubble">
      {{ tip }}
    </div>

    <div class="pet-stage">
      <div
        class="drag-grip"
        data-tauri-drag-region
        title="拖动桌宠"
      >
        <span />
        <span />
        <span />
      </div>
      <button
        class="pet-close"
        type="button"
        title="收起桌宠"
        @click.stop="closePet"
      >
        ×
      </button>

      <button
        class="pet-body"
        type="button"
        :aria-label="`${skin.name}：点击打开工作台`"
        @click.stop="openMain"
      >
        <CommanderFigure
          v-if="skin.figure === 'human'"
          class="pet-figure"
          :skin="skin"
          :mood="mood"
          :size="150"
        />
        <svg
          v-else
          class="pet-svg"
          viewBox="0 0 160 176"
          role="img"
          :aria-label="skin.name"
        >
          <g
            v-if="skin.ears === 'leaf'"
            :fill="skin.ear"
          >
            <path d="M70 44c-4-16 2-26 12-28 6 10 2 22-6 31z" />
            <path d="M90 44c4-16-2-26-12-28-6 10-2 22 6 31z" />
          </g>
          <g v-else-if="skin.ears === 'round'">
            <circle
              cx="52"
              cy="50"
              r="16"
              :fill="skin.ear"
            />
            <circle
              cx="108"
              cy="50"
              r="16"
              :fill="skin.ear"
            />
          </g>
          <g v-else-if="skin.ears === 'cat'">
            <path
              d="M46 56 48 22l26 20z"
              :fill="skin.ear"
            />
            <path
              d="M114 56 112 22 86 42z"
              :fill="skin.ear"
            />
            <path
              d="M53 50l2-15 12 10z"
              :fill="skin.cheek"
              opacity="0.55"
            />
            <path
              d="M107 50l-2-15-12 10z"
              :fill="skin.cheek"
              opacity="0.55"
            />
          </g>
          <g v-else-if="skin.ears === 'rabbit'">
            <ellipse
              cx="56"
              cy="40"
              rx="12"
              ry="28"
              :fill="skin.ear"
            />
            <ellipse
              cx="104"
              cy="40"
              rx="12"
              ry="28"
              :fill="skin.ear"
            />
            <ellipse
              cx="56"
              cy="42"
              rx="6"
              ry="18"
              :fill="skin.cheek"
              opacity="0.55"
            />
            <ellipse
              cx="104"
              cy="42"
              rx="6"
              ry="18"
              :fill="skin.cheek"
              opacity="0.55"
            />
          </g>
          <g v-else-if="skin.ears === 'fox'">
            <path
              d="M42 58 50 20l28 22z"
              :fill="skin.ear"
            />
            <path
              d="M118 58 110 20 82 42z"
              :fill="skin.ear"
            />
            <path
              d="M50 50l3-18 13 12z"
              :fill="skin.belly"
              opacity="0.8"
            />
            <path
              d="M110 50l-3-18-13 12z"
              :fill="skin.belly"
              opacity="0.8"
            />
          </g>
          <g v-else-if="skin.ears === 'panda'">
            <circle
              cx="50"
              cy="48"
              r="16"
              :fill="skin.ear"
            />
            <circle
              cx="110"
              cy="48"
              r="16"
              :fill="skin.ear"
            />
          </g>

          <ellipse
            cx="80"
            cy="124"
            rx="47"
            ry="42"
            :fill="skin.body"
          />
          <ellipse
            cx="80"
            cy="134"
            rx="29"
            ry="26"
            :fill="skin.belly"
          />
          <circle
            cx="80"
            cy="80"
            r="42"
            :fill="skin.body"
          />
          <circle
            cx="80"
            cy="80"
            r="42"
            :fill="skin.bodyDark"
            opacity="0.16"
          />

          <circle
            v-if="skin.ears === 'helmet'"
            cx="80"
            cy="80"
            r="47"
            fill="none"
            :stroke="skin.accent"
            stroke-width="4"
            opacity="0.8"
          />
          <circle
            v-if="skin.ears === 'helmet'"
            cx="118"
            cy="58"
            r="6"
            :fill="skin.belly"
            opacity="0.85"
          />

          <g class="pet-eyes">
            <ellipse
              cx="65"
              cy="78"
              rx="5"
              ry="6.5"
              fill="#2b2f36"
            />
            <ellipse
              cx="95"
              cy="78"
              rx="5"
              ry="6.5"
              fill="#2b2f36"
            />
            <circle
              cx="66.8"
              cy="75.8"
              r="1.8"
              fill="#ffffff"
            />
            <circle
              cx="96.8"
              cy="75.8"
              r="1.8"
              fill="#ffffff"
            />
          </g>
          <ellipse
            cx="54"
            cy="92"
            rx="7"
            ry="4"
            :fill="skin.cheek"
            opacity="0.5"
          />
          <ellipse
            cx="106"
            cy="92"
            rx="7"
            ry="4"
            :fill="skin.cheek"
            opacity="0.5"
          />
          <path
            d="M73 94c2 4 5 5 7 5s5-1 7-5"
            fill="none"
            stroke="#3a3f47"
            stroke-width="2.4"
            stroke-linecap="round"
          />

          <circle
            cx="120"
            cy="142"
            r="17"
            :fill="skin.belly"
            :stroke="skin.accent"
            stroke-width="2"
          />
          <text
            x="120"
            y="148"
            text-anchor="middle"
            font-size="17"
          >{{ skin.emoji }}</text>
        </svg>
      </button>

      <div class="pet-name">
        <span>{{ skin.name }}</span>
        <span
          class="pet-state"
          :class="`state-${mood}`"
        >{{ MOOD_LABEL[mood] }}</span>
      </div>
    </div>

    <div
      v-if="panelOpen"
      class="skin-panel"
      @click.stop
    >
      <header>
        <strong>换一套皮肤</strong>
        <div class="panel-tools">
          <button
            type="button"
            class="panel-mute"
            :aria-pressed="muted"
            @click="toggleMute"
          >
            {{ muted ? '开启提醒' : '静音提醒' }}
          </button>
          <span>右键收起</span>
        </div>
      </header>
      <div class="skin-grid">
        <button
          v-for="item in PET_SKINS"
          :key="item.id"
          type="button"
          class="skin-chip"
          :class="{ active: item.id === skin.id }"
          :aria-pressed="item.id === skin.id"
          @click="chooseSkin(item.id)"
        >
          <span
            class="skin-dot"
            :style="{ background: item.body, borderColor: item.ear }"
          />
          <span>{{ item.name }}</span>
        </button>
      </div>
      <p class="panel-note">
        {{ skin.tagline }}
      </p>
    </div>

    <p class="pet-hint">
      左键呼出工作台 · 右键换皮肤与静音
    </p>
  </div>
</template>

<script setup lang="ts">
  import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
  import { useRoute, useRouter } from 'vue-router'
  import CommanderFigure from '@/components/pet/CommanderFigure.vue'
  import {
    PET_SKINS,
    normalizePetSkin,
    petSkinById,
    readStoredPetSkin,
    writeStoredPetSkin,
    type PetSkinId,
  } from '@/utils/petSkins'
  import {
    hideDesktopPet,
    isDesktopShell,
    listenPetSkinChange,
    setDesktopPetSkin,
    showDesktopMainWindow,
  } from '@/utils/desktop'
  import {
    MOOD_LABEL,
    greetingFor,
    petMuted,
    readStoredPetScene,
    rememberPetMood,
    rememberPetScene,
    sceneTip,
    setPetMuted,
    subscribePetEvents,
    type PetEvent,
    type PetMood,
    type PetScene,
  } from '@/utils/petCopilot'

  const route = useRoute()
  const router = useRouter()
  const panelOpen = ref(false)
  const skinId = ref<PetSkinId>(readStoredPetSkin())
  const skin = computed(() => petSkinById(skinId.value))
  const mood = ref<PetMood>('idle')
  const scene = ref<PetScene>(readStoredPetScene())
  const greeting = ref(greetingFor())
  const muted = ref(petMuted())
  const speech = ref('')

  const TIPS = [
    '今天想先做哪件事？',
    '右键可以给我换衣服',
    '⌘/Ctrl + Shift + Space 呼出工作台',
    '我会安静待在角落，不抢你的焦点',
    '拖住顶部小点就能换位置',
  ]
  const tipIndex = ref(0)
  const tip = computed(() => {
    if (speech.value) return speech.value
    if (scene.value !== 'general') return sceneTip(scene.value)
    return TIPS[tipIndex.value] ?? TIPS[0]
  })

  let tipTimer: number | null = null
  let speechTimer: number | null = null
  let moodTimer: number | null = null
  let greetingTimer: number | null = null
  let idleTimer: number | null = null
  let unlistenSkin: (() => void) | null = null
  let unlistenCopilot: (() => void) | null = null
  let lastActivity = Date.now()

  function applySkin(next: PetSkinId): void {
    skinId.value = normalizePetSkin(next)
    writeStoredPetSkin(skinId.value)
  }

  async function chooseSkin(next: PetSkinId): Promise<void> {
    applySkin(next)
    if (isDesktopShell()) {
      await setDesktopPetSkin(skinId.value)
    }
  }

  function markActive(): void {
    lastActivity = Date.now()
    if (mood.value === 'sleepy') mood.value = 'idle'
  }

  function showSpeech(text: string): void {
    if (muted.value || !text.trim()) return
    speech.value = text.trim().slice(0, 60)
    if (speechTimer !== null) window.clearTimeout(speechTimer)
    speechTimer = window.setTimeout(() => {
      speech.value = ''
      speechTimer = null
    }, 7000)
  }

  function handlePetEvent(event: PetEvent): void {
    if (event.type === 'scene' && event.scene) {
      scene.value = event.scene
      rememberPetScene(event.scene)
      showSpeech(sceneTip(event.scene))
      markActive()
      return
    }
    if (event.type === 'say' && event.text) {
      showSpeech(event.text)
      markActive()
      return
    }
    if (event.type !== 'mood' || !event.mood) return
    mood.value = event.mood
    rememberPetMood(event.mood)
    markActive()
    if (event.text) showSpeech(event.text)
    if (moodTimer !== null) window.clearTimeout(moodTimer)
    if (event.mood === 'happy' || event.mood === 'worried') {
      moodTimer = window.setTimeout(() => {
        mood.value = 'idle'
        moodTimer = null
      }, 8000)
    }
  }

  function toggleMute(): void {
    muted.value = !muted.value
    setPetMuted(muted.value)
    if (muted.value) {
      speech.value = ''
      if (speechTimer !== null) window.clearTimeout(speechTimer)
      speechTimer = null
    } else {
      showSpeech('好，我继续提醒你。')
    }
  }

  function togglePanel(): void {
    panelOpen.value = !panelOpen.value
  }

  async function openMain(): Promise<void> {
    if (isDesktopShell()) {
      try {
        await showDesktopMainWindow()
        return
      } catch {
        /* 引擎未就绪时退回浏览器跳转 */
      }
    }
    void router.push('/')
  }

  async function closePet(): Promise<void> {
    if (isDesktopShell()) {
      try {
        await hideDesktopPet()
        return
      } catch {
        /* 忽略：浏览器预览只收起面板 */
      }
    }
    panelOpen.value = false
  }

  onMounted(async () => {
    document.documentElement.classList.add('pet-mode')
    const fromQuery = route.query.skin
    if (typeof fromQuery === 'string' && fromQuery) {
      applySkin(fromQuery as PetSkinId)
    }
    scene.value = readStoredPetScene()
    greeting.value = greetingFor()
    muted.value = petMuted()
    unlistenSkin = await listenPetSkinChange(next => applySkin(next as PetSkinId))
    unlistenCopilot = subscribePetEvents(handlePetEvent)
    window.addEventListener('pointermove', markActive, { passive: true })
    tipTimer = window.setInterval(() => {
      tipIndex.value = (tipIndex.value + 1) % TIPS.length
    }, 9000)
    greetingTimer = window.setInterval(() => {
      greeting.value = greetingFor()
    }, 15 * 60 * 1000)
    idleTimer = window.setInterval(() => {
      if (mood.value === 'idle' && Date.now() - lastActivity > 5 * 60 * 1000) {
        mood.value = 'sleepy'
      }
    }, 60 * 1000)
  })

  onBeforeUnmount(() => {
    document.documentElement.classList.remove('pet-mode')
    if (tipTimer !== null) window.clearInterval(tipTimer)
    if (speechTimer !== null) window.clearTimeout(speechTimer)
    if (moodTimer !== null) window.clearTimeout(moodTimer)
    if (greetingTimer !== null) window.clearInterval(greetingTimer)
    if (idleTimer !== null) window.clearInterval(idleTimer)
    window.removeEventListener('pointermove', markActive)
    unlistenSkin?.()
    unlistenCopilot?.()
  })
</script>

<style scoped>
  :global(html.pet-mode),
  :global(html.pet-mode body),
  :global(html.pet-mode #app) {
    background: transparent !important;
    overflow: hidden;
  }

  .pet-page {
    position: fixed;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    padding: 8px 8px 10px;
    user-select: none;
    -webkit-user-select: none;
    font-family: var(--ds-font, -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif);
  }

  .pet-greeting {
    max-width: 200px;
    margin-bottom: 3px;
    padding: 0 6px;
    color: rgba(226, 236, 248, 0.78);
    font-size: 9.5px;
    line-height: 1.4;
    text-align: center;
    text-shadow: 0 1px 4px rgba(8, 14, 24, 0.45);
  }

  .pet-bubble {
    max-width: 200px;
    margin-bottom: 4px;
    padding: 7px 11px;
    border-radius: 12px;
    background: rgba(28, 36, 48, 0.92);
    color: #e9f0f8;
    font-size: 11.5px;
    line-height: 1.45;
    text-align: center;
    box-shadow: 0 6px 18px rgba(15, 22, 32, 0.28);
    animation: float-in 0.35s ease-out;
  }

  .pet-stage {
    position: relative;
    width: 186px;
    display: flex;
    flex-direction: column;
    align-items: center;
    animation: bob 3.4s ease-in-out infinite;
  }

  .drag-grip {
    display: flex;
    gap: 3px;
    align-items: center;
    justify-content: center;
    width: 46px;
    height: 14px;
    cursor: grab;
  }

  .drag-grip span {
    width: 4px;
    height: 4px;
    border-radius: 50%;
    background: rgba(210, 220, 232, 0.5);
  }

  .pet-close {
    position: absolute;
    top: 8px;
    right: -4px;
    width: 20px;
    height: 20px;
    border: none;
    border-radius: 50%;
    background: rgba(28, 36, 48, 0.55);
    color: #d7e1ec;
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.18s ease;
  }

  .pet-stage:hover .pet-close {
    opacity: 1;
  }

  .pet-body {
    width: 150px;
    height: 166px;
    padding: 0;
    border: none;
    background: transparent;
    cursor: pointer;
    filter: drop-shadow(0 12px 14px rgba(20, 30, 44, 0.22));
    transition: transform 0.16s ease;
  }

  .pet-body:active {
    transform: scale(0.96);
  }

  .pet-svg {
    width: 100%;
    height: 100%;
    display: block;
  }

  /* 拟人化小人（田伯）：与圆形桌宠共用舞台尺寸 */
  .pet-figure {
    width: 100%;
    height: 100%;
    display: block;
  }

  .pet-eyes {
    transform-origin: 80px 78px;
    animation: blink 6.4s infinite;
  }

  .pet-name {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 2px;
    padding: 2px 10px;
    border-radius: 999px;
    background: rgba(28, 36, 48, 0.72);
    color: #eef4fb;
    font-size: 11px;
    letter-spacing: 0.04em;
  }

  .pet-state {
    padding: 1px 6px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.1);
    color: #a9bad0;
    font-size: 9.5px;
    letter-spacing: 0;
  }

  .pet-state.state-thinking {
    color: #8ab4ff;
  }

  .pet-state.state-busy {
    color: #ffd27a;
  }

  .pet-state.state-happy {
    color: #7fe0a8;
  }

  .pet-state.state-worried {
    color: #ff9a9a;
  }

  .pet-state.state-sleepy {
    color: #b7a9d8;
  }

  .skin-panel {
    position: absolute;
    left: 8px;
    right: 8px;
    bottom: 40px;
    padding: 10px;
    border: 1px solid rgba(150, 172, 196, 0.28);
    border-radius: 12px;
    background: rgba(24, 31, 42, 0.96);
    box-shadow: 0 16px 36px rgba(8, 14, 24, 0.4);
    backdrop-filter: blur(12px);
  }

  .skin-panel header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }

  .panel-tools {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .panel-mute {
    padding: 2px 8px;
    border: 1px solid rgba(140, 160, 184, 0.3);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.05);
    color: #cfdae7;
    font-size: 10px;
    cursor: pointer;
  }

  .panel-mute:hover {
    border-color: rgba(120, 180, 255, 0.6);
    color: #ffffff;
  }

  .skin-panel strong {
    color: #eaf2fb;
    font-size: 12px;
  }

  .skin-panel header span {
    color: #93a3b6;
    font-size: 10.5px;
  }

  .skin-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 6px;
  }

  .skin-chip {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 8px;
    border: 1px solid rgba(140, 160, 184, 0.22);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.04);
    color: #cfdae7;
    font-size: 11px;
    cursor: pointer;
  }

  .skin-chip:hover,
  .skin-chip.active {
    border-color: rgba(120, 180, 255, 0.65);
    background: rgba(76, 154, 255, 0.16);
    color: #ffffff;
  }

  .skin-dot {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid transparent;
    flex-shrink: 0;
  }

  .panel-note {
    margin: 8px 0 0;
    color: #93a3b6;
    font-size: 10.5px;
    line-height: 1.5;
  }

  .pet-hint {
    margin: 6px 0 0;
    color: rgba(210, 222, 236, 0.62);
    font-size: 9.5px;
    letter-spacing: 0.02em;
  }

  .mood-thinking .pet-stage {
    animation: think 2.4s ease-in-out infinite;
  }

  .mood-busy .pet-stage {
    animation: busy 1.1s ease-in-out infinite;
  }

  .mood-happy .pet-stage {
    animation: hop 0.9s ease-in-out infinite;
  }

  .mood-worried .pet-stage {
    animation: worry 0.5s ease-in-out 3;
  }

  .mood-sleepy .pet-stage {
    animation: sleepy 5.2s ease-in-out infinite;
  }

  .mood-sleepy .pet-eyes {
    animation: none;
    transform: scaleY(0.12);
  }

  @keyframes think {
    0%,
    100% {
      transform: rotate(-1.5deg);
    }
    50% {
      transform: rotate(2.5deg);
    }
  }

  @keyframes busy {
    0%,
    100% {
      transform: translateY(0);
    }
    50% {
      transform: translateY(-8px);
    }
  }

  @keyframes hop {
    0%,
    100% {
      transform: translateY(0) scale(1);
    }
    40% {
      transform: translateY(-12px) scale(1.03);
    }
  }

  @keyframes worry {
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

  @keyframes sleepy {
    0%,
    100% {
      transform: translateY(0) rotate(-1deg);
    }
    50% {
      transform: translateY(2px) rotate(1deg);
    }
  }

  @keyframes bob {
    0%,
    100% {
      transform: translateY(0);
    }
    50% {
      transform: translateY(-5px);
    }
  }

  @keyframes blink {
    0%,
    92%,
    100% {
      transform: scaleY(1);
    }
    95% {
      transform: scaleY(0.08);
    }
  }

  @keyframes float-in {
    from {
      opacity: 0;
      transform: translateY(4px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .pet-stage,
    .pet-eyes,
    .pet-bubble,
    .mood-thinking .pet-stage,
    .mood-busy .pet-stage,
    .mood-happy .pet-stage,
    .mood-worried .pet-stage,
    .mood-sleepy .pet-stage {
      animation: none;
    }
  }
</style>

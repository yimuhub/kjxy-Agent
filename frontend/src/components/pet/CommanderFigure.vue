<template>
  <svg
    class="fig"
    :class="[`mood-${mood}`, `act-${action}`]"
    :width="size"
    :height="Math.round(size * (200 / 160))"
    viewBox="0 0 160 200"
    role="img"
    :aria-label="`${skin.name}：${MOOD_LABEL[mood]}`"
  >
    <ellipse
      class="fig-shadow"
      cx="80"
      cy="192"
      rx="38"
      ry="6"
    />

    <g class="fig-body">
      <!-- 锄头与刨土手：一组，起落都跟着这只手 -->
      <g class="fig-arm-dig">
        <path
          d="M46 104 L28 172"
          :stroke="palette.handle"
          stroke-width="5"
          stroke-linecap="round"
        />
        <path
          d="M18 166q14-9 22 6q-13 7 -22 -6z"
          :fill="palette.metal"
        />
        <path
          d="M40 100h13v34a6.5 6.5 0 0 1-13 0z"
          :fill="palette.shirt"
        />
        <circle
          cx="46.5"
          cy="136"
          r="7.5"
          :fill="palette.skin"
        />
      </g>

      <!-- 砸下去时溅起的土块 -->
      <g class="fig-soil">
        <circle
          cx="24"
          cy="164"
          r="3.6"
          :fill="palette.soil"
        />
        <circle
          cx="34"
          cy="157"
          r="2.6"
          :fill="palette.soilLight"
        />
        <circle
          cx="15"
          cy="154"
          r="2.2"
          :fill="palette.soil"
        />
      </g>

      <!-- 腿与鞋 -->
      <path
        d="M62 142h13v36a6.5 6.5 0 0 1-13 0z"
        :fill="palette.pants"
      />
      <path
        d="M85 142h13v36a6.5 6.5 0 0 1-13 0z"
        :fill="palette.pants"
      />
      <ellipse
        cx="67"
        cy="182"
        rx="13"
        ry="6.5"
        :fill="palette.shoe"
      />
      <ellipse
        cx="93"
        cy="182"
        rx="13"
        ry="6.5"
        :fill="palette.shoe"
      />

      <!-- 上身：圆滚滚的短袖 + 背带工装 -->
      <rect
        x="52"
        y="96"
        width="56"
        height="52"
        rx="22"
        :fill="palette.shirt"
      />
      <rect
        x="56"
        y="116"
        width="48"
        height="34"
        rx="16"
        :fill="palette.pants"
      />
      <path
        d="M64 98h7v20h-7z"
        :fill="palette.pants"
      />
      <path
        d="M89 98h7v20h-7z"
        :fill="palette.pants"
      />
      <circle
        cx="67.5"
        cy="119"
        r="2.8"
        :fill="palette.button"
      />
      <circle
        cx="92.5"
        cy="119"
        r="2.8"
        :fill="palette.button"
      />
      <rect
        x="70"
        y="126"
        width="20"
        height="13"
        rx="4"
        :fill="palette.pantsDark"
      />

      <!-- 头：Q 版大头，脸占满 -->
      <g class="fig-head">
        <rect
          x="73"
          y="88"
          width="14"
          height="12"
          rx="6"
          :fill="palette.skinDark"
        />
        <circle
          cx="49"
          cy="66"
          r="6"
          :fill="palette.skinDark"
        />
        <circle
          cx="111"
          cy="66"
          r="6"
          :fill="palette.skinDark"
        />
        <circle
          cx="80"
          cy="62"
          r="31"
          :fill="palette.skin"
        />

        <g class="fig-brows">
          <path
            d="M62 49q6-4 12-1"
            :stroke="palette.hair"
            stroke-width="2.6"
            stroke-linecap="round"
            fill="none"
          />
          <path
            d="M86 48q6-3 12 1"
            :stroke="palette.hair"
            stroke-width="2.6"
            stroke-linecap="round"
            fill="none"
          />
        </g>

        <!-- 大眼睛 + 高光：可爱主要靠这两颗 -->
        <g class="fig-eyes">
          <ellipse
            cx="69"
            cy="61"
            rx="4.8"
            ry="6"
            :fill="palette.line"
          />
          <ellipse
            cx="91"
            cy="61"
            rx="4.8"
            ry="6"
            :fill="palette.line"
          />
          <circle
            cx="70.8"
            cy="59"
            r="1.8"
            fill="#ffffff"
          />
          <circle
            cx="92.8"
            cy="59"
            r="1.8"
            fill="#ffffff"
          />
        </g>

        <ellipse
          cx="58"
          cy="71"
          rx="6"
          ry="3.8"
          :fill="skin.cheek"
          opacity="0.62"
        />
        <ellipse
          cx="102"
          cy="71"
          rx="6"
          ry="3.8"
          :fill="skin.cheek"
          opacity="0.62"
        />
        <path
          d="M78 68q2 3 4 0"
          :stroke="palette.skinDark"
          stroke-width="2"
          stroke-linecap="round"
          fill="none"
        />

        <!-- 白胡子：农民伯伯的标志，嘴角往上才显得慈祥 -->
        <path
          d="M67 76q13 8 26 0q-3 9-13 9q-10 0-13-9z"
          :fill="palette.beard"
        />
        <path
          v-if="mood === 'happy'"
          class="fig-mouth"
          d="M71 75q9 11 18 0z"
          :fill="palette.mouth"
        />
        <path
          v-else-if="mood === 'worried'"
          class="fig-mouth"
          d="M73 78q7-4 14 0"
          :stroke="palette.mouth"
          stroke-width="2.6"
          stroke-linecap="round"
          fill="none"
        />
        <path
          v-else-if="mood === 'sleepy'"
          class="fig-mouth"
          d="M75 77h10"
          :stroke="palette.mouth"
          stroke-width="2.6"
          stroke-linecap="round"
          fill="none"
        />
        <path
          v-else
          class="fig-mouth"
          d="M73 74q7 6 14 0"
          :stroke="palette.mouth"
          stroke-width="2.6"
          stroke-linecap="round"
          fill="none"
        />

        <!-- 草帽：扇风时整顶抬起来摇 -->
        <g class="fig-hat">
          <path
            d="M58 42C60 20 100 20 102 42z"
            :fill="palette.hat"
          />
          <rect
            x="58"
            y="35"
            width="44"
            height="6"
            rx="3"
            :fill="palette.band"
          />
          <ellipse
            cx="80"
            cy="43"
            rx="44"
            ry="11"
            :fill="palette.hat"
          />
          <path
            d="M38 43q42 12 84 0"
            :stroke="palette.hatDark"
            stroke-width="1.8"
            fill="none"
            opacity="0.55"
          />
        </g>
      </g>

      <!-- 擦汗那只手：抬到额头，毛巾先亮再动 -->
      <g class="fig-arm-wipe">
        <path
          d="M101 100h13v36a6.5 6.5 0 0 1-13 0z"
          :fill="palette.shirt"
        />
        <circle
          cx="107.5"
          cy="138"
          r="7.5"
          :fill="palette.skin"
        />
        <rect
          class="fig-towel"
          x="99"
          y="134"
          width="17"
          height="12"
          rx="4"
          :fill="palette.towel"
        />
      </g>

      <!-- 汗珠：干活与担心时甩出来 -->
      <g class="fig-sweat">
        <path
          d="M50 52q4 7 0 10q-4-3 0-10z"
          :fill="palette.sweat"
        />
        <path
          d="M110 54q4 7 0 10q-4-3 0-10z"
          :fill="palette.sweat"
        />
        <path
          d="M118 72q3 5 0 7q-3-2 0-7z"
          :fill="palette.sweat"
        />
      </g>

      <!-- 扇草帽时的风 -->
      <g class="fig-wind">
        <path
          d="M114 84q11-4 17 2"
          :stroke="palette.sweat"
          stroke-width="2.6"
          stroke-linecap="round"
          fill="none"
        />
        <path
          d="M118 94q10-3 15 2"
          :stroke="palette.sweat"
          stroke-width="2"
          stroke-linecap="round"
          fill="none"
        />
      </g>

      <!-- 打盹时的 z -->
      <g class="fig-zzz">
        <text
          x="112"
          y="38"
          :fill="palette.line"
          font-size="15"
        >z</text>
        <text
          x="126"
          y="24"
          :fill="palette.line"
          font-size="11"
        >z</text>
      </g>
    </g>
  </svg>
</template>

<script setup lang="ts">
  /**
   * 拟人化农民小人（v3.52 重画）：Q 版大头、圆眼带高光、白胡子的卡通农民伯伯。
   *
   * 两层驱动，互不打架：
   * - mood 来自真实事件（忙碌/思考/开心/担心/打盹），优先级最高；
   * - action 是发呆时的自我循环（刨土 → 擦汗 → 扇草帽 → 点头），
   *   让「什么都不干」的时候也有桌面宠物的动态感，而不是站桩。
   */
  import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
  import { MOOD_LABEL, type PetMood } from '@/utils/petCopilot'
  import type { PetSkin } from '@/utils/petSkins'

  type FigureAction = 'idle' | 'dig' | 'wipe' | 'fan' | 'nod'

  /** 发呆循环：每个动作演多久（ms），够看清又不会腻 */
  const ACTION_HOLD: Record<Exclude<FigureAction, 'idle'>, number> = {
    dig: 2600,
    wipe: 2200,
    fan: 2400,
    nod: 1500,
  }
  const ACTION_ORDER: Exclude<FigureAction, 'idle'>[] = ['dig', 'wipe', 'fan', 'nod']
  /** 发呆多久才开始自己找活干 */
  const IDLE_BEFORE_ACTION = 1800

  const props = withDefaults(
    defineProps<{
      skin: PetSkin
      mood?: PetMood
      /** 渲染宽度（px），高度按 200/160 比例自适应 */
      size?: number
    }>(),
    { mood: 'idle', size: 120 },
  )

  const action = ref<FigureAction>('idle')
  let timer: ReturnType<typeof setTimeout> | null = null
  let step = 0

  function clearTimer(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  function advance(): void {
    if (props.mood !== 'idle') {
      action.value = 'idle'
      return
    }
    const name = ACTION_ORDER[step % ACTION_ORDER.length]
    step += 1
    action.value = name
    timer = setTimeout(advance, ACTION_HOLD[name])
  }

  /** 一有真实情绪就让位：动效不能和 mood 抢同一个部件 */
  function restart(): void {
    clearTimer()
    action.value = 'idle'
    if (props.mood === 'idle') {
      timer = setTimeout(advance, IDLE_BEFORE_ACTION)
    }
  }

  watch(() => props.mood, restart)
  onMounted(restart)
  onBeforeUnmount(clearTimer)

  /** 人形皮肤自带 human 配色；缺省时回落到皮肤主色，保证 8 套皮肤都能画 */
  const palette = computed(() => {
    const human = props.skin.human
    const band = human?.band ?? props.skin.accent
    return {
      skin: human?.skin ?? props.skin.body,
      skinDark: human?.skinDark ?? props.skin.bodyDark,
      shirt: human?.shirt ?? props.skin.belly,
      pants: human?.pants ?? props.skin.bodyDark,
      pantsDark: human?.pantsDark ?? props.skin.accent,
      hat: human?.hat ?? props.skin.ear,
      hatDark: human?.hatDark ?? props.skin.accent,
      band,
      shoe: human?.shoe ?? props.skin.accent,
      handle: human?.handle ?? props.skin.accent,
      metal: human?.metal ?? props.skin.bodyDark,
      towel: human?.towel ?? props.skin.belly,
      hair: human?.hair ?? props.skin.accent,
      mouth: human?.mouth ?? props.skin.accent,
      sweat: human?.sweat ?? '#bfe3ff',
      line: human?.line ?? '#2f2a26',
      // 派生色：不新增皮肤字段，直接从已有配色取
      beard: '#f4f1ea',
      button: band,
      soil: '#8a6242',
      soilLight: '#a97b53',
    }
  })
</script>

<style scoped>
  .fig {
    display: block;
    overflow: visible;
  }

  .fig-shadow {
    fill: rgba(0, 0, 0, 0.28);
  }

  /*
   * 动画锚点统一用 view-box + viewBox 坐标（不用 fill-box 百分比）：
   * 部件飞走几乎都源于默认 transform-origin。
   */
  .fig-body,
  .fig-head,
  .fig-hat,
  .fig-arm-wipe,
  .fig-arm-dig,
  .fig-eyes,
  .fig-sweat,
  .fig-soil,
  .fig-wind,
  .fig-zzz,
  .fig-towel {
    transform-box: view-box;
  }

  .fig-body {
    transform-origin: 80px 192px;
    animation: fig-breathe 3.6s ease-in-out infinite;
  }

  .fig-head {
    transform-origin: 80px 92px;
  }

  .fig-eyes {
    transform-origin: 80px 61px;
    animation: fig-blink 4.4s infinite;
  }

  /* 肩为轴：刨土手 (46.5,100)、擦汗手 (107.5,100) */
  .fig-arm-dig {
    transform-origin: 46.5px 100px;
  }

  .fig-arm-wipe {
    transform-origin: 107.5px 100px;
  }

  .fig-hat {
    transform-origin: 80px 43px;
  }

  .fig-zzz {
    transform-origin: 118px 30px;
  }

  .fig-towel,
  .fig-sweat,
  .fig-soil,
  .fig-wind,
  .fig-zzz {
    opacity: 0;
  }

  /* ---------- 发呆自循环：挥锄刨土 ---------- */
  .act-dig .fig-arm-dig {
    animation: fig-dig 1.3s ease-in-out 2;
  }

  .act-dig .fig-soil {
    animation: fig-soil 1.3s ease-out 2;
  }

  .act-dig .fig-body {
    animation: fig-dig-lean 1.3s ease-in-out 2;
  }

  /* ---------- 发呆自循环：抬袖擦汗 ---------- */
  .act-wipe .fig-arm-wipe {
    animation: fig-wipe 1.1s ease-in-out 2;
  }

  .act-wipe .fig-towel {
    animation: fig-towel-show 2.2s ease-in-out 1;
  }

  .act-wipe .fig-sweat {
    animation: fig-sweat 1.1s ease-in-out 2;
  }

  /* ---------- 发呆自循环：摘草帽扇风 ---------- */
  .act-fan .fig-hat {
    animation: fig-fan 0.9s ease-in-out 2.5;
  }

  .act-fan .fig-wind {
    animation: fig-wind 0.9s ease-out 2.5;
  }

  .act-fan .fig-head {
    animation: fig-head-tilt 0.9s ease-in-out 2.5;
  }

  /* ---------- 发呆自循环：满意点头 ---------- */
  .act-nod .fig-head {
    animation: fig-nod 0.75s ease-in-out 2;
  }

  .act-nod .fig-eyes {
    animation: fig-happy-eye 0.75s ease-in-out 2, fig-blink 4.4s infinite;
  }

  /* ---------- 真实情绪优先 ---------- */
  .mood-busy .fig-arm-dig {
    animation: fig-dig 1.15s ease-in-out infinite;
  }

  .mood-busy .fig-soil {
    animation: fig-soil 1.15s ease-out infinite;
  }

  .mood-busy .fig-arm-wipe {
    animation: fig-wipe 2.3s ease-in-out infinite;
  }

  .mood-busy .fig-towel {
    animation: fig-towel-show 2.3s ease-in-out infinite;
  }

  .mood-busy .fig-sweat {
    animation: fig-sweat 1.15s ease-in-out infinite;
  }

  /* 思考：帽子轻晃，眼神往旁边瞟 */
  .mood-thinking .fig-hat {
    animation: fig-hat-tilt 2.4s ease-in-out infinite;
  }

  .mood-thinking .fig-eyes {
    animation: fig-look 2.4s ease-in-out infinite, fig-blink 4.4s infinite;
  }

  /* 开心：蹦一下，两只胳膊一起摆 */
  .mood-happy .fig-body {
    animation: fig-hop 0.72s ease-in-out 3;
  }

  .mood-happy .fig-arm-dig,
  .mood-happy .fig-arm-wipe {
    animation: fig-wave 0.72s ease-in-out 3;
  }

  /* 担心：发抖 + 一串汗 */
  .mood-worried .fig-body {
    animation: fig-shake 0.9s ease-in-out 2;
  }

  .mood-worried .fig-sweat {
    animation: fig-sweat 1.1s ease-in-out infinite;
  }

  /* 打盹：闭眼冒 z */
  .mood-sleepy .fig-eyes {
    animation: none;
    transform: scaleY(0.12);
  }

  .mood-sleepy .fig-body {
    animation: fig-breathe 5.2s ease-in-out infinite;
  }

  .mood-sleepy .fig-zzz {
    animation: fig-zzz 2.8s ease-out infinite;
  }

  @keyframes fig-breathe {
    0%,
    100% {
      transform: translateY(0);
    }
    50% {
      transform: translateY(-3px);
    }
  }

  @keyframes fig-blink {
    0%,
    92%,
    100% {
      transform: scaleY(1);
    }
    95% {
      transform: scaleY(0.12);
    }
  }

  /* 举起锄头、砸下去、再抬回来 */
  @keyframes fig-dig {
    0%,
    100% {
      transform: rotate(0deg);
    }
    30% {
      transform: rotate(-26deg);
    }
    58% {
      transform: rotate(15deg);
    }
    78% {
      transform: rotate(4deg);
    }
  }

  @keyframes fig-dig-lean {
    0%,
    100% {
      transform: translateY(0) rotate(0deg);
    }
    30% {
      transform: translateY(-2px) rotate(-2deg);
    }
    58% {
      transform: translateY(1px) rotate(3deg);
    }
  }

  @keyframes fig-soil {
    0%,
    45%,
    100% {
      opacity: 0;
      transform: translateY(0) scale(0.6);
    }
    62% {
      opacity: 0.95;
      transform: translateY(-10px) scale(1);
    }
    85% {
      opacity: 0;
      transform: translateY(-2px) scale(0.8);
    }
  }

  @keyframes fig-wipe {
    0%,
    100% {
      transform: rotate(0deg);
    }
    35% {
      transform: rotate(146deg);
    }
    62% {
      transform: rotate(164deg);
    }
  }

  @keyframes fig-towel-show {
    0%,
    15%,
    95%,
    100% {
      opacity: 0;
    }
    30%,
    80% {
      opacity: 1;
    }
  }

  @keyframes fig-sweat {
    0%,
    100% {
      opacity: 0;
    }
    30%,
    70% {
      opacity: 0.95;
    }
  }

  /* 草帽抬起来左右摇：整顶离开头顶一点，像真在扇 */
  @keyframes fig-fan {
    0%,
    100% {
      transform: translateY(0) rotate(0deg);
    }
    25% {
      transform: translateY(-10px) rotate(-17deg);
    }
    55% {
      transform: translateY(-8px) rotate(13deg);
    }
    80% {
      transform: translateY(-3px) rotate(-6deg);
    }
  }

  @keyframes fig-wind {
    0% {
      opacity: 0;
      transform: translate(0, 0);
    }
    35%,
    65% {
      opacity: 0.85;
    }
    100% {
      opacity: 0;
      transform: translate(6px, -4px);
    }
  }

  @keyframes fig-head-tilt {
    0%,
    100% {
      transform: rotate(0deg);
    }
    50% {
      transform: rotate(3deg);
    }
  }

  @keyframes fig-nod {
    0%,
    100% {
      transform: translateY(0) rotate(0deg);
    }
    35% {
      transform: translateY(4px) rotate(5deg);
    }
    70% {
      transform: translateY(-1px) rotate(-2deg);
    }
  }

  @keyframes fig-happy-eye {
    0%,
    100% {
      transform: scaleY(1);
    }
    40% {
      transform: scaleY(0.45);
    }
  }

  @keyframes fig-hat-tilt {
    0%,
    100% {
      transform: rotate(0deg);
    }
    50% {
      transform: rotate(-4deg) translateY(-1px);
    }
  }

  @keyframes fig-look {
    0%,
    100% {
      transform: translateX(0);
    }
    50% {
      transform: translateX(-3px);
    }
  }

  @keyframes fig-hop {
    0%,
    100% {
      transform: translateY(0) scale(1);
    }
    40% {
      transform: translateY(-11px) scale(1.03);
    }
  }

  @keyframes fig-wave {
    0%,
    100% {
      transform: rotate(0deg);
    }
    50% {
      transform: rotate(-28deg);
    }
  }

  @keyframes fig-shake {
    0%,
    100% {
      transform: translateX(0);
    }
    25% {
      transform: translateX(-3px) rotate(-1deg);
    }
    75% {
      transform: translateX(3px) rotate(1deg);
    }
  }

  @keyframes fig-zzz {
    0% {
      opacity: 0;
      transform: translateY(4px) scale(0.8);
    }
    35%,
    70% {
      opacity: 0.9;
    }
    100% {
      opacity: 0;
      transform: translateY(-12px) scale(1.05);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .fig-body,
    .fig-head,
    .fig-eyes,
    .fig-hat,
    .fig-arm-dig,
    .fig-arm-wipe,
    .fig-towel,
    .fig-sweat,
    .fig-soil,
    .fig-wind,
    .fig-zzz {
      animation: none !important;
    }

    /* 静态也要能看出「正在刨土 / 擦汗 / 扇帽子」 */
    .mood-busy .fig-arm-wipe,
    .act-wipe .fig-arm-wipe {
      transform: rotate(152deg);
    }

    .mood-busy .fig-arm-dig,
    .act-dig .fig-arm-dig {
      transform: rotate(13deg);
    }

    .mood-happy .fig-arm-dig,
    .mood-happy .fig-arm-wipe {
      transform: rotate(-22deg);
    }

    .act-fan .fig-hat {
      transform: translateY(-9px) rotate(-13deg);
    }

    .mood-busy .fig-towel,
    .act-wipe .fig-towel,
    .mood-busy .fig-sweat,
    .act-wipe .fig-sweat,
    .mood-worried .fig-sweat,
    .mood-sleepy .fig-zzz,
    .act-dig .fig-soil,
    .act-fan .fig-wind {
      opacity: 1;
    }

    .mood-sleepy .fig-eyes {
      transform: scaleY(0.12);
    }
  }
</style>

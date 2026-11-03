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
    <!-- 地面影子 -->
    <ellipse
      class="fig-shadow"
      cx="80"
      cy="193"
      rx="44"
      ry="7"
    />

    <!-- 土垄与禾苗：给小人一个"站在田里"的场景感 -->
    <g class="fig-ground">
      <ellipse
        cx="80"
        cy="190"
        rx="58"
        ry="7"
        :fill="palette.soilLight"
        opacity="0.55"
      />
      <ellipse
        cx="46"
        cy="192"
        rx="22"
        ry="5"
        :fill="palette.soil"
        opacity="0.8"
      />
      <g class="fig-sprout">
        <path
          d="M24 190q0-7 5-11"
          :stroke="palette.leaf"
          stroke-width="2.2"
          stroke-linecap="round"
          fill="none"
        />
        <ellipse
          cx="27.5"
          cy="177"
          rx="4.6"
          ry="2.8"
          :fill="palette.leaf"
          transform="rotate(-28 27.5 177)"
        />
        <ellipse
          cx="22"
          cy="180"
          rx="4.2"
          ry="2.6"
          :fill="palette.leaf"
          opacity="0.8"
          transform="rotate(24 22 180)"
        />
      </g>
      <g
        class="fig-sprout"
        transform="translate(108 2)"
      >
        <path
          d="M24 190q1-6 5-10"
          :stroke="palette.leaf"
          stroke-width="2.2"
          stroke-linecap="round"
          fill="none"
        />
        <ellipse
          cx="27.5"
          cy="178"
          rx="4.4"
          ry="2.7"
          :fill="palette.leaf"
          transform="rotate(-24 27.5 178)"
        />
      </g>
    </g>

    <g class="fig-body">
      <!-- 双脚：圆头布鞋 -->
      <g class="fig-shoes">
        <rect
          x="56"
          y="176"
          width="21"
          height="13"
          rx="6.5"
          :fill="palette.shoe"
        />
        <rect
          x="83"
          y="176"
          width="21"
          height="13"
          rx="6.5"
          :fill="palette.shoe"
        />
        <rect
          x="56"
          y="184"
          width="21"
          height="5"
          rx="2.5"
          :fill="palette.shoe"
          opacity="0.55"
        />
        <rect
          x="83"
          y="184"
          width="21"
          height="5"
          rx="2.5"
          :fill="palette.shoe"
          opacity="0.55"
        />
      </g>
      <!-- 裤腿 -->
      <rect
        x="59"
        y="160"
        width="17"
        height="20"
        rx="5"
        :fill="palette.pants"
      />
      <rect
        x="84"
        y="160"
        width="17"
        height="20"
        rx="5"
        :fill="palette.pants"
      />
      <!-- 卷裤脚 -->
      <rect
        x="57.5"
        y="173"
        width="20"
        height="7"
        rx="3.5"
        :fill="palette.pantsDark"
      />
      <rect
        x="82.5"
        y="173"
        width="20"
        height="7"
        rx="3.5"
        :fill="palette.pantsDark"
      />

      <!-- 圆滚滚的背带裤身子 -->
      <path
        d="M52 116q0-10 10-10h36q10 0 10 10v42q0 12-12 12H64q-12 0-12-12z"
        :fill="palette.pants"
      />
      <!-- 裤兜 -->
      <path
        d="M70 148h20v10q0 5-5 5h-10q-5 0-5-5z"
        :fill="palette.pantsDark"
        opacity="0.75"
      />
      <!-- 背带 + 扣子 -->
      <path
        d="M62 108l4 34"
        :stroke="palette.band"
        stroke-width="7"
        stroke-linecap="round"
      />
      <path
        d="M98 108l-4 34"
        :stroke="palette.band"
        stroke-width="7"
        stroke-linecap="round"
      />
      <circle
        cx="64"
        cy="142"
        r="4"
        :fill="palette.metal"
      />
      <circle
        cx="96"
        cy="142"
        r="4"
        :fill="palette.metal"
      />
      <!-- 衬衫肩块与领口 -->
      <path
        d="M50 112q0-14 12-16h36q12 2 12 16v6H50z"
        :fill="palette.shirt"
      />
      <path
        d="M72 96h16v6q-4 4-8 4t-8-4z"
        :fill="palette.skinDark"
        opacity="0.35"
      />

      <!-- 刨土手：袖 + 手 + 锄头（整组绕左肩起落） -->
      <g class="fig-arm-dig">
        <rect
          x="38"
          y="100"
          width="16"
          height="34"
          rx="8"
          :fill="palette.shirt"
        />
        <rect
          x="38"
          y="126"
          width="16"
          height="8"
          rx="4"
          :fill="palette.shirt"
          opacity="0.7"
        />
        <circle
          cx="46"
          cy="138"
          r="8"
          :fill="palette.skin"
        />
        <g class="fig-hoe">
          <rect
            x="42.4"
            y="128"
            width="7.2"
            height="62"
            rx="3.6"
            :fill="palette.handle"
          />
          <path
            d="M32 186q10-10 28-8l-6 10q-12 2-22-2z"
            :fill="palette.metal"
          />
          <path
            d="M36 184q8-6 20-6"
            :stroke="palette.soilLight"
            stroke-width="1.6"
            fill="none"
            opacity="0.8"
          />
        </g>
      </g>
    </g>

    <!-- 头组：大头 + 五官 + 白胡子，擦汗/点头都绕这里转 -->
    <g class="fig-head">
      <!-- 耳朵 -->
      <circle
        cx="34"
        cy="82"
        r="9"
        :fill="palette.skin"
      />
      <circle
        cx="126"
        cy="82"
        r="9"
        :fill="palette.skin"
      />
      <circle
        cx="34"
        cy="82"
        r="4"
        :fill="palette.skinDark"
        opacity="0.5"
      />
      <circle
        cx="126"
        cy="82"
        r="4"
        :fill="palette.skinDark"
        opacity="0.5"
      />
      <!-- 大圆脸 -->
      <circle
        cx="80"
        cy="76"
        r="45"
        :fill="palette.skin"
      />
      <!-- 腮红 -->
      <ellipse
        cx="54"
        cy="88"
        rx="8.5"
        ry="5"
        :fill="palette.blush"
        opacity="0.55"
      />
      <ellipse
        cx="106"
        cy="88"
        rx="8.5"
        ry="5"
        :fill="palette.blush"
        opacity="0.55"
      />
      <!-- 眼睛：白底 + 双高光 -->
      <g class="fig-eyes">
        <circle
          cx="64"
          cy="70"
          r="9"
          fill="#ffffff"
        />
        <circle
          cx="96"
          cy="70"
          r="9"
          fill="#ffffff"
        />
        <circle
          cx="65.5"
          cy="71.5"
          r="4.6"
          :fill="palette.line"
        />
        <circle
          cx="97.5"
          cy="71.5"
          r="4.6"
          :fill="palette.line"
        />
        <circle
          cx="63.6"
          cy="69"
          r="1.7"
          fill="#ffffff"
        />
        <circle
          cx="95.6"
          cy="69"
          r="1.7"
          fill="#ffffff"
        />
      </g>
      <!-- 白眉 -->
      <path
        d="M55 56q9-6 18-1"
        :stroke="palette.beard"
        stroke-width="3.4"
        stroke-linecap="round"
        fill="none"
      />
      <path
        d="M87 55q9-5 18 1"
        :stroke="palette.beard"
        stroke-width="3.4"
        stroke-linecap="round"
        fill="none"
      />
      <!-- 鼻 -->
      <circle
        cx="80"
        cy="82"
        r="5.5"
        :fill="palette.skinDark"
        opacity="0.75"
      />
      <!-- 八字胡 -->
      <path
        d="M80 90q-12-2-19 6q10 3 19-2z"
        :fill="palette.beard"
      />
      <path
        d="M80 90q12-2 19 6q-10 3-19-2z"
        :fill="palette.beard"
      />
      <!-- 笑口 -->
      <path
        class="fig-mouth"
        d="M72 100q8 7 16 0"
        :stroke="palette.mouth"
        stroke-width="2.6"
        stroke-linecap="round"
        fill="none"
      />
      <!-- 山羊白胡团 -->
      <path
        d="M80 98q-13 2-11 12q4 8 11 8t11-8q2-10-11-12z"
        :fill="palette.beard"
      />
      <path
        d="M74 106q6 4 12 0"
        :stroke="palette.skinDark"
        stroke-width="1.4"
        stroke-linecap="round"
        fill="none"
        opacity="0.35"
      />
    </g>

    <!-- 草帽：宽檐 + 圆顶 + 帽带 + 编织纹（扇风时整顶掀起） -->
    <g class="fig-hat">
      <ellipse
        cx="80"
        cy="42"
        rx="48"
        ry="13"
        :fill="palette.hat"
      />
      <path
        d="M40 42q40 13 80 0"
        :stroke="palette.hatDark"
        stroke-width="1.6"
        fill="none"
        opacity="0.5"
      />
      <path
        d="M50 30q30-16 60 0l-3 12q-27-10-54 0z"
        :fill="palette.hat"
      />
      <path
        d="M50 30q30-16 60 0"
        :stroke="palette.hatDark"
        stroke-width="1.6"
        fill="none"
        opacity="0.55"
      />
      <rect
        x="52"
        y="33"
        width="56"
        height="7"
        rx="3.5"
        :fill="palette.band"
      />
      <rect
        x="52"
        y="33"
        width="56"
        height="3"
        rx="1.5"
        fill="#ffffff"
        opacity="0.18"
      />
    </g>

    <!-- 擦汗手：抬袖贴额，白毛巾（带红边） -->
    <g class="fig-arm-wipe">
      <rect
        x="100"
        y="100"
        width="16"
        height="34"
        rx="8"
        :fill="palette.shirt"
      />
      <rect
        x="100"
        y="126"
        width="16"
        height="8"
        rx="4"
        :fill="palette.shirt"
        opacity="0.7"
      />
      <circle
        cx="108"
        cy="138"
        r="8"
        :fill="palette.skin"
      />
      <g class="fig-towel">
        <rect
          x="99"
          y="132"
          width="18"
          height="13"
          rx="4"
          :fill="palette.towel"
        />
        <rect
          x="99"
          y="141"
          width="18"
          height="4"
          rx="2"
          :fill="palette.band"
          opacity="0.85"
        />
      </g>
    </g>

    <!-- 汗珠 -->
    <g class="fig-sweat">
      <path
        d="M50 54q4.4 7.6 0 10.6q-4.4-3 0-10.6z"
        :fill="palette.sweat"
      />
      <path
        d="M111 56q4 7 0 10q-4-3 0-10z"
        :fill="palette.sweat"
      />
      <path
        d="M120 74q3 5 0 7.4q-3-2.4 0-7.4z"
        :fill="palette.sweat"
      />
    </g>

    <!-- 扇帽风线 -->
    <g class="fig-wind">
      <path
        d="M118 82q12-5 19 2"
        :stroke="palette.sweat"
        stroke-width="2.8"
        stroke-linecap="round"
        fill="none"
      />
      <path
        d="M122 93q11-4 16 2"
        :stroke="palette.sweat"
        stroke-width="2"
        stroke-linecap="round"
        fill="none"
      />
    </g>

    <!-- 打盹 z -->
    <g class="fig-zzz">
      <text
        x="110"
        y="36"
        :fill="palette.line"
        font-size="16"
        font-weight="bold"
      >z</text>
      <text
        x="126"
        y="22"
        :fill="palette.line"
        font-size="11"
      >z</text>
    </g>
  </svg>
</template>

<script setup lang="ts">
  /**
   * 拟人化农民小人（v3.56 重画）：Q 版大圆脸、双高光圆眼、白眉八字胡山羊胡、
   * 背带裤布鞋、宽檐草帽，站在有禾苗的田垄上。
   *
   * 两层驱动，互不打架：
   * - mood 来自真实事件（忙碌/思考/开心/担心/打盹），优先级最高；
   * - action 是发呆时的自我循环（刨土 → 擦汗 → 扇草帽 → 点头），
   *   「什么都不干」的时候也有桌面宠物的动态感。
   *
   * v3.56 只重画造型：动画锚点（fig-hat/fig-arm-dig/fig-arm-wipe/…）与
   * transform-origin 坐标保持不变，keyframes 零改动，8 套皮肤字段照常生效。
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
      blush: '#f0a58f',
      leaf: '#69a244',
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
  .fig-towel,
  .fig-ground {
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

  /* 肩为轴：刨土手 (46,100)、擦汗手 (108,100) */
  .fig-arm-dig {
    transform-origin: 46px 100px;
  }

  .fig-arm-wipe {
    transform-origin: 108px 100px;
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

  .act-dig .fig-ground {
    animation: fig-ground-shake 1.3s ease-in-out 2;
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

  .act-fan .fig-arm-wipe {
    /* 另一只手抬起接应，像真的托着帽檐 */
    animation: fig-arm-assist 0.9s ease-in-out 2.5;
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

  /* 刨到土：田垄轻颤一下 */
  @keyframes fig-ground-shake {
    0%,
    100% {
      transform: translateY(0);
    }
    58% {
      transform: translateY(1.2px);
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

  /* 草帽掀起 + 左右摇摆：整顶离开头顶，像真在扇 */
  @keyframes fig-fan {
    0%,
    100% {
      transform: translateY(0) rotate(0deg);
    }
    25% {
      transform: translateY(-12px) rotate(-19deg);
    }
    55% {
      transform: translateY(-9px) rotate(15deg);
    }
    80% {
      transform: translateY(-4px) rotate(-7deg);
    }
  }

  /* 扇风时另一只手抬起托举（不与 busy 擦汗冲突：仅 act-fan 生效） */
  @keyframes fig-arm-assist {
    0%,
    100% {
      transform: rotate(0deg);
    }
    25%,
    70% {
      transform: rotate(-38deg);
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
    .fig-zzz,
    .fig-ground {
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
      transform: translateY(-11px) rotate(-15deg);
    }

    .act-fan .fig-arm-wipe {
      transform: rotate(-32deg);
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

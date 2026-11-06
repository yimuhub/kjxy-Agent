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
      <!-- 双脚：圆头布鞋（椭圆更憨） -->
      <g class="fig-shoes">
        <ellipse
          cx="65"
          cy="181"
          rx="13"
          ry="9"
          :fill="palette.shoe"
        />
        <ellipse
          cx="95"
          cy="181"
          rx="13"
          ry="9"
          :fill="palette.shoe"
        />
        <ellipse
          cx="62"
          cy="178"
          rx="5"
          ry="2.4"
          fill="#ffffff"
          opacity="0.22"
        />
        <ellipse
          cx="92"
          cy="178"
          rx="5"
          ry="2.4"
          fill="#ffffff"
          opacity="0.22"
        />
      </g>
      <!-- 短粗裤腿 + 卷裤脚 -->
      <rect
        x="59"
        y="158"
        width="17"
        height="22"
        rx="7"
        :fill="palette.pants"
      />
      <rect
        x="84"
        y="158"
        width="17"
        height="22"
        rx="7"
        :fill="palette.pants"
      />
      <rect
        x="57.5"
        y="171"
        width="20"
        height="7"
        rx="3.5"
        :fill="palette.pantsDark"
      />
      <rect
        x="82.5"
        y="171"
        width="20"
        height="7"
        rx="3.5"
        :fill="palette.pantsDark"
      />

      <!-- 圆滚滚蛋形身子：上窄下宽，坐墩一样敦实 -->
      <path
        d="M56 122q0-16 13-18h22q13 2 13 18v26q0 18-15 20h-18q-15-2-15-20z"
        :fill="palette.pants"
      />
      <!-- 圆角补丁兜 + 针脚 -->
      <path
        d="M71 146h18v9q0 5-5 5h-8q-5 0-5-5z"
        :fill="palette.pantsDark"
        opacity="0.7"
      />
      <path
        d="M73 150h14"
        :stroke="palette.pantsDark"
        stroke-width="1"
        stroke-dasharray="2 2.4"
        stroke-linecap="round"
        opacity="0.9"
        fill="none"
      />
      <!-- 背带 + 大圆扣 -->
      <path
        d="M63 110l4 32"
        :stroke="palette.band"
        stroke-width="7"
        stroke-linecap="round"
      />
      <path
        d="M97 110l-4 32"
        :stroke="palette.band"
        stroke-width="7"
        stroke-linecap="round"
      />
      <circle
        cx="65"
        cy="142"
        r="4.6"
        :fill="palette.metal"
      />
      <circle
        cx="95"
        cy="142"
        r="4.6"
        :fill="palette.metal"
      />
      <circle
        cx="63.6"
        cy="140.6"
        r="1.4"
        fill="#ffffff"
        opacity="0.8"
      />
      <circle
        cx="93.6"
        cy="140.6"
        r="1.4"
        fill="#ffffff"
        opacity="0.8"
      />
      <!-- 衬衫肩块与圆领口 -->
      <path
        d="M51 114q0-15 13-17h32q13 2 13 17v4H51z"
        :fill="palette.shirt"
      />
      <path
        d="M72 97h16v5q-4 4.4-8 4.4t-8-4.4z"
        :fill="palette.skinDark"
        opacity="0.35"
      />

      <!-- 刨土手：袖 + 手 + 锄头（整组绕左肩起落） -->
      <g class="fig-arm-dig">
        <rect
          x="39"
          y="98"
          width="15"
          height="32"
          rx="7.5"
          :fill="palette.shirt"
        />
        <rect
          x="39"
          y="124"
          width="15"
          height="7"
          rx="3.5"
          :fill="palette.shirt"
          opacity="0.7"
        />
        <circle
          cx="46.5"
          cy="136"
          r="7.5"
          :fill="palette.skin"
        />
        <g class="fig-hoe">
          <rect
            x="42.8"
            y="126"
            width="7"
            height="60"
            rx="3.5"
            :fill="palette.handle"
          />
          <path
            d="M32 184q10-11 28-9l-6 11q-13 2-22-2z"
            :fill="palette.metal"
          />
          <path
            d="M36 182q8-6 20-6"
            :stroke="palette.soilLight"
            stroke-width="1.6"
            fill="none"
            opacity="0.8"
          />
        </g>
      </g>
    </g>

    <!-- 头组：Q 版大头 + 水汪汪大眼 + 白胡子，擦汗/点头都绕这里转 -->
    <g class="fig-head">
      <!-- 耳朵 -->
      <circle
        cx="31"
        cy="80"
        r="9"
        :fill="palette.skin"
      />
      <circle
        cx="129"
        cy="80"
        r="9"
        :fill="palette.skin"
      />
      <circle
        cx="31"
        cy="80"
        r="4"
        :fill="palette.skinDark"
        opacity="0.5"
      />
      <circle
        cx="129"
        cy="80"
        r="4"
        :fill="palette.skinDark"
        opacity="0.5"
      />
      <!-- 大圆脸 -->
      <circle
        cx="80"
        cy="74"
        r="48"
        :fill="palette.skin"
      />
      <!-- 脸侧高光：额头一点光，皮肤更透亮 -->
      <ellipse
        cx="60"
        cy="48"
        rx="10"
        ry="6"
        fill="#ffffff"
        opacity="0.28"
        transform="rotate(-24 60 48)"
      />
      <!-- 腮红 -->
      <ellipse
        cx="49"
        cy="88"
        rx="10"
        ry="5.5"
        :fill="palette.blush"
        opacity="0.5"
      />
      <ellipse
        cx="111"
        cy="88"
        rx="10"
        ry="5.5"
        :fill="palette.blush"
        opacity="0.5"
      />
      <!-- 眼睛：水汪汪大圆眼 + 双高光 + 下缘反光 -->
      <g class="fig-eyes">
        <circle
          cx="61"
          cy="66"
          r="11.5"
          fill="#ffffff"
        />
        <circle
          cx="99"
          cy="66"
          r="11.5"
          fill="#ffffff"
        />
        <circle
          cx="62.5"
          cy="68"
          r="5.8"
          :fill="palette.line"
        />
        <circle
          cx="100.5"
          cy="68"
          r="5.8"
          :fill="palette.line"
        />
        <circle
          cx="60.6"
          cy="65.8"
          r="2.2"
          fill="#ffffff"
        />
        <circle
          cx="98.6"
          cy="65.8"
          r="2.2"
          fill="#ffffff"
        />
        <circle
          cx="64.6"
          cy="70.6"
          r="1.1"
          fill="#ffffff"
          opacity="0.85"
        />
        <circle
          cx="102.6"
          cy="70.6"
          r="1.1"
          fill="#ffffff"
          opacity="0.85"
        />
        <path
          d="M53.5 59.5q7.5-4.5 15-0.5"
          :stroke="palette.line"
          stroke-width="1.6"
          stroke-linecap="round"
          fill="none"
          opacity="0.55"
        />
        <path
          d="M91.5 59q7.5-4 15 0.5"
          :stroke="palette.line"
          stroke-width="1.6"
          stroke-linecap="round"
          fill="none"
          opacity="0.55"
        />
      </g>
      <!-- 白眉：圆圆的长寿眉 -->
      <path
        d="M52 50q10-7 21-2"
        :stroke="palette.beard"
        stroke-width="3.8"
        stroke-linecap="round"
        fill="none"
      />
      <path
        d="M87 48q11-5 21 2"
        :stroke="palette.beard"
        stroke-width="3.8"
        stroke-linecap="round"
        fill="none"
      />
      <!-- 鼻：小圆鼻 -->
      <ellipse
        cx="80"
        cy="82"
        rx="4.4"
        ry="3.4"
        :fill="palette.skinDark"
        opacity="0.6"
      />
      <!-- 八字胡：两撇软乎的白胡子 -->
      <path
        d="M80 88q-13-3-21 6q5 5 13 3q5-1 8-4z"
        :fill="palette.beard"
      />
      <path
        d="M80 88q13-3 21 6q-5 5-13 3q-5-1-8-4z"
        :fill="palette.beard"
      />
      <!-- 笑口：张嘴笑，露一点舌头 -->
      <path
        class="fig-mouth"
        d="M72 95q8 8 16 0q-1.5 8-8 8t-8-8z"
        :fill="palette.mouth"
      />
      <ellipse
        cx="80"
        cy="101"
        rx="3.4"
        ry="1.6"
        fill="#ffffff"
        opacity="0.35"
      />
      <!-- 云朵白胡团：三个圆鼓鼓的泡泡 -->
      <circle
        cx="68"
        cy="111"
        r="6.5"
        :fill="palette.beard"
      />
      <circle
        cx="92"
        cy="111"
        r="6.5"
        :fill="palette.beard"
      />
      <circle
        cx="80"
        cy="114"
        r="8"
        :fill="palette.beard"
      />
      <path
        d="M74 113q6 4 12 0"
        :stroke="palette.skinDark"
        stroke-width="1.4"
        stroke-linecap="round"
        fill="none"
        opacity="0.35"
      />
    </g>

    <!-- 草帽：宽檐 + 圆顶 + 帽带 + 帽檐小芽（扇风时整顶掀起） -->
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
        d="M49 32q3-18 31-18t31 18l-2 8q-29-11-58 0z"
        :fill="palette.hat"
      />
      <path
        d="M49 32q3-18 31-18"
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
      <!-- 帽带上的小嫩芽：憨里带点灵气 -->
      <path
        d="M103 32q7-7 11-5q-2 7-11 7z"
        :fill="palette.leaf"
      />
      <circle
        cx="80"
        cy="14.5"
        r="3.4"
        :fill="palette.hatDark"
      />
      <circle
        cx="79"
        cy="13.5"
        r="1.1"
        fill="#ffffff"
        opacity="0.55"
      />
    </g>

    <!-- 擦汗手：抬袖贴额，白毛巾（带红边） -->
    <g class="fig-arm-wipe">
      <rect
        x="106"
        y="98"
        width="15"
        height="32"
        rx="7.5"
        :fill="palette.shirt"
      />
      <rect
        x="106"
        y="124"
        width="15"
        height="7"
        rx="3.5"
        :fill="palette.shirt"
        opacity="0.7"
      />
      <circle
        cx="113.5"
        cy="136"
        r="7.5"
        :fill="palette.skin"
      />
      <g class="fig-towel">
        <rect
          x="102"
          y="131"
          width="20"
          height="14"
          rx="5"
          :fill="palette.towel"
        />
        <rect
          x="102"
          y="141"
          width="20"
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
        d="M44 52q4.4 7.6 0 10.6q-4.4-3 0-10.6z"
        :fill="palette.sweat"
      />
      <path
        d="M116 54q4 7 0 10q-4-3 0-10z"
        :fill="palette.sweat"
      />
      <path
        d="M124 72q3 5 0 7.4q-3-2.4 0-7.4z"
        :fill="palette.sweat"
      />
    </g>

    <!-- 扇帽风线 -->
    <g class="fig-wind">
      <path
        d="M122 82q12-5 19 2"
        :stroke="palette.sweat"
        stroke-width="2.8"
        stroke-linecap="round"
        fill="none"
      />
      <path
        d="M126 93q11-4 16 2"
        :stroke="palette.sweat"
        stroke-width="2"
        stroke-linecap="round"
        fill="none"
      />
    </g>

    <!-- 打盹 z（放在帽檐外右上，不压帽子） -->
    <g class="fig-zzz">
      <text
        x="130"
        y="30"
        :fill="palette.line"
        font-size="15"
        font-weight="bold"
      >z</text>
      <text
        x="144"
        y="17"
        :fill="palette.line"
        font-size="10.5"
      >z</text>
    </g>
  </svg>
</template>

<script setup lang="ts">
  /**
   * 拟人化农民小人（v3.70 再重画）：头身比更大、水汪汪大圆眼、云朵白胡团、
   * 蛋形墩子身、圆头布鞋，帽带上多一棵小嫩芽——往「憨态可掬」再走一步。
   *
   * 两层驱动，互不打架：
   * - mood 来自真实事件（忙碌/思考/开心/担心/打盹），优先级最高；
   * - action 是发呆时的自我循环（刨土 → 擦汗 → 扇草帽 → 挥手 → 点头），
   *   「什么都不干」的时候也有桌面宠物的动态感。
   *
   * 动画锚点（fig-hat/fig-arm-dig/fig-arm-wipe/…）与 transform-origin
   * 坐标保持不变，keyframes 只增不改，8 套皮肤字段照常生效。
   */
  import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
  import { MOOD_LABEL, type PetMood } from '@/utils/petCopilot'
  import type { PetSkin } from '@/utils/petSkins'

  type FigureAction = 'idle' | 'dig' | 'wipe' | 'fan' | 'wave' | 'nod'

  /** 发呆循环：每个动作演多久（ms），够看清又不会腻 */
  const ACTION_HOLD: Record<Exclude<FigureAction, 'idle'>, number> = {
    dig: 2600,
    wipe: 2200,
    fan: 2400,
    wave: 2400,
    nod: 1500,
  }
  const ACTION_ORDER: Exclude<FigureAction, 'idle'>[] = ['dig', 'wipe', 'fan', 'wave', 'nod']
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

  /* ---------- 发呆自循环：抡起锄头打招呼 ---------- */
  .act-wave .fig-arm-dig {
    animation: fig-wave-hi 0.8s ease-in-out 3;
  }

  .act-wave .fig-head {
    animation: fig-head-tilt 0.8s ease-in-out 3;
  }

  .act-wave .fig-eyes {
    animation: fig-happy-eye 0.8s ease-in-out 3, fig-blink 4.4s infinite;
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

  /* 挥手打招呼：锄头举过肩来回摆 */
  @keyframes fig-wave-hi {
    0%,
    100% {
      transform: rotate(0deg);
    }
    50% {
      transform: rotate(-46deg);
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

    .act-wave .fig-arm-dig {
      transform: rotate(-40deg);
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

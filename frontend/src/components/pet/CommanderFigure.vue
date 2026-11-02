<template>
  <svg
    class="fig"
    :class="`mood-${mood}`"
    :width="size"
    :height="Math.round(size * (170 / 140))"
    viewBox="0 0 140 170"
    role="img"
    :aria-label="`${skin.name}：${MOOD_LABEL[mood]}`"
  >
    <ellipse
      class="fig-shadow"
      cx="70"
      cy="163"
      rx="33"
      ry="5"
    />

    <g class="fig-body">
      <!-- 锄头：左手扶着，一眼看出是种地的 -->
      <g class="fig-tool">
        <path
          d="M56 84 L38 152"
          :stroke="palette.handle"
          stroke-width="4"
          stroke-linecap="round"
        />
        <path
          d="M30 148q10-5 15 5q-9 4 -15 -5z"
          :fill="palette.metal"
        />
      </g>

      <!-- 腿与鞋 -->
      <path
        d="M55 116h11v34a5 5 0 0 1-11 0z"
        :fill="palette.pants"
      />
      <path
        d="M74 116h11v34a5 5 0 0 1-11 0z"
        :fill="palette.pants"
      />
      <ellipse
        cx="58"
        cy="153"
        rx="10"
        ry="5"
        :fill="palette.shoe"
      />
      <ellipse
        cx="82"
        cy="153"
        rx="10"
        ry="5"
        :fill="palette.shoe"
      />

      <!-- 左臂（扶锄头） -->
      <g class="fig-arm-left">
        <path
          d="M44 88h11v30a5.5 5.5 0 0 1-11 0z"
          :fill="palette.shirt"
        />
        <circle
          cx="49.5"
          cy="120"
          r="6"
          :fill="palette.skin"
        />
      </g>

      <!-- 上身：短袖衬衫 + 背带工装 -->
      <rect
        x="49"
        y="84"
        width="42"
        height="40"
        rx="14"
        :fill="palette.shirt"
      />
      <rect
        x="52"
        y="100"
        width="36"
        height="24"
        rx="9"
        :fill="palette.pants"
      />
      <path
        d="M58 86h5v16h-5z"
        :fill="palette.pants"
      />
      <path
        d="M77 86h5v16h-5z"
        :fill="palette.pants"
      />
      <rect
        x="61"
        y="104"
        width="18"
        height="11"
        rx="3"
        :fill="palette.pantsDark"
      />

      <!-- 头与五官 -->
      <rect
        x="65"
        y="76"
        width="10"
        height="10"
        rx="4"
        :fill="palette.skinDark"
      />
      <circle
        cx="50"
        cy="63"
        r="4"
        :fill="palette.skinDark"
      />
      <circle
        cx="90"
        cy="63"
        r="4"
        :fill="palette.skinDark"
      />
      <circle
        cx="70"
        cy="61"
        r="20"
        :fill="palette.skin"
      />
      <g class="fig-brows">
        <path
          d="M58 52q4-3 8-1"
          :stroke="palette.hair"
          stroke-width="2"
          stroke-linecap="round"
          fill="none"
        />
        <path
          d="M74 51q4-2 8 1"
          :stroke="palette.hair"
          stroke-width="2"
          stroke-linecap="round"
          fill="none"
        />
      </g>
      <g class="fig-eyes">
        <ellipse
          cx="62"
          cy="60"
          rx="2.6"
          ry="3.3"
          :fill="palette.line"
        />
        <ellipse
          cx="78"
          cy="60"
          rx="2.6"
          ry="3.3"
          :fill="palette.line"
        />
      </g>
      <ellipse
        cx="55"
        cy="67"
        rx="4.2"
        ry="2.6"
        :fill="skin.cheek"
        opacity="0.55"
      />
      <ellipse
        cx="85"
        cy="67"
        rx="4.2"
        ry="2.6"
        :fill="skin.cheek"
        opacity="0.55"
      />
      <path
        v-if="mood === 'happy'"
        class="fig-mouth"
        d="M63 69q7 10 14 0z"
        :fill="palette.mouth"
      />
      <path
        v-else-if="mood === 'worried'"
        class="fig-mouth"
        d="M65 73q5-4 10 0"
        :stroke="palette.mouth"
        stroke-width="2.2"
        stroke-linecap="round"
        fill="none"
      />
      <path
        v-else-if="mood === 'sleepy'"
        class="fig-mouth"
        d="M66 72h8"
        :stroke="palette.mouth"
        stroke-width="2.2"
        stroke-linecap="round"
        fill="none"
      />
      <path
        v-else
        class="fig-mouth"
        d="M64 70q6 5 12 0"
        :stroke="palette.mouth"
        stroke-width="2.2"
        stroke-linecap="round"
        fill="none"
      />

      <!-- 汗珠：忙碌与担心时冒出来 -->
      <g class="fig-sweat">
        <path
          d="M50 54q3 5 0 7q-3-2 0-7z"
          :fill="palette.sweat"
        />
        <path
          d="M92 56q3 5 0 7q-3-2 0-7z"
          :fill="palette.sweat"
        />
      </g>

      <!-- 草帽 -->
      <g class="fig-hat">
        <path
          d="M52 45C53 27 87 27 88 45z"
          :fill="palette.hat"
        />
        <rect
          x="52"
          y="40"
          width="36"
          height="5"
          rx="2.5"
          :fill="palette.band"
        />
        <ellipse
          cx="70"
          cy="46"
          rx="35"
          ry="9"
          :fill="palette.hat"
        />
        <path
          d="M38 46q32 9 64 0"
          :stroke="palette.hatDark"
          stroke-width="1.6"
          fill="none"
          opacity="0.55"
        />
      </g>

      <!-- 右臂：擦汗那只，画在最前面，抬手时能挡住额头 -->
      <g class="fig-arm-wipe">
        <path
          d="M83 86h10v32a5 5 0 0 1-10 0z"
          :fill="palette.shirt"
        />
        <circle
          cx="88"
          cy="120"
          r="6"
          :fill="palette.skin"
        />
        <rect
          class="fig-towel"
          x="81"
          y="118"
          width="14"
          height="10"
          rx="3"
          :fill="palette.towel"
        />
      </g>

      <!-- 打盹时的 z -->
      <g class="fig-zzz">
        <text
          x="98"
          y="40"
          :fill="palette.line"
          font-size="12"
        >z</text>
        <text
          x="108"
          y="28"
          :fill="palette.line"
          font-size="9"
        >z</text>
      </g>
    </g>
  </svg>
</template>

<script setup lang="ts">
  /**
   * 拟人化小人（v3.51）：戴草帽、扶锄头的农民指挥官。
   * 与圆形桌宠不同，这里要的是「看得清的小人」——有头、身躯、两条胳膊，
   * 忙起来抬右手擦汗，担心时冒汗珠，打盹时闭眼冒 z。
   */
  import { computed } from 'vue'
  import { MOOD_LABEL, type PetMood } from '@/utils/petCopilot'
  import type { PetSkin } from '@/utils/petSkins'

  const props = withDefaults(
    defineProps<{
      skin: PetSkin
      mood?: PetMood
      /** 渲染宽度（px），高度按 170/140 比例自适应 */
      size?: number
    }>(),
    { mood: 'idle', size: 120 },
  )

  /** 人形皮肤自带 human 配色；缺省时回落到皮肤主色，保证 8 套皮肤都能画 */
  const palette = computed(() => {
    const human = props.skin.human
    return {
      skin: human?.skin ?? props.skin.body,
      skinDark: human?.skinDark ?? props.skin.bodyDark,
      shirt: human?.shirt ?? props.skin.belly,
      pants: human?.pants ?? props.skin.bodyDark,
      pantsDark: human?.pantsDark ?? props.skin.accent,
      hat: human?.hat ?? props.skin.ear,
      hatDark: human?.hatDark ?? props.skin.accent,
      band: human?.band ?? props.skin.accent,
      shoe: human?.shoe ?? props.skin.accent,
      handle: human?.handle ?? props.skin.accent,
      metal: human?.metal ?? props.skin.bodyDark,
      towel: human?.towel ?? props.skin.belly,
      hair: human?.hair ?? props.skin.accent,
      mouth: human?.mouth ?? props.skin.accent,
      sweat: human?.sweat ?? '#bfe3ff',
      line: human?.line ?? '#2f2a26',
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
  .fig-arm-wipe,
  .fig-arm-left,
  .fig-eyes,
  .fig-hat,
  .fig-sweat,
  .fig-zzz,
  .fig-towel {
    transform-box: view-box;
  }

  .fig-body {
    transform-origin: 70px 163px;
    animation: fig-float 3.6s ease-in-out infinite;
  }

  .fig-eyes {
    transform-origin: 70px 60px;
    animation: fig-blink 4.4s infinite;
  }

  /* 肩膀为轴：左臂 (49.5, 88)、右臂 (88, 88) */
  .fig-arm-left {
    transform-origin: 49.5px 88px;
  }

  .fig-arm-wipe {
    transform-origin: 88px 88px;
  }

  .fig-hat {
    transform-origin: 70px 46px;
  }

  .fig-zzz {
    transform-origin: 104px 34px;
  }

  .fig-towel,
  .fig-sweat,
  .fig-zzz {
    opacity: 0;
  }

  /* 忙碌：抬手到额头擦汗 */
  .mood-busy .fig-arm-wipe {
    animation: fig-wipe 1.9s ease-in-out infinite;
  }

  .mood-busy .fig-towel {
    animation: fig-towel-show 1.9s ease-in-out infinite;
  }

  .mood-busy .fig-sweat {
    animation: fig-sweat 1.9s ease-in-out infinite;
  }

  .mood-busy .fig-arm-left {
    animation: fig-tool-tap 1.9s ease-in-out infinite;
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

  .mood-happy .fig-arm-left,
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
    animation: fig-float 5.2s ease-in-out infinite;
  }

  .mood-sleepy .fig-zzz {
    animation: fig-zzz 2.8s ease-out infinite;
  }

  @keyframes fig-float {
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

  @keyframes fig-wipe {
    0%,
    100% {
      transform: rotate(0deg);
    }
    35% {
      transform: rotate(150deg);
    }
    62% {
      transform: rotate(168deg);
    }
  }

  @keyframes fig-towel-show {
    0%,
    20%,
    100% {
      opacity: 0;
    }
    35%,
    75% {
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

  @keyframes fig-tool-tap {
    0%,
    100% {
      transform: rotate(0deg);
    }
    50% {
      transform: rotate(-4deg);
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
      transform: translateX(-2.5px);
    }
  }

  @keyframes fig-hop {
    0%,
    100% {
      transform: translateY(0) scale(1);
    }
    40% {
      transform: translateY(-9px) scale(1.03);
    }
  }

  @keyframes fig-wave {
    0%,
    100% {
      transform: rotate(0deg);
    }
    50% {
      transform: rotate(-26deg);
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
    .fig-eyes,
    .fig-hat,
    .fig-arm-left,
    .fig-arm-wipe,
    .fig-towel,
    .fig-sweat,
    .fig-zzz {
      animation: none !important;
    }

    /* 静态也要能看出「正在擦汗 / 冒汗 / 打盹」 */
    .mood-busy .fig-arm-wipe {
      transform: rotate(158deg);
    }

    .mood-busy .fig-arm-left,
    .mood-happy .fig-arm-left,
    .mood-happy .fig-arm-wipe {
      transform: rotate(-20deg);
    }

    .mood-busy .fig-towel,
    .mood-busy .fig-sweat,
    .mood-worried .fig-sweat,
    .mood-sleepy .fig-zzz {
      opacity: 1;
    }

    .mood-sleepy .fig-eyes {
      transform: scaleY(0.12);
    }
  }
</style>

<template>
  <div
    class="trend-wrap"
    :style="{ height: `${height}px` }"
  >
    <div
      ref="chartEl"
      class="trend-canvas"
      :style="{ minWidth: `${Math.max(420, points.length * 34)}px`, height: '100%' }"
    />
  </div>
</template>

<script setup lang="ts">
  import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
  import * as echarts from 'echarts/core'
  import { BarChart, LineChart } from 'echarts/charts'
  import {
    GridComponent,
    LegendComponent,
    TooltipComponent,
  } from 'echarts/components'
  import { CanvasRenderer } from 'echarts/renderers'
  import type { EChartsCoreOption, EChartsType } from 'echarts/core'

  echarts.use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

  export interface UsageTrendPoint {
    day: string
    total_tokens: number
    calls: number
  }

  const props = withDefaults(
    defineProps<{
      points: UsageTrendPoint[]
      height?: number
    }>(),
    { height: 300 },
  )

  const chartEl = ref<HTMLDivElement | null>(null)
  let chart: EChartsType | null = null
  let observer: ResizeObserver | null = null

  function shortDay(day: string): string {
    return day.length >= 10 ? day.slice(5) : day
  }

  function buildOption(): EChartsCoreOption {
    return {
      backgroundColor: 'transparent',
      animationDuration: 400,
      grid: { left: 58, right: 52, top: 46, bottom: 30 },
      legend: {
        // scroll 型图例:条目放不下时自动翻页,杜绝双系列图例文字叠字
        type: 'scroll',
        top: 2,
        left: 58,
        right: 52,
        itemWidth: 12,
        itemHeight: 8,
        itemGap: 10,
        textStyle: { color: '#9aa1b5', fontSize: 11 },
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: 'rgba(20, 23, 34, 0.95)',
        borderColor: 'rgba(255,255,255,0.12)',
        textStyle: { color: '#E8EAF2', fontSize: 12 },
        formatter: (raw: unknown) => {
          const items = (Array.isArray(raw) ? raw : [raw]) as {
            axisValue?: string
            seriesName?: string
            value?: number
            marker?: string
          }[]
          if (!items.length) return ''
          const lines = [`<b>${items[0].axisValue ?? ''}</b>`]
          for (const item of items) {
            const value = Number(item.value ?? 0).toLocaleString()
            lines.push(`${item.marker ?? ''}${item.seriesName}：${value}`)
          }
          return lines.join('<br/>')
        },
      },
      xAxis: {
        type: 'category',
        data: props.points.map(p => shortDay(p.day)),
        axisTick: { show: false },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.12)' } },
        axisLabel: { color: '#8b90a3', fontSize: 11 },
      },
      yAxis: [
        {
          type: 'value',
          name: 'Tokens',
          nameTextStyle: { color: '#8b90a3', fontSize: 11, padding: [0, 0, 0, -8] },
          axisLabel: {
            color: '#8b90a3',
            fontSize: 11,
            formatter: (value: number) =>
              value >= 10000 ? `${(value / 10000).toFixed(1)}万` : String(value),
          },
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
        },
        {
          type: 'value',
          name: '调用',
          nameTextStyle: { color: '#8b90a3', fontSize: 11 },
          axisLabel: { color: '#8b90a3', fontSize: 11 },
          splitLine: { show: false },
          minInterval: 1,
        },
      ],
      series: [
        {
          name: 'Token 消耗',
          type: 'bar',
          barMaxWidth: 16,
          itemStyle: { color: '#34d399', borderRadius: [4, 4, 0, 0] },
          data: props.points.map(p => p.total_tokens),
        },
        {
          name: '调用次数',
          type: 'line',
          yAxisIndex: 1,
          smooth: true,
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { color: '#60a5fa', width: 2 },
          itemStyle: { color: '#60a5fa' },
          areaStyle: { color: 'rgba(96, 165, 250, 0.08)' },
          data: props.points.map(p => p.calls),
        },
      ],
    }
  }

  function render(): void {
    if (!chartEl.value) return
    if (!chart) chart = echarts.init(chartEl.value)
    chart.setOption(buildOption(), true)
  }

  watch(
    () => props.points,
    () => render(),
    { deep: true },
  )

  onMounted(() => {
    render()
    if (chartEl.value && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => chart?.resize())
      observer.observe(chartEl.value)
    }
    window.addEventListener('resize', onWindowResize)
  })

  function onWindowResize(): void {
    chart?.resize()
  }

  onBeforeUnmount(() => {
    window.removeEventListener('resize', onWindowResize)
    observer?.disconnect()
    chart?.dispose()
    chart = null
  })
</script>

<style scoped>
  .trend-wrap {
    width: 100%;
    overflow-x: auto;
    overflow-y: hidden;
  }
  .trend-canvas {
    width: 100%;
  }
</style>

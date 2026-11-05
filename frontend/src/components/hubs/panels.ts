/** 枢纽三种运营模式的视图注册表（v3.66）。
 *
 * 新增一种形态 = 加一个 Panel 组件 + 这里登记一行；后端只需要多一个 mode 枚举与
 * 分组字段映射，不需要改路由、鉴权或数据模型。
 */
import type { Component } from 'vue'
import ProvincePanel from './ProvincePanel.vue'
import MunicipalPanel from './MunicipalPanel.vue'
import StationPanel from './StationPanel.vue'

export const MODE_PANELS: Record<string, Component> = {
  province: ProvincePanel,
  municipal: MunicipalPanel,
  station: StationPanel,
}

/** 模式术语：标题与描述前后端同源，避免散落硬编码 */
export const MODE_TERMS: Record<string, { title: string; scope: string }> = {
  province: { title: '培养单位枢纽 · 省级科协模式', scope: '跨市分散 · 按培养单位聚合 · 达标监测' },
  municipal: { title: '市级专班 · 全域推广模式', scope: '县域全覆盖 · 推广进度 · 断更跟进' },
  station: { title: '实验站枢纽 · 小院群模式', scope: '小院群横向对比 · 一院一策 · 类型分布' },
}

export function panelFor(mode?: string): Component | null {
  return (mode && MODE_PANELS[mode]) || null
}

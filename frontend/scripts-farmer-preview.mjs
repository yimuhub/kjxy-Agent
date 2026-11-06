/**
 * 农民伯伯重画预览页生成器（v3.70）：
 * 读旧版（git HEAD）与新版 CommanderFigure.vue 的 <template>，
 * 内联田伯配色后并排渲染，生成 docs/farmer-redesign-preview.html。
 * 用法：node scripts-farmer-preview.mjs（在 frontend/ 目录下执行）
 */

import { execSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const componentPath = resolve(here, 'src/components/pet/CommanderFigure.vue')
const outPath = resolve(here, '../docs/farmer-redesign-preview.html')

/** 田伯（farmer 皮肤）实际配色 + 派生色，与 petSkins.ts 保持一致 */
const PALETTE = {
  skin: '#e8b47f',
  skinDark: '#d19a63',
  shirt: '#8fb6d9',
  pants: '#5f7f4c',
  pantsDark: '#4c6a3e',
  hat: '#d8a944',
  hatDark: '#a87c2c',
  band: '#6b8f4e',
  shoe: '#6b5138',
  handle: '#9c7245',
  metal: '#a8b2bd',
  towel: '#f6f1e5',
  hair: '#4a3a2c',
  mouth: '#a8514a',
  sweat: '#bfe3ff',
  line: '#33291f',
  beard: '#f4f1ea',
  blush: '#f0a58f',
  leaf: '#69a244',
  soil: '#8a6242',
  soilLight: '#a97b53',
}

function extractTemplate(source) {
  const start = source.indexOf('<template>')
  const end = source.lastIndexOf('</template>')
  if (start === -1 || end === -1) throw new Error('找不到 <template> 边界')
  return source.slice(start + '<template>'.length, end).trim()
}

function inlineSvg(template) {
  let svg = template.replace(/<svg[^>]*>/, '<svg class="fig" viewBox="0 0 160 200" width="230" height="287">')
  svg = svg.replace(/:(fill|stroke)="palette\.(\w+)"/g, (all, attr, key) => {
    const color = PALETTE[key]
    if (!color) throw new Error(`配色缺少字段：${key}`)
    return `${attr}="${color}"`
  })
  return svg
}

const oldSource = execSync('git show HEAD:frontend/src/components/pet/CommanderFigure.vue', {
  cwd: resolve(here, '..'),
  encoding: 'utf8',
})
const newSource = readFileSync(componentPath, 'utf8')

const oldSvg = inlineSvg(extractTemplate(oldSource))
const newSvg = inlineSvg(extractTemplate(newSource))

const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>农民伯伯重画预览（v3.70）</title>
<style>
  body { margin: 0; font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; }
  .band { padding: 28px 20px; }
  .band.dark { background: #121722; }
  .band.light { background: #f5f7fb; }
  h1 { text-align: center; margin: 24px 0 4px; color: #e8eefb; font-size: 20px; }
  .band.light h1 { color: #1b2333; }
  p.sub { text-align: center; margin: 0 0 8px; color: #8b98ad; font-size: 13px; }
  .row { display: flex; gap: 48px; justify-content: center; align-items: flex-end; flex-wrap: wrap; }
  .card { text-align: center; }
  .card .tag { display: inline-block; margin-bottom: 12px; padding: 3px 12px; border-radius: 999px; font-size: 13px;
    border: 1px solid rgba(150,172,196,.35); color: #cfd8e6; }
  .band.light .card .tag { border-color: rgba(15,23,42,.18); color: #47536b; }
  .card svg { display: block; margin: 0 auto; }
  .note { max-width: 640px; margin: 20px auto 0; color: #8b98ad; font-size: 12.5px; line-height: 1.8; }
</style>
</head>
<body>
  <h1>农民伯伯重画对比 · 新版更 Q 更憨</h1>
  <p class="sub">左旧右新；上暗色桌面、下亮色桌面。发呆动作照旧：刨土 → 擦汗 → 扇草帽 → 挥手（新增）→ 点头。</p>

  <div class="band dark">
    <div class="row">
      <div class="card"><span class="tag">旧版 v3.56</span>${oldSvg}</div>
      <div class="card"><span class="tag">新版 v3.70</span>${newSvg}</div>
    </div>
  </div>

  <div class="band light">
    <div class="row">
      <div class="card"><span class="tag">旧版 v3.56</span>${oldSvg}</div>
      <div class="card"><span class="tag">新版 v3.70</span>${newSvg}</div>
    </div>
    <p class="note">新版改动：头再大一圈（头身比 ≈ 1:1）、水汪汪大圆眼（双层高光 + 上睫毛线）、云朵白胡团、蛋形墩子身 + 圆头布鞋、帽带小嫩芽、张嘴笑露舌、腮红加大。动画锚点与皮肤字段全部不变，8 套皮肤照常可用。</p>
  </div>
</body>
</html>
`

writeFileSync(outPath, html)
console.log(`已生成：${outPath}`)

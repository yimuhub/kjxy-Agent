<template>
  <div class="safe-markdown">
    <Content />
  </div>
</template>

<script setup lang="ts">
import { defineComponent, h } from 'vue'
import { parseInline, parseMarkdown } from './safeMarkdown'
import { safeHttpUrl, safeImageUrl } from './presentation'

const props = defineProps<{ content: string }>()
function inline(text: string) {
  return parseInline(text).map(token => {
    if (token.kind === 'text') return token.text
    if (token.kind === 'link') {
      const href = safeHttpUrl(token.url)
      return href ? h('a', { href, target: '_blank', rel: 'noopener noreferrer' }, token.text) : token.text
    }
    if (token.kind === 'image') {
      // 海报由 <img> 直接加载、带不了鉴权头，因此只放行同源签名地址与 http(s) 外链
      const src = safeImageUrl(token.url)
      return src
        ? h('img', { src, alt: token.text, class: 'md-image', loading: 'lazy', referrerpolicy: 'no-referrer' })
        : token.text
    }
    return h(token.kind, token.text)
  })
}
// 全部内容经 Vue 文本节点转义，不使用 v-html 或 innerHTML。
const Content = defineComponent(() => () => parseMarkdown(props.content).map(block => {
  switch (block.kind) {
    case 'heading': return h(`h${block.level}`, inline(block.text))
    case 'paragraph': return h('p', inline(block.text))
    case 'quote': return h('blockquote', inline(block.text))
    case 'code': return h('pre', [h('code', block.text)])
    case 'rule': return h('hr')
    case 'list': return h(block.ordered ? 'ol' : 'ul', block.items.map(item => h('li', inline(item))))
    case 'table': return h('div', { class: 'table-scroll' }, [h('table', [
      h('thead', [h('tr', block.header.map(cell => h('th', inline(cell))))]),
      h('tbody', block.rows.map(row => h('tr', block.header.map((_, index) => h('td', inline(row[index] ?? '')))))),
    ])])
  }
}))
</script>

<style scoped>
.safe-markdown { overflow-wrap: anywhere; line-height: 1.8; color: var(--ds-text-1); font-size: 14px; }
.safe-markdown :deep(p), .safe-markdown :deep(.md-image) { display: block; max-width: 100%; height: auto; margin: 10px 0; border-radius: var(--ds-radius-control); border: 1px solid var(--ds-border); }
.safe-markdown :deep(blockquote) { white-space: pre-wrap; }
.safe-markdown :deep(h1) { font-size: 1.5em; }
.safe-markdown :deep(h2) { font-size: 1.3em; }
.safe-markdown :deep(h3) { font-size: 1.15em; }
.safe-markdown :deep(a) { color: var(--el-color-primary); }
.safe-markdown :deep(pre) { overflow: auto; padding: 12px; background: var(--ds-glass); border-radius: var(--ds-radius-control); }
.safe-markdown :deep(code) { font-size: 0.9em; background: var(--ds-glass); padding: 2px 4px; }
.safe-markdown :deep(.md-image) { display: block; max-width: 100%; height: auto; margin: 10px 0; border-radius: var(--ds-radius-control); border: 1px solid var(--ds-border); }
.safe-markdown :deep(blockquote) { margin-left: 0; border-left: 3px solid var(--ds-border-strong); padding-left: 12px; color: var(--ds-text-2); }
.safe-markdown :deep(.table-scroll) { overflow-x: auto; }
.safe-markdown :deep(table) { border-collapse: collapse; width: 100%; }
.safe-markdown :deep(th), .safe-markdown :deep(td) { border: 1px solid var(--ds-border); padding: 6px 10px; text-align: left; }
</style>

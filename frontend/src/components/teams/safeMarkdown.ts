/** 安全 Markdown 子集：只解析结构与文本，不接受 HTML、图片、内联样式。 */
export type InlineToken = { kind: 'text' | 'strong' | 'em' | 'code' | 'link'; text: string; url?: string }
export type MarkdownBlock =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'paragraph' | 'quote' | 'code'; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'table'; header: string[]; rows: string[][] }
  | { kind: 'rule' }

export function parseInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = []
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]\n]+\]\([^\s)]+\))/g
  let cursor = 0
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0
    if (index > cursor) tokens.push({ kind: 'text', text: text.slice(cursor, index) })
    const value = match[0]
    if (value.startsWith('`')) tokens.push({ kind: 'code', text: value.slice(1, -1) })
    else if (value.startsWith('**')) tokens.push({ kind: 'strong', text: value.slice(2, -2) })
    else if (value.startsWith('*')) tokens.push({ kind: 'em', text: value.slice(1, -1) })
    else {
      const separator = value.indexOf('](')
      tokens.push({ kind: 'link', text: value.slice(1, separator), url: value.slice(separator + 2, -1) })
    }
    cursor = index + value.length
  }
  if (cursor < text.length) tokens.push({ kind: 'text', text: text.slice(cursor) })
  return tokens
}

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim())
}
const listPattern = /^\s*(?:([-+*])|\d+[.)])\s+(.+)$/
function startsBlock(line: string): boolean {
  return /^(?:#{1,6}\s|```|~~~|>\s?|\s*(?:[-+*]|\d+[.)])\s+|\s*([-*_])(?:\s*\1){2,}\s*$)/.test(line)
}

export function parseMarkdown(content: string): MarkdownBlock[] {
  const lines = content.replace(/\r\n?/g, '\n').split('\n')
  const blocks: MarkdownBlock[] = []
  for (let i = 0; i < lines.length;) {
    const line = lines[i]
    if (!line.trim()) { i += 1; continue }
    const fence = line.match(/^(`{3,}|~{3,})/)
    if (fence) {
      const code: string[] = []
      i += 1
      while (i < lines.length && !lines[i].startsWith(fence[1])) code.push(lines[i++])
      if (i < lines.length) i += 1
      blocks.push({ kind: 'code', text: code.join('\n') })
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*$/)
    if (heading) {
      blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2] })
      i += 1
      continue
    }
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      blocks.push({ kind: 'rule' }); i += 1; continue
    }
    if (line.includes('|') && i + 1 < lines.length && cells(lines[i + 1]).every(cell => /^:?-{3,}:?$/.test(cell))) {
      const header = cells(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i].trim() && lines[i].includes('|')) rows.push(cells(lines[i++]))
      blocks.push({ kind: 'table', header, rows })
      continue
    }
    const list = line.match(listPattern)
    if (list) {
      const ordered = !list[1]
      const items: string[] = []
      while (i < lines.length) {
        const item = lines[i].match(listPattern)
        if (!item || !item[1] !== ordered) break
        items.push(item[2]); i += 1
      }
      blocks.push({ kind: 'list', ordered, items })
      continue
    }
    if (line.startsWith('>')) {
      const quote: string[] = []
      while (i < lines.length && lines[i].startsWith('>')) quote.push(lines[i++].replace(/^>\s?/, ''))
      blocks.push({ kind: 'quote', text: quote.join('\n') })
      continue
    }
    const paragraph = [line]
    i += 1
    while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])) {
      if (i + 1 < lines.length && lines[i].includes('|') && cells(lines[i + 1]).every(cell => /^:?-{3,}:?$/.test(cell))) break
      paragraph.push(lines[i++])
    }
    blocks.push({ kind: 'paragraph', text: paragraph.join('\n') })
  }
  return blocks
}

import type { Directive, DirectiveBinding } from 'vue'

/** 最小列宽（px） */
const MIN_WIDTH = 80
const STORAGE_PREFIX = 'kjxy:colw:'

interface ResizeState {
  observer?: MutationObserver
  frame?: number
  cleanup?: () => void
}

const states = new WeakMap<HTMLElement, ResizeState>()

function tableKey(el: HTMLElement, binding: DirectiveBinding<string | undefined>): string {
  if (binding.value) return String(binding.value)
  if (el.dataset.colResizeKey) return el.dataset.colResizeKey
  const tables = Array.from(document.querySelectorAll<HTMLElement>('.el-table'))
  const index = Math.max(0, tables.indexOf(el))
  return `${window.location.pathname}${window.location.search}#${index}`
}

function storageKey(key: string): string {
  return `${STORAGE_PREFIX}${key}`
}

function readWidths(key: string): Record<string, number> {
  try {
    const raw = window.localStorage.getItem(storageKey(key))
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Record<string, unknown>
    const out: Record<string, number> = {}
    Object.entries(parsed).forEach(([name, value]) => {
      const width = Number(value)
      if (Number.isFinite(width) && width >= MIN_WIDTH) out[name] = width
    })
    return out
  } catch {
    return {}
  }
}

function writeWidths(key: string, widths: Record<string, number>): void {
  try {
    if (Object.keys(widths).length) {
      window.localStorage.setItem(storageKey(key), JSON.stringify(widths))
    } else {
      window.localStorage.removeItem(storageKey(key))
    }
  } catch {
    /* 隐私模式/配额不足时忽略，列宽只在本次会话生效 */
  }
}

function headerCells(el: HTMLElement): HTMLTableCellElement[] {
  const header = el.querySelector<HTMLElement>('.el-table__header-wrapper')
  if (!header) return []
  return Array.from(header.querySelectorAll<HTMLTableCellElement>('thead th.el-table__cell'))
}

function headerLabel(cell: HTMLTableCellElement): string {
  const text = (cell.innerText || '').replace(/\s+/g, ' ').trim()
  return text || '列'
}

function columnName(cell: HTMLTableCellElement, index: number): string {
  return `${index}:${headerLabel(cell)}`
}

function tablesFor(el: HTMLElement): HTMLTableElement[] {
  const out: HTMLTableElement[] = []
  el.querySelectorAll<HTMLTableElement>('.el-table__header-wrapper > table').forEach(t =>
    out.push(t),
  )
  el.querySelectorAll<HTMLTableElement>('.el-table__body-wrapper table.el-table__body').forEach(t =>
    out.push(t),
  )
  return out
}

function setTableWidths(el: HTMLElement, widths: number[]): void {
  const total = widths.reduce((sum, width) => sum + width, 0)
  tablesFor(el).forEach(table => {
    table.style.width = `${total}px`
    table.style.minWidth = `${total}px`
  })
}

function applySavedWidths(el: HTMLElement, key: string): void {
  const cells = headerCells(el)
  if (!cells.length) return
  const saved = readWidths(key)
  const widths = cells.map((cell, index) => {
    const width = saved[columnName(cell, index)]
    return width && width >= MIN_WIDTH ? width : cell.getBoundingClientRect().width
  })
  if (!Object.keys(saved).length) return
  cells.forEach((cell, index) => {
    const width = saved[columnName(cell, index)]
    if (!width) return
    setColumnWidth(el, index, width)
  })
  setTableWidths(el, widths)
}

function setColumnWidth(el: HTMLElement, index: number, width: number): void {
  tablesFor(el).forEach(table => {
    const col = table.querySelectorAll<HTMLTableColElement>('colgroup col')[index]
    if (col) col.style.width = `${width}px`
  })
  const cells = headerCells(el)
  const cell = cells[index]
  if (cell) {
    cell.style.width = `${width}px`
    cell.style.minWidth = `${width}px`
    cell.style.maxWidth = `${width}px`
  }
}

function dragWidths(el: HTMLElement): number[] {
  const cells = headerCells(el)
  return cells.map(cell => Math.max(MIN_WIDTH, cell.getBoundingClientRect().width))
}

function enhance(el: HTMLElement, key: string): void {
  const cells = headerCells(el)
  if (!cells.length) return
  cells.forEach((cell, index) => {
    if (cell.querySelector(':scope > .col-resize-handle')) return
    if (cell.classList.contains('el-table-column--selection')) return
    if (cell.querySelector('.el-checkbox')) return
    cell.classList.add('col-resizable')
    const handle = document.createElement('span')
    handle.className = 'col-resize-handle'
    handle.setAttribute('aria-hidden', 'true')
    handle.title = '拖动调整列宽，双击恢复默认'
    cell.appendChild(handle)

    handle.addEventListener('pointerdown', event => {
      event.preventDefault()
      event.stopPropagation()
      const startX = event.clientX
      const startWidth = Math.max(MIN_WIDTH, cell.getBoundingClientRect().width)
      const widths = dragWidths(el)
      widths[index] = startWidth
      const pointerId = event.pointerId
      handle.setPointerCapture(pointerId)

      const onMove = (moveEvent: PointerEvent) => {
        const width = Math.max(MIN_WIDTH, Math.round(startWidth + moveEvent.clientX - startX))
        widths[index] = width
        setColumnWidth(el, index, width)
        setTableWidths(el, widths)
      }
      const onUp = () => {
        handle.removeEventListener('pointermove', onMove)
        handle.removeEventListener('pointerup', onUp)
        handle.removeEventListener('pointercancel', onUp)
        try {
          handle.releasePointerCapture(pointerId)
        } catch {
          /* 已释放 */
        }
        const saved = readWidths(key)
        saved[columnName(cell, index)] = widths[index]
        writeWidths(key, saved)
      }
      handle.addEventListener('pointermove', onMove)
      handle.addEventListener('pointerup', onUp)
      handle.addEventListener('pointercancel', onUp)
    })

    handle.addEventListener('dblclick', event => {
      event.preventDefault()
      event.stopPropagation()
      const saved = readWidths(key)
      delete saved[columnName(cell, index)]
      writeWidths(key, saved)
      // 清除本列强制宽度，交给 Element Plus 重新布局
      tablesFor(el).forEach(table => {
        const col = table.querySelectorAll<HTMLTableColElement>('colgroup col')[index]
        if (col) col.style.width = ''
      })
      cell.style.width = ''
      cell.style.minWidth = ''
      cell.style.maxWidth = ''
      tablesFor(el).forEach(table => {
        table.style.width = ''
        table.style.minWidth = ''
      })
    })
  })
  applySavedWidths(el, key)
}

function scheduleEnhance(el: HTMLElement, state: ResizeState, key: string): void {
  if (state.frame) cancelAnimationFrame(state.frame)
  state.frame = requestAnimationFrame(() => {
    state.frame = undefined
    enhance(el, key)
  })
}

export const colResize: Directive<HTMLElement, string | undefined> = {
  mounted(el, binding) {
    const key = tableKey(el, binding)
    if (binding.value) el.dataset.colResizeKey = String(binding.value)
    const state: ResizeState = {}
    states.set(el, state)
    const header = el.querySelector<HTMLElement>('.el-table__header-wrapper')
    if (header) {
      const observer = new MutationObserver(() => scheduleEnhance(el, state, key))
      observer.observe(header, { childList: true, subtree: true })
      state.observer = observer
    }
    scheduleEnhance(el, state, key)
  },
  updated(el, binding) {
    const state = states.get(el)
    if (state) scheduleEnhance(el, state, tableKey(el, binding))
  },
  unmounted(el) {
    const state = states.get(el)
    if (state?.frame) cancelAnimationFrame(state.frame)
    state?.observer?.disconnect()
    states.delete(el)
  },
}

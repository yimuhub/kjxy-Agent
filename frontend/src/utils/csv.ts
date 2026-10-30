/** CSV 导出工具：UTF-8 BOM + 标准转义，Excel 直接双击可读。 */

export type CsvCell = string | number | null | undefined

export interface CsvSection {
  title: string
  rows: CsvCell[][]
}

function cell(value: CsvCell): string {
  if (value === null || value === undefined) return ''
  const text = String(value)
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function toLines(rows: CsvCell[][]): string {
  return rows.map(row => row.map(cell).join(',')).join('\r\n')
}

/** 分节导出：每节一个标题行 + 一段表，节间空一行。 */
export function downloadCsvSections(filename: string, sections: CsvSection[]): void {
  const body = sections
    .filter(section => section.rows.length > 0)
    .map(section => `${toLines([[section.title]])}\r\n${toLines(section.rows)}`)
    .join('\r\n\r\n')
  downloadCsv(filename, body)
}

export function downloadCsv(filename: string, content: string): void {
  const blob = new Blob([`\ufeff${content}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

export function todayStamp(): string {
  return new Date().toISOString().slice(0, 10)
}

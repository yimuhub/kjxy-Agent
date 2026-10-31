/**
 * 统一导出：桌面端写本地「下载」目录，浏览器端保留原来的 Blob 锚点下载。
 *
 * WKWebView 没有下载代理时，`<a download>` + blob 会被静默丢弃；桌面端改走壳命令，
 * 顺带把文件名安全化与重名编号交给 Rust，前端不再各自拼一套下载代码。
 */

type InvokeFn = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>

function tauriInvoke(): InvokeFn | null {
  if (typeof window === 'undefined') return null
  const internals = (
    window as unknown as { __TAURI_INTERNALS__?: { invoke?: InvokeFn } }
  ).__TAURI_INTERNALS__
  return internals?.invoke ?? null
}

/** 浏览器回退：与历史导出完全一致，1 秒后释放临时 URL */
function anchorDownload(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(reader.error ?? new Error('读取文件失败'))
    reader.readAsDataURL(blob)
  })
}

async function toast(kind: 'success' | 'error', text: string): Promise<void> {
  try {
    const { ElMessage } = await import('element-plus')
    if (kind === 'success') ElMessage.success(text)
    else ElMessage.error(text)
  } catch {
    /* 单测/非 UI 环境没有 element-plus，静默即可 */
  }
}

/**
 * 保存 Blob：桌面端写「下载」目录并提示绝对路径；浏览器端走锚点下载。
 * 返回 true 表示走了桌面保存且成功。
 */
export async function saveBlobOrDownload(filename: string, blob: Blob): Promise<boolean> {
  const invoke = tauriInvoke()
  if (!invoke) {
    anchorDownload(filename, blob)
    return false
  }
  try {
    const dataBase64 = await blobToBase64(blob)
    const path = String(await invoke('save_export', { filename, dataBase64 }))
    await toast('success', `已保存到下载文件夹：${path}`)
    return true
  } catch {
    await toast('error', '保存失败，请重试')
    return false
  }
}

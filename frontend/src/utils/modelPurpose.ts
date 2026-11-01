/** 能力码 → 允许的模型模态（v3.37）
 *
 * 「按能力绑定 / 助手专属能力 / 视觉默认模型」三处下拉共用：只让用户看到真能用的模型，
 * 避免把对话模型绑到图像/视频/向量能力上，配完才发现调不通。
 */
const PURPOSE_MODALITIES: Record<string, string[]> = {
  image_generation: ['image'],
  video_generation: ['video'],
  embedding: ['embedding'],
  asr: ['asr'],
  tts: ['tts'],
}

export function modalitiesForPurpose(purpose?: string | null): string[] {
  if (!purpose) return []
  return PURPOSE_MODALITIES[purpose] ?? ['text']
}

/** 视觉能力固定映射（智能体页下方的两张卡） */
export function modalitiesForVisual(purpose: string): string[] {
  return purpose === 'image_generation' ? ['image'] : ['video']
}

export const VISUAL_PURPOSE_LABELS: Record<string, string> = {
  image_generation: '图像生成',
  video_generation: '视频生成',
}

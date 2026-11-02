import { ref, type Ref } from 'vue'

/**
 * 伙伴是否正停在视口右下角（v3.52）。
 * 悬浮小人盖在页面之上（z-index 2600），默认落点正好压住对话页输入条右侧的发送圆钮；
 * FloatingCompanion 负责按实际矩形更新这里，ChatPanel 据此把工具条右侧让开。
 * 用户把小人拖离角落，标记自动撤销，输入条恢复满宽。
 */
export const companionCoversCorner: Ref<boolean> = ref(false)

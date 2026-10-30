/**
 * 录音 → 16kHz 单声道 PCM16 WAV（v3.30）。
 *
 * 为什么自己编码：后端本机模型只吃 16k 单声道 WAV，浏览器原生 MediaRecorder 只能
 * 出 webm/ogg/mp4，服务端就得带 ffmpeg 解码，客户端体积和跨平台风险都会变大。
 * 这里在采集端一次降到 16k 并写成 WAV，三端（macOS/Windows/Linux 的内嵌 WebView）
 * 拿到的是同一份字节，后端不需要任何转码依赖。
 */

export const TARGET_SAMPLE_RATE = 16_000
/** 单次录音上限：60 秒约 1.9MB，超过后自动停止，避免误触后长传 */
export const MAX_RECORD_MS = 60_000

type AudioContextCtor = typeof AudioContext

function audioContextCtor(): AudioContextCtor | null {
  const w = window as unknown as {
    AudioContext?: AudioContextCtor
    webkitAudioContext?: AudioContextCtor
  }
  return w.AudioContext ?? w.webkitAudioContext ?? null
}

export function canRecord(): boolean {
  const media = navigator.mediaDevices as MediaDevices | undefined
  return Boolean(media && typeof media.getUserMedia === 'function' && audioContextCtor())
}

/** 多相平均降采样：取每个输出样本覆盖的输入窗口平均值，避免直接抽取造成的混叠 */
export function downsample(input: Float32Array, inRate: number, outRate: number): Float32Array {
  if (outRate >= inRate) return input
  const ratio = inRate / outRate
  const out = new Float32Array(Math.floor(input.length / ratio))
  for (let i = 0; i < out.length; i += 1) {
    const start = Math.floor(i * ratio)
    const end = Math.min(input.length, Math.floor((i + 1) * ratio))
    let sum = 0
    for (let j = start; j < end; j += 1) sum += input[j]
    out[i] = end > start ? sum / (end - start) : 0
  }
  return out
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i))
}

/** Float32 [-1,1] → PCM16 WAV 字节 */
export function encodeWav(samples: Float32Array, sampleRate = TARGET_SAMPLE_RATE): Blob {
  const bytes = samples.length * 2
  const buffer = new ArrayBuffer(44 + bytes)
  const view = new DataView(buffer)
  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + bytes, true)
  writeAscii(view, 8, 'WAVE')
  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true) // PCM
  view.setUint16(22, 1, true) // 单声道
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeAscii(view, 36, 'data')
  view.setUint32(40, bytes, true)
  let offset = 44
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true)
    offset += 2
  }
  return new Blob([buffer], { type: 'audio/wav' })
}

export class WavRecorder {
  private stream: MediaStream | null = null
  private ctx: AudioContext | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private processor: ScriptProcessorNode | null = null
  private chunks: Float32Array[] = []
  private startedAt = 0

  get durationMs(): number {
    return this.startedAt ? Date.now() - this.startedAt : 0
  }

  async start(onAutoStop?: () => void): Promise<void> {
    const Ctor = audioContextCtor()
    if (!Ctor || !navigator.mediaDevices?.getUserMedia) {
      throw new Error('UNSUPPORTED')
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    })
    this.ctx = new Ctor()
    this.source = this.ctx.createMediaStreamSource(this.stream)
    // ScriptProcessor 已被标记废弃，但内嵌 WebView（尤其 WKWebView）兼容性最稳；
    // 处理块 4096 在 16k 输出下约 85ms 一次，足够跟手且不占主线程。
    this.processor = this.ctx.createScriptProcessor(4096, 1, 1)
    this.chunks = []
    this.processor.onaudioprocess = event => {
      this.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)))
      if (onAutoStop && this.durationMs >= MAX_RECORD_MS) onAutoStop()
    }
    this.source.connect(this.processor)
    this.processor.connect(this.ctx.destination)
    this.startedAt = Date.now()
  }

  private teardown(): void {
    try {
      if (this.processor) {
        this.processor.onaudioprocess = null
        this.processor.disconnect()
      }
      this.source?.disconnect()
    } catch {
      /* 断开节点失败不影响已采集的数据 */
    }
    this.stream?.getTracks().forEach(track => track.stop())
    void this.ctx?.close().catch(() => undefined)
    this.processor = null
    this.source = null
    this.stream = null
    this.ctx = null
  }

  /** 停止并返回 16k 单声道 WAV；没有采到数据时返回空 Blob */
  stop(): Blob {
    const inputRate = this.ctx?.sampleRate ?? TARGET_SAMPLE_RATE
    const total = this.chunks.reduce((sum, chunk) => sum + chunk.length, 0)
    let merged = new Float32Array(total)
    let offset = 0
    for (const chunk of this.chunks) {
      merged.set(chunk, offset)
      offset += chunk.length
    }
    this.chunks = []
    this.teardown()
    this.startedAt = 0
    if (!total) return new Blob([], { type: 'audio/wav' })
    merged = downsample(merged, inputRate, TARGET_SAMPLE_RATE)
    return encodeWav(merged)
  }

  abort(): void {
    this.chunks = []
    this.teardown()
    this.startedAt = 0
  }
}

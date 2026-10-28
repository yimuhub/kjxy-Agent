/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_USE_MOCK?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

/** 构建时由 vite.config.ts 注入（package.json 的 version） */
declare const __APP_VERSION__: string

declare module 'element-plus/dist/locale/zh-cn.mjs' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const zhCn: any
  export default zhCn
}

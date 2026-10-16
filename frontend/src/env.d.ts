/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_USE_MOCK?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module 'element-plus/dist/locale/zh-cn.mjs' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const zhCn: any
  export default zhCn
}

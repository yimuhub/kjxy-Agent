import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { fileURLToPath, URL } from 'node:url'

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8')) as {
  version: string
}

// https://vite.dev/config/
export default defineConfig({
  // 版本握手用：前端把自身版本编译进产物，和后端 /api/v1/meta 比对
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  plugins: [
    vue(),
    // 模板中的 el-* 组件按需引入，避免整包打进首屏（样式仍由 main.ts 全量引入）
    Components({
      resolvers: [ElementPlusResolver({ importStyle: false })],
      dts: 'src/components.d.ts',
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      // 开发时代理后端 API，避免跨域；后端默认 http://localhost:8000
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // 业务页已按路由懒加载；框架单独成块，组件库交给 Rollup 按需拆分
    rollupOptions: {
      output: {
        manualChunks: {
          'vue-vendor': ['vue', 'vue-router', 'pinia'],
          // 组件库单独成块：业务迭代频繁重发时，用户浏览器可继续命中
          // element-plus 的长缓存（约占主 chunk 一半体积）
          'element-plus': ['element-plus'],
        },
      },
    },
    chunkSizeWarningLimit: 700,
  },
})

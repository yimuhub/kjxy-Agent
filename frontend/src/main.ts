import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/design-system.css'

import App from './App.vue'
import router from './router'

// 设计系统 v2「静夜玻璃」为暗色主题：在样式装载前给 html 挂 dark 类
document.documentElement.classList.add('dark')

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')

<template>
  <div class="setup-page">
    <div class="brand">
      <div class="logo">🌾</div>
      <div class="name">{{ brandName || '科技小院智能体平台' }}</div>
      <div class="ver">v{{ appVersion }} 初始化向导</div>
    </div>

    <div class="steps">
      <template v-for="(s, i) in STEPS" :key="s">
        <div
          class="step"
          :class="{ active: cur === i + 1, done: cur > i + 1 }"
        >
          <div class="dot">{{ cur > i + 1 ? '✓' : i + 1 }}</div>
          <div class="lbl">{{ s }}</div>
        </div>
        <div
          v-if="i < STEPS.length - 1"
          class="step-line"
          :class="{ done: cur > i + 1 }"
        />
      </template>
    </div>

    <el-card class="pane-card">
      <!-- 1 欢迎 -->
      <div v-show="cur === 1">
        <h2>欢迎使用科技小院智能体平台</h2>
        <p class="desc">
          系统检测到这是<b>全新部署</b>（尚未创建管理员账号）。向导将带你在 5 分钟内完成：
          创建超级管理员 → 小院信息 → 品牌外观 → 功能配置，完成后即可进入系统。
        </p>
        <div class="security-note">
          🔒 安全说明：本向导只在「还没有任何管理员账号」时出现；初始化完成后永久关闭，
          再次访问直接进入登录页。口令强度实时校验。
        </div>
        <div class="field">
          <label>部署形态（影响默认配置，创建后可改）</label>
          <div class="mode-grid">
            <div
              v-for="m in DEPLOY_MODES"
              :key="m.key"
              class="mode-card"
              :class="{ sel: form.deployment === m.key }"
              @click="form.deployment = m.key"
            >
              <div class="t">{{ m.t }}</div>
              <div class="d">{{ m.d }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 2 超管 -->
      <div v-show="cur === 2">
        <h2>创建超级管理员</h2>
        <p class="desc">该账号拥有最高权限（平台管理员 + 小院负责人），请牢记口令。其他账号后续在「成员管理」创建。</p>
        <div class="field">
          <label>登录账号 <span class="req">*</span></label>
          <el-input
            v-model="form.username"
            maxlength="32"
            placeholder="3-32 位字母、数字或下划线，如 admin"
          />
        </div>
        <div class="grid2">
          <div class="field">
            <label>显示名 <span class="req">*</span></label>
            <el-input
              v-model="form.display_name"
              maxlength="64"
              placeholder="如：张老师"
            />
          </div>
          <div class="field">
            <label>联系邮箱（可选）</label>
            <el-input
              v-model="form.email"
              maxlength="255"
              placeholder="用于找回凭据"
            />
          </div>
        </div>
        <div class="grid2">
          <div class="field">
            <label>设置口令 <span class="req">*</span></label>
            <el-input
              v-model="form.password"
              type="password"
              show-password
              maxlength="128"
              placeholder="至少 10 位，含字母与数字"

            />
            <div class="pwd-meter">
              <i :style="{ width: pwdBar + '%', background: pwdColor }" />
            </div>
            <div
              class="hint"
              :class="pwdHintClass"
            >
              强度：{{ pwdHint }}
            </div>
          </div>
          <div class="field">
            <label>确认口令 <span class="req">*</span></label>
            <el-input
              v-model="form.password2"
              type="password"
              show-password
              maxlength="128"
              placeholder="再输入一次"
            />
            <div
              v-if="form.password2"
              class="hint"
              :class="form.password2 === form.password ? 'ok' : 'bad'"
            >
              {{ form.password2 === form.password ? '✓ 一致' : '✗ 不一致' }}
            </div>
          </div>
        </div>
        <div class="security-note warn">
          ⚠️ 生产环境系统将拒绝演示弱口令；此账号创建后请立即妥善保管。
        </div>
      </div>

      <!-- 3 小院 -->
      <div v-show="cur === 3">
        <h2>小院信息</h2>
        <p class="desc">创建第一个小院（后续可再建更多），并选择产业画像——它决定官方技能包的推荐。</p>
        <div class="field">
          <label>小院名称 <span class="req">*</span></label>
          <el-input
            v-model="form.yard_name"
            maxlength="128"
            placeholder="如：江苏（句容）葡萄科技小院"
          />
        </div>
        <div class="grid2">
          <div class="field">
            <label>所在省</label>
            <el-input
              v-model="form.province"
              maxlength="64"
              placeholder="如：江苏"
            />
          </div>
          <div class="field">
            <label>所在市</label>
            <el-input
              v-model="form.city"
              maxlength="64"
              placeholder="如：镇江"
            />
          </div>
        </div>
        <div class="field">
          <label>产业画像（多选，决定官方技能包推荐）</label>
          <div class="chips">
            <span
              v-for="t in PROFILE_TYPES"
              :key="t.key"
              class="chip"
              :class="{ sel: form.profile_types.includes(t.key) }"
              @click="toggleType(t.key)"
            >
              {{ t.label }}
            </span>
          </div>
        </div>
        <div class="field">
          <label>小院简介（可选）</label>
          <el-input
            v-model="form.yard_description"
            maxlength="64"
            placeholder="一句话介绍，显示在门户与分享页"
          />
        </div>
      </div>

      <!-- 4 品牌 -->
      <div v-show="cur === 4">
        <h2>品牌与外观</h2>
        <p class="desc">平台级品牌包：登录页与门户即时生效（仅超管可改全站品牌）。</p>
        <div class="field">
          <label>平台名称</label>
          <el-input
            v-model="form.app_name"
            maxlength="40"
            placeholder="科技小院智能体平台"
          />
        </div>
        <div class="field">
          <label>主色调</label>
          <div class="chips">
            <span
              v-for="c in COLORS"
              :key="c.v"
              class="chip"
              :class="{ sel: form.primary_color === c.v }"
              :style="{ color: c.v }"
              @click="form.primary_color = c.v"
            >
              ● {{ c.label }}
            </span>
          </div>
        </div>
        <div class="field">
          <label>Logo 上传</label>
          <div class="soft-note">Logo 进入系统后在「系统设置 → 品牌与门户」上传；本步先定名称与主色。</div>
        </div>
      </div>

      <!-- 5 功能配置 -->
      <div v-show="cur === 5">
        <h2>功能配置</h2>
        <p class="desc">以下均可用推荐值直接完成，进入系统后随时在「系统设置」调整。</p>
        <div class="switch-row">
          <div>
            <div class="t">联网搜索</div>
            <div class="d">内置必应/搜狗/360 免密钥源；更强搜索可后续配 Tavily Key</div>
          </div>
          <el-switch v-model="form.search_enabled" />
        </div>
        <div class="switch-row">
          <div>
            <div class="t">一句话生成智能体</div>
            <div class="d">每人每日 {{ form.agent_generate_daily_limit }} 次（0-200 可调）</div>
          </div>
          <el-switch v-model="form.agent_generate_enabled" />
        </div>
        <div class="switch-row">
          <div>
            <div class="t">工作日志与考核牵引</div>
            <div class="d">日志 100 篇/年 · 驻点 120 天 官方锚点进度条</div>
          </div>
          <el-switch v-model="form.journal_enabled" />
        </div>
        <div class="field">
          <label>每日生成上限</label>
          <el-input-number
            v-model="form.agent_generate_daily_limit"
            :min="0"
            :max="200"
          />
        </div>
        <div class="field">
          <label>大模型供应商</label>
          <div class="chips">
            <span
              v-for="m in LLM_CHOICES"
              :key="m"
              class="chip"
              :class="{ sel: llmChoice === m }"
              @click="llmChoice = m"
            >
              {{ m }}
            </span>
          </div>
          <div class="soft-note">选「云端 API」或「本地模型」的，进入系统后到「模型供应商」填 Key / 地址。</div>
        </div>
      </div>

      <!-- 6 完成 -->
      <div v-show="cur === 6">
        <div class="done-icon">✓</div>
        <h2 class="center">初始化完成！</h2>
        <p class="desc center">
          配置已写入系统。点击下方按钮，用超级管理员 <b>{{ form.username }}</b> 登录进入系统。
        </p>
        <div class="summary">
          <div class="row"><span class="k">超级管理员</span><span>{{ form.username }}（{{ form.display_name }}）</span></div>
          <div class="row"><span class="k">首个小院</span><span>{{ form.yard_name }}</span></div>
          <div class="row"><span class="k">产业画像</span><span>{{ profileText }}</span></div>
          <div class="row"><span class="k">运营形态</span><span>{{ deployLabel }}</span></div>
          <div class="row"><span class="k">品牌</span><span>{{ form.app_name || '科技小院智能体平台' }} · {{ form.primary_color || '默认色' }}</span></div>
          <div class="row"><span class="k">功能开关</span><span>联网搜索 {{ form.search_enabled ? '✓' : '✗' }} · 一句话生成 {{ form.agent_generate_enabled ? `✓（${form.agent_generate_daily_limit} 次/日）` : '✗' }}</span></div>
          <div class="row"><span class="k">大模型</span><span>{{ llmChoice }}</span></div>
        </div>
      </div>
    </el-card>

    <div class="footer">
      <el-button
        v-show="cur > 1"
        @click="cur--"
      >
        上一步
      </el-button>
      <span style="flex:1" />
      <el-button
        v-show="cur < 6"
        type="primary"
        :loading="submitting"
        @click="next"
      >
        {{ cur === 1 ? '开始初始化 →' : '下一步 →' }}
      </el-button>
      <el-button
        v-show="cur === 6"
        type="primary"
        @click="goLogin"
      >
        进入系统 🚀
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import { setupApi } from '@/api/setup'
  import { useAuthStore } from '@/stores/auth'
  import { APP_VERSION } from '@/utils/version'

  /**
   * 初始化向导（v3.69）：仅当系统无任何用户时可进入（路由守卫强制），
   * 完成后调 /setup/initialize 一次性写入超管/小院/画像/品牌/开关。
   */
  const router = useRouter()
  const auth = useAuthStore()

  const STEPS = ['欢迎', '超级管理员', '小院信息', '品牌外观', '功能配置', '完成']
  const DEPLOY_MODES = [
    { key: 'desktop', t: '🖥 单机桌面版', d: '一台电脑自用，离线可用，本地大模型' },
    { key: 'server', t: '🏗 院内服务器版', d: '小院内网部署，多人同时使用' },
    { key: 'cloud', t: '☁ 云端多小院版', d: '公网服务多个小院/集群，含监管' },
  ]
  const PROFILE_TYPES = [
    { key: 'rice', label: '🌾 水稻/稻麦' },
    { key: 'aquaculture', label: '🦀 水产蟹虾' },
    { key: 'fruit', label: '🍇 应时鲜果' },
    { key: 'vegetable', label: '🥬 蔬菜瓜果' },
    { key: 'tea', label: '🍵 茶叶' },
    { key: 'livestock', label: '🐑 畜禽' },
    { key: 'smart_agri', label: '📡 智慧农业/农文旅' },
    { key: 'general', label: '🧰 通用' },
  ]
  const COLORS = [
    { v: '#6f8dff', label: '科技蓝（默认）' },
    { v: '#3f9d68', label: '田园绿' },
    { v: '#e0a83c', label: '丰收金' },
    { v: '#c56fff', label: '智慧紫' },
  ]
  const LLM_CHOICES = ['稍后配置（推荐）', '本地模型（Ollama 等）', '云端 API（现在填 Key）']

  const cur = ref(1)
  const submitting = ref(false)
  const brandName = ref('')
  const llmChoice = ref(LLM_CHOICES[0])

  const form = reactive({
    deployment: 'desktop',
    username: 'admin',
    display_name: '',
    email: '',
    password: '',
    password2: '',
    yard_name: '',
    province: '',
    city: '',
    profile_types: ['general'] as string[],
    yard_description: '',
    app_name: '',
    primary_color: '#6f8dff',
    search_enabled: true,
    agent_generate_enabled: true,
    agent_generate_daily_limit: 10,
    journal_enabled: true,
  })

  const pwdScore = computed(() => {
    const v = form.password
    if (!v) return 0
    let score = 0
    if (v.length >= 10) score++
    if (/[A-Za-z]/.test(v) && /\d/.test(v)) score++
    if (/[^A-Za-z0-9]/.test(v)) score++
    return score
  })
  const pwdBar = computed(() =>
    form.password ? Math.min(100, pwdScore.value * 34 + form.password.length) : 0,
  )
  const pwdColor = computed(() => (pwdScore.value >= 3 ? '#3f9d68' : pwdScore.value === 2 ? '#e0a83c' : '#ff7b72'))
  const pwdHint = computed(() =>
    pwdScore.value >= 3 ? '强 ✓' : pwdScore.value === 2 ? '中' : form.password ? '弱（建议混合字母数字与符号）' : '—',
  )
  const pwdHintClass = computed(() => ({ ok: pwdScore.value >= 3, bad: pwdScore.value < 2 }))

  const profileText = computed(() =>
    form.profile_types
      .map(k => PROFILE_TYPES.find(t => t.key === k)?.label.replace(/^\S+\s/, '') ?? k)
      .join(' · ') || '—',
  )
  const deployLabel = computed(() => DEPLOY_MODES.find(m => m.key === form.deployment)?.t ?? form.deployment)

  const appVersion = APP_VERSION

  function toggleType(key: string): void {
    const i = form.profile_types.indexOf(key)
    if (i >= 0) form.profile_types.splice(i, 1)
    else form.profile_types.push(key)
  }

  function next(): void {
    if (cur.value === 2) {
      if (form.username.trim().length < 3) return void ElWarn('请填写登录账号（3-32 位）')
      if (!form.display_name.trim()) return void ElWarn('请填写显示名')
      if (form.password.length < 10) return void ElWarn('口令至少 10 位')
      if (!( /[A-Za-z]/.test(form.password) && /\d/.test(form.password))) return void ElWarn('口令必须同时包含字母与数字')
      if (form.password !== form.password2) return void ElWarn('两次口令不一致')
    }
    if (cur.value === 3 && !form.yard_name.trim()) return void ElWarn('请填写小院名称')
    if (cur.value === 5) {
      // 第 5 步 →「完成」：真正调用 /setup/initialize 落库，成功才进入第 6 步
      void submitSetup()
      return
    }
    if (cur.value < 6) {
      cur.value++
    } else {
      goLogin()
    }
  }

  /**
   * 一次性写入超管/小院/画像/品牌/开关，并接管登录态。
   * 失败则停留当前步并明确报错，绝不显示"已完成"的假状态。
   */
  async function submitSetup(): Promise<void> {
    if (submitting.value) return
    submitting.value = true
    try {
      const res = await setupApi.initialize({
        username: form.username.trim(),
        password: form.password,
        display_name: form.display_name.trim(),
        email: form.email.trim() || null,
        yard_name: form.yard_name.trim(),
        province: form.province.trim(),
        city: form.city.trim(),
        profile_types: [...form.profile_types],
        yard_description: form.yard_description.trim(),
        app_name: form.app_name.trim(),
        primary_color: form.primary_color,
        search_enabled: form.search_enabled,
        agent_generate_enabled: form.agent_generate_enabled,
        agent_generate_daily_limit: form.agent_generate_daily_limit,
        journal_enabled: form.journal_enabled,
        deployment: form.deployment,
      })
      auth.adoptSetupSession(res.access_token)
      try {
        await auth.fetchMe()
      } catch {
        /* 进入系统后路由守卫会再次拉取 /auth/me */
      }
      cur.value = 6
    } catch (e) {
      const msg =
        (e as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error
          ?.message || (e as Error)?.message || '初始化失败，请稍后重试'
      showError(msg)
    } finally {
      submitting.value = false
    }
  }

  function ElWarn(msg: string): void {
    import('element-plus').then(({ ElMessage }) => ElMessage.warning(msg))
  }

  function showError(msg: string): void {
    import('element-plus').then(({ ElMessage }) => ElMessage.error(msg))
  }

  function goLogin(): void {
    // 提交成功后已接管登录态，直接进系统；否则退回登录页手动登录
    if (auth.isLoggedIn()) {
      router.replace('/')
    } else {
      router.push({ path: '/login', query: { username: form.username } })
    }
  }

  onMounted(async () => {
    try {
      const st = await setupApi.status()
      if (st.initialized) router.replace('/login')
    } catch {
      /* 状态取不到时按向导可用处理 */
    }
  })
</script>

<style scoped src="./setup-page.css" />

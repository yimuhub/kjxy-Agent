<template>
  <div class="setup-shell">
    <div class="setup-grid">
      <aside class="rail">
        <div class="brand">
          <div class="mark">
            小院
          </div>
          <div class="brand-txt">
            <strong>科技小院</strong>
            <span>首次安装 · v{{ version }}</span>
          </div>
        </div>
        <div class="progress">
          <div class="progress-top">
            <span>安装进度</span><span>{{ cur }} / 7</span>
          </div>
          <div class="progress-bar">
            <i :style="{ width: `${(cur / 7) * 100}%` }" />
          </div>
        </div>
        <ol class="steps">
          <li
            v-for="(s, i) in STEPS"
            :key="s.title"
            :class="['step', cur > i + 1 ? 'done' : cur === i + 1 ? 'current' : 'future']"
          >
            <span class="num">{{ cur > i + 1 ? '✓' : i + 1 }}</span>
            <span class="lab">{{ s.title }}<small>{{ s.sub }}</small></span>
          </li>
        </ol>
        <div class="rail-foot">
          <p>不预置任何账号或口令，管理员由你此刻创建；口令只以 bcrypt 哈希保存。</p>
          <p>数据只写入本机目录，服务仅监听 127.0.0.1。</p>
        </div>
      </aside>

      <section class="main">
        <div class="pane-wrap">
          <div
            v-show="cur === 1"
            class="pane"
            data-setup-step="1"
          >
            <p class="eyebrow">
              第 1 步 · 共 7 步
            </p>
            <h1>欢迎建立你的科技小院</h1>
            <p class="lead">
              这台电脑将成为小院的数据中心。下面会依次创建管理员、建立小院、挑选能力与外观——全程不会写入任何默认账号。
            </p>
            <div class="cards">
              <button
                v-for="d in DEPLOY"
                :key="d.key"
                type="button"
                :class="['opt-card', { sel: form.deployment === d.key }]"
                @click="form.deployment = d.key"
              >
                <span class="tag">{{ d.tag }}</span>
                <b>{{ d.title }}</b>
                <small>{{ d.desc }}</small>
              </button>
            </div>
            <p
              class="hint"
              data-testid="deploy-hint"
            >
              {{ deployHint }}
            </p>
            <div class="panel">
              <div class="panel-h">
                <b>数据落点</b><span class="chip">本机</span>
              </div>
              <div class="kv">
                <span>数据库</span><b>~/Library/Application Support/科技小院/app.db</b>
              </div>
              <div class="kv">
                <span>密钥与令牌</span><b>secret.key · supplier.key · engine.instance.token</b>
              </div>
              <div class="kv">
                <span>监听地址</span><b>{{ localAddress }}</b>
              </div>
              <div class="kv">
                <span>内置语音模型</span><b>随包安装（约 78 MB）</b>
              </div>
            </div>
            <div class="chips">
              <span class="chip">不预置账号</span>
              <span class="chip">数据不出本机</span>
              <span class="chip">随时可导出备份</span>
            </div>
          </div>

          <div
            v-show="cur === 2"
            class="pane"
            data-setup-step="2"
          >
            <p class="eyebrow">
              第 2 步 · 共 7 步
            </p>
            <h1>创建管理员账号</h1>
            <p class="lead">
              这是小院的第一个账号，拥有平台管理员与小院负责人权限。系统不会提供任何默认账号或默认口令。
            </p>
            <div class="grid-2">
              <div>
                <div
                  class="field"
                  :class="{ err: errors.username }"
                >
                  <label>登录账号 <em>*</em><small>3–32 位字母、数字或下划线</small></label>
                  <input
                    id="setup-username"
                    v-model.trim="form.username"
                    class="ctl"
                    autocomplete="username"
                    placeholder="例如 zhangliqun"
                    maxlength="32"
                    @blur="validateField('username')"
                  >
                  <p class="msg">
                    {{ errors.username || '登录时使用；不建议使用 admin / root 等弱名称。' }}
                  </p>
                </div>
                <div
                  class="field"
                  :class="{ err: errors.displayName }"
                >
                  <label>显示名 <em>*</em><small>出现在成员列表与对话署名</small></label>
                  <input
                    id="setup-display-name"
                    v-model.trim="form.displayName"
                    class="ctl"
                    placeholder="例如 张立群"
                    maxlength="20"
                    @blur="validateField('displayName')"
                  >
                  <p class="msg">
                    {{ errors.displayName }}
                  </p>
                </div>
                <div
                  class="field"
                  :class="{ err: errors.password }"
                >
                  <label>登录口令 <em>*</em><small>至少 10 位，含字母与数字</small></label>
                  <div class="pwd-wrap">
                    <input
                      id="setup-password"
                      v-model="form.password"
                      :type="showPwd ? 'text' : 'password'"
                      autocomplete="new-password"
                      placeholder="设置一个只有你知道的口令"
                      @input="validateField('password')"
                    >
                    <button
                      type="button"
                      class="icon-btn"
                      title="显示/隐藏口令"
                      @click="showPwd = !showPwd"
                    >
                      {{ showPwd ? '隐藏' : '显示' }}
                    </button>
                    <button
                      type="button"
                      class="icon-btn"
                      title="生成强口令"
                      @click="generatePassword"
                    >
                      生成
                    </button>
                  </div>
                  <div
                    class="strength"
                    :class="`s${strengthLevel}`"
                  >
                    <i /><i /><i /><i />
                  </div>
                  <div class="rules">
                    <span
                      v-for="r in pwdRules"
                      :key="r.key"
                      :class="['rule', { pass: r.ok, fail: r.req && !r.ok && form.password }]"
                    >
                      <i />{{ r.label }}
                    </span>
                  </div>
                  <p class="msg">
                    {{ errors.password || '口令不可找回，请自行妥善保存。' }}
                  </p>
                </div>
                <div
                  class="field"
                  :class="{ err: errors.password2 }"
                >
                  <label>确认口令 <em>*</em></label>
                  <input
                    id="setup-password2"
                    v-model="form.password2"
                    type="password"
                    class="ctl"
                    autocomplete="new-password"
                    placeholder="再次输入口令"
                    @blur="validateField('password2')"
                  >
                  <p class="msg">
                    {{ errors.password2 }}
                  </p>
                </div>
                <div class="row-2">
                  <div
                    class="field"
                    :class="{ err: errors.email }"
                  >
                    <label>邮箱 <small>选填</small></label>
                    <input
                      v-model.trim="form.email"
                      class="ctl"
                      type="email"
                      placeholder="用于找回与通知"
                      @blur="validateField('email')"
                    >
                    <p class="msg">
                      {{ errors.email }}
                    </p>
                  </div>
                  <div
                    class="field"
                    :class="{ err: errors.phone }"
                  >
                    <label>手机 <small>选填</small></label>
                    <input
                      v-model.trim="form.phone"
                      class="ctl"
                      type="tel"
                      placeholder="11 位手机号"
                      maxlength="11"
                      @blur="validateField('phone')"
                    >
                    <p class="msg">
                      {{ errors.phone }}
                    </p>
                  </div>
                </div>
              </div>
              <aside class="aside">
                <div class="aside-h">
                  账号预览
                </div>
                <div class="acct">
                  <div class="avatar">
                    {{ (form.displayName || form.username || '?').slice(0, 1) }}
                  </div>
                  <div>
                    <b>{{ form.displayName || '未填写显示名' }}</b>
                    <span>@{{ form.username || 'username' }}</span>
                    <div class="badges">
                      <em class="badge">平台管理员</em><em class="badge">小院负责人</em>
                    </div>
                  </div>
                </div>
                <div class="kv">
                  <span>口令保存方式</span><b>bcrypt · 12 轮</b>
                </div>
                <div class="kv">
                  <span>明文落盘</span><b>不会</b>
                </div>
                <div class="kv">
                  <span>写入日志</span><b>不会</b>
                </div>
                <p class="aside-note">
                  创建完成后可用该账号登录；忘记口令需在本机数据目录重置。
                </p>
              </aside>
            </div>
          </div>

          <div
            v-show="cur === 3"
            class="pane"
            data-setup-step="3"
          >
            <p class="eyebrow">
              第 3 步 · 共 7 步
            </p>
            <h1>建立我的小院</h1>
            <p class="lead">
              小院是数据、成员与技能的组织单位。产业画像会决定接下来推荐哪些内置技能。
            </p>
            <div class="grid-2">
              <div>
                <div
                  class="field"
                  :class="{ err: errors.yardName }"
                >
                  <label>小院名称 <em>*</em><small>2–64 个字符</small></label>
                  <input
                    id="setup-yard-name"
                    v-model.trim="form.yardName"
                    class="ctl"
                    placeholder="例如 张家港水稻科技小院"
                    maxlength="64"
                    @blur="validateField('yardName')"
                  >
                  <p class="msg">
                    {{ errors.yardName || '建议使用「地点 + 产业 + 科技小院」的写法，便于成员识别。' }}
                  </p>
                </div>
                <div class="row-3">
                  <div
                    class="field"
                    :class="{ err: errors.province }"
                  >
                    <label>省 / 直辖市 <em>*</em></label>
                    <select
                      id="setup-province"
                      v-model="form.province"
                      class="ctl"
                      @change="onProvinceChange"
                    >
                      <option value="">
                        请选择省份
                      </option>
                      <option
                        v-for="p in provinceNames"
                        :key="p"
                        :value="p"
                      >
                        {{ p }}
                      </option>
                    </select>
                    <p class="msg">
                      {{ errors.province }}
                    </p>
                  </div>
                  <div
                    class="field"
                    :class="{ err: errors.city }"
                  >
                    <label>地市 <em>*</em></label>
                    <select
                      id="setup-city"
                      v-model="form.city"
                      class="ctl"
                      @change="validateField('city')"
                    >
                      <option value="">
                        {{ form.province ? '请选择地市' : '请先选择省份' }}
                      </option>
                      <option
                        v-for="c in cities"
                        :key="c"
                        :value="c"
                      >
                        {{ c }}
                      </option>
                    </select>
                    <p class="msg">
                      {{ errors.city }}
                    </p>
                  </div>
                  <div class="field">
                    <label>县 / 区 <small>选填</small></label>
                    <input
                      v-model.trim="form.county"
                      class="ctl"
                      placeholder="如 张家港市"
                      maxlength="30"
                    >
                    <p class="msg" />
                  </div>
                </div>
                <div
                  class="field"
                  :class="{ err: errors.profiles }"
                >
                  <label>产业画像 <em>*</em><small>可多选</small></label>
                  <div class="chips">
                    <button
                      v-for="p in PROFILES"
                      :key="p.key"
                      type="button"
                      :class="['chip', 'btn-chip', { on: form.profileTypes.includes(p.key) }]"
                      @click="toggleProfile(p.key)"
                    >
                      {{ p.emoji }} {{ p.label }}
                    </button>
                  </div>
                  <p class="msg">
                    {{ errors.profiles }}
                  </p>
                </div>
                <div class="row-2">
                  <div class="field">
                    <label>培养单位 <small>选填</small></label>
                    <input
                      v-model.trim="form.school"
                      class="ctl"
                      placeholder="如 南京农业大学"
                      maxlength="40"
                    >
                  </div>
                  <div class="field">
                    <label>主导产业 <small>选填</small></label>
                    <input
                      v-model.trim="form.industry"
                      class="ctl"
                      placeholder="如 优质稻米"
                      maxlength="30"
                    >
                  </div>
                </div>
                <div class="field">
                  <label>小院简介 <small>选填 · {{ form.description.length }} / 64</small></label>
                  <textarea
                    v-model.trim="form.description"
                    class="ctl area"
                    maxlength="64"
                    placeholder="一句话说明小院在做什么，例如：稻麦轮作与优质食味稻米技术示范。"
                  />
                  <p class="msg" />
                </div>
              </div>
              <aside class="aside">
                <div class="aside-h">
                  小院预览
                </div>
                <div class="yard-card">
                  <span class="loc">{{ locText || '未选择地区' }}</span>
                  <b>{{ form.yardName || '你的小院' }}</b>
                  <div class="badges">
                    <em
                      v-for="k in form.profileTypes"
                      :key="k"
                      class="badge"
                    >{{ profileLabel(k) }}</em>
                    <em
                      v-if="!form.profileTypes.length"
                      class="badge"
                    >尚未选择画像</em>
                  </div>
                  <p>{{ form.description || '简介会显示在小院首页与成员邀请页。' }}</p>
                </div>
                <div class="kv">
                  <span>已勾选技能</span><b>{{ selectedSkills.size }} 个</b>
                </div>
                <div class="kv">
                  <span>负责人</span><b>@{{ form.username || 'username' }}</b>
                </div>
                <p class="aside-note">
                  地区与画像之后可在「设置 → 小院档案」中修改，技能可随时增删。
                </p>
              </aside>
            </div>
          </div>

          <div
            v-show="cur === 4"
            class="pane"
            data-setup-step="4"
          >
            <p class="eyebrow">
              第 4 步 · 共 7 步
            </p>
            <h1>智能能力与模型</h1>
            <p class="lead">
              先决定小院会哪些本事，再决定由哪个模型来驱动。模型可以稍后再配，不影响先建院。
            </p>
            <div class="panel">
              <div class="panel-h">
                <b>内置技能</b>
                <span class="chip">{{ selectedSkills.size }} / {{ catalog.length }}</span>
                <span class="grow" />
                <button
                  type="button"
                  class="mini-btn"
                  @click="selectAllSkills(true)"
                >
                  全选
                </button>
                <button
                  type="button"
                  class="mini-btn"
                  @click="selectAllSkills(false)"
                >
                  清空
                </button>
              </div>
              <p
                v-if="skillProfileLabels.length"
                class="skill-hint"
              >
                已按你的产业画像（{{ skillProfileLabels.join('、') }}）预选 {{ skillRecommendedCount }} 个；其余技能随时可在技能库添加。
              </p>
              <div class="skill-tools">
                <input
                  v-model.trim="skillQuery"
                  class="ctl skill-search"
                  type="search"
                  placeholder="搜索技能，如「病虫害」「灌溉」「PPT」"
                >
                <div class="chips">
                  <button
                    type="button"
                    :class="['chip', 'btn-chip', { on: skillGroupFilter === 'all' }]"
                    @click="skillGroupFilter = 'all'"
                  >
                    全部 {{ catalog.length }}
                  </button>
                  <button
                    type="button"
                    :class="['chip', 'btn-chip', { on: skillGroupFilter === 'recommended' }]"
                    @click="skillGroupFilter = 'recommended'"
                  >
                    推荐 {{ skillRecommendedCount }}
                  </button>
                  <button
                    v-for="g in skillGroups"
                    :key="g.key"
                    type="button"
                    :class="['chip', 'btn-chip', { on: skillGroupFilter === g.key }]"
                    @click="skillGroupFilter = g.key"
                  >
                    {{ g.label }} {{ g.total }}
                  </button>
                </div>
              </div>
              <div
                v-if="skillsLoading"
                class="skill-loading"
              >
                正在读取内置技能…
              </div>
              <div
                v-else
                class="skill-list"
              >
                <template
                  v-for="group in visibleSkillGroups"
                  :key="group.key"
                >
                  <p
                    v-if="group.label"
                    class="skill-group"
                  >
                    {{ group.label }} · {{ group.items.length }}
                  </p>
                  <label
                    v-for="s in group.items"
                    :key="s.code"
                    :class="['skill-row', { on: selectedSkills.has(s.code) }]"
                  >
                    <input
                      type="checkbox"
                      :checked="selectedSkills.has(s.code)"
                      @change="toggleSkill(s.code)"
                    >
                    <span class="skill-txt"><b>{{ s.name }}</b><small>{{ s.description }}</small></span>
                    <span class="skill-tags">
                      <em
                        v-if="s.recommended"
                        class="pack rec"
                      >推荐</em>
                      <em class="pack">{{ s.pack }}</em>
                    </span>
                  </label>
                </template>
                <p
                  v-if="!visibleSkillGroups.length"
                  class="skill-loading"
                >
                  没有匹配的技能，换个关键词或分类看看。
                </p>
              </div>
            </div>
            <div class="panel">
              <div class="panel-h">
                <b>功能开关</b><span class="chip">创建后仍可修改</span>
              </div>
              <div class="switch-row">
                <div class="txt">
                  <b>联网搜索</b><small>使用免费搜索源补充田间情报，单次查询 3–7 秒</small>
                </div>
                <button
                  type="button"
                  :class="['switch', { on: form.searchEnabled }]"
                  @click="form.searchEnabled = !form.searchEnabled"
                >
                  <i />
                </button>
              </div>
              <div class="switch-row">
                <div class="txt">
                  <b>智能体自动生成</b><small>在对话里用一句话创建助手，受每日上限约束</small>
                </div>
                <div class="stepper">
                  <button
                    type="button"
                    @click="form.dailyLimit = Math.max(1, form.dailyLimit - 1)"
                  >
                    −
                  </button>
                  <span>{{ form.dailyLimit }}</span>
                  <button
                    type="button"
                    @click="form.dailyLimit = Math.min(200, form.dailyLimit + 1)"
                  >
                    +
                  </button>
                </div>
                <button
                  type="button"
                  :class="['switch', { on: form.agentGenerate }]"
                  @click="form.agentGenerate = !form.agentGenerate"
                >
                  <i />
                </button>
              </div>
              <div class="switch-row">
                <div class="txt">
                  <b>工作日志</b><small>按角色开放农事记录与日志导出，进入系统后可调权限</small>
                </div>
                <span class="chip">默认开放</span>
              </div>
            </div>
            <div class="panel">
              <div class="panel-h">
                <b>模型接入</b><span class="chip">选择会真正写入供应商配置</span>
              </div>
              <div class="mode-cards">
                <button
                  type="button"
                  :class="['mode-card', { on: form.modelMode === 'later' }]"
                  @click="form.modelMode = 'later'"
                >
                  <b>稍后配置</b><small>先用文档、台账与技能；进入系统后在模型中心接入</small>
                </button>
                <button
                  type="button"
                  :class="['mode-card', { on: form.modelMode === 'ollama' }]"
                  @click="form.modelMode = 'ollama'"
                >
                  <b>本地模型</b><small>接入 Ollama 的 OpenAI 兼容接口，数据不出本机</small>
                </button>
                <button
                  type="button"
                  :class="['mode-card', { on: form.modelMode === 'cloud' }]"
                  @click="form.modelMode = 'cloud'"
                >
                  <b>云端 API</b><small>填 Key 后加密保存</small>
                </button>
              </div>
              <p
                v-if="form.modelMode === 'later'"
                class="hint"
                style="padding: 12px 15px 16px"
              >
                选择「稍后配置」时，本次不会写入任何供应商与 Key；对话页会提示先接入模型。
              </p>
              <div
                v-if="form.modelMode === 'ollama'"
                class="model-form"
              >
                <div class="row-2">
                  <div class="field">
                    <label>Ollama 地址</label>
                    <input
                      v-model.trim="form.ollamaUrl"
                      class="ctl"
                      placeholder="http://127.0.0.1:11434"
                    >
                  </div>
                  <div class="field">
                    <label>模型名</label>
                    <input
                      v-model.trim="form.ollamaModel"
                      class="ctl"
                      placeholder="如 qwen2.5:14b"
                    >
                  </div>
                </div>
                <p class="hint">
                  本地模型会以 OpenAI 兼容协议注册，Key 使用占位值本地保存。
                </p>
              </div>
              <div
                v-if="form.modelMode === 'cloud'"
                class="model-form"
              >
                <div class="row-2">
                  <div class="field">
                    <label>供应商</label>
                    <select
                      v-model="form.cloudProvider"
                      class="ctl"
                      @change="onProviderChange"
                    >
                      <option
                        v-for="p in PROVIDERS"
                        :key="p.key"
                        :value="p.key"
                      >
                        {{ p.label }}
                      </option>
                    </select>
                  </div>
                  <div class="field">
                    <label>模型名</label>
                    <input
                      v-model.trim="form.cloudModel"
                      class="ctl"
                      placeholder="例如 deepseek-chat"
                    >
                  </div>
                </div>
                <div class="field">
                  <label>接口地址</label>
                  <input
                    v-model.trim="form.cloudUrl"
                    class="ctl"
                    placeholder="https://api.example.com/v1"
                  >
                </div>
                <div class="field">
                  <label>API Key <small>加密保存，界面与日志不回显</small></label>
                  <input
                    v-model.trim="form.cloudKey"
                    class="ctl"
                    type="password"
                    placeholder="sk-…"
                    autocomplete="off"
                  >
                </div>
                <p class="hint">
                  创建时会做一次轻量校验：地址需以 http(s):// 开头，Key 与模型名必填。
                </p>
              </div>
            </div>
          </div>

          <div
            v-show="cur === 5"
            class="pane"
            data-setup-step="5"
          >
            <p class="eyebrow">
              第 5 步 · 共 7 步
            </p>
            <h1>品牌与外观</h1>
            <p class="lead">
              让小院看起来像你自己的。主色会立即应用到按钮、选中态与图表；Logo 与主题进入系统后继续调整。
            </p>
            <div class="grid-2">
              <div>
                <div
                  class="field"
                  :class="{ err: errors.appName }"
                >
                  <label>应用名称 <em>*</em><small>显示在窗口标题与登录页</small></label>
                  <input
                    id="setup-app-name"
                    v-model.trim="form.appName"
                    class="ctl"
                    maxlength="20"
                    placeholder="科技小院"
                    @blur="validateField('appName')"
                  >
                  <p class="msg">
                    {{ errors.appName }}
                  </p>
                </div>
                <div class="field">
                  <label>主色</label>
                  <div class="swatches">
                    <button
                      v-for="a in ACCENTS"
                      :key="a.value"
                      type="button"
                      :class="['sw', { on: form.primaryColor.toUpperCase() === a.value.toUpperCase() }]"
                      :style="{ background: a.value }"
                      :title="a.label"
                      @click="form.primaryColor = a.value"
                    />
                    <input
                      v-model="form.primaryColor"
                      type="color"
                      title="自定义主色"
                    >
                  </div>
                </div>
                <div class="field">
                  <label>登录页欢迎语 <small>选填 · 仅本次预览</small></label>
                  <input
                    v-model.trim="form.welcome"
                    class="ctl"
                    maxlength="40"
                    placeholder="把论文写在田野大地上。"
                  >
                </div>
                <p class="hint">
                  Logo 与明暗主题可在「设置 → 品牌与门户」上传和切换，避免安装阶段被图片大小卡住。
                </p>
              </div>
              <aside class="aside">
                <div class="aside-h">
                  实时预览
                </div>
                <div
                  class="preview-login"
                  :style="{ '--preview-brand': form.primaryColor }"
                >
                  <div class="plogo">
                    {{ (form.appName || '小院').slice(0, 2) }}
                  </div>
                  <b>{{ form.appName || '科技小院' }}</b>
                  <small>{{ form.welcome || '把论文写在田野大地上。' }}</small>
                  <span class="pinput">登录账号</span>
                  <span class="pinput">口令</span>
                  <span class="pbtn">进入小院</span>
                </div>
                <p class="aside-note">
                  主色同时作用于图表与状态标签；暗色下会自动提升对比度。
                </p>
              </aside>
            </div>
          </div>

          <div
            v-show="cur === 6"
            class="pane"
            data-setup-step="6"
          >
            <p class="eyebrow">
              第 6 步 · 共 7 步
            </p>
            <h1>确认并创建</h1>
            <p class="lead">
              以下内容会一次性写入本机数据库。任何一步失败都会整体回滚，不会留下半个账号或空小院。
            </p>
            <div class="sum-grid">
              <div
                v-for="g in summary"
                :key="g.title"
                class="sum"
              >
                <div class="sum-h">
                  <b>{{ g.title }}</b><button
                    type="button"
                    class="edit"
                    @click="cur = g.step"
                  >
                    修改
                  </button>
                </div>
                <div class="sum-b">
                  <div
                    v-for="r in g.rows"
                    :key="r[0]"
                    class="sum-row"
                  >
                    <span>{{ r[0] }}</span><b>{{ r[1] }}</b>
                  </div>
                </div>
              </div>
            </div>
            <label
              class="check-line"
              :class="{ on: savedPwdAck }"
            >
              <input
                id="setup-saved-pwd"
                v-model="savedPwdAck"
                type="checkbox"
              >
              <span class="txt"><b>我已妥善保存管理员口令</b><small>口令只保存哈希，创建后无法找回。</small></span>
            </label>
            <div
              v-if="submitting"
              class="create-box"
            >
              <div class="create-bar">
                <i :style="{ width: `${createPercent}%` }" />
              </div>
              <p class="hint">
                {{ createStage }}
              </p>
            </div>
            <div
              v-if="submitError"
              class="err-card"
            >
              <b>创建失败</b>
              <p>{{ submitError }}</p>
              <button
                type="button"
                class="btn"
                @click="submit"
              >
                重试
              </button>
            </div>
          </div>

          <div
            v-show="cur === 7"
            class="pane"
            data-setup-step="7"
          >
            <p class="eyebrow">
              安装完成
            </p>
            <h1>你的科技小院已就绪</h1>
            <p class="lead">
              管理员与「{{ done?.yard_name || form.yardName }}」已写入本机数据库，接下来直接进入工作台。
            </p>
            <div class="receipt">
              <div class="rt">
                本次写入回执
              </div>
              <div class="rr">
                <i>小院</i><b>{{ done?.yard_name || form.yardName }}</b>
              </div>
              <div class="rr">
                <i>管理员</i><b>@{{ done?.username || form.username }}</b>
              </div>
              <div class="rr">
                <i>内置技能</i><b>{{ done?.skill_count ?? selectedSkills.size }} 个已发布</b>
              </div>
              <div class="rr">
                <i>默认助手</i><b>{{ done?.agent_count ?? 0 }} 个已发布，可直接提问</b>
              </div>
              <div class="rr">
                <i>模型接入</i><b>{{ done?.model_configured ? '已写入并设为生效' : '稍后配置' }}</b>
              </div>
              <div class="rr">
                <i>本地地址</i><b>{{ localUrl }}</b>
              </div>
              <div class="rr">
                <i>审计</i><b>system_initialized 已记录</b>
              </div>
            </div>
            <div class="next-cards">
              <button
                type="button"
                class="next-card accent"
                @click="askFirstQuestion"
              >
                <b>去问一句</b><small>{{ sampleQuestion }}</small>
              </button>
              <button
                type="button"
                class="next-card"
                @click="enterWorkspace()"
              >
                <b>进入工作台</b><small>看看今日行动板与三步带练</small>
              </button>
              <button
                type="button"
                class="next-card"
                @click="enterWorkspace('/members')"
              >
                <b>邀请成员</b><small>为团队分配专家、科研人员等角色</small>
              </button>
              <button
                type="button"
                class="next-card"
                @click="enterWorkspace('/model-center')"
              >
                <b>接入更多模型</b><small>随时补充供应商与本地模型</small>
              </button>
            </div>
            <p class="hint">
              口令明文未落盘，安装包不再生成 desktop-login.txt。
            </p>
          </div>
        </div>

        <footer class="actionbar">
          <button
            v-if="cur > 1 && cur < 7"
            type="button"
            class="btn ghost"
            @click="prev"
          >
            上一步
          </button>
          <span class="grow" />
          <button
            v-if="cur < 7"
            :id="cur === 6 ? 'setup-create' : 'setup-next'"
            type="button"
            class="btn primary"
            :disabled="submitting || (cur === 6 && !savedPwdAck)"
            @click="next"
          >
            {{ cur === 6 ? (submitting ? '正在创建…' : '创建我的小院') : '继续' }}
          </button>
          <template v-else>
            <!-- 首问闭环（审查 D-14）：主按钮是「去问一句」，工作台入口降为次按钮。
                 注意两个按钮必须包在同一个 <template v-else> 里——此前写成两个
                 并列 v-else，模板编译直接报错（vue-tsc 不查结构，vite build 才暴露）。 -->
            <button
              id="setup-ask"
              type="button"
              class="btn primary"
              @click="askFirstQuestion"
            >
              去问一句
            </button>
            <button
              id="setup-enter"
              type="button"
              class="btn ghost"
              @click="enterWorkspace()"
            >
              进入工作台
            </button>
          </template>
        </footer>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 首次安装引导（v3.74）。
 *
 * 与旧版（v3.69）的差别：
 * - 桌面版不再预置演示账号，7 步里由用户自建管理员与小院；
 * - 新增真实的内置技能铺设与云端供应商写入（旧版模型选择只是 UI）；
 * - 口令规则与后端一致（≥10 位、字母+数字、弱口令、不含账号）；
 * - 全量使用 --ds-* 令牌，跟随品牌与明暗主题。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { setupApi, type SetupInitializeResult, type SetupSkillGroup, type SetupSkillItem, type SetupLlmProvider } from '@/api/setup'
import { useAuthStore } from '@/stores/auth'
import { PROVINCES, PROVINCE_NAMES } from '@/utils/regions'
import { APP_VERSION } from '@/utils/version'

const router = useRouter()
const auth = useAuthStore()
const version = APP_VERSION

const STEPS = [
  { title: '欢迎 · 形态', sub: '安装形态与数据落点' },
  { title: '管理员账号', sub: '第一个账号由你创建' },
  { title: '我的小院', sub: '名称、地区与产业画像' },
  { title: '能力与模型', sub: '技能、开关与模型接入' },
  { title: '品牌外观', sub: '名称与主色' },
  { title: '确认创建', sub: '核对并写入本机' },
  { title: '完成', sub: '进入工作台' },
]

const DEPLOY = [
  { key: 'desktop', title: '单机桌面版', desc: '一台电脑自用，离线可用，数据只在本地', tag: '推荐' },
  { key: 'server', title: '院内服务器版', desc: '部署在小院内网，多人同时使用', tag: '局域网' },
  { key: 'cloud', title: '云端多小院版', desc: '公网服务多个小院，含平台监管', tag: '集群' },
]

const PROFILES = [
  { key: 'rice', label: '水稻 / 稻麦', emoji: '🌾' },
  { key: 'aquaculture', label: '水产蟹虾', emoji: '🦀' },
  { key: 'fruit', label: '应时鲜果', emoji: '🍇' },
  { key: 'vegetable', label: '蔬菜瓜果', emoji: '🥬' },
  { key: 'tea', label: '茶叶', emoji: '🍵' },
  { key: 'livestock', label: '畜禽', emoji: '🐑' },
  { key: 'smart_agri', label: '智慧农业 / 农文旅', emoji: '📡' },
  { key: 'general', label: '通用', emoji: '🧰' },
]

const ACCENTS = [
  { value: '#6366F1', label: '靛蓝' },
  { value: '#3F9D68', label: '田园绿' },
  { value: '#E0A83C', label: '丰收金' },
  { value: '#C084FC', label: '智慧紫' },
  { value: '#E2604A', label: '蟹虾朱' },
  { value: '#22D3EE', label: '青碧' },
]

const PROVIDERS = [
  { key: 'deepseek', label: 'DeepSeek', providerType: 'deepseek', url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { key: 'qwen', label: '通义千问', providerType: 'qwen', url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { key: 'zhipu', label: '智谱 GLM', providerType: 'openai_compatible', url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-plus' },
  { key: 'kimi', label: 'Kimi (Moonshot)', providerType: 'moonshot', url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  { key: 'openai', label: 'OpenAI 兼容', providerType: 'openai', url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  // 自定义：任何 OpenAI 兼容端点（含企业内网代理、聚合网关）。
  // 此前下拉里只有 5 家known厂商，Agnes / OneAPI / vLLM / Ollama 这类
  // 常见部署根本选不到，用户只能改配置文件绕过向导（审查 D-10）。
  { key: 'custom', label: '自定义（OpenAI 兼容）', providerType: 'openai_compatible', url: '', model: '' },
]

const WEAK_USERS = ['admin', 'root', 'system', 'administrator', 'platform_admin', 'test', 'guest', 'user', 'yard_admin']
const WEAK_PWDS = ['admin123', 'yard123', 'demo123', 'password', '12345678', '123456789', 'qwerty', 'abc12345', 'admin1234', '11111111', 'password1', 'kjxy1234', '1234567890', 'admin12345']

const cur = ref(1)
const submitting = ref(false)
const skillsLoading = ref(false)
const catalog = ref<SetupSkillItem[]>([])
const selectedSkills = ref<Set<string>>(new Set())
const skillGroups = ref<SetupSkillGroup[]>([])
const skillProfileLabels = ref<string[]>([])
const skillRecommendedCount = ref(0)
const skillQuery = ref('')
const skillGroupFilter = ref<string>('all')
/** 用户手动改过勾选后，不再用「推荐预选」覆盖他的选择 */
const skillsTouched = ref(false)
const done = ref<SetupInitializeResult | null>(null)
const savedPwdAck = ref(false)
const showPwd = ref(false)
const submitError = ref('')
const createPercent = ref(0)
const createStage = ref('正在写入本机数据库…')
const errors = reactive<Record<string, string>>({})

const form = reactive({
  deployment: 'desktop',
  username: '',
  displayName: '',
  password: '',
  password2: '',
  email: '',
  phone: '',
  yardName: '',
  province: '',
  city: '',
  county: '',
  profileTypes: ['rice'] as string[],
  school: '',
  industry: '',
  description: '',
  searchEnabled: true,
  agentGenerate: true,
  dailyLimit: 10,
  modelMode: 'later' as 'later' | 'ollama' | 'cloud',
  ollamaUrl: 'http://127.0.0.1:11434',
  ollamaModel: 'qwen2.5:7b',
  cloudProvider: 'deepseek',
  cloudUrl: 'https://api.deepseek.com/v1',
  cloudModel: 'deepseek-chat',
  cloudKey: '',
  appName: '科技小院',
  primaryColor: '#6366F1',
  welcome: '把论文写在田野大地上。',
})

const provinceNames = PROVINCE_NAMES
const cities = computed(() => (form.province ? PROVINCES[form.province] ?? [] : []))
const locText = computed(() => [form.province, form.city, form.county].filter(Boolean).join(' · '))

/**
 * 本机实际访问地址。
 *
 * 桌面壳会在 18700-18710 里挑一个空闲端口（client/desktop/src-tauri/src/lib.rs
 * 的 PORT_RANGE），此前向导把 `127.0.0.1:18700` 写死在 3 处，换端口就显示错
 * 地址（审查 D-09）。这里改成读当前页面来源——桌面形态下界面正是由引擎自己
 * 提供的，window.location 就是真值。
 */
const localAddress = computed(() => {
  if (typeof window === 'undefined') return '127.0.0.1:18700'
  const { hostname, port } = window.location
  return port ? `${hostname}:${port}` : hostname
})
const localUrl = computed(() => {
  if (typeof window === 'undefined') return 'http://127.0.0.1:18700'
  return window.location.origin
})
const deployHint = computed(() => {
  if (form.deployment === 'desktop') return '单机桌面版固定只监听 127.0.0.1，不对外开放端口。'
  if (form.deployment === 'server') return '本安装器仍只准备本机数据；局域网多用户请在院内服务器上按部署文档另行安装。'
  return '本安装器仍只准备本机数据；云端多小院请使用服务器部署包，并配置反向代理与备份。'
})

const pwdRules = computed(() => {
  const pw = form.password
  const user = form.username.toLowerCase()
  return [
    { key: 'len', label: '至少 10 位', ok: pw.length >= 10, req: true },
    { key: 'alpha', label: '包含字母', ok: /[A-Za-z]/.test(pw), req: true },
    { key: 'digit', label: '包含数字', ok: /\d/.test(pw), req: true },
    { key: 'weak', label: '不是常见弱口令', ok: !!pw && !WEAK_PWDS.includes(pw.toLowerCase()), req: true },
    { key: 'name', label: '不含账号名', ok: !user || !pw.toLowerCase().includes(user), req: true },
    { key: 'sym', label: '包含符号（建议）', ok: /[^A-Za-z0-9]/.test(pw), req: false },
  ]
})
const strengthLevel = computed(() => {
  if (!form.password) return 0
  const passed = pwdRules.value.filter(r => r.ok).length
  return Math.min(4, Math.max(1, Math.ceil((passed / pwdRules.value.length) * 4)))
})

const summary = computed(() => {
  const deploy = DEPLOY.find(d => d.key === form.deployment)?.title ?? form.deployment
  const model = form.modelMode === 'later'
    ? '稍后配置（不写供应商）'
    : form.modelMode === 'ollama'
      ? `本地模型 · ${form.ollamaModel || '未填'}`
      : `${PROVIDERS.find(p => p.key === form.cloudProvider)?.label ?? ''} · ${form.cloudModel || '未填模型'}`
  return [
    { title: '安装形态', step: 1, rows: [['形态', deploy], ['监听地址', localAddress.value]] },
    { title: '管理员', step: 2, rows: [['登录账号', form.username || '未填写'], ['显示名', form.displayName || '未填写'], ['口令', '••••••••'], ['邮箱 / 手机', [form.email, form.phone].filter(Boolean).join(' · ') || '未填写']] },
    { title: '小院', step: 3, rows: [['名称', form.yardName || '未填写'], ['所在地', locText.value || '未填写'], ['产业画像', form.profileTypes.map(profileLabel).join('、') || '未选择'], ['培养单位', form.school || '未填写']] },
    { title: '能力与模型', step: 4, rows: [['内置技能', `${selectedSkills.value.size} 个`], ['联网搜索', form.searchEnabled ? '开' : '关'], ['智能体生成', `${form.agentGenerate ? '开' : '关'} · 每日 ${form.dailyLimit} 次`], ['模型接入', model]] },
    { title: '外观', step: 5, rows: [['应用名称', form.appName || '科技小院'], ['主色', form.primaryColor.toUpperCase()]] },
  ]
})

function profileLabel(key: string): string {
  return PROFILES.find(p => p.key === key)?.label ?? key
}

function clearError(key: string): void {
  delete errors[key]
}

function setError(key: string, message: string): false {
  errors[key] = message
  return false
}

function validateField(key: string): boolean {
  clearError(key)
  switch (key) {
    case 'username': {
      const v = form.username.trim()
      if (!v) return setError('username', '请填写登录账号')
      if (!/^[A-Za-z0-9_]{3,32}$/.test(v)) return setError('username', '需为 3–32 位字母、数字或下划线')
      if (WEAK_USERS.includes(v.toLowerCase())) return setError('username', '该名称过于常见，容易被猜中，请换一个专有账号')
      return true
    }
    case 'displayName':
      if (!form.displayName.trim()) return setError('displayName', '请填写显示名')
      return true
    case 'password': {
      if (!form.password) return setError('password', '请设置登录口令')
      const bad = pwdRules.value.filter(r => r.req && !r.ok)
      if (bad.length) return setError('password', bad.map(r => r.label).join('、'))
      return true
    }
    case 'password2':
      if (!form.password2) return setError('password2', '请再次输入口令')
      if (form.password2 !== form.password) return setError('password2', '两次输入的口令不一致')
      return true
    case 'email':
      if (form.email && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(form.email)) return setError('email', '邮箱格式不正确')
      return true
    case 'phone':
      if (form.phone && !/^1[3-9]\d{9}$/.test(form.phone)) return setError('phone', '请输入 11 位手机号')
      return true
    case 'yardName':
      if (!form.yardName.trim()) return setError('yardName', '请填写小院名称')
      if (form.yardName.trim().length < 2) return setError('yardName', '小院名称至少 2 个字符')
      return true
    case 'province':
      if (!form.province) return setError('province', '请选择省份')
      return true
    case 'city':
      if (!form.city) return setError('city', '请选择地市')
      return true
    case 'profiles':
      if (!form.profileTypes.length) return setError('profiles', '请至少选择一个产业画像')
      return true
    case 'appName':
      if (!form.appName.trim()) return setError('appName', '请填写应用名称')
      return true
    default:
      return true
  }
}

function validateStep(step: number): boolean {
  if (step === 1) return true
  if (step === 2) {
    const ok = ['username', 'displayName', 'password', 'password2', 'email', 'phone'].map(validateField).every(Boolean)
    if (!ok) ElMessage.warning('请先修正标红的字段')
    return ok
  }
  if (step === 3) {
    const ok = ['yardName', 'province', 'city', 'profiles'].map(validateField).every(Boolean)
    if (!ok) ElMessage.warning('请补全小院信息')
    return ok
  }
  if (step === 4) {
    if (form.modelMode === 'cloud') {
      if (!/^https?:\/\//.test(form.cloudUrl)) return fail('接口地址需以 http:// 或 https:// 开头')
      if (!form.cloudKey) return fail('请填写 API Key，或改选「稍后配置」')
      if (!form.cloudModel) return fail('请填写模型名')
    }
    if (form.modelMode === 'ollama' && (!form.ollamaUrl || !form.ollamaModel)) {
      return fail('请填写 Ollama 地址与模型名，或改选「稍后配置」')
    }
    return true
  }
  if (step === 5) return validateField('appName')
  if (step === 6) {
    if (!savedPwdAck.value) return fail('请先勾选「我已妥善保存管理员口令」')
    return true
  }
  return true
}

function fail(message: string): false {
  ElMessage.warning(message)
  return false
}

function next(): void {
  if (submitting.value) return
  if (cur.value === 6) {
    if (validateStep(6)) void submit()
    return
  }
  if (!validateStep(cur.value)) return
  cur.value = Math.min(7, cur.value + 1)
  // 进入第 4 步时按当前产业画像刷新推荐预选（用户在第 3 步可能改过画像）
  if (cur.value === 4) void loadSkills()
}

function prev(): void {
  if (cur.value > 1) cur.value -= 1
}

function onProvinceChange(): void {
  form.city = ''
  validateField('province')
}

function toggleProfile(key: string): void {
  const i = form.profileTypes.indexOf(key)
  if (i >= 0) form.profileTypes.splice(i, 1)
  else form.profileTypes.push(key)
  validateField('profiles')
}

/** 关键词 + 分类过滤后的技能（本地过滤，35 条目录无需服务端检索） */
const filteredSkills = computed<SetupSkillItem[]>(() => {
  const filter = skillGroupFilter.value
  let items = catalog.value
  if (filter === 'recommended') items = items.filter(s => s.recommended)
  else if (filter !== 'all') items = items.filter(s => s.group === filter)
  const q = skillQuery.value.trim().toLowerCase()
  if (!q) return items
  return items.filter(
    s =>
      s.name.toLowerCase().includes(q) ||
      s.description.toLowerCase().includes(q) ||
      s.pack.toLowerCase().includes(q) ||
      s.keywords.some(k => k.toLowerCase().includes(q))
  )
})

/** 按分类分组的可见技能（分类视图带分组标题，「推荐」视图不重复分组） */
const visibleSkillGroups = computed<{ key: string; label: string; items: SetupSkillItem[] }[]>(() => {
  if (skillGroupFilter.value === 'recommended') {
    return [{ key: 'recommended', label: '', items: filteredSkills.value }]
  }
  const order = skillGroups.value.map(g => g.key)
  const buckets = new Map<string, SetupSkillItem[]>()
  for (const item of filteredSkills.value) {
    const list = buckets.get(item.group) ?? []
    list.push(item)
    buckets.set(item.group, list)
  }
  return order
    .filter(key => (buckets.get(key) ?? []).length)
    .map(key => ({
      key,
      label: skillGroups.value.find(g => g.key === key)?.label ?? key,
      items: buckets.get(key) ?? []
    }))
})

function toggleSkill(code: string): void {
  const nextSet = new Set(selectedSkills.value)
  if (nextSet.has(code)) nextSet.delete(code)
  else nextSet.add(code)
  selectedSkills.value = nextSet
  skillsTouched.value = true
}

function selectAllSkills(all: boolean): void {
  // 全选/清空只作用于**当前可见**的技能（搜索或分类过滤后），
  // 否则用户筛到「农技」再点全选，会把看不见的 PPT 技能也一并勾上。
  const visible = visibleSkillGroups.value.flatMap(g => g.items)
  const nextSet = new Set(selectedSkills.value)
  if (all) visible.forEach(s => nextSet.add(s.code))
  else visible.forEach(s => nextSet.delete(s.code))
  selectedSkills.value = nextSet
  skillsTouched.value = true
}

/**
 * 读取内置技能清单，并按第 3 步勾选的产业画像给出「推荐预选」。
 * 审查 D-13：旧实现一次性返回 35 个技能并默认全选，县级农技小院看到的是
 * 一片「PPT 模板与母版套用」这类不认识的名字。
 */
async function loadSkills(): Promise<void> {
  skillsLoading.value = true
  try {
    const res = await setupApi.skills(form.profileTypes)
    catalog.value = res.items
    skillGroups.value = res.groups
    skillProfileLabels.value = res.profile_labels
    skillRecommendedCount.value = res.recommended_count
    if (!skillsTouched.value) {
      selectedSkills.value = new Set(res.recommended_codes)
    }
  } catch {
    catalog.value = []
    skillGroups.value = []
    skillProfileLabels.value = []
    skillRecommendedCount.value = 0
  } finally {
    skillsLoading.value = false
  }
}

function generatePassword(): void {
  const sets = ['ABCDEFGHJKLMNPQRSTUVWXYZ', 'abcdefghijkmnopqrstuvwxyz', '23456789', '!@#$%^&*-_=+']
  const all = sets.join('')
  const pick = (s: string) => {
    const buf = new Uint32Array(1)
    crypto.getRandomValues(buf)
    return s[buf[0] % s.length]
  }
  let out = sets.map(pick).join('')
  for (let i = 0; i < 12; i += 1) out += pick(all)
  out = out.split('').sort(() => Math.random() - 0.5).join('')
  form.password = out
  form.password2 = out
  showPwd.value = true
  validateField('password')
  validateField('password2')
  ElMessage.success('已生成 16 位强口令并填入两次，请妥善保存')
}

function onProviderChange(): void {
  const p = PROVIDERS.find(x => x.key === form.cloudProvider)
  if (!p) return
  form.cloudUrl = p.url
  form.cloudModel = p.model
}

function buildProvider(): SetupLlmProvider | null {
  if (form.modelMode === 'later') return null
  if (form.modelMode === 'ollama') {
    return {
      provider_type: 'openai_compatible',
      name: 'Ollama 本地',
      base_url: form.ollamaUrl.trim(),
      api_key: 'ollama',
      model_id: form.ollamaModel.trim(),
    }
  }
  const preset = PROVIDERS.find(p => p.key === form.cloudProvider)
  return {
    provider_type: preset?.providerType ?? 'openai_compatible',
    name: preset?.label ?? '云端模型',
    base_url: form.cloudUrl.trim(),
    api_key: form.cloudKey.trim(),
    model_id: form.cloudModel.trim(),
  }
}

async function submit(): Promise<void> {
  if (submitting.value || !validateStep(6)) return
  submitting.value = true
  submitError.value = ''
  createPercent.value = 12
  createStage.value = '正在创建管理员账号…'

  const stageTimer = window.setInterval(() => {
    createPercent.value = Math.min(88, createPercent.value + 7)
    if (createPercent.value > 30) createStage.value = '正在建立小院与铺设内置技能…'
    if (createPercent.value > 60) createStage.value = '正在写入能力开关与品牌…'
  }, 420)

  try {
    const result = await setupApi.initialize({
      username: form.username.trim(),
      password: form.password,
      display_name: form.displayName.trim(),
      email: form.email.trim() || null,
      phone: form.phone.trim() || null,
      yard_name: form.yardName.trim(),
      province: form.province,
      city: form.city,
      county: form.county.trim(),
      profile_types: [...form.profileTypes],
      yard_description: form.description.trim(),
      school: form.school.trim(),
      industry: form.industry.trim(),
      skill_codes: [...selectedSkills.value],
      llm_provider: buildProvider(),
      app_name: form.appName.trim(),
      primary_color: form.primaryColor,
      search_enabled: form.searchEnabled,
      agent_generate_enabled: form.agentGenerate,
      agent_generate_daily_limit: form.dailyLimit,
      deployment: form.deployment,
    })
    createPercent.value = 100
    createStage.value = '写入完成，正在登录…'
    auth.adoptSetupSession(result.access_token, result.refresh_token)
    try {
      await auth.fetchMe()
    } catch {
      /* 进入系统后路由守卫会再拉一次 */
    }
    done.value = result
    // 与 DesktopFirstRun.vue 的 DONE_KEY 对齐：向导已接入模型后不再重复弹引导
    if (result.model_configured) localStorage.setItem('ky_desktop_welcome_done', '1')
    cur.value = 7
    ElMessage.success('创建完成，已登录管理员会话')
  } catch (e) {
    const err = (e as { response?: { data?: { error?: { code?: string; message?: string } } } })?.response?.data?.error
    const code = err?.code ?? ''
    const message = err?.message ?? (e as Error)?.message ?? '创建失败，请稍后重试'
    if (code === 'SETUP_WEAK_USERNAME' || code === 'SETUP_INVALID_USERNAME' || code === 'SETUP_USERNAME_TAKEN') {
      errors.username = message
      cur.value = 2
    } else if (code === 'SETUP_WEAK_PASSWORD') {
      errors.password = message
      cur.value = 2
    } else if (code === 'EMAIL_TAKEN') {
      errors.email = message
      cur.value = 2
    } else if (code === 'PHONE_TAKEN') {
      errors.phone = message
      cur.value = 2
    } else if (code === 'SETUP_YARD_NAME_REQUIRED') {
      errors.yardName = message
      cur.value = 3
    } else if (code === 'SETUP_ALREADY_DONE') {
      ElMessage.warning('系统已完成初始化，请直接登录')
      void router.replace('/login')
    }
    submitError.value = message
    ElMessage.error(message)
  } finally {
    window.clearInterval(stageTimer)
    submitting.value = false
    createPercent.value = 0
  }
}

function enterWorkspace(path = '/'): void {
  void router.replace(path)
}

/**
 * 首问示例问题（审查 D-14）。
 *
 * 装完系统的第一分钟没有闭环：第 7 步只给了「进工作台 / 邀请成员 / 接入模型」
 * 三个出口，**没有一个是「提个问题试试」**。小白装完的第一件事应该是
 * 成功提一次问，所以这里按产业画像给一句可直接发送的示例问题，
 * 跳到 /chat?q=... 后由对话页预填草稿。
 */
const SAMPLE_QUESTIONS: Record<string, string> = {
  rice: '当前水稻田块的灌溉墒情怎么样？该不该补水？',
  aquaculture: '今天的塘口水质和投喂要注意什么？',
  fruit: '葡萄疏果期要注意什么？',
  vegetable: '这茬蔬菜的病虫害怎么预防？',
  tea: '当前茶园的采摘与施肥怎么安排？',
  livestock: '今天猪舍的温湿度和饲喂该怎么调整？',
  smart_agri: '本周小院最该先做的一件事是什么？',
  general: '帮我梳理一下本周小院的工作要点。',
}

const sampleQuestion = computed(
  () => SAMPLE_QUESTIONS[form.profileTypes[0]] ?? SAMPLE_QUESTIONS.general,
)

function askFirstQuestion(): void {
  void router.replace({ path: '/chat', query: { q: sampleQuestion.value } })
}

onMounted(async () => {
  try {
    const st = await setupApi.status()
    if (st.initialized) {
      void router.replace('/login')
      return
    }
  } catch {
    /* 状态取不到时按可初始化处理，提交时会再校验 */
  }
  skillsLoading.value = true
  try {
    await loadSkills()
  } catch {
    catalog.value = []
  } finally {
    skillsLoading.value = false
  }
})
</script>

<style scoped>
.setup-shell {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 22px;
  background:
    radial-gradient(900px 520px at 12% -10%, color-mix(in srgb, var(--ds-accent) 26%, transparent), transparent 62%),
    radial-gradient(760px 460px at 92% 8%, color-mix(in srgb, var(--ds-accent-2) 16%, transparent), transparent 60%),
    var(--ds-bg-0);
}
.setup-grid {
  width: min(1180px, 100%);
  height: min(860px, calc(100dvh - 44px));
  min-height: 600px;
  display: grid;
  grid-template-columns: 264px minmax(0, 1fr);
  border: 1px solid var(--ds-border);
  border-radius: 16px;
  overflow: hidden;
  background: var(--ds-bg-1);
  box-shadow: var(--ds-shadow);
}
.rail {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 22px 18px 16px;
  border-right: 1px solid var(--ds-border);
  background: linear-gradient(180deg, color-mix(in srgb, var(--ds-bg-2) 55%, transparent), transparent 70%);
}
.brand { display: flex; gap: 11px; align-items: center; }
.mark {
  width: 38px; height: 38px; border-radius: 12px; display: grid; place-items: center;
  color: #fff; font-weight: 700; font-size: 14px;
  background: linear-gradient(145deg, var(--ds-accent), var(--ds-accent-2));
  box-shadow: 0 8px 20px color-mix(in srgb, var(--ds-accent) 34%, transparent);
}
.brand-txt strong { display: block; font-size: 13.5px; }
.brand-txt span { display: block; font-size: 11px; color: var(--ds-text-3); margin-top: 2px; }
.progress { display: flex; flex-direction: column; gap: 6px; }
.progress-top { display: flex; justify-content: space-between; font-size: 11px; color: var(--ds-text-3); }
.progress-bar { height: 4px; border-radius: 999px; background: var(--ds-bg-3); overflow: hidden; }
.progress-bar i { display: block; height: 100%; background: linear-gradient(90deg, var(--ds-accent), var(--ds-accent-2)); transition: width .3s ease; }
.steps { list-style: none; display: flex; flex-direction: column; gap: 2px; }
.step { display: grid; grid-template-columns: 30px 1fr; gap: 10px; align-items: start; padding: 8px 6px; border-radius: 10px; }
.step .num {
  width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center;
  font-size: 11.5px; border: 1px solid var(--ds-border-strong); background: var(--ds-bg-1); color: var(--ds-text-3);
}
.step .lab { font-size: 12.5px; color: var(--ds-text-3); line-height: 1.35; padding-top: 1px; }
.step .lab small { display: block; font-size: 10.5px; opacity: .7; margin-top: 2px; }
.step.done .num { background: var(--ds-success); border-color: transparent; color: #04140e; }
.step.done .lab { color: var(--ds-text-2); }
.step.current { background: color-mix(in srgb, var(--ds-accent) 16%, transparent); }
.step.current .num { background: var(--ds-accent); border-color: transparent; color: #fff; }
.step.current .lab { color: var(--ds-text-1); font-weight: 600; }
.rail-foot { margin-top: auto; border-top: 1px solid var(--ds-border); padding-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.rail-foot p { font-size: 10.8px; color: var(--ds-text-3); line-height: 1.55; }

.main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.pane-wrap { flex: 1; min-height: 0; overflow: auto; }
.pane { padding: 30px 34px 26px; max-width: 820px; }
.eyebrow { font-size: 11px; letter-spacing: .12em; color: var(--ds-accent-2); font-weight: 600; margin-bottom: 8px; }
.pane h1 { font-size: 23px; font-weight: 650; line-height: 1.3; margin-bottom: 8px; }
.lead { font-size: 13px; color: var(--ds-text-2); line-height: 1.75; max-width: 640px; margin-bottom: 22px; }

.cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.opt-card {
  position: relative; text-align: left; border: 1px solid var(--ds-border); border-radius: 14px;
  padding: 16px 15px 14px; background: rgba(255, 255, 255, .045); display: flex; flex-direction: column; gap: 6px;
}
.opt-card:hover { border-color: var(--ds-border-strong); }
.opt-card.sel { border-color: var(--ds-accent); background: color-mix(in srgb, var(--ds-accent) 16%, transparent); }
.opt-card .tag { font-size: 10px; color: var(--ds-text-3); border: 1px solid var(--ds-border); border-radius: 999px; padding: 1px 7px; align-self: flex-start; }
.opt-card b { font-size: 13px; }
.opt-card small { font-size: 11.5px; color: var(--ds-text-3); line-height: 1.55; }
.opt-card.sel .tag { color: var(--ds-accent-2); border-color: color-mix(in srgb, var(--ds-accent) 45%, transparent); }

.panel { border: 1px solid var(--ds-border); border-radius: 14px; background: rgba(255, 255, 255, .035); margin-top: 18px; overflow: hidden; }
.panel-h { display: flex; align-items: center; gap: 10px; padding: 12px 15px; border-bottom: 1px solid var(--ds-border); font-size: 13px; }
.panel-h b { font-weight: 600; }
.chip { font-size: 10.5px; color: var(--ds-text-3); border: 1px solid var(--ds-border); border-radius: 999px; padding: 1px 8px; }
.grow { flex: 1; }
.kv { display: flex; justify-content: space-between; gap: 14px; padding: 8px 15px; font-size: 11.5px; border-bottom: 1px dashed var(--ds-border); }
.kv:last-child { border-bottom: 0; }
.kv span { color: var(--ds-text-3); }
.kv b { font-weight: 500; color: var(--ds-text-1); text-align: right; overflow-wrap: anywhere; max-width: 68%; }

.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.chips .chip { height: 28px; display: inline-flex; align-items: center; padding: 0 12px; font-size: 11.5px; }
.btn-chip { background: transparent; }
.btn-chip.on { border-color: var(--ds-accent); background: color-mix(in srgb, var(--ds-accent) 18%, transparent); color: var(--ds-text-1); }

.grid-2 { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 26px; align-items: start; }
.aside { border: 1px solid var(--ds-border); border-radius: 14px; background: rgba(255, 255, 255, .035); overflow: hidden; }
.aside-h { padding: 11px 14px; border-bottom: 1px solid var(--ds-border); font-size: 11.5px; color: var(--ds-text-3); }
.aside-note { padding: 10px 14px 14px; font-size: 11px; color: var(--ds-text-3); line-height: 1.6; }
.aside .kv { padding: 7px 14px; }
.acct { display: flex; gap: 12px; align-items: center; padding: 14px; }
.avatar {
  width: 44px; height: 44px; border-radius: 13px; display: grid; place-items: center;
  background: linear-gradient(145deg, var(--ds-accent), var(--ds-accent-2)); color: #fff; font-size: 17px; font-weight: 650;
}
.acct b { display: block; font-size: 13px; }
.acct span { display: block; font-family: var(--mono, monospace); font-size: 11px; color: var(--ds-text-3); margin-top: 2px; }
.badges { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
.badge { font-style: normal; font-size: 10px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--ds-border); color: var(--ds-text-2); }

.field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 15px; }
.field > label { font-size: 12.5px; color: var(--ds-text-2); display: flex; align-items: center; gap: 6px; }
.field > label em { color: var(--ds-danger); font-style: normal; font-size: 11px; }
.field > label small { color: var(--ds-text-3); font-size: 11px; margin-left: auto; }
.ctl {
  width: 100%; height: 40px; padding: 0 12px; border-radius: 10px; font-size: 13.5px;
  background: var(--ds-bg-0); border: 1px solid var(--ds-border); color: var(--ds-text-1);
}
.ctl:focus { border-color: var(--ds-accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ds-accent) 18%, transparent); }
.ctl.area { height: auto; min-height: 66px; padding: 9px 12px; resize: vertical; line-height: 1.6; }
select.ctl { appearance: none; }
.field.err .ctl, .field.err .pwd-wrap { border-color: var(--ds-danger); }
.msg { font-size: 11.5px; color: var(--ds-text-3); line-height: 1.5; min-height: 16px; }
.field.err .msg { color: var(--ds-danger); }
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
.pwd-wrap { display: flex; align-items: center; gap: 6px; border: 1px solid var(--ds-border); border-radius: 10px; background: var(--ds-bg-0); padding-right: 8px; }
.pwd-wrap:focus-within { border-color: var(--ds-accent); }
.pwd-wrap input { flex: 1; height: 38px; border: 0; background: none; padding: 0 12px; font-size: 13.5px; color: var(--ds-text-1); }
.icon-btn { font-size: 11px; color: var(--ds-text-3); padding: 4px 7px; border-radius: 6px; background: none; }
.icon-btn:hover { color: var(--ds-text-1); background: rgba(255, 255, 255, .06); }
.strength { display: flex; gap: 5px; margin-top: 2px; }
.strength i { flex: 1; height: 4px; border-radius: 999px; background: var(--ds-bg-3); }
.strength.s1 i:nth-child(1) { background: var(--ds-danger); }
.strength.s2 i:nth-child(-n+2) { background: var(--ds-warning); }
.strength.s3 i:nth-child(-n+3) { background: var(--ds-success); }
.strength.s4 i { background: var(--ds-success); }
.rules { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; margin-top: 6px; }
.rule { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--ds-text-3); }
.rule i { width: 5px; height: 5px; border-radius: 50%; background: var(--ds-border-strong); }
.rule.pass { color: var(--ds-text-2); }
.rule.pass i { background: var(--ds-success); }
.rule.fail { color: var(--ds-danger); }
.rule.fail i { background: var(--ds-danger); }

.yard-card { margin: 14px; border: 1px solid var(--ds-border); border-radius: 12px; padding: 13px; background: var(--ds-bg-0); }
.yard-card .loc { font-size: 10.5px; color: var(--ds-text-3); }
.yard-card b { display: block; font-size: 14px; margin: 3px 0 6px; overflow-wrap: anywhere; }
.yard-card p { font-size: 11.5px; color: var(--ds-text-2); line-height: 1.6; margin-top: 8px; }

.skill-list { max-height: 240px; overflow: auto; padding: 6px 8px; }
.skill-hint { margin: 0; padding: 10px 15px; font-size: 11.5px; color: var(--ds-text-3); line-height: 1.55; border-bottom: 1px solid var(--ds-border); }
.skill-tools { display: flex; flex-direction: column; gap: 10px; padding: 12px 15px; border-bottom: 1px solid var(--ds-border); }
.skill-tools .chips { margin-top: 0; }
.skill-search { width: 100%; height: 34px; }
.skill-group { margin: 10px 4px 4px; font-size: 10.5px; letter-spacing: .04em; color: var(--ds-text-3); text-transform: none; }
.skill-row { display: grid; grid-template-columns: 16px 1fr auto; gap: 10px; align-items: start; padding: 8px; border-radius: 8px; cursor: pointer; }
.skill-row:hover { background: rgba(255, 255, 255, .05); }
.skill-row input { margin-top: 3px; accent-color: var(--ds-accent); }
.skill-txt b { display: block; font-size: 12.5px; font-weight: 500; }
.skill-txt small { display: block; font-size: 11px; color: var(--ds-text-3); margin-top: 1px; line-height: 1.45; }
.skill-tags { display: inline-flex; align-items: center; gap: 6px; }
.skill-row .pack { font-style: normal; font-size: 10px; color: var(--ds-text-3); border: 1px solid var(--ds-border); border-radius: 999px; padding: 1px 7px; }
.skill-row .pack.rec { color: var(--ds-accent); border-color: color-mix(in srgb, var(--ds-accent) 45%, var(--ds-border)); }
.skill-row.on { background: color-mix(in srgb, var(--ds-accent) 12%, transparent); }
.skill-loading { padding: 16px; font-size: 12px; color: var(--ds-text-3); }

.switch-row { display: flex; align-items: center; gap: 14px; padding: 12px 15px; border-bottom: 1px solid var(--ds-border); }
.switch-row:last-child { border-bottom: 0; }
.switch-row .txt { flex: 1; }
.switch-row .txt b { display: block; font-size: 12.5px; font-weight: 550; }
.switch-row .txt small { display: block; font-size: 11px; color: var(--ds-text-3); margin-top: 1px; }
.switch { width: 38px; height: 22px; border-radius: 999px; background: var(--ds-bg-3); border: 1px solid var(--ds-border); position: relative; }
.switch i { position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%; background: var(--ds-text-3); transition: .18s; }
.switch.on { background: var(--ds-accent); border-color: transparent; }
.switch.on i { left: 18px; background: #fff; }
.stepper { display: flex; align-items: center; border: 1px solid var(--ds-border); border-radius: 8px; overflow: hidden; }
.stepper button { width: 26px; height: 26px; background: var(--ds-bg-0); color: var(--ds-text-2); }
.stepper span { min-width: 34px; text-align: center; font-size: 12px; line-height: 26px; border-inline: 1px solid var(--ds-border); }

.mode-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; padding: 14px 15px 0; }
.mode-card { text-align: left; border: 1px solid var(--ds-border); border-radius: 11px; padding: 12px; background: var(--ds-bg-0); }
.mode-card.on { border-color: var(--ds-accent); background: color-mix(in srgb, var(--ds-accent) 14%, transparent); }
.mode-card b { display: block; font-size: 12.5px; margin-bottom: 3px; }
.mode-card small { font-size: 11px; color: var(--ds-text-3); line-height: 1.5; display: block; }
.model-form { padding: 14px 15px 16px; display: flex; flex-direction: column; gap: 12px; border-top: 1px dashed var(--ds-border); margin-top: 12px; }
.hint { font-size: 11.5px; color: var(--ds-text-3); line-height: 1.6; margin-top: 8px; }

.swatches { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.sw { width: 30px; height: 30px; border-radius: 9px; border: 2px solid transparent; }
.sw.on { border-color: var(--ds-text-1); box-shadow: 0 0 0 2px var(--ds-bg-1) inset; }
.swatches input[type='color'] { width: 34px; height: 30px; padding: 0; border: 1px solid var(--ds-border); border-radius: 9px; background: none; }

.preview-login {
  margin: 14px; border-radius: 12px; padding: 16px; display: flex; flex-direction: column; gap: 8px; align-items: center;
  background: radial-gradient(220px 120px at 50% 0%, color-mix(in srgb, var(--preview-brand) 22%, transparent), transparent 70%), var(--ds-bg-0);
}
.plogo { width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; color: #fff; font-size: 12px; font-weight: 700; background: linear-gradient(145deg, var(--preview-brand), color-mix(in srgb, var(--preview-brand) 55%, #22d3ee)); }
.preview-login b { font-size: 12.5px; }
.preview-login small { font-size: 9.5px; color: var(--ds-text-3); text-align: center; line-height: 1.5; }
.pinput { width: 100%; height: 24px; border-radius: 7px; border: 1px solid var(--ds-border); background: var(--ds-bg-1); font-size: 9.5px; color: var(--ds-text-3); display: flex; align-items: center; padding: 0 8px; }
.pbtn { width: 100%; height: 24px; border-radius: 7px; background: var(--preview-brand); color: #fff; font-size: 10px; display: grid; place-items: center; }

.sum-grid { display: flex; flex-direction: column; gap: 10px; }
.sum { border: 1px solid var(--ds-border); border-radius: 12px; overflow: hidden; background: rgba(255, 255, 255, .035); }
.sum-h { display: flex; align-items: center; padding: 9px 14px; border-bottom: 1px solid var(--ds-border); font-size: 12.5px; }
.sum-h .edit { margin-left: auto; font-size: 11px; color: var(--ds-accent-text); }
.sum-b { padding: 8px 14px; }
.sum-row { display: flex; justify-content: space-between; gap: 16px; font-size: 12px; padding: 5px 0; border-bottom: 1px dashed var(--ds-border); }
.sum-row:last-child { border-bottom: 0; }
.sum-row span { color: var(--ds-text-3); }
.sum-row b { font-weight: 500; text-align: right; overflow-wrap: anywhere; }

.check-line { display: flex; gap: 10px; align-items: flex-start; padding: 14px 0 6px; cursor: pointer; }
.check-line input { margin-top: 2px; accent-color: var(--ds-accent); }
.check-line .txt b { display: block; font-size: 12.5px; font-weight: 550; }
.check-line .txt small { display: block; font-size: 11px; color: var(--ds-text-3); margin-top: 1px; }
.create-box { margin-top: 14px; }
.create-bar { height: 5px; border-radius: 999px; background: var(--ds-bg-3); overflow: hidden; }
.create-bar i { display: block; height: 100%; background: linear-gradient(90deg, var(--ds-accent), var(--ds-accent-2)); transition: width .35s; }
.err-card { margin-top: 14px; border: 1px solid var(--ds-danger); background: rgba(248, 113, 113, .13); border-radius: 12px; padding: 13px 14px; }
.err-card b { display: block; font-size: 12.5px; margin-bottom: 3px; }
.err-card p { font-size: 11.5px; color: var(--ds-text-2); line-height: 1.6; }
.err-card .btn { margin-top: 9px; }

.receipt { border: 1px solid var(--ds-border); border-radius: 12px; padding: 13px 15px; background: var(--ds-bg-0); margin-bottom: 16px; }
.receipt .rt { font-size: 11px; color: var(--ds-text-3); margin-bottom: 8px; }
.receipt .rr { display: flex; gap: 10px; font-size: 11.5px; padding: 3px 0; color: var(--ds-text-2); }
.receipt .rr i { font-style: normal; color: var(--ds-text-3); min-width: 56px; }
.receipt .rr b { font-weight: 500; color: var(--ds-text-1); font-family: var(--mono, monospace); font-size: 11px; overflow-wrap: anywhere; }
.next-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.next-card { text-align: left; border: 1px solid var(--ds-border); border-radius: 12px; padding: 13px; background: rgba(255, 255, 255, .035); display: flex; flex-direction: column; gap: 5px; }
.next-card:hover { border-color: color-mix(in srgb, var(--ds-accent) 45%, transparent); background: color-mix(in srgb, var(--ds-accent) 12%, transparent); }
.next-card b { font-size: 12.5px; }
.next-card small { font-size: 11px; color: var(--ds-text-3); line-height: 1.5; }
/* 首问闭环（审查 D-14）：第一张卡是「去问一句」，视觉上要比其余出口更突出 */
.next-card.accent { border-color: color-mix(in srgb, var(--ds-accent) 55%, transparent); background: color-mix(in srgb, var(--ds-accent) 14%, transparent); }
.next-card.accent b { color: var(--ds-accent); font-weight: 600; }

.actionbar { flex: 0 0 auto; display: flex; align-items: center; gap: 10px; padding: 13px 24px 14px; border-top: 1px solid var(--ds-border); }
.btn { height: 38px; padding: 0 17px; border-radius: 10px; font-size: 13px; font-weight: 550; border: 1px solid var(--ds-border); background: rgba(255, 255, 255, .045); color: var(--ds-text-1); }
.btn:hover { border-color: var(--ds-border-strong); }
.btn.primary { background: var(--ds-accent); border-color: transparent; color: #fff; }
.btn.primary:disabled { opacity: .5; cursor: not-allowed; }
.btn.ghost { background: none; border-color: transparent; color: var(--ds-text-2); }
.mini-btn { font-size: 11px; color: var(--ds-text-3); padding: 4px 8px; border-radius: 6px; }
.mini-btn:hover { color: var(--ds-text-1); background: rgba(255, 255, 255, .06); }

@media (max-width: 980px) {
  .setup-shell { padding: 0; }
  .setup-grid { height: 100dvh; border-radius: 0; border: 0; grid-template-columns: 1fr; }
  .rail { flex-direction: row; align-items: center; gap: 12px; padding: 10px 12px; border-right: 0; border-bottom: 1px solid var(--ds-border); overflow: auto; }
  .brand-txt span, .progress, .rail-foot, .step .lab small { display: none; }
  .steps { flex-direction: row; gap: 4px; }
  .step { grid-template-columns: auto; padding: 5px 7px; }
  .step .num { width: 24px; height: 24px; font-size: 10.5px; }
  .step .lab { font-size: 11px; white-space: nowrap; }
  .pane { padding: 20px 18px; max-width: none; }
  .grid-2 { grid-template-columns: 1fr; }
  .cards, .mode-cards, .next-cards { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .row-2, .row-3, .rules { grid-template-columns: 1fr; }
  .pane h1 { font-size: 20px; }
}
</style>

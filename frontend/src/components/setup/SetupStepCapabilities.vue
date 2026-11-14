<template>
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
</template>

<script setup lang="ts">
/** 向导第 4 步：智能能力与模型（技能目录/开关/模型接入）（v3.82 拆分：状态与校验统一在 SetupWizard.vue，经 provide 下发）。 */
import { inject } from 'vue'
import { setupWizardKey } from './wizardContext'

const { cur, form, PROVIDERS, onProviderChange, selectedSkills, catalog, selectAllSkills, skillProfileLabels, skillRecommendedCount, skillQuery, skillGroupFilter, skillGroups, skillsLoading, visibleSkillGroups, toggleSkill } = inject(setupWizardKey)!
</script>

<style scoped>
.pane { padding: 30px 34px 26px; max-width: 820px; }
.eyebrow { font-size: 11px; letter-spacing: .12em; color: var(--ds-accent-2); font-weight: 600; margin-bottom: 8px; }
.pane h1 { font-size: 23px; font-weight: 650; line-height: 1.3; margin-bottom: 8px; }
.lead { font-size: 13px; color: var(--ds-text-2); line-height: 1.75; max-width: 640px; margin-bottom: 22px; }
.panel { border: 1px solid var(--ds-border); border-radius: 14px; background: rgba(255, 255, 255, .035); margin-top: 18px; overflow: hidden; }
.panel-h { display: flex; align-items: center; gap: 10px; padding: 12px 15px; border-bottom: 1px solid var(--ds-border); font-size: 13px; }
.panel-h b { font-weight: 600; }
.chip { font-size: 10.5px; color: var(--ds-text-3); border: 1px solid var(--ds-border); border-radius: 999px; padding: 1px 8px; }
.grow { flex: 1; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.chips .chip { height: 28px; display: inline-flex; align-items: center; padding: 0 12px; font-size: 11.5px; }
.btn-chip { background: transparent; }
.btn-chip.on { border-color: var(--ds-accent); background: color-mix(in srgb, var(--ds-accent) 18%, transparent); color: var(--ds-text-1); }
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
.field.err .msg { color: var(--ds-danger); }
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
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
.check-line .txt b { display: block; font-size: 12.5px; font-weight: 550; }
.check-line .txt small { display: block; font-size: 11px; color: var(--ds-text-3); margin-top: 1px; }
.mini-btn { font-size: 11px; color: var(--ds-text-3); padding: 4px 8px; border-radius: 6px; }
.mini-btn:hover { color: var(--ds-text-1); background: rgba(255, 255, 255, .06); }
@media (max-width: 980px) {
  .pane { padding: 20px 18px; max-width: none; }
  .cards, .mode-cards, .next-cards { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .row-2, .row-3, .rules { grid-template-columns: 1fr; }
  .pane h1 { font-size: 20px; }
}
</style>

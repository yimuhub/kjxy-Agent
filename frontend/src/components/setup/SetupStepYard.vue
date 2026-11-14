<template>
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
</template>

<script setup lang="ts">
/** 向导第 3 步：建立我的小院（地区级联与产业画像）（v3.82 拆分：状态与校验统一在 SetupWizard.vue，经 provide 下发）。 */
import { inject } from 'vue'
import { setupWizardKey } from './wizardContext'

const { cur, form, errors, validateField, provinceNames, cities, onProvinceChange, PROFILES, toggleProfile, locText, profileLabel, selectedSkills } = inject(setupWizardKey)!
</script>

<style scoped>
.pane { padding: 30px 34px 26px; max-width: 820px; }
.eyebrow { font-size: 11px; letter-spacing: .12em; color: var(--ds-accent-2); font-weight: 600; margin-bottom: 8px; }
.pane h1 { font-size: 23px; font-weight: 650; line-height: 1.3; margin-bottom: 8px; }
.lead { font-size: 13px; color: var(--ds-text-2); line-height: 1.75; max-width: 640px; margin-bottom: 22px; }
.chip { font-size: 10.5px; color: var(--ds-text-3); border: 1px solid var(--ds-border); border-radius: 999px; padding: 1px 8px; }
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
.yard-card { margin: 14px; border: 1px solid var(--ds-border); border-radius: 12px; padding: 13px; background: var(--ds-bg-0); }
.yard-card .loc { font-size: 10.5px; color: var(--ds-text-3); }
.yard-card b { display: block; font-size: 14px; margin: 3px 0 6px; overflow-wrap: anywhere; }
.yard-card p { font-size: 11.5px; color: var(--ds-text-2); line-height: 1.6; margin-top: 8px; }
.skill-tools .chips { margin-top: 0; }
@media (max-width: 980px) {
  .pane { padding: 20px 18px; max-width: none; }
  .grid-2 { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .row-2, .row-3, .rules { grid-template-columns: 1fr; }
  .pane h1 { font-size: 20px; }
}
</style>

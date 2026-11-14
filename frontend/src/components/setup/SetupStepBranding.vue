<template>
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
</template>

<script setup lang="ts">
/** 向导第 5 步：品牌与外观（名称/主色/登录页预览）（v3.82 拆分：状态与校验统一在 SetupWizard.vue，经 provide 下发）。 */
import { inject } from 'vue'
import { setupWizardKey } from './wizardContext'

const { cur, form, errors, validateField, ACCENTS } = inject(setupWizardKey)!
</script>

<style scoped>
.pane { padding: 30px 34px 26px; max-width: 820px; }
.eyebrow { font-size: 11px; letter-spacing: .12em; color: var(--ds-accent-2); font-weight: 600; margin-bottom: 8px; }
.pane h1 { font-size: 23px; font-weight: 650; line-height: 1.3; margin-bottom: 8px; }
.lead { font-size: 13px; color: var(--ds-text-2); line-height: 1.75; max-width: 640px; margin-bottom: 22px; }
.grid-2 { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 26px; align-items: start; }
.aside { border: 1px solid var(--ds-border); border-radius: 14px; background: rgba(255, 255, 255, .035); overflow: hidden; }
.aside-h { padding: 11px 14px; border-bottom: 1px solid var(--ds-border); font-size: 11.5px; color: var(--ds-text-3); }
.aside-note { padding: 10px 14px 14px; font-size: 11px; color: var(--ds-text-3); line-height: 1.6; }
.aside .kv { padding: 7px 14px; }
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
@media (max-width: 980px) {
  .pane { padding: 20px 18px; max-width: none; }
  .grid-2 { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .pane h1 { font-size: 20px; }
}
</style>

<template>
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
</template>

<script setup lang="ts">
/** 向导第 1 步：欢迎 · 安装形态与数据落点（v3.82 拆分：状态与校验统一在 SetupWizard.vue，经 provide 下发）。 */
import { inject } from 'vue'
import { setupWizardKey } from './wizardContext'

const { cur, form, DEPLOY, deployHint, localAddress } = inject(setupWizardKey)!
</script>

<style scoped>
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
.kv { display: flex; justify-content: space-between; gap: 14px; padding: 8px 15px; font-size: 11.5px; border-bottom: 1px dashed var(--ds-border); }
.kv:last-child { border-bottom: 0; }
.kv span { color: var(--ds-text-3); }
.kv b { font-weight: 500; color: var(--ds-text-1); text-align: right; overflow-wrap: anywhere; max-width: 68%; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.chips .chip { height: 28px; display: inline-flex; align-items: center; padding: 0 12px; font-size: 11.5px; }
.aside .kv { padding: 7px 14px; }
.skill-tools .chips { margin-top: 0; }
.hint { font-size: 11.5px; color: var(--ds-text-3); line-height: 1.6; margin-top: 8px; }
@media (max-width: 980px) {
  .pane { padding: 20px 18px; max-width: none; }
  .cards, .mode-cards, .next-cards { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .pane h1 { font-size: 20px; }
}
</style>

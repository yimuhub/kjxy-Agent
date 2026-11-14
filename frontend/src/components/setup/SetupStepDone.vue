<template>
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
</template>

<script setup lang="ts">
/** 向导第 7 步：完成回执与下一步闭环（v3.82 拆分：状态与校验统一在 SetupWizard.vue，经 provide 下发）。 */
import { inject } from 'vue'
import { setupWizardKey } from './wizardContext'

const { cur, form, done, selectedSkills, localUrl, askFirstQuestion, enterWorkspace, sampleQuestion } = inject(setupWizardKey)!
</script>

<style scoped>
.pane { padding: 30px 34px 26px; max-width: 820px; }
.eyebrow { font-size: 11px; letter-spacing: .12em; color: var(--ds-accent-2); font-weight: 600; margin-bottom: 8px; }
.pane h1 { font-size: 23px; font-weight: 650; line-height: 1.3; margin-bottom: 8px; }
.lead { font-size: 13px; color: var(--ds-text-2); line-height: 1.75; max-width: 640px; margin-bottom: 22px; }
.hint { font-size: 11.5px; color: var(--ds-text-3); line-height: 1.6; margin-top: 8px; }
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
.next-card.accent { border-color: color-mix(in srgb, var(--ds-accent) 55%, transparent); background: color-mix(in srgb, var(--ds-accent) 14%, transparent); }
.next-card.accent b { color: var(--ds-accent); font-weight: 600; }
@media (max-width: 980px) {
  .pane { padding: 20px 18px; max-width: none; }
  .cards, .mode-cards, .next-cards { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .pane h1 { font-size: 20px; }
}
</style>

<template>
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
</template>

<script setup lang="ts">
/** 向导第 6 步：确认并创建（回执核对/进度/失败重试）（v3.82 拆分：状态与校验统一在 SetupWizard.vue，经 provide 下发）。 */
import { inject } from 'vue'
import { setupWizardKey } from './wizardContext'

const { cur, summary, savedPwdAck, submitting, createPercent, createStage, submitError, submit } = inject(setupWizardKey)!
</script>

<style scoped>
.pane { padding: 30px 34px 26px; max-width: 820px; }
.eyebrow { font-size: 11px; letter-spacing: .12em; color: var(--ds-accent-2); font-weight: 600; margin-bottom: 8px; }
.pane h1 { font-size: 23px; font-weight: 650; line-height: 1.3; margin-bottom: 8px; }
.lead { font-size: 13px; color: var(--ds-text-2); line-height: 1.75; max-width: 640px; margin-bottom: 22px; }
.switch-row .txt { flex: 1; }
.switch-row .txt b { display: block; font-size: 12.5px; font-weight: 550; }
.switch-row .txt small { display: block; font-size: 11px; color: var(--ds-text-3); margin-top: 1px; }
.hint { font-size: 11.5px; color: var(--ds-text-3); line-height: 1.6; margin-top: 8px; }
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
.btn { height: 38px; padding: 0 17px; border-radius: 10px; font-size: 13px; font-weight: 550; border: 1px solid var(--ds-border); background: rgba(255, 255, 255, .045); color: var(--ds-text-1); }
.btn:hover { border-color: var(--ds-border-strong); }
.btn.primary { background: var(--ds-accent); border-color: transparent; color: #fff; }
.btn.primary:disabled { opacity: .5; cursor: not-allowed; }
.btn.ghost { background: none; border-color: transparent; color: var(--ds-text-2); }
@media (max-width: 980px) {
  .pane { padding: 20px 18px; max-width: none; }
}
@media (max-width: 620px) {
  .pane h1 { font-size: 20px; }
}
</style>

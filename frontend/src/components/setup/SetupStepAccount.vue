<template>
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
</template>

<script setup lang="ts">
/** 向导第 2 步：创建管理员账号（口令规则与强度条）（v3.82 拆分：状态与校验统一在 SetupWizard.vue，经 provide 下发）。 */
import { inject } from 'vue'
import { setupWizardKey } from './wizardContext'

const { cur, form, errors, validateField, showPwd, generatePassword, pwdRules, strengthLevel } = inject(setupWizardKey)!
</script>

<style scoped>
.pane { padding: 30px 34px 26px; max-width: 820px; }
.eyebrow { font-size: 11px; letter-spacing: .12em; color: var(--ds-accent-2); font-weight: 600; margin-bottom: 8px; }
.pane h1 { font-size: 23px; font-weight: 650; line-height: 1.3; margin-bottom: 8px; }
.lead { font-size: 13px; color: var(--ds-text-2); line-height: 1.75; max-width: 640px; margin-bottom: 22px; }
.kv { display: flex; justify-content: space-between; gap: 14px; padding: 8px 15px; font-size: 11.5px; border-bottom: 1px dashed var(--ds-border); }
.kv:last-child { border-bottom: 0; }
.kv span { color: var(--ds-text-3); }
.kv b { font-weight: 500; color: var(--ds-text-1); text-align: right; overflow-wrap: anywhere; max-width: 68%; }
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
@media (max-width: 980px) {
  .pane { padding: 20px 18px; max-width: none; }
  .grid-2 { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .row-2, .row-3, .rules { grid-template-columns: 1fr; }
  .pane h1 { font-size: 20px; }
}
</style>

<template>
  <section
    v-if="visible"
    class="first-run"
    data-first-run-guide
  >
    <div class="guide-head">
      <div>
        <h3>第一次来这里？三步上手</h3>
        <p>不用看手册：跟着走完这三步，你就已经会用整个平台了。</p>
      </div>
      <el-button
        link
        @click="dismiss"
      >
        我知道了，不再显示
      </el-button>
    </div>
    <ol class="steps">
      <li>
        <span class="num">1</span>
        <div class="body">
          <strong>说一句话，交给指挥官</strong>
          <span>去智能对话，直接说你要办的事，比如「帮我把本周设备异常整理成报告」。</span>
        </div>
        <el-button
          size="small"
          type="primary"
          @click="go('/chat')"
        >
          去说话
        </el-button>
      </li>
      <li>
        <span class="num">2</span>
        <div class="body">
          <strong>看计划卡与产物</strong>
          <span>指挥官会拆任务、派助手；做完的文件会回到对话里，也能在工作台回看。</span>
        </div>
        <el-button
          size="small"
          @click="go('/commander')"
        >
          看工作台
        </el-button>
      </li>
      <li>
        <span class="num">3</span>
        <div class="body">
          <strong>认识你的小伙伴</strong>
          <span>右下角的农民小人会播报天气、工单和任务结果；点他随时呼出工作台，拖他换位置。</span>
        </div>
        <el-button
          size="small"
          @click="dismiss"
        >
          明白了
        </el-button>
      </li>
    </ol>
  </section>
</template>

<script setup lang="ts">
  /**
   * 小白首访三步带练（v3.71）：审查报告「小白用户」维度的落地。
   * 只在第一次使用时出现（localStorage 记忆），任何一步点了「去说话/看工作台」
   * 都不算完成——真正完成过一次对话后由 Dashboard 根据会话数自动收起。
   */
  import { computed, onMounted, ref } from 'vue'
  import { useRouter } from 'vue-router'

  const KEY = 'ky_first_run_done'
  const router = useRouter()
  const done = ref(true)
  const hasChatted = ref(false)

  const visible = computed(() => !done.value && !hasChatted.value)

  function go(path: string): void {
    // 用户点了任一步的「去说话/看工作台」即视为已进入流程，收起引导
    dismiss()
    void router.push(path)
  }

  function dismiss(): void {
    done.value = true
    try {
      localStorage.setItem(KEY, '1')
    } catch {
      /* 写不进去只影响下次还显示 */
    }
  }

  onMounted(() => {
    try {
      done.value = localStorage.getItem(KEY) === '1'
      hasChatted.value = localStorage.getItem('ky_first_run_chatted') === '1'
    } catch {
      done.value = true
    }
  })
</script>

<style scoped>
  .first-run {
    border: 1px solid color-mix(in srgb, var(--ds-accent) 40%, transparent);
    border-radius: 14px;
    background: color-mix(in srgb, var(--ds-accent) 7%, var(--ds-bg-1));
    padding: 14px 18px;
    margin-bottom: 16px;
  }

  .guide-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }

  .guide-head h3 {
    margin: 0 0 2px;
    font-size: 15px;
  }

  .guide-head p {
    margin: 0;
    color: var(--ds-text-3);
    font-size: 12.5px;
  }

  .steps {
    list-style: none;
    margin: 12px 0 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 12px;
  }

  .steps li {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 12px;
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    background: var(--ds-bg-1);
  }

  .num {
    flex: none;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: var(--ds-accent);
    color: #fff;
    font-size: 12px;
    font-weight: 700;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-top: 2px;
  }

  .body {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }

  .body strong {
    font-size: 13px;
  }

  .body span {
    color: var(--ds-text-3);
    font-size: 12px;
    line-height: 1.5;
  }

  .steps .el-button {
    margin-top: 2px;
  }
</style>

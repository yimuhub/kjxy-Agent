<template>
  <div class="stat-row">
    <div
      v-for="stat in stats"
      :key="stat.label"
      class="stat"
    >
      <span class="stat-value">{{ stat.value }}</span>
      <span class="stat-label">{{ stat.label }}</span>
      <span
        v-if="stat.hint"
        class="stat-hint"
      >{{ stat.hint }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
  defineProps<{
    stats: { label: string; value: string | number; hint?: string }[]
  }>()
</script>

<style scoped>
  .stat-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px;
  }
  .stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 12px 14px;
    border: 1px solid var(--ds-border, #e5e7eb);
    border-radius: var(--ds-radius-md, 10px);
    background: var(--ds-surface, #fafafa);
  }
  .stat-value {
    font-size: 22px;
    font-weight: 500;
    line-height: 1.2;
    color: var(--ds-text-1, #1f2937);
  }
  .stat-label {
    font-size: 12px;
    color: var(--ds-text-3, #6b7280);
  }
  .stat-hint {
    font-size: 11.5px;
    color: var(--ds-text-3, #9ca3af);
  }
</style>

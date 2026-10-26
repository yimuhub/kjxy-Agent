<template>
  <article
    class="lib-card glass-panel"
    :class="{ 'is-installed': installed }"
    tabindex="0"
    :aria-label="`${item.name}，${typeLabel(item.item_type)}，${sourceLabel(item.source)}，${installed ? '已安装' : '未安装'}`"
    @keydown.enter.self.prevent="emit('preview')"
    @keydown.space.self.prevent="emit('preview')"
  >
    <header class="card-head">
      <div class="card-title">
        <h3>{{ item.name || '未命名条目' }}</h3>
        <span
          v-if="item.author"
          class="author"
        >{{ item.author }}</span>
      </div>
      <div class="badges">
        <el-tag
          size="small"
          :type="sourceTagType(item.source)"
          effect="plain"
        >
          {{ sourceLabel(item.source) }}
        </el-tag>
        <el-tag
          size="small"
          type="info"
          effect="plain"
        >
          {{ typeLabel(item.item_type) }}
        </el-tag>
        <el-tag
          v-if="item.verified"
          size="small"
          type="success"
          effect="plain"
        >
          已验证
        </el-tag>
        <el-tag
          size="small"
          :type="installed ? 'success' : 'info'"
          effect="plain"
        >
          {{ installed ? '已安装' : '未安装' }}
        </el-tag>
      </div>
    </header>

    <p class="card-desc">
      {{ item.description || '暂无描述' }}
    </p>

    <dl class="card-meta">
      <div>
        <dt>许可证</dt>
        <dd>{{ item.license || '未声明' }}</dd>
      </div>
      <div>
        <dt>风险</dt>
        <dd>
          <el-tag
            size="small"
            :type="riskTagType(item.risk_level)"
            effect="plain"
          >
            {{ riskLabel(item.risk_level) }}
          </el-tag>
        </dd>
      </div>
      <div>
        <dt>版本</dt>
        <dd>{{ item.version || '—' }}</dd>
      </div>
    </dl>

    <footer class="card-actions">
      <el-button
        link
        type="primary"
        :aria-label="`预览 ${item.name || '条目'}`"
        @click.stop="emit('preview')"
      >
        预览
      </el-button>
      <el-button
        type="primary"
        size="small"
        :loading="installing"
        :disabled="installed"
        :aria-label="`${installButtonText} ${item.name || '条目'}`"
        @click.stop="emit('install')"
      >
        {{ installed ? '已安装' : installButtonText }}
      </el-button>
    </footer>
  </article>
</template>

<script setup lang="ts">
  import { computed } from 'vue'
  import {
    isPlugin,
    itemInstalled,
    riskLabel,
    riskTagType,
    sourceLabel,
    sourceTagType,
    typeLabel,
    type LibraryItem,
  } from './libraryMeta'

  const props = withDefaults(
    defineProps<{
      item: LibraryItem
      installing?: boolean
    }>(),
    { installing: false },
  )

  const emit = defineEmits<{
    (e: 'preview'): void
    (e: 'install'): void
  }>()

  const installed = computed(() => itemInstalled(props.item))
  const installButtonText = computed(() => (isPlugin(props.item) ? '安装并提交审核' : '安装'))
</script>

<style scoped>
  .lib-card {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 16px;
    min-height: 208px;
    outline: none;
    transition:
      border-color 0.2s,
      background 0.2s,
      transform 0.2s;
  }
  .lib-card:hover {
    border-color: var(--ds-border-strong);
    background: var(--ds-glass-strong);
    transform: translateY(-2px);
  }
  .lib-card:focus-visible {
    border-color: var(--ds-accent);
    box-shadow: 0 0 0 2px var(--ds-accent) inset;
  }
  .lib-card.is-installed {
    opacity: 0.9;
  }
  .card-head {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .card-title {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
  }
  .card-title h3 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    line-height: 1.4;
    color: var(--ds-text-1);
  }
  .author {
    flex: none;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .card-desc {
    margin: 0;
    flex: 1;
    font-size: 13px;
    line-height: 1.6;
    color: var(--ds-text-2);
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .card-meta {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    margin: 0;
    padding-top: 10px;
    border-top: 1px solid var(--ds-border);
  }
  .card-meta div {
    min-width: 0;
  }
  .card-meta dt {
    margin-bottom: 4px;
    font-size: 11px;
    color: var(--ds-text-3);
  }
  .card-meta dd {
    margin: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .card-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    padding-top: 4px;
  }
</style>

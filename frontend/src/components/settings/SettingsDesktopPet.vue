<template>
  <el-card
    shadow="never"
    class="panel"
  >
    <template #header>
      <div class="panel-head">
        <div>
          <h2>桌面伙伴</h2>
          <p>一只常驻桌面的小院伙伴：不抢焦点，点一下回到工作台，右键换皮肤。</p>
        </div>
        <el-tag
          size="small"
          effect="plain"
          :type="available ? 'success' : 'info'"
        >
          {{ available ? '桌面版' : '浏览器预览' }}
        </el-tag>
      </div>
    </template>

    <el-alert
      v-if="!available"
      type="info"
      :closable="false"
      show-icon
      title="桌面伙伴只在安装版客户端生效；浏览器里可先打开 /pet 预览皮肤。"
      class="hint"
    />

    <el-form label-width="130px">
      <el-form-item label="启用桌面伙伴">
        <el-switch
          v-model="enabled"
          :disabled="!available"
          @change="onEnabledChange"
        />
        <span class="note">开启后启动客户端时自动出现；关闭即收起</span>
      </el-form-item>

      <el-form-item label="任务气泡">
        <el-switch
          v-model="bubbles"
          @change="onBubblesChange"
        />
        <span class="note">任务开始、完成或失败时用气泡同步进度；不弹系统通知</span>
      </el-form-item>

      <el-form-item label="页面内悬浮伙伴">
        <el-switch
          v-model="overlay"
          @change="onOverlayChange"
        />
        <span class="note">在网页端与桌面端的所有页面常驻，可拖到任意位置；点头像即可对话下指令</span>
        <el-button
          size="small"
          class="reset-pos"
          @click="resetOverlayPos"
        >
          重置位置
        </el-button>
      </el-form-item>

      <el-form-item label="桌面快捷键">
        <div class="shortcut-list">
          <span class="shortcut"><kbd>⌘/Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>Space</kbd> 呼出工作台</span>
          <span class="shortcut"><kbd>⌘/Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>P</kbd> 显示/收起伙伴</span>
        </div>
      </el-form-item>

      <el-form-item label="立即显示">
        <el-button
          size="small"
          :disabled="!available || busy !== ''"
          :loading="busy === 'show'"
          @click="showPet"
        >
          显示伙伴
        </el-button>
        <el-button
          size="small"
          :disabled="!available || busy !== ''"
          :loading="busy === 'hide'"
          @click="hidePet"
        >
          收起伙伴
        </el-button>
        <span class="note">显示/收起只影响当前窗口，不改变「启用」开关</span>
      </el-form-item>
    </el-form>

    <el-divider content-position="left">
      皮肤
    </el-divider>
    <div class="skin-grid">
      <button
        v-for="item in PET_SKINS"
        :key="item.id"
        type="button"
        class="skin-card"
        :class="{ active: item.id === skinId }"
        :aria-pressed="item.id === skinId"
        @click="chooseSkin(item.id)"
      >
        <span
          class="skin-avatar"
          :style="{ background: item.body, borderColor: item.ear }"
        >
          {{ item.emoji }}
        </span>
        <span class="skin-meta">
          <strong>{{ item.name }}</strong>
          <small>{{ item.tagline }}</small>
        </span>
      </button>
    </div>
    <p class="foot-note">
      皮肤选择会同步到桌面伙伴窗口；在桌宠上右键也能直接换。
    </p>
  </el-card>
</template>

<script setup lang="ts">
  import { onBeforeUnmount, onMounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import {
    PET_ENABLED_STORAGE_KEY,
    PET_SKINS,
    readStoredPetSkin,
    setPetEnabledLocal,
    writeStoredPetSkin,
    type PetSkinId,
  } from '@/utils/petSkins'
  import {
    invokeDesktop,
    isDesktopShell,
    setDesktopPetSkin,
    setDesktopPetVisible,
  } from '@/utils/desktop'
  import { petMuted, setPetMuted } from '@/utils/petCopilot'
  import {
    petOverlayEnabled,
    resetPetPanelRect,
    resetPetPos,
    setPetOverlayEnabled,
  } from '@/utils/petOverlay'

  const available = isDesktopShell()
  const enabled = ref(false)
  const bubbles = ref(!petMuted())
  const overlay = ref(petOverlayEnabled())
  const skinId = ref<PetSkinId>(readStoredPetSkin())
  const busy = ref('')

  function readEnabled(): boolean {
    try {
      return localStorage.getItem(PET_ENABLED_STORAGE_KEY) === '1'
    } catch {
      return false
    }
  }

  async function onEnabledChange(value: string | number | boolean): Promise<void> {
    const on = value === true
    setPetEnabledLocal(on)
    if (!available) return
    busy.value = on ? 'show' : 'hide'
    try {
      await setDesktopPetVisible(on, skinId.value)
      ElMessage.success(on ? '桌面伙伴已开启' : '桌面伙伴已收起')
    } catch {
      ElMessage.error('桌面伙伴切换失败，请稍后重试')
    } finally {
      busy.value = ''
    }
  }

  async function chooseSkin(next: PetSkinId): Promise<void> {
    skinId.value = next
    writeStoredPetSkin(next)
    if (!available) return
    try {
      await setDesktopPetSkin(next)
      if (enabled.value) await setDesktopPetVisible(true, next)
    } catch {
      ElMessage.error('皮肤切换失败，请稍后重试')
    }
  }

  async function showPet(): Promise<void> {
    busy.value = 'show'
    try {
      await setDesktopPetVisible(true, skinId.value)
      ElMessage.success('桌面伙伴已显示')
    } catch (e) {
      ElMessage.error(String(e || '显示失败，请确认客户端已就绪'))
    } finally {
      busy.value = ''
    }
  }

  async function hidePet(): Promise<void> {
    busy.value = 'hide'
    try {
      await setDesktopPetVisible(false, skinId.value)
    } catch {
      ElMessage.error('收起桌面伙伴失败')
    } finally {
      busy.value = ''
    }
  }

  function onBubblesChange(value: string | number | boolean): void {
    setPetMuted(value !== true)
  }

  function onOverlayChange(value: string | number | boolean): void {
    setPetOverlayEnabled(value === true)
    ElMessage.success(value === true ? '悬浮伙伴已开启' : '悬浮伙伴已收起')
  }

  function resetOverlayPos(): void {
    resetPetPos()
    resetPetPanelRect()
    ElMessage.success('伙伴的位置与面板大小已恢复到默认')
  }

  function onStorage(event: StorageEvent): void {
    if (event.key === 'ky_pet_skin' && event.newValue) {
      skinId.value = readStoredPetSkin()
    }
    if (event.key === 'ky_pet_mute') {
      bubbles.value = !petMuted()
    }
    if (event.key === 'ky_pet_overlay') {
      overlay.value = petOverlayEnabled()
    }
  }

  onMounted(async () => {
    enabled.value = readEnabled()
    window.addEventListener('storage', onStorage)
    if (available) {
      try {
        await invokeDesktop<{ visible: boolean; skin: string }>('pet_status')
      } catch {
        /* 状态读取失败不阻塞设置页 */
      }
    }
  })

  onBeforeUnmount(() => {
    window.removeEventListener('storage', onStorage)
  })
</script>

<style scoped>
  .panel {
    border: 1px solid var(--ds-border);
    border-radius: 12px;
    background: var(--ds-surface);
  }

  .panel-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }

  .panel-head h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .panel-head p {
    margin: 4px 0 0;
    font-size: 12.5px;
    line-height: 1.6;
    color: var(--ds-text-3);
  }

  .hint {
    margin-bottom: 14px;
  }

  .note {
    margin-left: 10px;
    font-size: 12px;
    color: var(--ds-text-3);
  }

  .shortcut-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 12px;
    color: var(--ds-text-2);
  }

  kbd {
    display: inline-block;
    padding: 1px 5px;
    border: 1px solid var(--ds-border);
    border-radius: 5px;
    background: var(--ds-field);
    font-family: var(--ds-font-mono, ui-monospace, monospace);
    font-size: 11px;
  }

  .skin-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 10px;
  }

  .skin-card {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px;
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    background: var(--ds-field);
    cursor: pointer;
    text-align: left;
    transition:
      border-color 0.16s ease,
      transform 0.16s ease;
  }

  .skin-card:hover {
    transform: translateY(-1px);
    border-color: var(--ds-accent, #4c9aff);
  }

  .skin-card.active {
    border-color: var(--ds-accent, #4c9aff);
    box-shadow: 0 0 0 2px rgba(76, 154, 255, 0.18);
  }

  .skin-avatar {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 38px;
    height: 38px;
    border-radius: 50%;
    border: 2px solid transparent;
    font-size: 18px;
    flex-shrink: 0;
  }

  .skin-meta {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .skin-meta strong {
    font-size: 13px;
    color: var(--ds-text-1);
  }

  .skin-meta small {
    font-size: 11px;
    color: var(--ds-text-3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .foot-note {
    margin: 12px 0 0;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
</style>

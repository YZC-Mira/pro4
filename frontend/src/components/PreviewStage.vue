<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ previewUrl: string; pageId: string }>()
const emit = defineEmits<{ (e: 'share'): void }>()

const canShare = computed(() => Boolean(props.pageId))

function openInNewWindow() {
  if (props.previewUrl) {
    window.open(props.previewUrl, '_blank')
  }
}
</script>

<template>
  <div class="stage-pane">
    <template v-if="previewUrl">
      <div class="stage-wrap">
        <div class="stage-toolbar">
          <div class="title">算法动画<small>交互式可视化 · 单步执行/回看</small></div>
          <div class="grow"></div>
          <button class="tbtn ghost" @click="openInNewWindow">↗ 新窗口打开</button>
          <button class="tbtn primary" :disabled="!canShare" @click="emit('share')">🔗 分享</button>
        </div>
        <iframe
          :src="previewUrl"
          class="preview-frame"
          sandbox="allow-scripts allow-modals"
          title="算法动画预览"
        />
      </div>
    </template>
    <div v-else class="empty-stage">
      <div class="empty-icon">◆</div>
      <h3>还没有动画</h3>
      <p>在右侧描述你的需求，AI 将生成可交互的算法动画</p>
    </div>
  </div>
</template>

<style scoped>
.stage-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--bg);
}

.stage-wrap {
  flex: 1;
  margin: 12px 4px 16px 20px;
  border-radius: 18px;
  background: var(--card);
  border: 1px solid var(--line);
  box-shadow: var(--shadow-md);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}

.stage-toolbar {
  height: 52px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  padding: 0 18px;
  gap: 10px;
  flex-shrink: 0;
}

.title {
  font-size: 14px;
  font-weight: 700;
  min-width: 0;
}

.title small {
  display: block;
  font-size: 11.5px;
  color: var(--text-3);
  font-weight: 400;
  margin-top: -2px;
}

.grow {
  flex: 1;
}

.tbtn {
  border: 1px solid var(--line);
  background: #fff;
  color: var(--text-1);
  padding: 7px 14px;
  border-radius: 9px;
  font-size: 12.5px;
  cursor: pointer;
  transition: 0.15s;
  flex-shrink: 0;
}

.tbtn:hover:not(:disabled) {
  border-color: #c9d4e4;
  background: #fbfcfe;
}

.tbtn.primary {
  background: var(--grad);
  color: #fff;
  border: none;
  font-weight: 600;
  box-shadow: 0 4px 12px rgba(79, 110, 247, 0.3);
}

.tbtn.primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.tbtn.ghost {
  border: none;
  background: transparent;
  color: var(--text-2);
}

.tbtn.ghost:hover {
  background: #f0f3f9;
}

.preview-frame {
  flex: 1;
  border: none;
  width: 100%;
  min-height: 0;
  background: #fff;
}

.empty-stage {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-3);
}

.empty-icon {
  width: 72px;
  height: 72px;
  border-radius: 22px;
  background: var(--grad);
  opacity: 0.85;
  color: #fff;
  font-size: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow-md);
  margin-bottom: 6px;
}

.empty-stage h3 {
  margin: 0;
  color: var(--text-2);
  font-size: 16px;
}

.empty-stage p {
  margin: 0;
  font-size: 13px;
}
</style>
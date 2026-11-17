<script setup lang="ts">
import { computed } from 'vue'
import type { GenProgress } from '../composables/useGenerateStream'

const props = defineProps<{ progress: GenProgress }>()

interface StepDef {
  key: string
  label: string
}

const STEPS: StepDef[] = [
  { key: 'clarifying', label: '澄清' },
  { key: 'generating', label: '生成' },
  { key: 'verifying', label: '验证' },
  { key: 'repairing', label: '优化' },
  { key: 'done', label: '完成' },
]

/** stage → 界面步骤（不暴露自修复：repairing 显示为"优化"） */
const STAGE_TO_STEP: Record<string, number> = {
  clarifying: 0,
  generating: 1,
  verifying: 2,
  repairing: 3,
  done: 4,
  failed: 3,
  cancelled: 1,
}

const currentStep = computed(() => {
  if (!props.progress.stage) return -1
  const idx = STAGE_TO_STEP[props.progress.stage]
  return idx ?? -1
})

const failed = computed(() => props.progress.stage === 'failed')
const cancelledNow = computed(() => props.progress.stage === 'cancelled')

const stepLabels = computed(() =>
  STEPS.map((s) => (s.key === 'generating' && props.progress.iterating ? '调整' : s.label)),
)

function stateOf(i: number): 'done' | 'cur' | 'todo' {
  if ((failed.value || cancelledNow.value) && i > currentStep.value) return 'todo'
  if (i < currentStep.value) return 'done'
  if (i === currentStep.value) return failed.value || cancelledNow.value ? 'todo' : 'cur'
  return 'todo'
}

const subLabel = computed(() => {
  if (cancelledNow.value) return '已停止生成'
  if (failed.value) return props.progress.iterating ? '调整未完成，可重试' : '生成未完成，可重试'
  const map: Record<string, string> = {
    clarifying: '正在确认需求',
    generating: props.progress.iterating ? 'AI 正在按反馈调整动画' : 'AI 正在编写动画',
    verifying: '正在检查动画效果',
    repairing: '正在打磨动画细节',
    done: props.progress.iterating ? '动画已调整完成' : '动画已生成完成',
  }
  return map[props.progress.stage] ?? ''
})
</script>

<template>
  <div v-if="progress.active || progress.stage === 'done' || progress.stage === 'failed'" class="stage-steps">
    <div class="steps">
      <div v-for="(s, i) in STEPS" :key="s.key" class="step" :class="stateOf(i)">
        <div class="dot">
          <span v-if="stateOf(i) === 'done'">✓</span>
          <span v-else-if="stateOf(i) === 'cur'" class="spin-mark">✦</span>
          <span v-else>{{ i + 1 }}</span>
        </div>
        <div class="lbl">{{ stepLabels[i] }}</div>
        <div v-if="stateOf(i) === 'cur'" class="sub">{{ subLabel }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stage-steps {
  padding: 14px 26px 4px;
  flex-shrink: 0;
}

.steps {
  display: flex;
  align-items: flex-start;
}

.step {
  flex: 1;
  text-align: center;
  position: relative;
}

.dot {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: #fff;
  border: 2px solid var(--line);
  color: var(--text-3);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 7px;
  font-size: 13px;
  position: relative;
  z-index: 2;
}

.lbl {
  font-size: 12.5px;
  color: var(--text-3);
}

.sub {
  font-size: 11px;
  color: var(--text-3);
  margin-top: 1px;
}

.step.done .dot {
  background: var(--grad);
  border-color: transparent;
  color: #fff;
}

.step.done .lbl {
  color: var(--text-1);
  font-weight: 600;
}

.step.cur .dot {
  border-color: var(--brand-1);
  color: var(--brand-1);
  box-shadow: 0 0 0 5px rgba(79, 110, 247, 0.15);
}

.step.cur .lbl {
  color: var(--brand-1);
  font-weight: 700;
}

.step.cur .sub {
  color: var(--warn-deep);
  font-weight: 600;
}

.spin-mark {
  display: inline-block;
  animation: spin 1.6s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.step:not(:last-child)::after {
  content: '';
  position: absolute;
  top: 15px;
  left: calc(50% + 20px);
  width: calc(100% - 40px);
  height: 2px;
  background: var(--line);
  z-index: 1;
}

.step.done:not(:last-child)::after {
  background: var(--brand-2);
}
</style>
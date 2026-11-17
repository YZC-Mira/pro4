<script setup lang="ts">
import { computed, nextTick, onUpdated, ref, watch } from 'vue'
import type { ChatMessage, ModelItem } from '../api'
import type { GenProgress } from '../composables/useGenerateStream'

const props = defineProps<{
  messages: ChatMessage[]
  progress: GenProgress
  lastResult: { ok: boolean; summary: string } | null
  phase: 'idle' | 'clarifying'
  sending: boolean
  clarifying: boolean
  clarifyQuestions: string[]
  serviceError: string
  hasWork: boolean
  models: ModelItem[]
  selectedModelId: number | null
  noModel: boolean
  modelPlaceholder: string
}>()

const emit = defineEmits<{
  (e: 'send', task: string): void
  (e: 'submit-clarify', answers: string[]): void
  (e: 'skip-clarify'): void
  (e: 'select-model', mid: number | null): void
  (e: 'stop-generate'): void
}>()

const input = ref('')
const msgListEl = ref<HTMLElement | null>(null)
const answers = ref<string[]>([])

// 会话语义：本会话已有作品 → 输入即"调整反馈"（迭代当前作品）；
// 想生成全新动画 → 新建对话（走澄清流程）。无需模式切换。
const inputPlaceholder = computed(() => {
  if (props.noModel) return '暂无可用模型——请到个人中心「模型」页添加，或联系管理员启用内置模型'
  if (props.phase === 'clarifying') return '问题在上方卡片中回答…'
  if (props.hasWork) return '继续描述可调整当前动画，如：动画太快了，数组改成 5 个元素…'
  return '描述你的需求，如：为大二生成 Dijkstra 教学动画，突出松弛操作…'
})

async function scrollToBottom() {
  await nextTick()
  if (msgListEl.value) msgListEl.value.scrollTop = msgListEl.value.scrollHeight
}

onUpdated(scrollToBottom)
watch(() => props.messages.length, scrollToBottom)
watch(
  () => props.phase,
  (p) => {
    if (p === 'clarifying') {
      answers.value = props.clarifyQuestions.map(() => '')
      scrollToBottom()
    }
  },
)

function submit() {
  const text = input.value.trim()
  if (!text || props.sending || props.clarifying) return
  if (props.noModel) return
  if (props.phase !== 'clarifying') {
    input.value = ''
    emit('send', text)
  }
}

function submitClarifyForm() {
  if (props.phase !== 'clarifying' || props.sending) return
  emit('submit-clarify', answers.value.map((a) => a.trim()))
}

const answeredCount = ref(0)
watch(
  answers,
  (arr) => {
    answeredCount.value = arr.filter((a) => a.trim()).length
  },
  { deep: true },
)

function progressText(): string {
  const p = props.progress
  if (p.checking) return '正在检查动画效果…'
  if (p.progressSeconds > 0) {
    return `AI 正在编写动画… 已接收 ${p.progressKb} KB（${p.progressSeconds}s）`
  }
  return 'AI 正在编写动画…'
}

const platformModels = computed(() => props.models.filter((m) => m.scope === 'platform' && m.enabled !== 0))
const myModels = computed(() => props.models.filter((m) => m.scope === 'user'))
</script>

<template>
  <div class="chat-pane">
    <div ref="msgListEl" class="msg-list">
      <div v-for="m in messages" :key="m.id" class="msg-row" :class="m.role === 'user' ? 'me' : 'ai'">
        <div class="av brand-avatar">{{ m.role === 'user' ? '我' : '◆' }}</div>
        <div class="bubble">{{ m.content }}</div>
      </div>

      <!-- 澄清问题卡片：每个问题一个输入框，一次填完 -->
      <div v-if="phase === 'clarifying'" class="msg-row ai">
        <div class="av brand-avatar">◆</div>
        <div class="clarify-form">
          <div class="cf-head">✦ 请补充以下信息</div>
          <div v-for="(q, i) in clarifyQuestions" :key="i" class="cf-item">
            <label>{{ i + 1 }}. {{ q }}</label>
            <el-input
              v-model="answers[i]"
              :placeholder="`回答 ${i + 1}…`"
              :disabled="sending"
              size="default"
              @keydown.enter.prevent="submitClarifyForm"
            />
          </div>
          <div class="cf-foot">
            <button class="btn ghost" :disabled="sending" @click="emit('skip-clarify')">跳过，直接生成</button>
            <button class="btn primary" :disabled="sending" @click="submitClarifyForm">
              开始生成（{{ answeredCount }}/{{ clarifyQuestions.length }}）
            </button>
          </div>
        </div>
      </div>

      <div v-if="progress.active" class="msg-row ai">
        <div class="av brand-avatar">◆</div>
        <div class="bubble generating">
          <div class="gen-title">
            {{ progress.stageLabel || '生成中' }}
            <span v-if="progress.stage === 'repairing'" class="pill">✦ 优化细节中</span>
          </div>
          <div class="gen-status">{{ progressText() }}</div>
        </div>
      </div>

      <div v-if="serviceError" class="msg-row ai">
        <div class="av brand-avatar">◆</div>
        <div class="bubble fail">{{ serviceError }}</div>
      </div>

      <div v-if="lastResult" class="msg-row ai">
        <div class="av brand-avatar">◆</div>
        <div class="bubble" :class="lastResult.ok ? 'ok' : 'fail'">
          {{ lastResult.ok ? `✓ ${lastResult.summary}，已在左侧展示` : `✕ ${lastResult.summary}` }}
        </div>
      </div>
    </div>

    <footer class="input-area">

      <div class="input-box">
        <el-input
          v-model="input"
          type="textarea"
          :rows="3"
          :placeholder="inputPlaceholder"
          :disabled="sending || clarifying || phase === 'clarifying' || noModel"
          resize="none"
          @keydown.enter.exact.prevent="submit"
        />
        <div class="input-foot">
          <el-select
            :model-value="selectedModelId"
            size="small"
            class="model-select"
            :placeholder="modelPlaceholder"
            :disabled="sending || noModel"
            @update:model-value="(v: any) => emit('select-model', v === null || v === undefined || v === '' ? null : Number(v))"
          >
            <el-option-group v-if="platformModels.length" label="内置模型">
              <el-option v-for="m in platformModels" :key="m.id" :value="m.id" :label="m.name" />
            </el-option-group>
            <el-option-group v-if="myModels.length" label="我的模型">
              <el-option v-for="m in myModels" :key="m.id" :value="m.id" :label="m.name" />
            </el-option-group>
          </el-select>
          <button
            v-if="sending"
            class="btn stop-btn"
            title="停止生成"
            @click="emit('stop-generate')"
          >
            ■ 停止
          </button>
          <button
            v-else
            class="btn primary send-btn"
            :disabled="!input.trim() || clarifying || phase === 'clarifying'"
            @click="submit"
          >
            {{ clarifying ? '分析中…' : '➤ 发送' }}
          </button>
        </div>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.chat-pane {
  display: flex;
  flex-direction: column;
  background: var(--card);
   height: 100%;
   min-height: 0;
 }

/* 模型选择器（输入框左下角，与发送按钮同行；chip 风格与背景区分） */
.model-select {
  width: 150px;
  flex-shrink: 0;
}

.model-select :deep(.el-select__wrapper) {
  background: #eef1f8;
  box-shadow: 0 0 0 1px var(--line) inset;
  padding-left: 8px;
  padding-right: 6px;
  border-radius: 8px;
  min-height: 26px;
  font-size: 12.5px;
  color: var(--text-2);
  cursor: pointer;
  transition: 0.15s;
}

.model-select :deep(.el-select__wrapper:hover) {
  background: rgba(79, 110, 247, 0.1);
  box-shadow: 0 0 0 1px rgba(79, 110, 247, 0.45) inset;
}

.model-select :deep(.el-select__wrapper.is-focused) {
  background: rgba(79, 110, 247, 0.08);
  box-shadow: 0 0 0 1.5px var(--brand-1) inset;
}

.model-select :deep(.el-select__caret) {
  color: var(--text-3);
}

.model-select :deep(.el-select__placeholder),
.model-select :deep(.el-select__selected-item) {
  color: var(--text-2);
  font-size: 12.5px;
  font-weight: 600;
}

.msg-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 18px 12px;
  background: linear-gradient(180deg, #fbfcfe, #f7f9fc);
}

.msg-row {
  display: flex;
  gap: 10px;
  margin-bottom: 18px;
  align-items: flex-start;
}

.msg-row.me {
  flex-direction: row-reverse;
}

.av {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  flex-shrink: 0;
  font-size: 13px;
}

.msg-row.me .av {
  background: #e8ecf3;
  color: var(--text-2);
}

.bubble {
  max-width: 82%;
  padding: 10px 14px;
  border-radius: 13px;
  font-size: 13.5px;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
}

.msg-row.ai .bubble {
  background: #fff;
  border: 1px solid var(--line);
  border-top-left-radius: 4px;
  box-shadow: var(--shadow-sm);
}

.msg-row.me .bubble {
  background: var(--grad);
  color: #fff;
  border-top-right-radius: 4px;
  box-shadow: 0 4px 14px rgba(79, 110, 247, 0.25);
}

.bubble.generating {
  width: 82%;
}

.gen-title {
  font-weight: 600;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.pill {
  font-size: 11px;
  padding: 2px 9px;
  border-radius: 20px;
  font-weight: 600;
  background: #fef3e2;
  color: var(--warn-deep);
}

.gen-status {
  font-size: 13px;
  color: var(--text-2);
}

.bubble.ok {
  background: linear-gradient(135deg, rgba(16, 185, 129, 0.08), rgba(16, 185, 129, 0.03));
  border: 1px solid rgba(16, 185, 129, 0.35);
  border-top-left-radius: 4px;
  color: var(--ok-deep);
  font-weight: 600;
}

.bubble.fail {
  background: rgba(239, 68, 68, 0.06);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-top-left-radius: 4px;
  color: var(--err);
}

/* ---- 澄清问题卡片 ---- */
.clarify-form {
  width: 84%;
  background: #fff;
  border: 1px solid var(--line);
  border-top-left-radius: 4px;
  border-radius: 13px;
  box-shadow: var(--shadow-sm);
  padding: 14px 16px 12px;
}

.cf-head {
  font-size: 13px;
  font-weight: 700;
  color: var(--brand-1);
  margin-bottom: 12px;
}

.cf-item {
  margin-bottom: 12px;
}

.cf-item label {
  display: block;
  font-size: 12.5px;
  color: var(--text-1);
  margin-bottom: 6px;
  line-height: 1.5;
}

.cf-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}

.input-area {
  padding: 14px 16px 16px;
  border-top: 1px solid var(--line);
   background: var(--card);
 }

.input-box {
  border: 1.5px solid var(--line);
  border-radius: 13px;
  padding: 10px 12px 8px;
  background: #fbfcfe;
  transition: 0.2s;
}

.input-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}

.tip {
  font-size: 11px;
  color: var(--text-3);
}

.btn {
  border: none;
  border-radius: 9px;
  padding: 8px 18px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: 0.15s;
}

.btn.primary {
  background: var(--grad);
  color: #fff;
  box-shadow: 0 4px 12px rgba(79, 110, 247, 0.3);
}

.btn.primary:hover:not(:disabled) {
  opacity: 0.92;
  transform: translateY(-1px);
}

.btn.primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  transform: none;
}

.btn.ghost {
  background: transparent;
  color: var(--text-2);
}

.btn.ghost:hover:not(:disabled) {
  background: #f0f3f9;
}

/* 发送按钮常驻醒目 */
.send-btn {
  min-width: 96px;
  letter-spacing: 1px;
}

/* 停止按钮（生成中替换发送键） */
.stop-btn {
  min-width: 96px;
  background: #fff;
  border: 1.5px solid var(--err);
  color: var(--err);
  box-shadow: none;
}

.stop-btn:hover {
  background: rgba(239, 68, 68, 0.08);
}
</style>
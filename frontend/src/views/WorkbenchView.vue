<script setup lang="ts">
import { computed, onActivated, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  clarifyTask,
  createConversation,
  deleteConversation,
  fetchConversations,
  fetchLatestPage,
  fetchMe,
  fetchMessages,
  fetchModelAvailability,
  fetchModels,
  fetchPageStatus,
  pagePreviewUrl,
  postMessage,
  renameConversation,
  type AuthUser,
  type ChatMessage,
  type ConversationItem,
  type ModelItem,
} from '../api'
import { useGenerateStream } from '../composables/useGenerateStream'
import AppHeader from '../components/AppHeader.vue'
import StageSteps from '../components/StageSteps.vue'
import PreviewStage from '../components/PreviewStage.vue'
import ChatPanel from '../components/ChatPanel.vue'
import ShareDialog from '../components/ShareDialog.vue'

defineOptions({ name: 'WorkbenchView' }) // keep-alive include 匹配（App.vue 缓存本组件：路由切换时保住 SSE 与生成进度）

const router = useRouter()

const user = ref<AuthUser | null>(null)
const conversationId = ref<number | null>(null)
const messages = ref<ChatMessage[]>([])

const phase = ref<'idle' | 'clarifying'>('idle')
const clarifying = ref(false)
const pendingTask = ref('')
const clarifyQuestions = ref<string[]>([])
const clarifyAnswers = ref<string[]>([])

const previewUrl = ref('')
const currentPageId = ref('')
const shareVisible = ref(false)
const conversations = ref<ConversationItem[]>([])
const models = ref<ModelItem[]>([])
const selectedModelId = ref<number | null>(null)

const MODEL_KEY = 'algoagent.modelId'

const { progress, lastResult, finalPageId, serviceError, cancelled, run, stop } = useGenerateStream()

const CONV_KEY = 'algoagent.conversationId'
const PREVIEW_KEY = 'algoagent.previewPageId'
const SPLIT_KEY = 'algoagent.chatPaneWidth'
const DEFAULT_CHAT_W = 420

// 刷新恢复：生成中 F5 → 检测会话 pending 作品 → 轮询状态 → 完成后恢复预览
const recovering = ref(false)
let recoverTimer: ReturnType<typeof setInterval> | null = null

async function checkPendingRecovery(cid: number) {
  try {
    const lp = await fetchLatestPage(cid, true)
    if (!lp || lp.status !== 'pending') return
    // 该会话有进行中的生成（可能是刷新前发起的）→ 进入恢复轮询
    recovering.value = true
    progress.value.active = true
    progress.value.stage = 'generating'
    progress.value.stageLabel = '生成'
    progress.value.checking = false
    const pid = lp.page_id
    sessionStorage.setItem(PREVIEW_KEY, pid)
    if (recoverTimer) clearInterval(recoverTimer)
    recoverTimer = setInterval(async () => {
      try {
        const st = await fetchPageStatus(pid)
        if (st.status === 'pending') return
        if (recoverTimer) clearInterval(recoverTimer)
        recoverTimer = null
        recovering.value = false
        progress.value.active = false
        if (st.status === 'passed') {
          currentPageId.value = pid
          previewUrl.value = pagePreviewUrl(pid)
          lastResult.value = { ok: true, summary: '检测到生成已完成，已恢复展示' }
        } else {
          progress.value.stage = st.status === 'cancelled' ? 'cancelled' : 'failed'
          lastResult.value = {
            ok: false,
            summary: st.status === 'cancelled' ? '该次生成已停止' : '该次生成未完成',
          }
        }
        if (conversationId.value !== null) {
          try {
            messages.value = await fetchMessages(conversationId.value)
          } catch { /* 忽略 */ }
        }
      } catch {
        /* 轮询失败下轮再试 */
      }
    }, 5000)
  } catch {
    /* 恢复检测失败不影响正常使用 */
  }
}

async function stopGenerate() {
  if (recovering.value) {
    // 恢复轮询模式下"停止" = 停止等待（后端生成继续，完成后作品照常入库）
    if (recoverTimer) clearInterval(recoverTimer)
    recoverTimer = null
    recovering.value = false
    progress.value.active = false
    progress.value.stage = 'cancelled'
    lastResult.value = { ok: false, summary: '已停止等待（后台生成完成后可在我的作品查看）' }
    return
  }
  await stop()
}

const bodyEl = ref<HTMLElement | null>(null)
const chatPaneW = ref(Number(localStorage.getItem(SPLIT_KEY)) || DEFAULT_CHAT_W)
const dragging = ref(false)

onMounted(async () => {
  user.value = await fetchMe()
  if (!user.value) {
    router.push({ name: 'login' })
    return
  }
  await loadModels()
  await loadConversations()
  const cached = Number(sessionStorage.getItem(CONV_KEY)) || null
  if (cached !== null && conversations.value.some((c) => c.id === cached)) {
    await switchConversation(cached)
    return
  }
  if (conversations.value.length > 0) {
    await switchConversation(conversations.value[0].id)
    return
  }
  await newConversation()
})

// keep-alive 回流钩子：组件从缓存激活时执行（onMounted 不再触发）。
// 同步"从个人中心/管理端点预览跳转"传来的作品切换（sessionStorage PREVIEW_KEY）。
onActivated(() => {
  if (progress.value.active) return // 生成中不切预览（完成后 onDone 会设置新作品）
  const pid = sessionStorage.getItem(PREVIEW_KEY)
  if (pid && pid !== currentPageId.value) {
    currentPageId.value = pid
    previewUrl.value = pagePreviewUrl(pid)
  }
  loadModels() // 回流时刷新模型列表（管理端/我的模型可能新增或删除）
})

async function loadModels() {
  try {
    models.value = await fetchModels()
    // 平台可用性（后端判定：无启用的内置模型 且 环境变量未设——此时管理员也无模型可用）
    const availability = await fetchModelAvailability()
    platformNoModel.value = availability.no_model
    // 可用列表 = 未停用内置 + 自己的自定义（admin 含停用内置，供管理；对话选择仅用启用项）
    const usable = models.value.filter((m) => m.scope === 'user' || m.enabled !== 0)
    // 选中初始化：上次选择 → 默认标记 → 第一个可用 → null
    const saved = Number(localStorage.getItem(MODEL_KEY)) || null
    if (saved !== null && usable.some((m) => m.id === saved)) {
      selectedModelId.value = saved
    } else {
      const def = usable.find((m) => m.is_default)
      const first = def ?? usable[0]
      selectedModelId.value = first ? first.id : null
      if (first) {
        localStorage.setItem(MODEL_KEY, String(first.id))
      } else {
        localStorage.removeItem(MODEL_KEY)
      }
    }
  } catch (e) {
    console.error('模型列表加载失败', e)
  }
}

// 平台从未配置模型（后端 availability 接口判定：无启用的内置模型 且 env 未设）
const platformNoModel = ref(false)

// 当前用户是否无可用模型。判定顺序（先看自己有什么，再看平台）：
// 1. 有可用列表项（自己的自定义模型 或 启用的内置模型）→ 可用；
// 2. 平台从未配置（无内置无 env）→ 任何人（含 admin）都不可用；
// 3. 仅有 env 兜底 → 仅 admin 可用。
// ※ 自定义模型是用户自足的可用资源——平台没配内置模型不等于用户没模型可用。
const noModel = computed(() => {
  const usable = models.value.filter((m) => m.scope === 'user' || m.enabled !== 0)
  if (usable.length > 0) return false
  if (platformNoModel.value) return true
  return user.value?.role !== 'admin'
})

// 模型选择器 placeholder（无可用模型时的提示）
const modelPlaceholder = computed(() => {
  if (noModel.value) return '暂无可用模型'
  if (models.value.length === 0) return '环境配置'
  return '选择模型'
})

function onSelectModel(mid: number | null) {
  selectedModelId.value = mid
  if (mid === null) {
    localStorage.removeItem(MODEL_KEY)
  } else {
    localStorage.setItem(MODEL_KEY, String(mid))
  }
}

async function loadConversations() {
  try {
    conversations.value = await fetchConversations()
  } catch (e) {
    console.error('会话列表加载失败', e)
  }
}

async function switchConversation(cid: number) {
  // 生成中禁止切换（SSE 绑定旧会话）；澄清请求挂起中也禁止——
  // 此时消息只在本地内存、尚未入库，切走再切回会被 fetchMessages 覆盖导致消息"消失"
  if (progress.value.active || clarifying.value) return
  conversationId.value = cid
  sessionStorage.setItem(CONV_KEY, String(cid))
  phase.value = 'idle'
  clarifyQuestions.value = []
  try {
    messages.value = await fetchMessages(cid)
  } catch {
    messages.value = []
  }
  // 恢复该会话的作品上下文（迭代依赖 currentPageId；预览跟随会话）
  let restored = false
  try {
    const lp = await fetchLatestPage(cid)
    if (lp && lp.page_id) {
      currentPageId.value = lp.page_id
      previewUrl.value = pagePreviewUrl(lp.page_id)
      sessionStorage.setItem(PREVIEW_KEY, lp.page_id)
      restored = true
    }
  } catch {
    /* 拉取失败按无作品处理 */
  }
  if (!restored) {
    currentPageId.value = ''
    previewUrl.value = ''
    sessionStorage.removeItem(PREVIEW_KEY)
  }
  // 刷新恢复：检测该会话是否有进行中的生成（pending）
  await checkPendingRecovery(cid)
}

async function newConversation() {
  if (progress.value.active || clarifying.value) return // 生成/澄清挂起中禁止新建（防丢消息）
  try {
    const id = await createConversation('新对话')
    conversations.value = await loadConversationsList()
    conversationId.value = id
    sessionStorage.setItem(CONV_KEY, String(id))
    messages.value = []
    currentPageId.value = ''
    previewUrl.value = ''
    sessionStorage.removeItem(PREVIEW_KEY)
  } catch (e) {
    console.error('会话创建失败', e)
  }
}

async function loadConversationsList(): Promise<ConversationItem[]> {
  try {
    return await fetchConversations()
  } catch {
    return conversations.value
  }
}

async function renameConversationById(c: ConversationItem) {
  const title = window.prompt('重命名对话', c.title ?? '')
  if (title === null) return
  const trimmed = title.trim()
  if (!trimmed) {
    ElMessage.warning('标题不能为空')
    return
  }
  try {
    await renameConversation(c.id, trimmed)
    conversations.value = await loadConversationsList()
    ElMessage.success('已重命名')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function deleteConversationById(c: ConversationItem) {
  const ok = window.confirm(`删除对话「${c.title ?? ''}」？\n（作品会保留在我的作品中）`)
  if (!ok) return
  try {
    await deleteConversation(c.id)
    conversations.value = conversations.value.filter((x) => x.id !== c.id)
    ElMessage.success('对话已删除（作品保留）')
    if (c.id === conversationId.value) {
      if (conversations.value.length > 0) {
        await switchConversation(conversations.value[0].id)
      } else {
        await newConversation()
      }
    }
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

function convTime(t: string): string {
  return (t || '').replace('T', ' ').slice(5, 16)
}


async function onSend(task: string) {
  messages.value.push(makeMsg('user', task))
  scrollToBottom()

  // 会话已有作品 → 输入即调整反馈（对话上下文迭代，无需澄清）；
  // 想生成全新动画 → 新建对话（走澄清流程）
  if (currentPageId.value) {
    await startGenerate(task, currentPageId.value)
    return
  }

  // cid 快照：异步回调期间禁止切换已保证 cid 不变（防御性双保险——
  // 若未来放开禁切，消息也不会写进别的会话）
  const cid = conversationId.value

  clarifying.value = true
  let result: { need_clarify: boolean; questions: string[]; reason: string; out_of_scope?: boolean; error?: string }
  try {
    result = await clarifyTask(task, selectedModelId.value)
  } catch (e) {
    // 澄清请求失败（含后端 422 无模型等）：显示错误提示，不再盲目兜底生成——
    // 否则生成会再次撞上同一问题，用户双重等待
    clarifying.value = false
    const errMsg = (e as Error).message || '请求失败，请稍后再试'
    messages.value.push(makeMsg('assistant', errMsg))
    if (cid !== null) {
      try {
        await postMessage(cid, 'user', task)
      } catch { /* 入库失败不阻塞界面 */ }
      postMessage(cid, 'assistant', errMsg).catch(() => {})
    }
    scrollToBottom()
    return
  }
  clarifying.value = false

  // 平台从未配置模型：显示引导提示，不进入生成
  if (result.error === 'no_model') {
    const guideMsg = result.reason || '平台暂未配置模型：请管理员到「管理 → 模型配置」添加内置模型后再试'
    messages.value.push(makeMsg('assistant', guideMsg))
    if (cid !== null) {
      try {
        await postMessage(cid, 'user', task)
      } catch { /* 入库失败不阻塞界面 */ }
      postMessage(cid, 'assistant', guideMsg).catch(() => {})
    }
    scrollToBottom()
    return
  }

  // 非算法教学任务（闲聊/日常请求）：AI 回复能力边界引导，不进入生成
  // user 消息与 assistant 引导都入库（此分支不走生成，orchestrator 不会代写 user 消息——
  // 缺了它刷新后用户提问会消失）。必须 await 串行写入：并发 fetch 到达顺序不确定，
  // 会让 assistant 先入库（id 更小），刷新后显示顺序颠倒。
  if (result.out_of_scope) {
    const guide = result.reason || '我是算法可视化智能体，可以生成冒泡排序、最短路径等算法的交互式教学动画。描述一个算法教学需求试试？'
    messages.value.push(makeMsg('assistant', guide))
    if (cid !== null) {
      try {
        await postMessage(cid, 'user', task)
      } catch { /* 入库失败不阻塞界面 */ }
      postMessage(cid, 'assistant', guide).catch(() => {})
    }
    scrollToBottom()
    return
  }

  if (result.need_clarify && result.questions.length > 0) {
    pendingTask.value = task
    clarifyQuestions.value = result.questions
    clarifyAnswers.value = []
    phase.value = 'clarifying'
    const qText =
      '在开始生成前，我想先确认几点：\n' +
      result.questions.map((q, i) => `${i + 1}. ${q}`).join('\n') +
      (result.reason ? `\n（${result.reason}）` : '')
    messages.value.push(makeMsg('assistant', qText))
    if (cid !== null) {
      try {
        await postMessage(cid, 'user', task)
      } catch { /* 入库失败不阻塞界面 */ }
      postMessage(cid, 'assistant', qText).catch(() => {})
    }
    scrollToBottom()
  } else {
    await startGenerate(task)
  }
}

async function onSubmitClarify(answers: string[]) {
  if (phase.value !== 'clarifying') return
  clarifyAnswers.value = answers
  const task = pendingTask.value
  const qa = clarifyQuestions.value
    .map((q, i) => `问：${q}\n答：${answers[i]?.trim() || '（未回答，按默认处理）'}`)
    .join('\n')
  const merged = `${task}\n\n补充回答：\n${qa}`
  phase.value = 'idle'
  clarifyQuestions.value = []
  await startGenerate(merged)
}

async function onSkipClarify() {
  if (phase.value !== 'clarifying') return
  const task = pendingTask.value
  phase.value = 'idle'
  clarifyQuestions.value = []
  await startGenerate(task)
}

async function startGenerate(task: string, iteratePageId?: string) {
  if (conversationId.value === null) return
  if (!iteratePageId) currentPageId.value = ''
  await run(task, conversationId.value, {
    onDone: (ok, pageId) => {
      if (ok && pageId) {
        currentPageId.value = pageId
        previewUrl.value = pagePreviewUrl(pageId)
        sessionStorage.setItem(PREVIEW_KEY, pageId)
      }
    },
  }, iteratePageId, selectedModelId.value)
  if (conversationId.value !== null) {
    try {
      messages.value = await fetchMessages(conversationId.value)
    } catch {
      /* 消息流对齐失败不影响结果展示 */
    }
  }
}

function makeMsg(role: 'user' | 'assistant', content: string): ChatMessage {
  return {
    id: Date.now() + Math.floor(Math.random() * 1000),
    conversation_id: conversationId.value ?? 0,
    role,
    content,
    created_at: new Date().toISOString(),
  }
}

function scrollToBottom() {
  /* ChatPanel 内部 onUpdated 自动滚底，此处预留扩展 */
}

/* ---- 拖拽分界条（宽度 localStorage 记忆） ---- */
function onDragStart(e: MouseEvent) {
  e.preventDefault()
  dragging.value = true
  document.body.style.userSelect = 'none'

  const body = bodyEl.value
  if (!body) return
  const rect = body.getBoundingClientRect()

  const move = (ev: MouseEvent) => {
    const min = 340
    const max = Math.max(380, body.clientWidth - 480)
    const w = Math.min(max, Math.max(min, rect.right - ev.clientX))
    chatPaneW.value = w
  }
  const up = () => {
    dragging.value = false
    document.body.style.userSelect = ''
    localStorage.setItem(SPLIT_KEY, String(Math.round(chatPaneW.value)))
    document.removeEventListener('mousemove', move)
    document.removeEventListener('mouseup', up)
  }
  document.addEventListener('mousemove', move)
  document.addEventListener('mouseup', up)
}
</script>

<template>
  <div class="workbench">
    <AppHeader :user="user" @logout="() => {}" />

    <div class="main-area">
      <StageSteps :progress="progress" />

      <div ref="bodyEl" class="app-body">
        <!-- 左：会话导航栏（Claude 式列表 + 新建） -->
        <aside class="conv-nav">
          <button class="conv-new" :disabled="progress.active || recovering || clarifying" @click="newConversation">
            ＋ 新建对话
          </button>
          <div class="conv-list">
            <div
              v-for="c in conversations"
              :key="c.id"
              class="conv-item"
              :class="{ on: c.id === conversationId, disabled: progress.active || recovering || clarifying }"
              @click="switchConversation(c.id)"
            >
              <div class="conv-text">
                <div class="conv-title">{{ c.title }}</div>
                <div class="conv-time">{{ convTime(c.created_at) }}</div>
              </div>
              <div class="conv-ops" @click.stop>
                <button class="op-btn" title="重命名" :disabled="progress.active" @click="renameConversationById(c)">✎</button>
                <button class="op-btn danger" title="删除（作品会保留）" :disabled="progress.active" @click="deleteConversationById(c)">🗑</button>
              </div>
            </div>
            <div v-if="conversations.length === 0" class="conv-empty">暂无对话</div>
          </div>
        </aside>

        <PreviewStage :preview-url="previewUrl" :page-id="currentPageId" @share="shareVisible = true" />

        <div class="drag-bar" :class="{ active: dragging }" title="拖拽调整布局" @mousedown="onDragStart">
          <span class="grip"></span>
        </div>

        <ChatPanel
          :style="{ width: chatPaneW + 'px' }"
          :messages="messages"
          :progress="progress"
          :last-result="lastResult"
          :phase="phase"
          :sending="progress.active || recovering"
          :clarifying="clarifying"
          :clarify-questions="clarifyQuestions"
          :service-error="serviceError"
          :has-work="Boolean(currentPageId)"
          :models="models"
          :selected-model-id="selectedModelId"
          :no-model="noModel"
          :model-placeholder="modelPlaceholder"
          @send="onSend"
          @select-model="onSelectModel"
          @stop-generate="stopGenerate"
          @submit-clarify="onSubmitClarify"
          @skip-clarify="onSkipClarify"
        />
      </div>
    </div>

    <ShareDialog v-model:visible="shareVisible" :page-id="currentPageId" />
  </div>
</template>

<style scoped>
.workbench {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg);
}

.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.app-body {
  flex: 1;
  display: flex;
  min-height: 0;
  padding: 0 0 0 0;
}

/* 布局：动画在左（主角）、对话在右、分界条可拖拽 */
.drag-bar {
  order: 2;
  width: 8px;
  cursor: col-resize;
  background: transparent;
  position: relative;
  flex-shrink: 0;
  transition: background 0.2s;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: center;
}

.drag-bar .grip {
  width: 3px;
  height: 44px;
  border-radius: 3px;
  background: var(--line);
  transition: 0.2s;
}

.drag-bar:hover,
.drag-bar.active {
  background: rgba(79, 110, 247, 0.08);
}

.drag-bar:hover .grip,
.drag-bar.active .grip {
  background: var(--brand-1);
  height: 64px;
}

/* 布局 order：会话导航栏(0，DOM 首个) → 动画(1) → 拖拽条(2) → 对话(3，DOM 末个) */
.conv-nav {
  order: 0;
}

.app-body > :nth-child(2) {
  order: 1;
}

.app-body > :last-child {
  order: 3;
  border-left: 1px solid var(--line);
}

/* 左侧会话导航栏 */
.conv-nav {
  width: 216px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--card);
  border-right: 1px solid var(--line);
  min-height: 0;
}

.conv-new {
  margin: 12px 12px 8px;
  padding: 9px 0;
  border: 1.5px solid var(--brand-1);
  background: var(--brand-soft);
  color: var(--brand-1);
  border-radius: 10px;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: 0.15s;
  flex-shrink: 0;
}

.conv-new:hover:not(:disabled) {
  background: rgba(79, 110, 247, 0.16);
}

.conv-new:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 8px 12px;
  min-height: 0;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 10px;
  cursor: pointer;
  transition: 0.15s;
  margin-bottom: 2px;
}

.conv-item:hover:not(.disabled) {
  background: #f0f3f9;
}

.conv-item.on {
  background: var(--brand-soft);
}

.conv-item.disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.conv-text {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 13px;
  color: var(--text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-item.on .conv-title {
  color: var(--brand-1);
  font-weight: 700;
}

.conv-time {
  font-size: 11px;
  color: var(--text-3);
  margin-top: 1px;
}

.conv-ops {
  display: none;
  gap: 2px;
  flex-shrink: 0;
}

.conv-item:hover .conv-ops {
  display: flex;
}

.op-btn {
  border: none;
  background: transparent;
  color: var(--text-3);
  width: 22px;
  height: 22px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.op-btn:hover:not(:disabled) {
  background: #fff;
  color: var(--brand-1);
}

.op-btn.danger:hover:not(:disabled) {
  color: var(--err);
}

.op-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.conv-empty {
  padding: 20px 0;
  text-align: center;
  color: var(--text-3);
  font-size: 12.5px;
}
</style>
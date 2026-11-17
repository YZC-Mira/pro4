/** 生成流状态管理（SSE 事件 → 响应式状态）。
 *
 * 界面话术原则（样品 A 定稿）：不暴露 AI 内部失败/重试——
 * repairing → "优化"，attempt 次数不显示，失败摘要以友好语言呈现。
 * 后端 stage 事件本身保持真实术语（架构层），仅本层做展示映射。
 */

import { ref } from 'vue'
import { streamGenerate, type StreamEvent } from '../api'

export type Phase = 'idle' | 'clarifying'

export interface GenProgress {
  active: boolean
  stage: string          // 当前界面阶段键：clarifying/generating/verifying/optimizing/done/failed
  stageLabel: string     // 界面话术
  attemptNo: number      // 内部计数（不展示次数，仅驱动状态）
  progressKb: number
  progressSeconds: number
  checking: boolean
  iterating: boolean     // 迭代模式：话术用"调整"
}

/** 后端 stage → 界面话术（不暴露自修复） */
const STAGE_LABELS: Record<string, string> = {
  clarifying: '澄清',
  generating: '生成',
  verifying: '验证',
  repairing: '优化',
  done: '完成',
  failed: '未成功',
  cancelled: '已停止',
}

const SUB_LABELS: Record<string, string> = {
  clarifying: '正在确认需求',
  generating: 'AI 正在编写动画',
  verifying: '正在检查动画效果',
  repairing: '正在打磨动画细节',
  done: '动画已生成完成',
  failed: '生成未完成，可重试',
  cancelled: '已停止生成',
}

export function useGenerateStream() {
  const progress = ref<GenProgress>({
    active: false,
    stage: '',
    stageLabel: '',
    attemptNo: 0,
    progressKb: 0,
    progressSeconds: 0,
    checking: false,
    iterating: false,
  })

  const lastResult = ref<{ ok: boolean; summary: string } | null>(null)
  const finalPageId = ref('')
  const serviceError = ref('')
  const cancelled = ref(false)
  let abortController: AbortController | null = null
  let currentGenId: string | null = null  // SSE start 事件下发的生成任务 ID（显式停止用）

  function resetProgress() {
    progress.value = {
      active: true,
      stage: '',
      stageLabel: '',
      attemptNo: 0,
      progressKb: 0,
      progressSeconds: 0,
      checking: false,
      iterating: false,
    }
  }

  function finishProgress() {
    progress.value.active = false
    progress.value.checking = false
  }

  function applyStage(stage: string) {
    progress.value.stage = stage
    let label = STAGE_LABELS[stage] ?? stage
    if (progress.value.iterating && stage === 'generating') label = '调整'
    progress.value.stageLabel = label
  }

  async function run(
    task: string,
    conversationId: number | null,
    hooks: {
      onAttemptStart?: () => void
      onDone?: (ok: boolean, pageId: string) => void
    } = {},
    iteratePageId?: string | null,
    modelId?: number | null,
  ): Promise<void> {
    lastResult.value = null
    serviceError.value = ''
    if (!iteratePageId) finalPageId.value = ''
    cancelled.value = false
    abortController = new AbortController()
    resetProgress()
    progress.value.iterating = Boolean(iteratePageId)

    try {
      await streamGenerate(task, conversationId, (e: StreamEvent) => {
        if (e.event === 'start') {
          currentGenId = e.data.gen_id ?? null  // 显式停止接口的任务标识
        } else if (e.event === 'stage') {
          applyStage(e.data.stage)
        } else if (e.event === 'attempt_start') {
          progress.value.attemptNo = e.data.attempt
          progress.value.checking = false
          hooks.onAttemptStart?.()
        } else if (e.event === 'progress') {
          progress.value.progressKb = e.data.kb
          progress.value.progressSeconds = e.data.seconds
        } else if (e.event === 'sandbox_check') {
          progress.value.checking = true
        } else if (e.event === 'error') {
          serviceError.value = `生成服务异常：${e.data.message ?? '未知错误'}`
        } else if (e.event === 'done') {
          const ok = e.data.ok === true
          const wasCancelled = e.data.cancelled === true
          applyStage(ok ? 'done' : wasCancelled ? 'cancelled' : 'failed')
          lastResult.value = {
            ok,
            summary: wasCancelled
              ? '已停止'
              : ok
                ? iteratePageId
                  ? `动画已按反馈调整完成（${Math.round(e.data.seconds / 60)} 分 ${Math.round(e.data.seconds % 60)} 秒），分享链接自动更新`
                  : `动画已生成完成（${Math.round(e.data.seconds / 60)} 分 ${Math.round(e.data.seconds % 60)} 秒）`
                : iteratePageId
                  ? '调整未完成，请换个说法描述你的修改需求'
                  : '生成未完成，请调整任务描述后重试',
          }
          if (ok && e.data.page_id) {
            finalPageId.value = e.data.page_id
            hooks.onDone?.(true, e.data.page_id)
          } else {
            hooks.onDone?.(false, '')
          }
        }
      }, iteratePageId, modelId, abortController.signal)
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        cancelled.value = true
        applyStage('cancelled')
        lastResult.value = { ok: false, summary: '已停止' }
        hooks.onDone?.(false, '')
      } else {
        throw e
      }
    } finally {
      finishProgress()
      abortController = null
      currentGenId = null
    }
  }

  /** 用户显式停止：先调取消接口（置后端取消标志），再断 SSE。
   * 断 SSE 本身不再触发取消（刷新/HMR 断开让生成继续，恢复轮询拿结果）。 */
  async function stop() {
    const genId = currentGenId
    if (genId) {
      try {
        await fetch(`/api/generations/${genId}/cancel`, { method: 'POST' })
      } catch {
        /* 取消请求失败也继续断开（本地同源概率极低） */
      }
    }
    if (abortController) {
      abortController.abort()
    }
  }

  return {
    progress,
    lastResult,
    finalPageId,
    serviceError,
    cancelled,
    run,
    stop,
    stageSubLabel: (stage: string) => SUB_LABELS[stage] ?? '',
  }
}
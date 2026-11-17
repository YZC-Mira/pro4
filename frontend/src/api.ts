/** 后端 API 封装（任务 6-8：SSE 流式 + 会话 REST + 认证 + 分享 + 我的作品）。 */

export interface ChatMessage {
  id: number
  conversation_id: number
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export interface StreamEvent {
  event: string
  data: any
}

export interface ShareInfo {
  token: string
  url: string
  qrcode_url: string
  validity: string
  expires_at: string | null
}

export interface MyPageItem {
  page_id: string
  task: string
  title: string | null
  status: string
  attempt_count: number
  final_html_path: string | null
  error_summary: string | null
  model: string | null
  created_at: string
  updated_at: string
}

// ---------- token 管理（任务 8：登录态） ----------

const TOKEN_KEY = 'algoagent.token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function jsonHeaders(): Record<string, string> {
  return { 'Content-Type': 'application/json', ...authHeaders() }
}

// ---------- 认证（任务 7） ----------

export interface AuthUser {
  id: number
  username: string
  role: string
  token?: string
}

export async function register(username: string, password: string): Promise<AuthUser> {
  const resp = await fetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `注册失败：${resp.status}`)
  const user = await resp.json()
  if (user.token) setToken(user.token)
  return user
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const resp = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `登录失败：${resp.status}`)
  const user = await resp.json()
  if (user.token) setToken(user.token)
  return user
}

export async function fetchMe(): Promise<AuthUser | null> {
  if (!getToken()) return null
  const resp = await fetch('/api/auth/me', { headers: authHeaders() })
  if (resp.status === 401) {
    clearToken()
    return null
  }
  if (!resp.ok) return null
  return await resp.json()
}

// ---------- 会话（任务 5；任务 8 起带登录态；多会话切换） ----------

export interface ConversationItem {
  id: number
  title: string
  created_at: string
}

export async function createConversation(title = '新对话'): Promise<number> {
  const resp = await fetch('/api/conversations', {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify({ title }),
  })
  if (!resp.ok) throw new Error(`创建会话失败：${resp.status}`)
  return (await resp.json()).id
}

export async function fetchConversations(): Promise<ConversationItem[]> {
  const resp = await fetch('/api/conversations', { headers: authHeaders() })
  if (!resp.ok) throw new Error(`获取会话列表失败：${resp.status}`)
  return await resp.json()
}

export interface LatestPageInfo {
  page_id: string
  task: string
  status: string
  created_at: string
}

export async function fetchLatestPage(
  conversationId: number,
  includePending = false,
): Promise<LatestPageInfo | null> {
  const url = `/api/conversations/${conversationId}/latest-page${includePending ? '?include_pending=true' : ''}`
  const resp = await fetch(url, { headers: authHeaders() })
  if (resp.status === 404) return null
  if (!resp.ok) throw new Error(`获取会话作品失败：${resp.status}`)
  return await resp.json()
}

export async function renameConversation(conversationId: number, title: string): Promise<void> {
  const resp = await fetch(`/api/conversations/${conversationId}`, {
    method: 'PATCH',
    headers: jsonHeaders(),
    body: JSON.stringify({ title }),
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `重命名失败：${resp.status}`)
}

export async function deleteConversation(conversationId: number): Promise<void> {
  const resp = await fetch(`/api/conversations/${conversationId}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `删除失败：${resp.status}`)
}

export async function renamePage(pageId: string, title: string): Promise<void> {
  const resp = await fetch(`/api/pages/${pageId}`, {
    method: 'PATCH',
    headers: jsonHeaders(),
    body: JSON.stringify({ title }),
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `重命名失败：${resp.status}`)
}

export async function deletePage(pageId: string): Promise<void> {
  const resp = await fetch(`/api/pages/${pageId}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `删除失败：${resp.status}`)
}

// ---------- 管理端（仅 admin） ----------

export interface AdminUserItem {
  id: number
  username: string
  role: string
  created_at: string
  page_count: number
}

export async function fetchAdminUsers(): Promise<AdminUserItem[]> {
  const resp = await fetch('/api/admin/users', { headers: authHeaders() })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `获取用户列表失败：${resp.status}`)
  return await resp.json()
}

export async function adminResetPassword(userId: number, password: string): Promise<void> {
  const resp = await fetch(`/api/admin/users/${userId}/password`, {
    method: 'PATCH',
    headers: jsonHeaders(),
    body: JSON.stringify({ password }),
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `重置失败：${resp.status}`)
}

export interface AdminPageItem extends MyPageItem {
  author_name: string
}

export async function fetchAdminPages(): Promise<AdminPageItem[]> {
  const resp = await fetch('/api/admin/pages', { headers: authHeaders() })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `获取作品列表失败：${resp.status}`)
  return await resp.json()
}

// ---------- 模型注册表（多模型：内置模型 + 自定义模型；对话中可选；默认 = 内置之一标记） ----------

export interface ModelItem {
  id: number
  name: string
  base_url: string
  model_id: string
  scope: 'platform' | 'user'
  owner_id: number | null
  api_key_masked?: string
  mine?: boolean
  is_default?: boolean
  enabled?: number | boolean
  created_at: string
}

export async function fetchModels(): Promise<ModelItem[]> {
  const resp = await fetch('/api/models', { headers: authHeaders() })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `获取模型列表失败：${resp.status}`)
  return await resp.json()
}

export interface ModelAvailability {
  no_model: boolean // 平台从未配置模型（无内置 + 无 env）——管理员也不应能发对话
}

export async function fetchModelAvailability(): Promise<ModelAvailability> {
  const resp = await fetch('/api/models/availability', { headers: authHeaders() })
  if (!resp.ok) return { no_model: false } // 接口异常不阻塞（保守放行）
  return await resp.json()
}

export async function createModel(body: {
  name: string
  base_url: string
  api_key?: string
  model_id: string
  scope?: 'user' | 'platform'
}): Promise<{ id: number }> {
  const resp = await fetch('/api/models', {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `创建模型失败：${resp.status}`)
  return await resp.json()
}

export async function updateModel(id: number, body: {
  name: string
  base_url: string
  model_id: string
  api_key?: string
}): Promise<void> {
  const resp = await fetch(`/api/models/${id}`, {
    method: 'PATCH',
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `保存失败：${resp.status}`)
}

export async function setDefaultModel(id: number): Promise<void> {
  const resp = await fetch(`/api/models/${id}/default`, { method: 'POST', headers: authHeaders() })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `设置失败：${resp.status}`)
}

export async function toggleModel(id: number): Promise<{ enabled: boolean }> {
  const resp = await fetch(`/api/models/${id}/toggle`, { method: 'POST', headers: authHeaders() })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `操作失败：${resp.status}`)
  return await resp.json()
}

export async function deleteModel(id: number): Promise<void> {
  const resp = await fetch(`/api/models/${id}`, { method: 'DELETE', headers: authHeaders() })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `删除失败：${resp.status}`)
}

export async function testModel(body: {
  base_url: string
  api_key?: string
  model_id: string
}): Promise<{ ok: boolean; reply?: string; error?: string; seconds: number }> {
  const resp = await fetch('/api/models/test', {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `测试失败：${resp.status}`)
  return await resp.json()
}

// ---------- 生成状态（刷新恢复轮询 / 停止） ----------

export interface PageStatusInfo {
  page_id: string
  status: 'pending' | 'passed' | 'failed' | 'cancelled' | string
  error_summary: string | null
  conversation_id: number | null
  updated_at: string
}

export async function fetchPageStatus(pageId: string): Promise<PageStatusInfo> {
  const resp = await fetch(`/api/pages/${pageId}/status`, { headers: authHeaders() })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `获取状态失败：${resp.status}`)
  return await resp.json()
}

export async function fetchMessages(conversationId: number): Promise<ChatMessage[]> {
  const resp = await fetch(`/api/conversations/${conversationId}/messages`, { headers: authHeaders() })
  if (!resp.ok) throw new Error(`拉取历史失败：${resp.status}`)
  return await resp.json()
}

export async function postMessage(
  conversationId: number,
  role: 'user' | 'assistant',
  content: string,
): Promise<void> {
  const resp = await fetch(`/api/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify({ role, content }),
  })
  if (!resp.ok) throw new Error(`写入消息失败：${resp.status}`)
}

// ---------- 澄清（PRD F1） ----------

export interface ClarifyResult {
  need_clarify: boolean
  questions: string[]
  reason: string
  out_of_scope?: boolean
  error?: string // no_model：平台未配置模型（显示引导提示，不进入生成）
}

export async function clarifyTask(task: string, modelId?: number | null): Promise<ClarifyResult> {
  const resp = await fetch('/api/chat/clarify', {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify({ task, model_id: modelId ?? null }),
  })
  if (!resp.ok) throw new Error(`澄清请求失败：${resp.status}`)
  return await resp.json()
}

// ---------- SSE 流式生成（任务 6；任务 8 起带登录态） ----------

/** POST + ReadableStream 解析 SSE（EventSource 不支持 POST，故手写解析）。
 * iteratePageId 非空 = 对话式迭代：按反馈修改既有作品（PRD F1 上下文）。
 * modelId 非空 = 使用模型注册表中选择的模型；null = 平台默认。
 * signal = AbortController.signal：中断 SSE（后端检测断开取消生成）。 */
export async function streamGenerate(
  task: string,
  conversationId: number | null,
  onEvent: (e: StreamEvent) => void,
  iteratePageId?: string | null,
  modelId?: number | null,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify({
      task,
      conversation_id: conversationId,
      iterate_page_id: iteratePageId ?? null,
      model_id: modelId ?? null,
    }),
    signal,
  })
  if (resp.status === 401) throw new Error('登录已过期，请重新登录')
  if (!resp.ok || !resp.body) {
    // 透传后端错误信息（如 422"平台暂未配置模型"），而不是只给状态码
    let detail = `流式请求失败：${resp.status}`
    try {
      const j = await resp.json()
      if (j && j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* 保留默认 */ }
    throw new Error(detail)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let event = 'message'
      const dataLines: string[] = []
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim()
        else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
      }
      if (dataLines.length > 0) {
        try {
          onEvent({ event, data: JSON.parse(dataLines.join('\n')) })
        } catch {
          /* 忽略坏帧（如心跳空行） */
        }
      }
    }
  }
}

// ---------- 作品与分享（任务 8：F5 / F7） ----------

export function pagePreviewUrl(pageId: string): string {
  return `/api/pages/${pageId}/html`
}

export async function createShare(pageId: string, validity: '1d' | '7d' | 'forever'): Promise<ShareInfo> {
  const resp = await fetch('/api/shares', {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify({ page_id: pageId, validity }),
  })
  if (!resp.ok) throw new Error((await resp.json()).detail ?? `创建分享失败：${resp.status}`)
  return await resp.json()
}

export async function fetchMyPages(): Promise<MyPageItem[]> {
  const resp = await fetch('/api/pages/my', { headers: authHeaders() })
  if (!resp.ok) throw new Error(`获取作品列表失败：${resp.status}`)
  return await resp.json()
}
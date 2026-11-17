<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  adminResetPassword,
  createModel,
  deleteModel,
  deletePage,
  fetchAdminPages,
  fetchAdminUsers,
  fetchMe,
  fetchModels,
  fetchMyPages,
  renamePage,
  setDefaultModel,
  toggleModel,
  testModel,
  updateModel,
  type AdminPageItem,
  type AdminUserItem,
  type AuthUser,
  type ModelItem,
  type MyPageItem,
} from '../api'
import AppHeader from '../components/AppHeader.vue'
import ShareDialog from '../components/ShareDialog.vue'

const router = useRouter()

const user = ref<AuthUser | null>(null)
const tab = ref<'info' | 'works' | 'models' | 'admin'>('works')
const works = ref<MyPageItem[]>([])
const loading = ref(false)

const shareVisible = ref(false)
const sharePageId = ref('')

const adminTab = ref<'users' | 'pages' | 'llm'>('users')
const adminUsers = ref<AdminUserItem[]>([])
const adminPages = ref<AdminPageItem[]>([])
const adminLoading = ref(false)

// 模型（所有用户）：列表 + 添加/编辑共用表单
// editingModelId：null = 收起；'new' = 新增；数字 = 编辑该模型（预填，api_key 留空 = 不变）
const myModels = ref<ModelItem[]>([])
const myModelsLoading = ref(false)
const modelForm = ref({ name: '', base_url: '', api_key: '', model_id: '' })
const modelSaving = ref(false)
const modelTesting = ref(false)
const modelTestResult = ref<{ ok: boolean; reply?: string; error?: string; seconds: number } | null>(null)
const editingModelId = ref<number | 'new' | null>(null)

function openNewModel() {
  editingModelId.value = 'new'
  modelForm.value = { name: '', base_url: '', api_key: '', model_id: '' }
  modelTestResult.value = null
}

function editModel(m: ModelItem) {
  editingModelId.value = m.id
  modelForm.value = { name: m.name, base_url: m.base_url, api_key: '', model_id: m.model_id }
  modelTestResult.value = null
}

function closeModelForm() {
  editingModelId.value = null
  modelForm.value = { name: '', base_url: '', api_key: '', model_id: '' }
  modelTestResult.value = null
}

const isAdmin = computed(() => user.value?.role === 'admin')
const adminModels = computed(() => myModels.value.filter((m) => m.scope === 'platform'))
const myPlatformList = computed(() => myModels.value.filter((m) => m.scope === 'platform'))
const myCustomList = computed(() => myModels.value.filter((m) => m.scope === 'user'))
const initial = computed(() => user.value?.username?.charAt(0) ?? '?')
const roleLabel = computed(() => (user.value?.role === 'admin' ? '管理员' : '用户'))

onMounted(async () => {
  user.value = await fetchMe()
  if (!user.value) {
    router.push({ name: 'login' })
    return
  }
  await loadWorks()
})

async function loadWorks() {
  loading.value = true
  try {
    works.value = await fetchMyPages()
  } catch (e) {
    console.error('作品列表加载失败', e)
  } finally {
    loading.value = false
  }
}

function previewWork(item: MyPageItem) {
  sessionStorage.setItem('algoagent.previewPageId', item.page_id)
  router.push({ name: 'work' })
}

function shareWork(item: MyPageItem) {
  sharePageId.value = item.page_id
  shareVisible.value = true
}

async function renameWork(item: MyPageItem) {
  const title = window.prompt('重命名作品', item.title || item.task.slice(0, 30))
  if (title === null) return
  const trimmed = title.trim()
  if (!trimmed) {
    ElMessage.warning('标题不能为空')
    return
  }
  try {
    await renamePage(item.page_id, trimmed)
    await loadWorks()
    ElMessage.success('已重命名')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function removeWork(item: MyPageItem) {
  const name = item.title || item.task.slice(0, 20)
  const ok = window.confirm(`删除作品「${name}」？\n（对应的分享链接也将失效，不可恢复）`)
  if (!ok) return
  try {
    await deletePage(item.page_id)
    await loadWorks()
    if (sessionStorage.getItem('algoagent.previewPageId') === item.page_id) {
      sessionStorage.removeItem('algoagent.previewPageId')
    }
    ElMessage.success('作品已删除')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

function workTitle(item: MyPageItem): string {
  return item.title || item.task.slice(0, 30)
}

// ---------- 管理端（admin） ----------

async function loadAdmin() {
  adminLoading.value = true
  try {
    if (adminTab.value === 'users') {
      adminUsers.value = await fetchAdminUsers()
    } else if (adminTab.value === 'pages') {
      adminPages.value = await fetchAdminPages()
    } else {
      myModels.value = await fetchModels()  // 内置模型列表（管理端用，含默认标记）
    }
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    adminLoading.value = false
  }
}

async function switchAdminTab(t: 'users' | 'pages' | 'llm') {
  adminTab.value = t
  await loadAdmin()
}

async function resetUserPassword(item: AdminUserItem) {
  const pwd = window.prompt(`重置「${item.username}」的密码（至少 6 位）`, '')
  if (pwd === null) return
  if (pwd.trim().length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  try {
    await adminResetPassword(item.id, pwd.trim())
    ElMessage.success(`已重置 ${item.username} 的密码`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function adminDeleteWork(item: AdminPageItem) {
  const name = item.title || item.task.slice(0, 20)
  const ok = window.confirm(`删除「${item.author_name}」的作品「${name}」？\n（对应分享链接将失效，不可恢复）`)
  if (!ok) return
  try {
    await deletePage(item.page_id)
    await loadAdmin()
    ElMessage.success('作品已删除')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function adminRenameWork(item: AdminPageItem) {
  const title = window.prompt('重命名作品', item.title || item.task.slice(0, 30))
  if (title === null) return
  const trimmed = title.trim()
  if (!trimmed) {
    ElMessage.warning('标题不能为空')
    return
  }
  try {
    await renamePage(item.page_id, trimmed)
    await loadAdmin()
    ElMessage.success('已重命名')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

function adminPreviewWork(item: AdminPageItem) {
  sessionStorage.setItem('algoagent.previewPageId', item.page_id)
  router.push({ name: 'work' })
}

// ---------- 我的模型（所有用户） ----------

async function loadMyModels() {
  myModelsLoading.value = true
  try {
    myModels.value = await fetchModels()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    myModelsLoading.value = false
  }
}

async function saveCustomModel() {
  if (modelSaving.value) return
  const f = modelForm.value
  if (!f.name.trim() || !f.base_url.trim() || !f.model_id.trim()) {
    ElMessage.warning('名称、接口地址、模型名均不能为空')
    return
  }
  modelSaving.value = true
  try {
    if (editingModelId.value === 'new') {
      await createModel({
        name: f.name.trim(),
        base_url: f.base_url.trim(),
        api_key: f.api_key.trim() || undefined,
        model_id: f.model_id.trim(),
        scope: 'user',
      })
      ElMessage.success('模型已添加，可在对话中选择使用')
    } else if (typeof editingModelId.value === 'number') {
      await updateModel(editingModelId.value, {
        name: f.name.trim(),
        base_url: f.base_url.trim(),
        model_id: f.model_id.trim(),
        api_key: f.api_key.trim() || undefined,
      })
      ElMessage.success('模型已更新')
    }
    closeModelForm()
    await loadMyModels()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    modelSaving.value = false
  }
}

async function removeMyModel(item: ModelItem) {
  const ok = window.confirm(`删除模型「${item.name}」？`)
  if (!ok) return
  try {
    await deleteModel(item.id)
    if (editingModelId.value === item.id) closeModelForm()
    await loadMyModels()
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function testMyModel() {
  if (modelTesting.value) return
  const f = modelForm.value
  if (!f.base_url.trim() || !f.model_id.trim()) {
    ElMessage.warning('填写接口地址与模型名后可测试')
    return
  }
  modelTesting.value = true
  modelTestResult.value = null
  try {
    modelTestResult.value = await testModel({
      base_url: f.base_url.trim(),
      api_key: f.api_key.trim() || undefined,
      model_id: f.model_id.trim(),
    })
  } catch (e) {
    modelTestResult.value = { ok: false, error: (e as Error).message, seconds: 0 }
  } finally {
    modelTesting.value = false
  }
}

// ---------- 内置模型管理（admin：管理端模型配置 tab 下） ----------

async function savePlatformModel() {
  if (modelSaving.value) return
  const f = modelForm.value
  if (!f.name.trim() || !f.base_url.trim() || !f.model_id.trim()) {
    ElMessage.warning('名称、接口地址、模型名均不能为空')
    return
  }
  modelSaving.value = true
  try {
    if (editingModelId.value === 'new') {
      await createModel({
        name: f.name.trim(),
        base_url: f.base_url.trim(),
        api_key: f.api_key.trim() || undefined,
        model_id: f.model_id.trim(),
        scope: 'platform',
      })
      ElMessage.success('内置模型已添加，所有人可在对话中选择')
    } else if (typeof editingModelId.value === 'number') {
      await updateModel(editingModelId.value, {
        name: f.name.trim(),
        base_url: f.base_url.trim(),
        model_id: f.model_id.trim(),
        api_key: f.api_key.trim() || undefined,
      })
      ElMessage.success('内置模型已更新')
    }
    closeModelForm()
    await loadAdmin()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    modelSaving.value = false
  }
}

async function markDefault(m: ModelItem) {
  try {
    await setDefaultModel(m.id)
    ElMessage.success(`已将「${m.name}」设为默认模型`)
    await loadAdmin()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function toggleMyModel(m: ModelItem) {
  try {
    const r = await toggleModel(m.id)
    ElMessage.success(r.enabled ? `已启用「${m.name}」` : `已停用「${m.name}」（普通用户将不可见）`)
    await loadAdmin()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

function taskCategory(item: MyPageItem): string {
  const t = item.task
  for (const key of ['Dijkstra', '最短路径']) if (t.includes(key)) return '最短路径'
  for (const key of ['二分查找', '二分']) if (t.includes(key)) return '二分查找'
  for (const key of ['冒泡', '排序']) if (t.includes(key)) return '排序'
  for (const key of ['遍历', '树']) if (t.includes(key)) return '树遍历'
  return '算法动画'
}

function fmtTime(t: string): string {
  return t.replace('T', ' ').slice(0, 16)
}
</script>

<template>
  <div class="center-page">
    <AppHeader :user="user" @logout="() => {}" />

    <div class="center-body">
      <aside class="side">
        <div class="side-user">
          <div class="side-avatar brand-avatar">{{ initial }}</div>
          <div class="side-name">{{ user?.username ?? '…' }}</div>
          <div class="side-role">{{ roleLabel }}</div>
        </div>
        <div class="side-menu">
          <div class="side-item" :class="{ on: tab === 'info' }" @click="tab = 'info'">👤 个人信息</div>
          <div class="side-item" :class="{ on: tab === 'works' }" @click="tab = 'works'">🗂 我的作品</div>
          <div class="side-item" :class="{ on: tab === 'models' }" @click="tab = 'models'; loadMyModels()">🤖 模型</div>
          <div v-if="isAdmin" class="side-item admin" :class="{ on: tab === 'admin' }" @click="tab = 'admin'; switchAdminTab(adminTab)">
            ⚙ 管理
          </div>
        </div>
      </aside>

      <div class="side-content">
        <template v-if="tab === 'works'">
          <div class="works-head">
            <h3>我的作品</h3>
            <p>共 {{ works.length }} 件作品</p>
          </div>
          <div v-loading="loading" class="works-grid">
            <div v-if="!loading && works.length === 0" class="empty-works">
              <div class="empty-icon">◆</div>
              <h4>还没有作品</h4>
              <el-button type="primary" round @click="router.push({ name: 'work' })">去生成第一个</el-button>
            </div>
            <div v-for="item in works" :key="item.page_id" class="work-card">
              <div class="thumb">
                <i style="height: 40%"></i><i style="height: 85%"></i><i style="height: 60%"></i>
                <i style="height: 30%"></i><i style="height: 95%"></i><i style="height: 70%"></i>
              </div>
              <div class="wtop">
                <h4 :title="item.task">{{ workTitle(item) }}</h4>
                <span class="wtag" :class="item.status === 'passed' ? 'ok' : 'fail'">
                  {{ item.status === 'passed' ? '✓ 可用' : '✗ 生成未成功' }}
                </span>
              </div>
              <div class="wmeta">
                <span>🕘 {{ fmtTime(item.created_at) }}</span>
                <span>{{ taskCategory(item) }}</span>
              </div>
              <div class="wacts">
                <button class="tbtn" :disabled="item.status !== 'passed'" @click="previewWork(item)">👁 预览</button>
                <button
                  class="tbtn primary"
                  :disabled="item.status !== 'passed'"
                  @click="shareWork(item)"
                >
                  🔗 分享
                </button>
                <button class="tbtn icon-only" title="重命名" @click="renameWork(item)">✎</button>
                <button class="tbtn icon-only danger" title="删除作品" @click="removeWork(item)">🗑</button>
              </div>
            </div>
          </div>
        </template>

        <template v-else-if="tab === 'models'">
          <div class="works-head">
            <h3>模型</h3>
            <p>对话时可在输入框下方切换；内置模型所有人可用，自定义模型仅自己可用</p>
          </div>
          <div v-loading="myModelsLoading" class="models-body">
            <div class="glass-card model-list-card">
              <div class="llm-head">内置模型</div>
              <div v-if="!myModelsLoading && myPlatformList.length === 0" class="model-empty">
                暂无内置模型，可联系管理员配置
              </div>
              <div v-for="m in myPlatformList" :key="m.id" class="model-row">
                <div class="model-info">
                  <b>{{ m.name }}</b>
                </div>
                <el-tag v-if="m.is_default" size="small" type="success">默认</el-tag>
                <el-tag size="small" type="primary">内置</el-tag>
              </div>
            </div>

            <div class="glass-card model-list-card">
              <div class="llm-head">我的自定义模型 <span class="llm-sub">仅自己可用</span></div>
              <div v-if="!myModelsLoading && myCustomList.length === 0" class="model-empty">
                还没有自定义模型，点击下方按钮添加
              </div>
              <div v-for="m in myCustomList" :key="m.id" class="model-row">
                <div class="model-info">
                  <b>{{ m.name }}</b>
                  <span class="model-meta">{{ m.model_id }}</span>
                </div>
                <button class="tbtn icon-only" title="编辑" @click="editModel(m)">✎</button>
                <button class="tbtn icon-only danger" title="删除" @click="removeMyModel(m)">🗑</button>
              </div>
            </div>

            <div class="glass-card model-form-card">
              <button v-if="editingModelId === null" class="add-model-btn" @click="openNewModel">
                ＋ 添加自定义模型
              </button>
              <template v-else>
                <div class="llm-head">{{ editingModelId === 'new' ? '添加自定义模型' : '编辑模型' }}</div>
                <div class="llm-fields">
                  <div class="field">
                    <label>名称（对话中显示）</label>
                    <el-input v-model="modelForm.name" placeholder="如 DeepSeek 官方" :disabled="modelSaving" />
                  </div>
                  <div class="field">
                    <label>接口地址（OpenAI 兼容 base_url）</label>
                    <el-input v-model="modelForm.base_url" placeholder="如 https://api.example.com/v1" :disabled="modelSaving" />
                  </div>
                  <div class="field">
                    <label>模型名称</label>
                    <el-input v-model="modelForm.model_id" placeholder="如 deepseek-chat" :disabled="modelSaving" />
                  </div>
                  <div class="field">
                    <label>API Key{{ editingModelId === 'new' ? '（内网端点可留空）' : '（留空保持不变）' }}</label>
                    <el-input v-model="modelForm.api_key" type="password" show-password placeholder="sk-…" :disabled="modelSaving" />
                  </div>
                </div>
                <div class="llm-actions">
                  <el-button :loading="modelTesting" @click="testMyModel">测试连接</el-button>
                  <el-button type="primary" :loading="modelSaving" @click="saveCustomModel">
                    {{ editingModelId === 'new' ? '添加' : '保存' }}
                  </el-button>
                  <el-button :disabled="modelSaving || modelTesting" @click="closeModelForm">取消</el-button>
                </div>
                <div v-if="modelTestResult" class="llm-test" :class="modelTestResult.ok ? 'ok' : 'fail'">
                  <template v-if="modelTestResult.ok">
                    ✓ 连接成功（{{ modelTestResult.seconds }}s）：{{ modelTestResult.reply }}
                  </template>
                  <template v-else>
                    ✗ 连接失败（{{ modelTestResult.seconds }}s）：{{ modelTestResult.error }}
                  </template>
                </div>
              </template>
            </div>
          </div>
        </template>

        <template v-else-if="tab === 'info'">
          <div class="works-head">
            <h3>个人信息</h3>
            <p>账号基础资料</p>
          </div>
          <div class="info-card glass-card">
            <div class="info-row">
              <label>头像</label>
              <div class="info-avatar brand-avatar">{{ initial }}</div>
            </div>
            <div class="info-row">
              <label>用户名</label>
              <div>{{ user?.username ?? '…' }}</div>
            </div>
            <div class="info-row">
              <label>角色</label>
              <div>
                <el-tag :type="user?.role === 'admin' ? 'primary' : 'info'" size="small">{{ roleLabel }}</el-tag>
              </div>
            </div>
            <div class="info-row">
              <label>作品数</label>
              <div>{{ works.length }}</div>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="works-head">
            <h3>管理</h3>
            <p>用户与作品管理（仅管理员可见）</p>
          </div>
          <div class="admin-tabs">
            <div class="admin-tab" :class="{ on: adminTab === 'users' }" @click="switchAdminTab('users')">用户</div>
            <div class="admin-tab" :class="{ on: adminTab === 'pages' }" @click="switchAdminTab('pages')">全部作品</div>
            <div class="admin-tab" :class="{ on: adminTab === 'llm' }" @click="switchAdminTab('llm')">模型配置</div>
          </div>

          <div v-loading="adminLoading" class="admin-body">
            <template v-if="adminTab === 'users'">
              <el-table :data="adminUsers" style="width: 100%">
                <el-table-column prop="username" label="用户名" min-width="120" />
                <el-table-column label="角色" width="90">
                  <template #default="{ row }">
                    <el-tag :type="row.role === 'admin' ? 'primary' : 'info'" size="small">
                      {{ row.role === 'admin' ? '管理员' : '用户' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="作品数" prop="page_count" width="80" />
                <el-table-column label="注册时间" width="150">
                  <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
                </el-table-column>
                <el-table-column label="操作" width="120" fixed="right">
                  <template #default="{ row }">
                    <el-button
                      size="small"
                      :disabled="row.role === 'admin'"
                      @click="resetUserPassword(row)"
                    >
                      重置密码
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </template>

            <template v-else-if="adminTab === 'pages'">
              <el-table :data="adminPages" style="width: 100%">
                <el-table-column label="标题" min-width="180">
                  <template #default="{ row }">{{ row.title || row.task.slice(0, 30) }}</template>
                </el-table-column>
                <el-table-column prop="author_name" label="作者" width="110" />
                <el-table-column label="状态" width="90">
                  <template #default="{ row }">
                    <el-tag :type="row.status === 'passed' ? 'success' : 'danger'" size="small">
                      {{ row.status === 'passed' ? '可用' : '未成功' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="创建时间" width="150">
                  <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
                </el-table-column>
                <el-table-column label="操作" width="220" fixed="right">
                  <template #default="{ row }">
                    <el-button size="small" :disabled="row.status !== 'passed'" @click="adminPreviewWork(row)">
                      预览
                    </el-button>
                    <el-button size="small" @click="adminRenameWork(row)">重命名</el-button>
                    <el-button size="small" type="danger" plain @click="adminDeleteWork(row)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </template>

            <template v-else>
              <div class="llm-panel">
                <div class="glass-card">
                  <div class="llm-head">内置模型</div>
                  <div v-if="adminModels.length === 0" class="model-empty">暂无内置模型</div>
                  <div v-for="m in adminModels" :key="m.id" class="model-row" :class="{ off: m.enabled === 0 }">
                    <div class="model-info">
                      <b>{{ m.name }}</b>
                      <span class="model-meta">{{ m.model_id }} · {{ m.base_url }}</span>
                    </div>
                    <el-tag v-if="m.enabled === 0" size="small" type="info">已停用</el-tag>
                    <el-tag v-else-if="m.is_default" size="small" type="success">默认</el-tag>
                    <el-button v-else size="small" @click="markDefault(m)">设为默认</el-button>
                    <el-button size="small" :type="m.enabled === 0 ? 'success' : 'warning'" plain @click="toggleMyModel(m)">
                      {{ m.enabled === 0 ? '启用' : '停用' }}
                    </el-button>
                    <button class="tbtn icon-only" title="编辑" @click="editModel(m)">✎</button>
                    <button class="tbtn icon-only danger" title="删除" @click="removeMyModel(m)">🗑</button>
                  </div>
                  <p class="llm-tip">停用后其他用户将无法使用该模型。</p>
                  <button v-if="editingModelId === null" class="add-model-btn" @click="openNewModel">
                    ＋ 添加内置模型
                  </button>
                  <template v-else>
                    <div class="llm-fields" style="margin-top: 14px">
                      <div class="field">
                        <label>名称</label>
                        <el-input v-model="modelForm.name" placeholder="如 DeepSeek 官方" size="default" :disabled="modelSaving" />
                      </div>
                      <div class="field">
                        <label>接口地址</label>
                        <el-input v-model="modelForm.base_url" placeholder="https://api.example.com/v1" size="default" :disabled="modelSaving" />
                      </div>
                      <div class="field">
                        <label>模型名称</label>
                        <el-input v-model="modelForm.model_id" placeholder="deepseek-chat" size="default" :disabled="modelSaving" />
                      </div>
                      <div class="field">
                        <label>API Key{{ editingModelId === 'new' ? '（内网端点可留空）' : '（留空保持不变）' }}</label>
                        <el-input v-model="modelForm.api_key" type="password" show-password placeholder="sk-…" size="default" :disabled="modelSaving" />
                      </div>
                    </div>
                    <div class="llm-actions">
                      <el-button :loading="modelTesting" @click="testMyModel">测试连接</el-button>
                      <el-button type="primary" :loading="modelSaving" @click="savePlatformModel">
                        {{ editingModelId === 'new' ? '添加为内置模型' : '保存' }}
                      </el-button>
                      <el-button :disabled="modelSaving || modelTesting" @click="closeModelForm">取消</el-button>
                    </div>
                    <div v-if="modelTestResult" class="llm-test" :class="modelTestResult.ok ? 'ok' : 'fail'">
                      <template v-if="modelTestResult.ok">
                        ✓ 连接成功（{{ modelTestResult.seconds }}s）：{{ modelTestResult.reply }}
                      </template>
                      <template v-else>
                        ✗ 连接失败（{{ modelTestResult.seconds }}s）：{{ modelTestResult.error }}
                      </template>
                    </div>
                  </template>
                </div>
              </div>
            </template>
          </div>
        </template>
      </div>
    </div>

    <ShareDialog v-model:visible="shareVisible" :page-id="sharePageId" />
  </div>
</template>

<style scoped>
.center-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg);
}

.center-body {
  flex: 1;
  display: flex;
  min-height: 0;
}

.side {
  width: 230px;
  flex-shrink: 0;
  border-right: 1px solid var(--line);
  background: var(--card);
  padding: 24px 16px;
}

.side-user {
  text-align: center;
  padding: 6px 0 18px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 14px;
}

.side-avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  font-size: 26px;
  font-weight: 700;
  margin: 0 auto 10px;
  box-shadow: 0 8px 20px rgba(79, 110, 247, 0.3);
}

.side-name {
  font-size: 16px;
  font-weight: 700;
}

.side-role {
  display: inline-block;
  font-size: 11px;
  color: var(--brand-1);
  background: var(--brand-soft);
  padding: 2px 10px;
  border-radius: 20px;
  margin-top: 5px;
  font-weight: 600;
}

.side-menu {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.side-item {
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 13.5px;
  color: var(--text-2);
  cursor: pointer;
  transition: 0.15s;
}

.side-item:hover {
  background: #f0f3f9;
  color: var(--text-1);
}

.side-item.on {
  background: var(--brand-soft);
  color: var(--brand-1);
  font-weight: 700;
}

.side-item.admin {
  color: var(--warn-deep);
}

.side-item.admin.on {
  background: #fef3e2;
  color: var(--warn-deep);
}

/* 管理端 tab */
.admin-tabs {
  display: flex;
  gap: 8px;
  padding: 0 32px;
  margin-bottom: 14px;
}

.admin-tab {
  padding: 8px 22px;
  border: 1.5px solid var(--line);
  border-radius: 10px;
  font-size: 13px;
  color: var(--text-2);
  cursor: pointer;
  transition: 0.15s;
  background: var(--card);
}

.admin-tab.on {
  border-color: var(--warn);
  color: var(--warn-deep);
  font-weight: 700;
  background: #fef3e2;
}

.admin-body {
  padding: 0 32px 32px;
  min-height: 200px;
}

/* 模型配置面板 */
/* 我的模型 */
.models-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 0 32px 32px;
  max-width: 640px;
}

.model-list-card,
.model-form-card {
  padding: 18px 22px;
}

.model-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 4px;
  border-bottom: 1px solid var(--line);
}

.model-row:last-of-type {
  border-bottom: none;
}

.model-row.off .model-info b {
  color: var(--text-3);
  text-decoration: line-through;
}

.model-info {
  flex: 1;
  min-width: 0;
}

.model-info b {
  font-size: 14px;
  display: block;
}

.model-meta {
  font-size: 12px;
  color: var(--text-3);
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-empty {
  padding: 18px 0;
  text-align: center;
  color: var(--text-3);
  font-size: 13px;
}

/* 添加模型按钮（表单折叠态） */
.add-model-btn {
  width: 100%;
  margin-top: 14px;
  padding: 10px 0;
  border: 1.5px dashed #c9d4e4;
  background: #fbfcfe;
  color: var(--text-2);
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: 0.15s;
}

.add-model-btn:hover {
  border-color: var(--brand-1);
  color: var(--brand-1);
  background: var(--brand-soft);
}

.llm-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 640px;
}

.llm-panel .glass-card {
  padding: 18px 22px;
}

.llm-head {
  font-size: 14px;
  font-weight: 700;
  margin-bottom: 14px;
}

.llm-head .llm-sub {
  font-size: 11.5px;
  font-weight: 400;
  color: var(--text-3);
  margin-left: 8px;
}

.llm-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.llm-item {
  display: flex;
  align-items: center;
  gap: 12px;
}

.llm-item label {
  width: 88px;
  flex-shrink: 0;
  font-size: 12.5px;
  color: var(--text-2);
}

.llm-item .val {
  flex: 1;
  font-size: 13.5px;
  word-break: break-all;
}

.src {
  flex-shrink: 0;
  font-size: 10.5px;
  padding: 1px 8px;
  border-radius: 10px;
  font-weight: 700;
}

.src.db {
  background: #fef3e2;
  color: var(--warn-deep);
}

.src.env {
  background: rgba(79, 110, 247, 0.09);
  color: var(--brand-1);
}

.src.yaml {
  background: #f0f3f9;
  color: var(--text-3);
}

.llm-tip {
  margin: 12px 0 0;
  font-size: 11.5px;
  color: var(--text-3);
}

.llm-fields {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.llm-fields .field label {
  display: block;
  font-size: 12.5px;
  color: var(--text-2);
  margin-bottom: 6px;
}

.llm-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}

.llm-test {
  margin-top: 14px;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 13px;
}

.llm-test.ok {
  background: rgba(16, 185, 129, 0.08);
  border: 1px solid rgba(16, 185, 129, 0.35);
  color: var(--ok-deep);
}

.llm-test.fail {
  background: rgba(239, 68, 68, 0.06);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: var(--err);
}

.side-content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.works-head {
  padding: 26px 32px 6px;
}

.works-head h3 {
  font-size: 19px;
  margin: 0;
}

.works-head p {
  color: var(--text-2);
  font-size: 13px;
  margin: 4px 0 0;
}

.works-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 18px;
  padding: 20px 32px 32px;
  min-height: 120px;
}

.empty-works {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 48px 0;
  color: var(--text-3);
}

.empty-icon {
  width: 56px;
  height: 56px;
  border-radius: 18px;
  background: var(--grad);
  opacity: 0.85;
  color: #fff;
  font-size: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-works h4 {
  margin: 0;
  color: var(--text-2);
}

.work-card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 18px;
  box-shadow: var(--shadow-sm);
  transition: 0.25s;
}

.work-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
  border-color: #c9d4e4;
}

.thumb {
  height: 90px;
  border-radius: 11px;
  margin-bottom: 12px;
  background: linear-gradient(135deg, #edf1f7, #f7f9fc);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 7px;
  padding: 12px 20px;
}

.thumb i {
  width: 12px;
  border-radius: 4px 4px 2px 2px;
  background: #c9d4e4;
}

.thumb i:nth-child(2) {
  background: var(--brand-1);
}

.thumb i:nth-child(5) {
  background: var(--brand-2);
}

.wtop {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 10px;
}

.wtop h4 {
  font-size: 14px;
  line-height: 1.45;
  margin: 0;
  min-height: 40px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.wtag {
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 20px;
  font-weight: 600;
  flex-shrink: 0;
}

.wtag.ok {
  background: rgba(16, 185, 129, 0.1);
  color: var(--ok);
}

.wtag.fail {
  background: rgba(239, 68, 68, 0.09);
  color: var(--err);
}

.wmeta {
  color: var(--text-3);
  font-size: 12px;
  margin: 0 0 14px;
  display: flex;
  gap: 12px;
}

.wacts {
  display: flex;
  gap: 8px;
}

.tbtn {
  flex: 1;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--text-1);
  padding: 7px 14px;
  border-radius: 9px;
  font-size: 12.5px;
  cursor: pointer;
  transition: 0.15s;
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

.tbtn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.tbtn.icon-only {
  flex: 0 0 40px;
  padding: 7px 0;
}

.tbtn.icon-only.danger:hover {
  border-color: var(--err);
  color: var(--err);
  background: rgba(239, 68, 68, 0.06);
}

.info-card {
  margin: 16px 32px;
  padding: 8px 24px;
  max-width: 520px;
}

.info-row {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 18px 0;
  border-bottom: 1px solid var(--line);
  font-size: 14px;
}

.info-row:last-child {
  border-bottom: none;
}

.info-row label {
  width: 70px;
  color: var(--text-2);
  font-size: 13px;
  flex-shrink: 0;
}

.info-avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  font-size: 20px;
  font-weight: 700;
}
</style>
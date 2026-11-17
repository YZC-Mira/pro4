<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { clearToken, type AuthUser } from '../api'

const props = defineProps<{ user: AuthUser | null }>()
const emit = defineEmits<{ (e: 'logout'): void }>()

const route = useRoute()
const router = useRouter()
const dropdown = ref()

const initial = computed(() => props.user?.username?.charAt(0) ?? '?')
const roleLabel = computed(() => (props.user?.role === 'admin' ? '管理员' : '用户'))
const active = computed(() => route.name)

function nav(name: string) {
  router.push({ name })
}

async function onCommand(cmd: string) {
  if (cmd === 'center') {
    router.push({ name: 'center' })
  } else if (cmd === 'logout') {
    clearToken()
    sessionStorage.removeItem('algoagent.conversationId')
    sessionStorage.removeItem('algoagent.previewPageId')
    emit('logout')
    // 整页跳转：清掉 keep-alive 缓存的工作台（含旧用户态与进行中的 SSE）
    window.location.href = '/login'
  }
}
</script>

<template>
  <header class="app-header">
    <div class="brand" @click="nav('work')">
      <div class="logo">◆</div>
      <div class="brand-text">
        <b>AlgoAgent</b>
        <span>算法可视化智能体</span>
      </div>
    </div>
    <nav class="nav">
      <a :class="{ on: active === 'work' }" @click="nav('work')">💬 对话</a>
      <a :class="{ on: active === 'center' }" @click="nav('center')">👤 个人中心</a>
    </nav>
    <div class="spacer"></div>
    <el-dropdown ref="dropdown" trigger="click" @command="onCommand">
      <div class="user-chip">
        <div class="avatar brand-avatar">{{ initial }}</div>
        <div class="user-meta">
          <div class="name">{{ user?.username ?? '…' }}</div>
          <div class="role">{{ roleLabel }}</div>
        </div>
        <span class="chev">▾</span>
      </div>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="center">个人中心</el-dropdown-item>
          <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </header>
</template>

<style scoped>
.app-header {
  height: 58px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  padding: 0 22px;
  gap: 26px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}

.logo {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: var(--grad);
  color: #fff;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  box-shadow: 0 4px 12px rgba(79, 110, 247, 0.3);
}

.brand-text b {
  font-size: 16px;
  letter-spacing: 0.3px;
}

.brand-text span {
  color: var(--text-3);
  font-size: 12px;
  display: block;
  margin-top: -3px;
}

.nav {
  display: flex;
  gap: 4px;
}

.nav a {
  padding: 7px 16px;
  border-radius: 9px;
  color: var(--text-2);
  text-decoration: none;
  font-size: 13.5px;
  cursor: pointer;
}

.nav a.on {
  background: var(--brand-soft);
  color: var(--brand-1);
  font-weight: 600;
}

.nav a:hover {
  background: #f0f3f9;
}

.spacer {
  flex: 1;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 12px 5px 6px;
  border-radius: 30px;
  cursor: pointer;
  border: 1px solid transparent;
  outline: none;
}

.user-chip:hover {
  background: #f0f3f9;
  border-color: var(--line);
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  font-size: 13px;
}

.user-meta .name {
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.15;
  color: var(--text-1);
}

.user-meta .role {
  font-size: 11px;
  color: var(--brand-1);
  line-height: 1.1;
}

.chev {
  color: var(--text-3);
  font-size: 11px;
}
</style>
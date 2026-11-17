<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { login, register, type AuthUser } from '../api'

const router = useRouter()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const loading = ref(false)

async function submit() {
  if (loading.value) return
  const name = username.value.trim()
  const pwd = password.value
  if (!name || !pwd) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    let user: AuthUser
    if (mode.value === 'login') {
      user = await login(name, pwd)
    } else {
      if (pwd.length < 6) {
        ElMessage.warning('密码至少 6 位')
        loading.value = false
        return
      }
      user = await register(name, pwd)
    }
    ElMessage.success(`欢迎，${user.username}`)
    router.push({ name: 'work' })
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="grid-deco"></div>
    <div class="login-card">
      <div class="login-logo">◆</div>
      <h3>AlgoAgent</h3>
      <div class="sub">算法可视化智能体 · 描述需求，生成交互式算法动画</div>

      <div class="tab-row">
        <div class="tab" :class="{ on: mode === 'login' }" @click="mode = 'login'">登 录</div>
        <div class="tab" :class="{ on: mode === 'register' }" @click="mode = 'register'">注 册</div>
      </div>

      <div class="field">
        <label>用户名</label>
        <el-input v-model="username" placeholder="请输入用户名" :disabled="loading" autocomplete="username" />
      </div>
      <div class="field">
        <label>密码</label>
        <el-input
          v-model="password"
          type="password"
          placeholder="密码（注册需至少 6 位）"
          show-password
          :disabled="loading"
          autocomplete="current-password"
          @keydown.enter="submit"
        />
      </div>

      <button class="btn-grad" :disabled="loading" @click="submit">
        {{ loading ? '请稍候…' : mode === 'login' ? '登 录' : '注册并进入' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  background:
    radial-gradient(800px 400px at 15% -10%, rgba(79, 110, 247, 0.14), transparent 60%),
    radial-gradient(700px 380px at 95% 110%, rgba(34, 193, 220, 0.14), transparent 60%),
    var(--bg);
  overflow: hidden;
}

.grid-deco {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(var(--line) 1px, transparent 1px),
    linear-gradient(90deg, var(--line) 1px, transparent 1px);
  background-size: 44px 44px;
  opacity: 0.35;
  mask-image: radial-gradient(600px 400px at 50% 50%, #000, transparent 75%);
}

.login-card {
  width: 400px;
  background: var(--card);
  border-radius: 20px;
  box-shadow: var(--shadow-lg);
  padding: 36px 40px 32px;
  position: relative;
  border: 1px solid rgba(232, 236, 243, 0.9);
}

.login-logo {
  width: 52px;
  height: 52px;
  border-radius: 15px;
  background: var(--grad);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 800;
  font-size: 24px;
  box-shadow: 0 8px 20px rgba(79, 110, 247, 0.35);
  margin-bottom: 16px;
}

.login-card h3 {
  font-size: 22px;
  letter-spacing: 0.5px;
  margin: 0;
}

.sub {
  color: var(--text-2);
  font-size: 13px;
  margin: 6px 0 22px;
}

.tab-row {
  display: flex;
  background: #f0f3f9;
  border-radius: 11px;
  padding: 4px;
  margin-bottom: 20px;
}

.tab {
  flex: 1;
  text-align: center;
  padding: 8px 0;
  font-size: 13px;
  border-radius: 8px;
  color: var(--text-2);
  cursor: pointer;
  transition: 0.15s;
}

.tab.on {
  background: var(--card);
  color: var(--brand-1);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}

.field {
  margin-bottom: 14px;
}

.field label {
  display: block;
  font-size: 12px;
  color: var(--text-2);
  margin-bottom: 6px;
  font-weight: 600;
}

.btn-grad {
  width: 100%;
  padding: 12px;
  border: none;
  border-radius: 11px;
  background: var(--grad);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 8px 20px rgba(79, 110, 247, 0.35);
  margin-top: 6px;
  transition: 0.15s;
}

.btn-grad:hover {
  opacity: 0.92;
}

.btn-grad:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { createShare, type ShareInfo } from '../api'

const props = defineProps<{ visible: boolean; pageId: string }>()
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>()

const validity = ref<'1d' | '7d' | 'forever'>('7d')
const creating = ref(false)
const shareInfo = ref<ShareInfo | null>(null)

watch(
  () => props.visible,
  (v) => {
    if (v) shareInfo.value = null
  },
)

function close() {
  emit('update:visible', false)
}

async function doCreate() {
  if (!props.pageId || creating.value) return
  creating.value = true
  try {
    shareInfo.value = await createShare(props.pageId, validity.value)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    creating.value = false
  }
}

async function copyUrl() {
  if (!shareInfo.value) return
  try {
    await navigator.clipboard.writeText(shareInfo.value.url)
    ElMessage.success('链接已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择复制')
  }
}

const validityLabel: Record<string, string> = { '1d': '1 天', '7d': '7 天', forever: '永久' }
</script>

<template>
  <el-dialog :model-value="visible" title="🔗 分享" width="440px" @update:model-value="close">
    <template v-if="!shareInfo">
      <p class="share-tip">创建免登录分享链接，打开即用（无需注册）</p>
      <div class="validity-row">
        <div class="vopt" :class="{ on: validity === '1d' }" @click="validity = '1d'">1 天</div>
        <div class="vopt" :class="{ on: validity === '7d' }" @click="validity = '7d'">7 天</div>
        <div class="vopt" :class="{ on: validity === 'forever' }" @click="validity = 'forever'">永久</div>
      </div>
      <div class="share-btn-row">
        <button class="btn-grad" :disabled="creating" @click="doCreate">
          {{ creating ? '创建中…' : '创建分享' }}
        </button>
      </div>
    </template>
    <template v-else>
      <div class="share-result">
        <div class="qr-box">
          <img :src="shareInfo.qrcode_url" alt="分享二维码" class="qr" />
        </div>
        <div class="share-meta">
          <p>有效期：{{ validityLabel[shareInfo.validity] }}</p>
          <p v-if="shareInfo.expires_at">到期时间：{{ shareInfo.expires_at.replace('T', ' ') }}</p>
        </div>
        <div class="url-row">
          <div class="u">{{ shareInfo.url }}</div>
          <button class="cp" @click="copyUrl">复制</button>
        </div>
        <p class="share-note">ⓘ 作品更新后，此链接将自动展示最新内容</p>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.share-tip {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--text-2);
}

.validity-row {
  display: flex;
  gap: 8px;
  margin-bottom: 18px;
}

.vopt {
  flex: 1;
  text-align: center;
  padding: 10px 0;
  border: 1.5px solid var(--line);
  border-radius: 11px;
  font-size: 13px;
  color: var(--text-2);
  cursor: pointer;
  transition: 0.15s;
}

.vopt.on {
  border-color: var(--brand-1);
  color: var(--brand-1);
  font-weight: 700;
  background: var(--brand-soft);
}

.share-btn-row {
  margin-top: 16px;
  text-align: center;
}

.btn-grad {
  padding: 11px 32px;
  border: none;
  border-radius: 11px;
  background: var(--grad);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 8px 20px rgba(79, 110, 247, 0.35);
}

.btn-grad:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.share-result {
  display: flex;
  flex-direction: column;
  gap: 13px;
  align-items: center;
}

.qr-box {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 10px;
}

.qr {
  width: 176px;
  height: 176px;
  display: block;
}

.share-meta {
  width: 100%;
  font-size: 13px;
  color: var(--text-2);
  text-align: center;
}

.share-meta p {
  margin: 2px 0;
}

.url-row {
  width: 100%;
  display: flex;
  border: 1.5px solid var(--line);
  border-radius: 11px;
  overflow: hidden;
}

.url-row .u {
  flex: 1;
  padding: 10px 14px;
  font-size: 12.5px;
  color: var(--text-2);
  background: #fbfcfe;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.url-row .cp {
  border: none;
  background: var(--grad);
  color: #fff;
  padding: 0 20px;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
}

.share-note {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-3);
  text-align: center;
}
</style>
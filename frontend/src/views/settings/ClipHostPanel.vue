<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ClipHostStatus } from '../../api/contracts'
import { ApiError } from '../../api/http'
import * as api from '../../api/clipHost'
const props = defineProps<{ active: boolean }>()
const { t, te } = useI18n()
const status = ref<ClipHostStatus | null>(null), enabled = ref(false), busy = ref(false), error = ref(''), notice = ref('')
let alive = true, generation = 0, controller: AbortController | undefined
function current(token: number, request: AbortController) { return alive && props.active && token === generation && !request.signal.aborted }
function errorText(value: unknown) { const key = value instanceof ApiError ? `clipHost.errors.${value.message}` : ''; return t(key && te(key) ? key : 'clipHost.errors.unknown') }
async function perform(operation: (signal: AbortSignal) => Promise<ClipHostStatus>, message = '') {
  if (!alive || !props.active || busy.value) return
  const token = ++generation, request = new AbortController(); controller = request; busy.value = true; error.value = ''; notice.value = ''
  try {
    const value = await operation(request.signal)
    if (current(token, request)) { status.value = value; enabled.value = value.enabled ?? false; if (message) notice.value = t(message) }
  } catch (value) { if (current(token, request)) error.value = errorText(value) }
  finally { if (current(token, request)) { busy.value = false; controller = undefined } }
}
function save() { const value = enabled.value; void perform(signal => api.saveClipHostSettings({ enabled: value }, signal), 'clipHost.saved') }
function check() { void perform(api.checkClipHostConnection, 'clipHost.checked') }
function cleanup() { void perform(api.cleanupClipHostAssets, 'clipHost.cleaned') }
watch(() => props.active, value => {
  generation++; controller?.abort(); controller = undefined; busy.value = false; error.value = ''; notice.value = ''
  if (value) void perform(api.getClipHostStatus)
}, { immediate: true })
onBeforeUnmount(() => { alive = false; generation++; controller?.abort() })
</script>
<template>
  <section data-clip-host class="space-y-4 border-t border-border pt-5" aria-labelledby="clip-host-heading" :aria-busy="busy">
    <header><h3 id="clip-host-heading" class="text-lg font-semibold text-text">{{ t('clipHost.title') }}</h3><p class="mt-2 max-w-3xl text-sm leading-relaxed text-text-dim">{{ t('clipHost.intro') }}</p></header>
    <div class="clip-host-card">
      <p class="text-sm text-text-dim">{{ t('clipHost.setupHint') }}</p>
      <p v-if="status && !status.binary_available" role="status" class="text-sm text-status-queued">{{ t('clipHost.binaryMissing') }} <code class="rounded bg-panel-2 px-2 py-1">brew install cloudflared</code></p>
      <a data-clip-host-docs href="https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/" target="_blank" rel="noopener noreferrer" class="inline-flex min-h-11 items-center text-sm text-text underline">{{ t('clipHost.docs') }}</a>
      <form class="space-y-3" @submit.prevent="save">
        <label class="flex min-h-11 items-center gap-3 text-sm text-text"><input v-model="enabled" data-clip-host-enabled type="checkbox" :disabled="busy || !status">{{ t('clipHost.enabled') }}</label>
        <button data-clip-host-save-settings type="submit" class="clip-host-button" :disabled="busy || !status">{{ t('clipHost.save') }}</button>
      </form>
      <div class="flex flex-wrap gap-2"><button data-clip-host-check type="button" class="clip-host-button" :disabled="busy || !status" @click="check">{{ t('clipHost.check') }}</button><button data-clip-host-cleanup type="button" class="clip-host-button" :disabled="busy || !status" @click="cleanup">{{ t('clipHost.cleanup') }}</button></div>
      <div v-if="status" role="status" class="space-y-2 text-sm text-text-dim"><p>{{ t(status.configured ? 'clipHost.ready' : 'clipHost.notReady') }}</p><p>{{ t(status.tunnel_active ? 'clipHost.tunnelActive' : 'clipHost.noTunnel') }}</p><p>{{ t('clipHost.activeLinks', { count: status.active_clips ?? 0 }) }}</p></div>
      <p class="text-sm leading-relaxed text-text-dim">{{ t('clipHost.access') }}</p><p class="text-sm leading-relaxed text-text-dim">{{ t('clipHost.retention') }}</p><p class="text-sm leading-relaxed text-text-dim">{{ t('clipHost.keepOnline') }}</p><p class="text-xs leading-relaxed text-text-dim">{{ t('clipHost.disableHint') }}</p>
    </div>
    <p v-if="busy" role="status" class="text-sm text-text-dim">{{ t('clipHost.working') }}</p><p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p><p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>
  </section>
</template>
<style scoped>
@reference "../../style.css";
.clip-host-card { @apply flex flex-col gap-4 rounded-xl border border-border bg-panel p-5; }
.clip-host-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

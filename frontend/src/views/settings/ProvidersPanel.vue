<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useProvidersStore } from '../../stores/providers'
import * as api from '../../api/openrouter'
import type { OpenRouterCatalog, OpenRouterConnection, OpenRouterReceipt, OpenRouterStatus } from '../../api/generated'
const props = defineProps<{ active: boolean }>()
const { t } = useI18n()
const providers=useProvidersStore()
const status = ref<OpenRouterStatus | null>(null)
const enabled = ref(false)
const limit = ref(1)
const secret = ref('')
const persist = ref(false)
const connection = ref<OpenRouterConnection | null>(null)
const catalog = ref<OpenRouterCatalog | null>(null)
const receipts = ref<OpenRouterReceipt[]>([])
const historyIncomplete=ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
let alive = true
let generation = 0
let controller: AbortController | undefined
const canConnect = computed(() => !!status.value?.credential_configured && !!status.value?.enabled)
function accept(value: OpenRouterStatus) { providers.accept(value); status.value = value; enabled.value = value.enabled ?? false; limit.value = value.estimate_limit_usd ?? 1; persist.value = value.secure_storage_available ?? false }
async function perform(action: (signal: AbortSignal) => Promise<void>) {
  if (!props.active || !alive || busy.value) return
  const token = ++generation
  const request = new AbortController(); controller = request
  busy.value = true; error.value = ''; notice.value = ''
  try { await action(request.signal) }
  catch { if (alive && props.active && token === generation) error.value = t('cloudProviders.failed') }
  finally { if (alive && token === generation) { busy.value = false; controller = undefined } }
}
function current(signal: AbortSignal) { return alive && props.active && !signal.aborted }
function load() { void perform(async signal => { const value = await api.getProviderStatus(signal); if (current(signal)) accept(value) }) }
function saveSettings() { if (!Number.isFinite(limit.value) || limit.value <= 0 || limit.value > 1000) return; void perform(async signal => { const value = await api.saveProviderSettings({ enabled: enabled.value, estimate_limit_usd: limit.value }, signal); if (current(signal)) { accept(value); connection.value = null; notice.value = t('cloudProviders.saved') } }) }
function saveKey() { if (secret.value.length < 16) return; const key = secret.value; const requestedPersistence=persist.value && !!status.value?.secure_storage_available; secret.value = ''; void perform(async signal => { const value = await api.saveProviderKey({ api_key: key, persist: requestedPersistence }, signal); if (current(signal)) { accept(value); connection.value = null; notice.value = t(requestedPersistence && value.credential_source === 'session' ? 'cloudProviders.sessionFallback' : 'cloudProviders.keySaved') } }) }
function removeKey() { void perform(async signal => { const value = await api.removeProviderKey(signal); if (current(signal)) { accept(value); secret.value = ''; connection.value = null } }) }
function check() { void perform(async signal => { const value = await api.checkProviderConnection(signal); if (current(signal)) connection.value = value }) }
function refresh() { void perform(async signal => { const value = await api.refreshProviderCatalog(signal); if (current(signal)) catalog.value = value }) }
function history() { void perform(async signal => { const value = await api.getProviderReceipts(signal); if (current(signal)) { receipts.value = value.requests.slice(0,25); historyIncomplete.value=value.history_incomplete ?? false } }) }
watch(() => props.active, value => {
  generation++; controller?.abort(); controller = undefined; busy.value = false; secret.value = ''; error.value = ''; notice.value = ''
  if (value) load()
}, { immediate: true })
onBeforeUnmount(() => { alive = false; generation++; controller?.abort(); secret.value = '' })
</script>
<template>
  <section class="space-y-5" :aria-busy="busy">
    <div><h2 class="text-xl font-semibold text-text">{{ t('cloudProviders.title') }}</h2><p class="mt-2 max-w-3xl text-sm text-text-dim">{{ t('cloudProviders.intro') }}</p></div>
    <p class="rounded-xl border border-border bg-panel p-4 text-sm text-text-dim">{{ t('cloudProviders.privacy') }}</p>
    <div class="grid gap-5 lg:grid-cols-2">
      <form class="provider-card" @submit.prevent="saveKey">
        <div class="flex items-center justify-between gap-3"><h3 class="font-semibold text-text">OpenRouter</h3><span class="rounded-full bg-panel-2 px-3 py-1 text-xs text-text-dim">{{ t(status?.credential_configured ? 'cloudProviders.configured' : 'cloudProviders.missing') }}</span></div>
        <p v-if="status" class="text-xs text-text-dim">{{ t('cloudProviders.source', { source: t(`cloudProviders.sources.${status.credential_source ?? 'none'}`) }) }}</p>
        <label class="provider-label">{{ t('cloudProviders.key') }}<input v-model="secret" type="password" class="provider-input" autocomplete="off" spellcheck="false" :disabled="busy" maxlength="512" /></label>
        <label v-if="status?.secure_storage_available" class="flex items-center gap-2 text-sm text-text-dim"><input v-model="persist" type="checkbox" :disabled="busy" />{{ t('cloudProviders.persist') }}</label>
        <p v-else class="text-xs leading-relaxed text-text-dim">{{ t('cloudProviders.sessionOnly') }}</p>
        <p v-if="status?.credential_source === 'environment'" class="text-xs text-text-dim">{{ t('cloudProviders.environment') }}</p>
        <div class="flex flex-wrap gap-2"><button class="provider-button bg-accent1 text-white" :disabled="busy || secret.length < 16" type="submit">{{ t('cloudProviders.saveKey') }}</button><button class="provider-button" :disabled="busy || !status?.credential_configured || status.credential_source === 'environment'" type="button" @click="removeKey">{{ t('cloudProviders.removeKey') }}</button></div>
      </form>
      <form class="provider-card" @submit.prevent="saveSettings">
        <label class="flex items-center gap-3 font-medium text-text"><input v-model="enabled" type="checkbox" :disabled="busy || !status" />{{ t('cloudProviders.enabled') }}</label>
        <label class="provider-label">{{ t('cloudProviders.estimate') }}<input v-model.number="limit" type="number" min="0.001" max="1000" step="0.001" required class="provider-input" :disabled="busy || !status" /></label>
        <p class="text-xs leading-relaxed text-text-dim">{{ t('cloudProviders.budgetHelp') }}</p>
        <button class="provider-button self-start" :disabled="busy || !status" type="submit">{{ t('cloudProviders.save') }}</button>
      </form>
    </div>
    <div class="flex flex-wrap gap-2"><button class="provider-button" :disabled="busy || !canConnect" @click="check">{{ t('cloudProviders.check') }}</button><button class="provider-button" :disabled="busy || !canConnect" @click="refresh">{{ t('cloudProviders.refresh') }}</button><button class="provider-button" :disabled="busy" @click="history">{{ t('cloudProviders.checkHistory') }}</button></div>
    <p v-if="busy" role="status" class="text-sm text-text-dim">{{ t('cloudProviders.busy') }}</p><p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p><p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>
    <div v-if="connection" class="provider-card" role="status"><p class="text-status-done">{{ connection.connected ? t('cloudProviders.connection', { usage: (connection.usage_usd ?? 0).toFixed(3) }) : t('cloudProviders.connectionUnverified') }}</p><p class="text-sm text-text-dim">{{ connection.limit_remaining_usd === null ? t('cloudProviders.noLimit') : t('cloudProviders.remaining', { remaining: connection.limit_remaining_usd?.toFixed(3) }) }}</p></div>
    <div v-if="catalog" class="provider-card"><h3 class="font-semibold text-text">{{ t('cloudProviders.catalog') }}</h3><p class="text-sm text-text-dim">{{ t('cloudProviders.catalogHelp') }}</p><ul class="divide-y divide-border"><li v-for="model in catalog.models" :key="`${model.kind}:${model.id}`" class="flex flex-wrap items-center justify-between gap-2 py-3"><span class="text-sm text-text">{{ model.name }}<span class="ml-2 rounded bg-panel-2 px-2 py-1 text-xs text-text-dim">{{ model.kind }}</span></span><span class="text-xs text-text-dim">{{ model.prices.map(p => `$${p.rate_usd}/${t(`cloudProviders.units.${p.unit}`)}`).join(' · ') }}</span></li></ul></div>
    <p v-if="historyIncomplete" role="alert" class="text-sm text-status-queued">{{ t('cloudProviders.historyIncomplete') }}</p>
    <div v-if="receipts.length" class="provider-card"><h3 class="font-semibold text-text">{{ t('cloudProviders.history') }}</h3><ul class="space-y-3"><li v-for="receipt in receipts" :key="receipt.id" class="rounded-lg border border-border p-3 text-sm text-text-dim"><p class="break-words text-text">{{ receipt.model_id }}</p><p>{{ receipt.state }} · {{ t('cloudProviders.cost') }} ${{ receipt.estimated_usd.toFixed(3) }}<span v-if="receipt.actual_cost_usd != null"> · {{ t('cloudProviders.actual') }} ${{ receipt.actual_cost_usd.toFixed(3) }}</span></p><p v-if="receipt.state === 'submission_unknown'" role="alert" class="mt-2 text-status-queued">{{ t('cloudProviders.unknown') }}</p></li></ul></div>
  </section>
</template>
<style scoped>
@reference "../../style.css";
.provider-card { @apply flex flex-col gap-4 rounded-xl border border-border bg-panel p-5; }
.provider-label { @apply flex flex-col gap-2 text-sm text-text-dim; }
.provider-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
.provider-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

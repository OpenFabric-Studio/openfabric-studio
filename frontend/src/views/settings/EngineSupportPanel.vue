<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { browserSupportReport, downloadSupportReport, fallbackConnection, getEngineRuntime, getSupportReport, reviewedReportJson, stopOwnedEngine, supportErrorCode } from '../../api/support'
import type { EngineRuntime, SupportAction, SupportReport } from '../../api/generated'

const { t } = useI18n()
const engines = ref<EngineRuntime[]>([])
const loading = ref(false)
const stopping = ref<EngineRuntime['id'] | null>(null)
const preparing = ref(false)
const statusError = ref('')
const actionError = ref('')
const notice = ref('')
const report = ref<SupportReport | null>(null)
const previewJson = computed(() => report.value ? reviewedReportJson(report.value) : '')
let active = true
let statusOperation = 0
let reportOperation = 0
let statusController: AbortController | null = null
let reportController: AbortController | null = null
let stopController: AbortController | null = null
let actions: SupportAction[] = []

function record(id: SupportAction['id'], outcome: SupportAction['outcome'], error_code: SupportAction['error_code'] = null) {
  actions = [...actions, { id, outcome, error_code }].slice(-20)
}
function engineName(id: EngineRuntime['id']): string { return id === 'ace_step' ? 'ACE-Step' : 'YuE' }

async function refresh() {
  if (loading.value || stopping.value) return
  const operation = ++statusOperation
  const controller = new AbortController(); statusController = controller
  loading.value = true; statusError.value = ''
  try {
    const response = await getEngineRuntime(controller.signal)
    if (!active || operation !== statusOperation) return
    engines.value = response.engines
    record('refresh_engines', 'completed')
  } catch (error) {
    if (!active || operation !== statusOperation) return
    statusError.value = t('supportWorkspace.statusFailed')
    engines.value = []
    record('refresh_engines', 'failed', supportErrorCode(error))
  } finally {
    if (active && operation === statusOperation) { loading.value = false; statusController = null }
  }
}

async function stop(engine: EngineRuntime) {
  if (!engine.can_stop || !engine.owned || !engine.instance_id || engine.idle_state !== 'idle' || engine.state !== 'running' || stopping.value || loading.value || preparing.value) return
  const controller = new AbortController(); stopController = controller
  stopping.value = engine.id; notice.value = ''; actionError.value = ''
  report.value = null
  let failed = false
  try {
    const response = await stopOwnedEngine({ engine_id: engine.id, instance_id: engine.instance_id }, controller.signal)
    if (!active) return
    engines.value = response.engines
    record('stop_engine', 'completed')
    notice.value = t('supportWorkspace.stopped', { engine: engineName(engine.id) })
  } catch (error) {
    if (!active) return
    failed = true
    const code = supportErrorCode(error)
    record('stop_engine', 'failed', code)
    actionError.value = t(`supportWorkspace.errors.${code}`)
  } finally {
    if (active) { stopping.value = null; stopController = null }
  }
  if (active && failed) await refresh()
}

async function preview() {
  if (preparing.value || stopping.value) return
  const operation = ++reportOperation
  const controller = new AbortController(); reportController = controller
  preparing.value = true; report.value = null; notice.value = ''
  try {
    const response = await getSupportReport(controller.signal)
    if (!active || operation !== reportOperation) return
    record('preview_report', 'completed')
    report.value = { ...response, recent_actions: [...(response.recent_actions ?? []), ...actions].slice(-20) }
  } catch (error) {
    if (!active || operation !== reportOperation) return
    record('preview_report', 'failed', supportErrorCode(error))
    report.value = browserSupportReport(actions, fallbackConnection(error))
  } finally {
    if (active && operation === reportOperation) { preparing.value = false; reportController = null }
  }
}

function download() {
  if (!report.value || !previewJson.value || preparing.value) return
  try {
    downloadSupportReport(previewJson.value)
    record('download_report', 'completed'); notice.value = t('supportWorkspace.downloaded')
  } catch {
    record('download_report', 'failed', 'support_unavailable'); notice.value = ''; actionError.value = t('supportWorkspace.downloadFailed')
  }
}
onMounted(() => { void refresh() })
onBeforeUnmount(() => { active = false; statusOperation++; reportOperation++; statusController?.abort(); reportController?.abort(); stopController?.abort() })
</script>

<template>
  <div class="space-y-6">
    <section class="support-panel" :aria-busy="loading || stopping !== null">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div><h2 class="text-lg font-semibold text-text">{{ t('supportWorkspace.runtimeTitle') }}</h2><p class="mt-2 max-w-3xl text-sm text-text-dim">{{ t('supportWorkspace.runtimeIntro') }}</p></div>
        <button type="button" class="support-button" :disabled="loading || stopping !== null" @click="refresh">{{ t('supportWorkspace.refresh') }}</button>
      </div>
      <p class="text-sm text-text-dim">{{ t('supportWorkspace.persistentOnly') }}</p>
      <p v-if="loading" role="status" class="text-sm text-text-dim">{{ t('supportWorkspace.loading') }}</p>
      <p v-if="statusError" role="alert" class="text-sm text-status-failed">{{ statusError }}</p>
      <div class="grid gap-3 sm:grid-cols-2">
        <div v-for="engine in engines" :key="engine.id" class="space-y-3 rounded-lg border border-border bg-panel-2/40 p-4">
          <div class="flex items-center justify-between gap-3"><h3 class="font-medium text-text">{{ engineName(engine.id) }}</h3><span class="text-xs text-text-dim">{{ t(`supportWorkspace.states.${engine.state}`) }}</span></div>
          <p class="text-xs text-text-dim">{{ t(engine.idle_state === 'busy' ? 'supportWorkspace.busy' : engine.idle_state === 'unknown' ? 'supportWorkspace.unknownIdle' : engine.owned ? 'supportWorkspace.idle' : 'supportWorkspace.notOwned') }}</p>
          <button type="button" class="support-button w-full" :disabled="!engine.can_stop || loading || stopping !== null || preparing" @click="stop(engine)">{{ t(stopping === engine.id ? 'supportWorkspace.stopping' : 'supportWorkspace.stop', { engine: engineName(engine.id) }) }}</button>
        </div>
      </div>
      <p v-if="actionError" role="alert" class="text-sm text-status-failed">{{ actionError }}</p>
      <p v-if="notice" role="status" class="text-sm text-text-dim">{{ notice }}</p>
    </section>
    <section class="support-panel" :aria-busy="preparing">
      <h2 class="text-lg font-semibold text-text">{{ t('supportWorkspace.reportTitle') }}</h2>
      <p class="text-sm text-text-dim">{{ t('supportWorkspace.reportIntro') }}</p>
      <p class="rounded-lg border border-border bg-panel-2 p-3 text-sm text-text-dim">{{ t('supportWorkspace.privacy') }}</p>
      <button type="button" class="support-button" :disabled="preparing || stopping !== null" @click="preview">{{ t(preparing ? 'supportWorkspace.preparing' : 'supportWorkspace.preview') }}</button>
      <div v-if="report" class="space-y-3">
        <p class="text-sm text-text-dim">{{ t('supportWorkspace.reportSummary', { platform: report.platform, architecture: report.architecture, version: report.app_version ?? t('supportWorkspace.unknown') }) }}</p>
        <p v-if="report.source === 'browser_fallback'" role="status" class="text-sm text-status-queued">{{ t('supportWorkspace.fallback') }}</p>
        <pre tabindex="0" :aria-label="t('supportWorkspace.previewLabel')" class="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-panel-2 p-4 text-xs text-text-dim [overflow-wrap:anywhere]">{{ previewJson }}</pre>
        <div class="flex flex-wrap gap-3"><button type="button" class="support-button" @click="download">{{ t('supportWorkspace.download') }}</button><button type="button" class="support-button" @click="report = null">{{ t('supportWorkspace.hide') }}</button></div>
      </div>
    </section>
  </div>
</template>

<style scoped>
@reference "../../style.css";
.support-panel { @apply space-y-4 rounded-xl border border-border bg-panel p-5; }
.support-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

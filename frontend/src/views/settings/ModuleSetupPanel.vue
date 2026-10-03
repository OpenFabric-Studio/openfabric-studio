<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { routeLocationKey } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useModulesStore, type ModuleId } from '../../stores/modules'
import * as api from '../../api/modules'
import { ApiError } from '../../api/http'
import type { ModuleInfo, ModulePlan, ModuleInstallJob } from '../../api/generated'

const { t } = useI18n(), store = useModulesStore()
const wizard = ref(false), step = ref(0), selected = ref<ModuleId[]>([]), downloadModels = ref(false)
const plan = ref<ModulePlan | null>(null), working = ref(false), error = ref('')
const wizardHeading = ref<HTMLElement | null>(null), setupButton = ref<HTMLButtonElement | null>(null)
let alive = true, generation = 0, controller: AbortController | null = null
const steps = ['computer', 'features', 'review', 'install']
const groups: { label: string; ids: ModuleId[] }[] = [
  { label: 'music', ids: ['ace_step', 'yue2'] },
  { label: 'voices', ids: ['speech', 'singing', 'separation'] },
  { label: 'videoTools', ids: ['video', 'media', 'transcription', 'source_import', 'ebooks'] },
]
const modules = computed(() => store.inventory?.modules ?? [])
const route = inject(routeLocationKey, undefined)
let focusedHash = ''
watch(() => [route?.hash, store.inventory], async () => {
  const hash = route?.hash ?? ''
  if (!hash.startsWith('#module-') || hash === focusedHash) return
  await nextTick()
  if (!alive) return
  const article = document.getElementById(hash.slice(1))
  if (!article) return
  focusedHash = hash; const details = article.querySelector('details'); if (details) details.open = true
  article.scrollIntoView({ block: 'start', behavior: 'smooth' }); article.focus({ preventScroll: true })
}, { immediate: true })
function platformName(value: string): string { return ['darwin', 'win32', 'linux'].includes(value) ? t(`moduleWorkspace.platforms.${value}`) : value }
function groupModules(ids: ModuleId[]) { return modules.value.filter(module => ids.includes(module.id)) }
function bytes(value: number | null | undefined): string {
  if (value == null) return t('moduleWorkspace.unknownSize')
  return value >= 1000000000 ? `${(value / 1000000000).toFixed(1)} GB` : `${(value / 1000000).toFixed(1)} MB`
}
function safeUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined
  try { const url = new URL(value); return url.protocol === 'https:' ? url.href : undefined } catch { return undefined }
}
function statusClass(module: ModuleInfo) {
  const state = store.stateOf(module.id)
  return state === 'ready' ? 'text-status-done' : state === 'partial' ? 'text-status-queued' : 'text-text-dim'
}
function begin(ids: ModuleId[] = []) { selected.value = [...ids]; plan.value = null; wizard.value = true; step.value = 0; error.value = ''; void focusHeading() }
async function focusHeading() { await nextTick(); if (alive) wizardHeading.value?.focus() }
function changeStep(value: number) { if (working.value) return; step.value = value; error.value = ''; void focusHeading() }
function close() { if (working.value) return; generation++; controller?.abort(); wizard.value = false; error.value = ''; void nextTick(() => setupButton.value?.focus()) }
async function review() {
  if (working.value || !selected.value.length) return
  const token = ++generation; controller?.abort(); const request = new AbortController(); controller = request
  working.value = true; error.value = ''
  try {
    const response = await api.planModules({ features: [...selected.value], download_models: downloadModels.value }, request.signal)
    if (!alive || token !== generation) return
    plan.value = response; step.value = 2; void focusHeading()
  } catch { if (alive && token === generation) error.value = t('moduleWorkspace.planFailed') }
  finally { if (alive && token === generation) { working.value = false; controller = null } }
}
function errorText(err: unknown, fallback: string): string {
  const messages: Record<string, string> = { setup_busy: 'busy', plan_changed: 'changed', insufficient_disk: 'disk', worker_unverified: 'ownership', setup_not_resumable: 'notResumable' }
  const key = err instanceof ApiError ? messages[err.message] : undefined
  return t(key ? `moduleWorkspace.errors.${key}` : fallback)
}
function retainJob(job: ModuleInstallJob) { store.acceptJob(job) }
async function install() {
  if (working.value || !plan.value?.can_install) return
  const reviewed = plan.value, token = ++generation
  controller?.abort(); const request = new AbortController(); controller = request; working.value = true; error.value = ''
  try {
    const job = await api.installModules({ features: reviewed.features, download_models: reviewed.download_models, plan_token: reviewed.plan_token }, request.signal)
    if (!alive || token !== generation) return
    retainJob(job); step.value = 3; void focusHeading()
  } catch (err) { if (alive && token === generation) error.value = errorText(err, 'moduleWorkspace.installFailed') }
  finally { if (alive && token === generation) { working.value = false; controller = null } }
}
async function control(job: ModuleInstallJob, action: 'cancel' | 'resume') {
  if (working.value) return
  const token = ++generation; controller?.abort(); const request = new AbortController(); controller = request; working.value = true; error.value = ''
  try { const response = await api.controlModuleJob(job.id, action, request.signal); if (alive && token === generation) retainJob(response) }
  catch (err) { if (alive && token === generation) error.value = errorText(err, 'moduleWorkspace.controlFailed') }
  finally { if (alive && token === generation) { working.value = false; controller = null } }
}
onMounted(() => { void store.refresh() })
onBeforeUnmount(() => { alive = false; generation++; controller?.abort() })
</script>

<template>
  <section aria-labelledby="modules-title" class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div><h2 id="modules-title" class="text-xl font-semibold text-text">{{ t('moduleWorkspace.title') }}</h2><p class="mt-2 max-w-2xl text-sm text-text-dim">{{ t('moduleWorkspace.intro') }}</p></div>
      <div class="flex gap-2"><button type="button" class="setup-button" :disabled="store.loading" @click="store.refresh()">{{ t('moduleWorkspace.refresh') }}</button><button ref="setupButton" type="button" class="setup-button bg-accent1 text-white" :disabled="!store.inventory || store.offline" @click="begin()">{{ t('moduleWorkspace.setup') }}</button></div>
    </div>
    <p v-if="store.loading" role="status" class="text-sm text-text-dim">{{ t('moduleWorkspace.loading') }}</p>
    <p v-if="store.offline" role="alert" class="rounded-lg border border-border bg-panel p-3 text-sm text-status-queued">{{ t('moduleWorkspace.offline') }}</p>
    <dl v-if="store.inventory" class="grid gap-4 rounded-xl border border-border bg-panel p-4 text-sm sm:grid-cols-3">
      <div><dt class="text-xs text-text-dim">{{ t('moduleWorkspace.computer') }}</dt><dd class="mt-1 text-text">{{ platformName(store.inventory.platform) }} · {{ store.inventory.architecture }}</dd></div>
      <div><dt class="text-xs text-text-dim">{{ t('moduleWorkspace.acceleration') }}</dt><dd class="mt-1 text-text">{{ store.inventory.acceleration.replaceAll('_', ' ') }}</dd></div>
      <div><dt class="text-xs text-text-dim">{{ t('moduleWorkspace.free') }}</dt><dd class="mt-1 text-text">{{ bytes(store.inventory.free_bytes) }}</dd></div>
      <div class="sm:col-span-3"><dt class="text-xs text-text-dim">{{ t('moduleWorkspace.root') }}</dt><dd class="mt-1 break-all font-mono text-xs text-text">{{ store.inventory.managed_root }}</dd></div>
    </dl>

    <div v-if="wizard" class="space-y-5 rounded-xl border border-accent1/50 bg-panel p-5" :aria-busy="working">
      <div class="flex items-start justify-between gap-3"><h3 ref="wizardHeading" tabindex="-1" class="text-lg font-semibold text-text outline-none">{{ t(`moduleWorkspace.${steps[step]}`) }}</h3><button type="button" class="setup-button" :disabled="working" @click="close">{{ t('moduleWorkspace.close') }}</button></div>
      <ol class="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4" :aria-label="t('moduleWorkspace.setupSteps')"><li v-for="(name, index) in steps" :key="name" class="rounded-lg border border-border px-3 py-2" :class="index === step ? 'bg-accent1/15 text-text' : 'text-text-dim'" :aria-current="index === step ? 'step' : undefined">{{ index + 1 }}. {{ t(`moduleWorkspace.${name}`) }}</li></ol>
      <template v-if="step === 0"><p class="text-sm text-text-dim">{{ t('moduleWorkspace.computerHint') }}</p><button type="button" class="setup-button bg-accent1 text-white" @click="changeStep(1)">{{ t('moduleWorkspace.next') }}</button></template>
      <template v-if="step === 1">
        <p class="text-sm text-text-dim">{{ t('moduleWorkspace.selectHint') }}</p>
        <div class="grid gap-3 sm:grid-cols-2"><label v-for="module in modules" :key="module.id" class="flex cursor-pointer items-start gap-3 rounded-lg border border-border bg-panel-2/40 p-3" :class="{ 'opacity-50': !module.supported }"><input v-model="selected" type="checkbox" :value="module.id" :disabled="!module.supported || working" class="mt-1 size-4 accent-accent1"><span><span class="block text-sm font-medium text-text">{{ module.name }}</span><span class="block text-xs text-text-dim">{{ module.description }}</span></span></label></div>
        <label class="flex items-start gap-3 text-sm text-text"><input v-model="downloadModels" type="checkbox" :disabled="working" class="mt-1 size-4 accent-accent1"><span>{{ t('moduleWorkspace.models') }}<span class="mt-1 block text-xs text-text-dim">{{ t('moduleWorkspace.modelsHint') }}</span></span></label>
        <p class="text-xs text-text-dim">{{ t('moduleWorkspace.selected', { count: selected.length }) }}</p>
        <div class="flex gap-2"><button type="button" class="setup-button" :disabled="working" @click="changeStep(0)">{{ t('moduleWorkspace.back') }}</button><button type="button" class="setup-button bg-accent1 text-white" :disabled="working || !selected.length" @click="review">{{ working ? t('moduleWorkspace.reviewing') : t('moduleWorkspace.reviewAction') }}</button></div>
      </template>
      <template v-if="step === 2 && plan">
        <dl class="grid gap-3 text-sm sm:grid-cols-2"><div><dt class="text-text-dim">{{ t('moduleWorkspace.knownDownloads') }}</dt><dd class="text-text">{{ bytes(plan.estimated_download_bytes) }}</dd></div><div><dt class="text-text-dim">{{ t('moduleWorkspace.required') }}</dt><dd class="text-text">{{ bytes(plan.required_free_bytes) }}</dd></div></dl>
        <p v-if="plan.download_size_unknown" class="text-sm text-status-queued">{{ t('moduleWorkspace.unknownDownload') }}</p>
        <ul v-if="plan.warnings.length" class="list-disc space-y-1 pl-5 text-sm text-status-queued"><li v-for="warning in plan.warnings" :key="warning">{{ warning }}</li></ul>
        <ol class="space-y-3"><li v-for="item in plan.steps" :key="item.module_id" class="rounded-lg border border-border bg-panel-2/40 p-3"><h4 class="text-sm font-medium text-text">{{ item.name }}</h4><p class="mt-1 text-sm text-text-dim">{{ item.detail }}</p><template v-for="action in item.actions" :key="action.label"><p class="mt-2 text-xs text-text-dim">{{ action.detail }}</p><a v-if="safeUrl(action.url)" :href="safeUrl(action.url)" target="_blank" rel="noopener noreferrer" class="inline-block min-h-11 py-3 text-sm text-text underline decoration-accent2">{{ action.label }}</a></template></li></ol>
        <div class="flex gap-2"><button type="button" class="setup-button" :disabled="working" @click="changeStep(1)">{{ t('moduleWorkspace.back') }}</button><button type="button" class="setup-button bg-accent1 text-white" :disabled="working || !plan.can_install || store.busy" @click="install">{{ t('moduleWorkspace.installAction') }}</button></div>
      </template>
      <p v-if="step === 3" class="text-sm text-text-dim">{{ t('moduleWorkspace.noPercentage') }}</p>
    </div>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>

    <section v-for="group in groups" :key="group.label" class="space-y-2"><h3 class="text-xs font-semibold uppercase tracking-wider text-text-dim">{{ t(`moduleWorkspace.${group.label}`) }}</h3>
      <article v-for="module in groupModules(group.ids)" :id="`module-${module.id}`" :key="module.id" tabindex="-1" class="scroll-mt-28 rounded-xl border border-border bg-panel p-4 focus-visible:outline-2 focus-visible:outline-accent1">
        <div class="flex flex-wrap items-start justify-between gap-3"><div><h4 class="font-semibold text-text">{{ module.name }}</h4><p class="mt-1 text-sm text-text-dim">{{ module.description }}</p></div><span class="rounded-full border border-border px-3 py-1 text-xs" :class="statusClass(module)">{{ t(`moduleWorkspace.states.${store.stateOf(module.id)}`) }}</span></div>
        <details class="mt-3 text-sm"><summary class="cursor-pointer py-2 text-text-dim focus-visible:outline-2 focus-visible:outline-accent1">{{ t('moduleWorkspace.details') }}</summary><div class="space-y-3 pt-2"><p class="text-xs text-text-dim">{{ module.managed ? t('moduleWorkspace.managed') : t('moduleWorkspace.external') }}</p><ul class="space-y-2"><li v-for="evidence in module.evidence" :key="evidence.code" class="text-text-dim"><span aria-hidden="true" :class="evidence.verified ? 'text-status-done' : 'text-status-queued'">{{ evidence.verified ? '✓' : '○' }}</span><span class="sr-only">{{ t(evidence.verified ? 'moduleWorkspace.verified' : 'moduleWorkspace.unverified') }}: </span> {{ evidence.detail }}</li></ul><div v-for="action in module.actions" :key="action.label"><p class="text-text-dim">{{ action.detail }}</p><a v-if="safeUrl(action.url)" :href="safeUrl(action.url)" target="_blank" rel="noopener noreferrer" class="inline-block min-h-11 py-3 text-text underline decoration-accent2">{{ action.label }}</a></div><button v-if="module.supported" type="button" class="setup-button" :disabled="store.offline" @click="begin([module.id])">{{ t('moduleWorkspace.setup') }}</button></div></details>
      </article>
    </section>

    <section class="space-y-3 rounded-xl border border-border bg-panel p-4"><div class="flex flex-wrap items-center justify-between gap-3"><h3 class="font-semibold text-text">{{ t('moduleWorkspace.activity') }}</h3><button type="button" class="setup-button" @click="store.refresh()">{{ t('moduleWorkspace.reconnect') }}</button></div><p v-if="store.jobsOffline" role="alert" class="text-sm text-status-queued">{{ t('moduleWorkspace.jobsOffline') }}</p><p v-if="!store.jobs.length" class="text-sm text-text-dim">{{ t('moduleWorkspace.noJobs') }}</p>
      <article v-for="job in store.jobs" :key="job.id" class="space-y-2 rounded-lg border border-border p-3"><div class="flex flex-wrap items-center justify-between gap-3"><p role="status" class="text-sm font-medium text-text">{{ t(`moduleWorkspace.jobs.${job.state}`) }}</p><span class="text-xs text-text-dim">{{ job.created_at }}</span></div><p v-if="job.current_step != null" class="text-xs text-text-dim">{{ t('moduleWorkspace.currentStep', { current: job.current_step + 1, total: job.steps.length }) }}</p><ol class="space-y-2 text-sm"><li v-for="item in job.steps" :key="item.module_id"><span class="text-text">{{ item.name }} · {{ t(`moduleWorkspace.steps.${item.state}`) }}</span><p class="mt-1 text-xs text-text-dim">{{ item.detail }}</p></li></ol><p v-if="job.restart_required" class="text-xs text-status-queued">{{ t('moduleWorkspace.restartHint') }}</p><p v-if="job.state === 'awaiting_manual'" class="text-xs text-status-queued">{{ t('moduleWorkspace.manualHint') }}</p><div class="flex gap-2"><button v-if="job.state === 'queued' || job.state === 'running'" type="button" class="setup-button" :disabled="working" @click="control(job, 'cancel')">{{ t('moduleWorkspace.cancel') }}</button><button v-if="['failed', 'interrupted', 'cancelled', 'awaiting_manual'].includes(job.state)" type="button" class="setup-button" :disabled="working || store.busy" @click="control(job, 'resume')">{{ t('moduleWorkspace.resume') }}</button></div></article>
    </section>
  </section>
</template>

<style scoped>
@reference "../../style.css";
.setup-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

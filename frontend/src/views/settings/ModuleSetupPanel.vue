<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { routeLocationKey } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useModulesStore, type ModuleId } from '../../stores/modules'
import * as api from '../../api/modules'
import { ApiError } from '../../api/http'
import type { ModuleInfo, ModulePlan, ModuleInstallJob } from '../../api/generated'
import ModuleLicenseCards from './ModuleLicenseCards.vue'
import ModuleSetupJob from './ModuleSetupJob.vue'
import CollapsibleDetails from '../../components/shared/CollapsibleDetails.vue'
import { formatCreated } from '../../composables/formatCreated'

const { t } = useI18n(), store = useModulesStore()
const wizard = ref(false), step = ref(0), selected = ref<ModuleId[]>([]), downloadModels = ref(true)
const reinstall = ref<ModuleId[]>([])
const plan = ref<ModulePlan | null>(null), working = ref(false), error = ref('')
const wizardHeading = ref<HTMLElement | null>(null), activityHeading = ref<HTMLElement | null>(null), setupButton = ref<HTMLButtonElement | null>(null)
let alive = true, generation = 0, controller: AbortController | null = null
const steps = ['computer', 'features', 'review', 'install']
const groups: { label: string; ids: ModuleId[] }[] = [
  { label: 'music', ids: ['ace_step', 'yue2', 'heartmula', 'stable_audio3'] },
  { label: 'voices', ids: ['speech', 'singing', 'separation', 'kokoro', 'chatterbox', 'rvc', 'speaker_review'] },
  { label: 'videoTools', ids: ['video', 'wan22', 'media', 'transcription', 'source_import', 'ebooks'] },
]
const modules = computed(() => store.inventory?.modules ?? [])
const currentJob = computed(() => {
  const active = store.jobs.find(job => job.state === 'queued' || job.state === 'running')
  if (active) return active
  let latest = store.jobs[0], updated = -Infinity
  for (const job of store.jobs) {
    const timestamp = Date.parse(job.updated_at)
    if (Number.isFinite(timestamp) && timestamp > updated) { latest = job; updated = timestamp }
  }
  return latest
})
const history = computed(() => store.jobs.filter(job => job.id !== currentJob.value?.id))
function jobTitle(job: ModuleInstallJob): string {
  return job.features.map(id => modules.value.find(module => module.id === id)?.name ?? job.steps.find(item => item.module_id === id)?.name ?? id).join(', ')
}
function jobDate(value: string): string { return formatCreated(Date.parse(value)).full }
const route = inject(routeLocationKey, undefined)
const automatic = ref<ModuleId[]>([]), requestedModule = ref<ModuleInfo | null>(null)
const selectedModules = computed(() => modules.value.filter(module => selected.value.includes(module.id)))
const installedModules = computed(() => modules.value.filter(isInstalled))
const automaticSelected = computed(() => !reinstall.value.length && selected.value.length === automatic.value.length && automatic.value.every(id => selected.value.includes(id)))
const requestedUnavailable = computed(() => requestedModule.value && !modules.value.some(module => module.id === requestedModule.value?.id && module.supported))
const requestedInstalled = computed(() => modules.value.find(module => module.id === requestedModule.value?.id && isInstalled(module)))
const emptyAutomatic = computed(() => automaticSelected.value && !automatic.value.length && installedModules.value.length > 0 && !requestedUnavailable.value)
const selectionSupported = computed(() => validSelection(selected.value, reinstall.value))
const reviewedSupported = computed(() => plan.value && validSelection(plan.value.features, plan.value.reinstall ?? []))
watch(selected, ids => { reinstall.value = reinstall.value.filter(id => ids.includes(id)) })
function isInstalled(module: ModuleInfo): boolean { return module.state === 'installed' || module.state === 'ready' }
function selectable(module: ModuleInfo): boolean { return module.supported && (!isInstalled(module) || (reinstall.value.includes(module.id) && module.can_reinstall === true)) }
function validSelection(ids: ModuleId[], replacements: ModuleId[]): boolean {
  return ids.length > 0 && replacements.every(id => ids.includes(id)) && ids.every(id => modules.value.some(module => module.id === id && module.supported && (replacements.includes(id) ? isInstalled(module) && module.can_reinstall === true : !isInstalled(module))))
}
function resetAutomatic() {
  reinstall.value = []
  selected.value = automatic.value.filter(id => modules.value.some(module => module.id === id && module.supported && !isInstalled(module)))
}
function toggleReinstall(module: ModuleInfo) {
  if (working.value || store.offline) return
  if (reinstall.value.includes(module.id)) { reinstall.value = reinstall.value.filter(id => id !== module.id); selected.value = selected.value.filter(id => id !== module.id); return }
  if (!module.supported || !isInstalled(module) || !module.can_reinstall) return
  reinstall.value = [...reinstall.value, module.id]
  if (!selected.value.includes(module.id)) selected.value = [...selected.value, module.id]
}
function beginReinstall(module: ModuleInfo) { if (working.value) return; begin([module.id]); toggleReinstall(module) }
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
  if (value < 1000000) return `${value.toLocaleString()} B`
  return value >= 1000000000 ? `${(value / 1000000000).toFixed(1)} GB` : `${(value / 1000000).toFixed(1)} MB`
}
function safeUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined
  try { const url = new URL(value); return url.protocol === 'https:' ? url.href : undefined } catch { return undefined }
}
function statusClass(module: ModuleInfo) {
  const state = store.stateOf(module.id)
  return state === 'ready' || state === 'installed' ? 'text-status-done' : state === 'partial' ? 'text-status-queued' : 'text-text-dim'
}
function begin(ids: ModuleId[] = []) {
  if (working.value) return
  requestedModule.value = ids.length ? modules.value.find(module => ids.includes(module.id)) ?? null : modules.value.find(module => route?.hash === `#module-${module.id}`) ?? null
  const suggested = ids.length ? ids : requestedModule.value ? [requestedModule.value.id] : store.inventory?.recommended_features ?? []
  automatic.value = suggested.filter(id => modules.value.some(module => module.id === id && module.supported && !isInstalled(module)))
  resetAutomatic()
  downloadModels.value = true
  plan.value = null; wizard.value = true; step.value = 0; error.value = ''; void focusHeading()
}
async function focusHeading() { await nextTick(); if (alive) wizardHeading.value?.focus() }
async function focusActivity() { await nextTick(); if (alive) activityHeading.value?.focus() }
function changeStep(value: number) { if (working.value) return; step.value = value; error.value = ''; void focusHeading() }
function close() { if (working.value) return; generation++; controller?.abort(); wizard.value = false; error.value = ''; void nextTick(() => setupButton.value?.focus()) }
async function review() {
  if (working.value || store.offline || !selectionSupported.value) return
  const token = ++generation; controller?.abort(); const request = new AbortController(); controller = request
  working.value = true; error.value = ''
  try {
    const response = await api.planModules({ features: [...selected.value], download_models: downloadModels.value, ...(reinstall.value.length ? { reinstall: [...reinstall.value] } : {}) }, request.signal)
    if (!alive || token !== generation) return
    plan.value = response; step.value = 2; void focusHeading()
  } catch (err) { if (alive && token === generation) error.value = errorText(err, 'moduleWorkspace.planFailed') }
  finally { if (alive && token === generation) { working.value = false; controller = null } }
}
function errorText(err: unknown, fallback: string): string {
  const messages: Record<string, string> = { setup_busy: 'busy', plan_changed: 'changed', insufficient_disk: 'disk', worker_unverified: 'ownership', setup_not_resumable: 'notResumable', already_installed: 'alreadyInstalled', reinstall_unavailable: 'reinstallUnavailable' }
  const key = err instanceof ApiError ? messages[err.message] : undefined
  return t(key ? `moduleWorkspace.errors.${key}` : fallback)
}
function retainJob(job: ModuleInstallJob) { store.acceptJob(job) }
async function install() {
  if (working.value || !plan.value?.can_install || !reviewedSupported.value || store.offline) return
  const reviewed = plan.value, token = ++generation
  controller?.abort(); const request = new AbortController(); controller = request; working.value = true; error.value = ''
  try {
    const job = await api.installModules({ features: reviewed.features, download_models: reviewed.download_models, plan_token: reviewed.plan_token, ...(reviewed.reinstall?.length ? { reinstall: reviewed.reinstall } : {}) }, request.signal)
    if (!alive || token !== generation) return
    retainJob(job); step.value = 3; void focusActivity()
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
      <div class="flex gap-2"><button type="button" class="setup-button" :disabled="store.loading || working" @click="store.refresh()">{{ t('moduleWorkspace.refresh') }}</button><button ref="setupButton" type="button" class="setup-button bg-accent1 text-white" :disabled="!store.inventory || store.offline || working" @click="begin()">{{ t('moduleWorkspace.setup') }}</button></div>
    </div>
    <p v-if="store.loading" role="status" class="text-sm text-text-dim">{{ t('moduleWorkspace.loading') }}</p>
    <p v-if="store.offline" role="alert" class="rounded-lg border border-border bg-panel p-3 text-sm text-status-queued">{{ t('moduleWorkspace.offline') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <section aria-labelledby="setup-activity-title" class="space-y-4 rounded-xl border border-border bg-panel p-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h3 id="setup-activity-title" ref="activityHeading" tabindex="-1" class="scroll-mt-28 font-semibold text-text outline-none">{{ t('moduleWorkspace.activity') }}</h3>
        <button type="button" class="setup-button" :disabled="store.loading || working" @click="store.refresh()">{{ t('moduleWorkspace.reconnect') }}</button>
      </div>
      <p v-if="store.jobsOffline" role="alert" class="text-sm text-status-queued">{{ t('moduleWorkspace.jobsOffline') }}</p>
      <p v-if="!store.jobs.length" class="text-sm text-text-dim">{{ t('moduleWorkspace.noJobs') }}</p>
      <ModuleSetupJob v-if="currentJob" :data-current-job="currentJob.id" :job="currentJob" :title="jobTitle(currentJob)" :working="working" @control="control(currentJob, $event)" />
      <details v-if="history.length" data-job-history class="rounded-lg border border-border">
        <summary class="min-h-11 cursor-pointer px-4 py-3 text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-accent1">{{ t('moduleWorkspace.history', { count: history.length }) }}</summary>
        <ol class="divide-y divide-border border-t border-border">
          <li v-for="job in history" :key="job.id" data-history-job>
            <details>
              <summary class="min-h-11 cursor-pointer px-4 py-3 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1">
                <span class="font-medium">{{ jobTitle(job) }}</span>
                <span class="ml-2" :class="job.state === 'failed' ? 'text-status-failed' : 'text-text-dim'">{{ t(`moduleWorkspace.jobs.${job.state}`) }}</span>
                <time :datetime="job.created_at" class="ml-2 text-xs text-text-dim">{{ jobDate(job.created_at) }}</time>
              </summary>
              <div class="px-3 pb-3"><ModuleSetupJob :job="job" :title="jobTitle(job)" :working="working" @control="control(job, $event)" /></div>
            </details>
          </li>
        </ol>
      </details>
    </section>
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
        <div class="space-y-3 rounded-lg border border-border bg-panel-2/40 p-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <h4 class="font-semibold text-text">{{ t(emptyAutomatic ? 'moduleWorkspace.auto.noNewSetup' : automaticSelected ? 'moduleWorkspace.auto.chosen' : 'moduleWorkspace.auto.customSelection') }}</h4>
            <button v-if="!automaticSelected" type="button" class="setup-button" :disabled="working" @click="resetAutomatic">{{ t('moduleWorkspace.auto.reset') }}</button>
          </div>
          <p class="text-sm text-text-dim">{{ requestedInstalled ? t('moduleWorkspace.installedHint', { name: requestedInstalled.name }) : emptyAutomatic ? t('moduleWorkspace.auto.installedSelection') : automaticSelected && requestedModule && !requestedUnavailable ? t('moduleWorkspace.auto.context', { name: requestedModule.name }) : t('moduleWorkspace.auto.hint') }}</p>
          <p v-if="requestedUnavailable" data-automatic-unavailable role="alert" class="text-sm text-status-queued">{{ t('moduleWorkspace.auto.unavailable', { name: requestedModule?.name }) }}</p>
          <ul v-if="selectedModules.length" data-selected-features class="space-y-2"><li v-for="module in selectedModules" :key="module.id" class="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-panel p-3"><span class="text-sm font-medium text-text">{{ module.name }}</span><span class="text-xs" :class="statusClass(module)">{{ t(`moduleWorkspace.states.${store.stateOf(module.id)}`) }}</span></li></ul>
          <p v-else class="text-sm text-text-dim">{{ t('moduleWorkspace.noSelection') }}</p>
          <p v-if="selected.length && !selectionSupported" role="alert" class="text-sm text-status-queued">{{ t('moduleWorkspace.auto.selectionChanged') }}</p>
          <p class="text-xs leading-relaxed text-text-dim">{{ t('moduleWorkspace.selectHint') }}</p>
        </div>
        <section v-if="installedModules.length" data-installed-features aria-labelledby="installed-features-title" class="space-y-3 rounded-lg border border-border bg-panel-2/40 p-4">
          <h4 id="installed-features-title" class="font-semibold text-text">{{ t('moduleWorkspace.installedFeatures') }}</h4>
          <ul class="space-y-3">
            <li v-for="module in installedModules" :key="module.id" :data-installed-module="module.id" class="space-y-2 rounded-lg border border-border bg-panel p-3">
              <div class="flex flex-wrap items-center justify-between gap-2"><span class="text-sm font-medium text-text">{{ module.name }}</span><span class="text-xs" :class="statusClass(module)">{{ t(`moduleWorkspace.states.${store.stateOf(module.id)}`) }}</span></div>
              <button v-if="reinstall.includes(module.id) || (module.supported && module.can_reinstall)" type="button" class="setup-button" :disabled="working || store.offline" :aria-label="reinstall.includes(module.id) ? t('moduleWorkspace.cancelReinstall') : t('moduleWorkspace.reinstallFor', { name: module.name })" @click="toggleReinstall(module)">{{ t(reinstall.includes(module.id) ? 'moduleWorkspace.cancelReinstall' : 'moduleWorkspace.reinstall') }}</button>
              <p v-else class="text-xs leading-relaxed text-text-dim">{{ t('moduleWorkspace.reinstallUnavailableHint') }}</p>
              <p v-if="reinstall.includes(module.id)" class="text-xs leading-relaxed text-status-queued">{{ t('moduleWorkspace.reinstallHint') }}</p>
            </li>
          </ul>
        </section>
        <CollapsibleDetails :summary="t('moduleWorkspace.auto.customize')">
          <fieldset v-for="group in groups" :key="group.label" class="min-w-0 space-y-3">
            <legend class="mb-2 text-sm font-semibold text-text">{{ t(`moduleWorkspace.${group.label}`) }}</legend>
            <div class="grid gap-3 sm:grid-cols-2"><label v-for="module in groupModules(group.ids)" :key="module.id" class="flex items-start gap-3 rounded-lg border border-border bg-panel-2/40 p-3" :class="{ 'cursor-pointer': selectable(module), 'opacity-50': !module.supported }"><input v-model="selected" type="checkbox" :value="module.id" :disabled="!selectable(module) || working" class="mt-1 size-4 accent-accent1"><span><span class="block text-sm font-medium text-text">{{ module.name }}</span><span class="mt-1 block text-xs" :class="statusClass(module)">{{ t(`moduleWorkspace.states.${store.stateOf(module.id)}`) }}</span><span class="mt-1 block text-xs text-text-dim">{{ module.description }}</span></span></label></div>
          </fieldset>
          <label class="flex items-start gap-3 text-sm text-text"><input v-model="downloadModels" type="checkbox" :disabled="working" class="mt-1 size-4 accent-accent1"><span>{{ t('moduleWorkspace.models') }}<span class="mt-1 block text-xs text-text-dim">{{ t('moduleWorkspace.modelsHint') }}</span></span></label>
        </CollapsibleDetails>
        <p class="text-xs text-text-dim">{{ t(downloadModels ? 'moduleWorkspace.modelScopeIncluded' : 'moduleWorkspace.modelScopeExcluded') }}</p>
        <p class="text-xs text-text-dim">{{ t('moduleWorkspace.selected', { count: selected.length }) }}</p>
        <div class="flex gap-2"><button type="button" class="setup-button" :disabled="working" @click="changeStep(0)">{{ t('moduleWorkspace.back') }}</button><button type="button" class="setup-button bg-accent1 text-white" :disabled="working || store.offline || !selectionSupported" @click="review">{{ working ? t('moduleWorkspace.reviewing') : t('moduleWorkspace.reviewAction') }}</button></div>
      </template>
      <template v-if="step === 2 && plan">
        <p data-model-scope class="text-sm text-text">{{ t(plan.download_models ? 'moduleWorkspace.modelScopeIncluded' : 'moduleWorkspace.modelScopeExcluded') }}</p>
        <div v-if="plan.reinstall?.length" data-reinstall-review class="space-y-2 rounded-lg border border-status-queued/50 bg-panel-2/40 p-3">
          <h4 class="text-sm font-semibold text-text">{{ t('moduleWorkspace.reinstallReview') }}</h4>
          <p class="text-sm text-text">{{ plan.steps.filter(item => plan?.reinstall?.includes(item.module_id)).map(item => item.name).join(', ') }}</p>
          <p class="text-xs leading-relaxed text-text-dim">{{ t('moduleWorkspace.reinstallHint') }}</p>
        </div>
        <p v-if="!reviewedSupported" role="alert" class="text-sm text-status-queued">{{ t('moduleWorkspace.auto.selectionChanged') }}</p>
        <dl class="grid gap-3 text-sm sm:grid-cols-2"><div><dt class="text-text-dim">{{ t('moduleWorkspace.knownDownloads') }}</dt><dd class="text-text">{{ bytes(plan.estimated_download_bytes) }}</dd></div><div><dt class="text-text-dim">{{ t('moduleWorkspace.required') }}</dt><dd class="text-text">{{ bytes(plan.required_free_bytes) }}</dd></div></dl>
        <p v-if="plan.download_size_unknown" class="text-sm text-status-queued">{{ t('moduleWorkspace.unknownDownload') }}</p>
        <ul v-if="plan.warnings.length" class="list-disc space-y-1 pl-5 text-sm text-status-queued"><li v-for="warning in plan.warnings" :key="warning">{{ warning }}</li></ul>
        <ol class="space-y-3"><li v-for="item in plan.steps" :key="item.module_id" class="rounded-lg border border-border bg-panel-2/40 p-3"><h4 class="text-sm font-medium text-text">{{ item.name }}</h4><p v-if="item.operation === 'reuse'" data-reused-module class="mt-1 text-xs text-status-done">{{ t('moduleWorkspace.reuse') }}</p><p class="mt-1 text-sm text-text-dim">{{ item.detail }}</p><template v-for="action in item.actions" :key="action.label"><p class="mt-2 text-xs text-text-dim">{{ action.detail }}</p><a v-if="safeUrl(action.url)" :href="safeUrl(action.url)" target="_blank" rel="noopener noreferrer" class="inline-block min-h-11 py-3 text-sm text-text underline decoration-accent2">{{ action.label }}</a></template><ModuleLicenseCards data-license-review :licenses="item.licenses" /></li></ol>
        <div class="flex gap-2"><button type="button" class="setup-button" :disabled="working" @click="changeStep(1)">{{ t('moduleWorkspace.back') }}</button><button type="button" class="setup-button bg-accent1 text-white" :disabled="working || !plan.can_install || !reviewedSupported || store.offline || store.busy" @click="install">{{ t('moduleWorkspace.installAction') }}</button></div>
      </template>
      <p v-if="step === 3" class="text-sm text-text-dim">{{ t('moduleWorkspace.noPercentage') }}</p>
    </div>

    <section v-for="group in groups" :key="group.label" class="space-y-2"><h3 class="text-xs font-semibold uppercase tracking-wider text-text-dim">{{ t(`moduleWorkspace.${group.label}`) }}</h3>
      <article v-for="module in groupModules(group.ids)" :id="`module-${module.id}`" :key="module.id" tabindex="-1" class="scroll-mt-28 rounded-xl border border-border bg-panel p-4 focus-visible:outline-2 focus-visible:outline-accent1">
        <div class="flex flex-wrap items-start justify-between gap-3"><div><h4 class="font-semibold text-text">{{ module.name }}</h4><p class="mt-1 text-sm text-text-dim">{{ module.description }}</p></div><span class="rounded-full border border-border px-3 py-1 text-xs" :class="statusClass(module)">{{ t(`moduleWorkspace.states.${store.stateOf(module.id)}`) }}</span></div>
        <details class="mt-3 text-sm"><summary class="cursor-pointer py-2 text-text-dim focus-visible:outline-2 focus-visible:outline-accent1">{{ t('moduleWorkspace.details') }}</summary><div class="space-y-3 pt-2"><p class="text-xs text-text-dim">{{ module.managed ? t('moduleWorkspace.managed') : t('moduleWorkspace.external') }}</p><ul class="space-y-2"><li v-for="evidence in module.evidence" :key="evidence.code" class="text-text-dim"><span aria-hidden="true" :class="evidence.verified ? 'text-status-done' : 'text-status-queued'">{{ evidence.verified ? '✓' : '○' }}</span><span class="sr-only">{{ t(evidence.verified ? 'moduleWorkspace.verified' : 'moduleWorkspace.unverified') }}: </span> {{ evidence.detail }}</li></ul><div v-for="action in module.actions" :key="action.label"><p class="text-text-dim">{{ action.detail }}</p><a v-if="safeUrl(action.url)" :href="safeUrl(action.url)" target="_blank" rel="noopener noreferrer" class="inline-block min-h-11 py-3 text-text underline decoration-accent2">{{ action.label }}</a></div><ModuleLicenseCards :licenses="module.licenses" /><button v-if="module.supported" type="button" class="setup-button" :disabled="store.offline || working || isInstalled(module)" @click="begin([module.id])">{{ t(isInstalled(module) ? `moduleWorkspace.states.${module.state}` : 'moduleWorkspace.setup') }}</button><button v-if="module.supported && isInstalled(module) && module.can_reinstall" type="button" class="setup-button" :disabled="store.offline || working" :aria-label="t('moduleWorkspace.reinstallFor', { name: module.name })" @click="beginReinstall(module)">{{ t('moduleWorkspace.reinstall') }}</button></div></details>
      </article>
    </section>

  </section>
</template>

<style scoped>
@reference "../../style.css";
.setup-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

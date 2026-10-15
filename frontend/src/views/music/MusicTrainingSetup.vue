<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/modules'
import { ApiError } from '../../api/http'
import type { ModuleInstallJob, ModulePlan, StableLoraTrainingRequest } from '../../api/generated'
import { createPollingLoop } from '../../composables/polling'
import ModuleSetupJob from '../settings/ModuleSetupJob.vue'

const props = withDefaults(defineProps<{ engine?: 'stable_audio3' | 'heartmula' | 'yue2'; variant?: NonNullable<StableLoraTrainingRequest['variant']>; supported: boolean; installed: boolean; ready: boolean }>(), { engine: 'stable_audio3', variant: 'small-music' })
const emit = defineEmits<{ refresh: [] }>()
const { t } = useI18n()
const jobs = ref<ModuleInstallJob[]>([]), plan = ref<ModulePlan | null>(null)
const known = ref(false), offline = ref(false), working = ref(false), error = ref('')
const currentJob = computed(() => jobs.value.filter(job => props.engine === 'stable_audio3' ? job.stable_training_variant === props.variant : job.music_training_engine === props.engine).sort((a, b) => b.created_at.localeCompare(a.created_at))[0])
const licenses = computed(() => plan.value?.steps.flatMap(step => step.licenses ?? []) ?? [])
const active = computed(() => jobs.value.some(job => (!!job.stable_training_variant || !!job.music_training_engine) && (job.state === 'queued' || job.state === 'running')))
const canPrepare = computed(() => props.supported && (props.installed || props.engine !== 'stable_audio3') && !props.ready && known.value && !offline.value && !active.value && !working.value)
let alive = true, generation = 0, controller: AbortController | undefined, completedId: string | undefined
function bytes(value: number): string { return value >= 1e9 ? `${(value / 1e9).toFixed(2)} GB` : `${(value / 1e6).toFixed(1)} MB` }
const poll = createPollingLoop(async context => {
  try {
    const response = await api.listModuleJobs(context.signal)
    if (!alive || !context.isCurrent()) return false
    jobs.value = response; known.value = true; offline.value = false
    const completed = currentJob.value
    if (completed?.state === 'completed' && completedId !== completed.id) { completedId = completed.id; emit('refresh') }
  } catch { if (alive && context.isCurrent()) offline.value = true }
  return active.value || !known.value || offline.value
}, 2500)
async function action(work: (signal: AbortSignal, current: () => boolean) => Promise<void>, mutation = false) {
  if (!alive || working.value) return
  poll.stop(); controller?.abort()
  const request = new AbortController(), token = ++generation
  const current = () => alive && generation === token && !request.signal.aborted
  controller = request; working.value = true; error.value = ''
  try { await work(request.signal, current) }
  catch (cause) {
    if (current()) {
      const code = cause instanceof ApiError ? cause.message : ''
      if (code === 'plan_changed') plan.value = null
      error.value = t(code === 'plan_changed' ? 'musicLora.prepare.changed' : ['setup_busy', 'module_setup_busy', 'music_queue_busy'].includes(code) ? 'musicLora.errors.busy' : 'musicLora.prepare.error')
    }
  } finally {
    if (current()) { working.value = false; controller = undefined; if (mutation || active.value || !known.value || offline.value) poll.start() }
  }
}
function review() {
  if (!canPrepare.value) return
  const selection = props.engine === 'stable_audio3' ? { stable_training_variant: props.variant } : { music_training_engine: props.engine }
  const engine = props.engine
  return action(async (signal, current) => {
    const response = await api.planModules({ features: [engine], download_models: true, ...selection }, signal)
    if (current()) plan.value = response
  })
}
function prepare() {
  const reviewed = plan.value
  if (!canPrepare.value || !reviewed?.can_install || (props.engine === 'stable_audio3' ? reviewed.stable_training_variant !== props.variant : reviewed.music_training_engine !== props.engine)) return
  return action(async (signal, current) => {
    known.value = false
    const response = await api.installModules({ features: reviewed.features, download_models: reviewed.download_models, reinstall: reviewed.reinstall, ...(reviewed.stable_training_variant ? { stable_training_variant: reviewed.stable_training_variant } : { music_training_engine: reviewed.music_training_engine }), plan_token: reviewed.plan_token }, signal)
    if (current()) { jobs.value = [response, ...jobs.value.filter(job => job.id !== response.id)]; known.value = true; plan.value = null }
  }, true)
}
function control(actionName: 'cancel' | 'resume') {
  const job = currentJob.value
  if (!job) return
  return action(async (signal, current) => {
    known.value = false
    const response = await api.controlModuleJob(job.id, actionName, signal)
    if (current()) { jobs.value = [response, ...jobs.value.filter(item => item.id !== response.id)]; known.value = true }
  }, true)
}
watch(() => [props.engine, props.variant], () => { ++generation; controller?.abort(); controller = undefined; working.value = false; plan.value = null; error.value = ''; poll.stop(); poll.start() })
onMounted(() => poll.start())
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort(); poll.stop() })
</script>

<template>
  <section data-training-setup class="space-y-3" :aria-busy="working">
    <p v-if="offline" role="status" class="text-sm text-status-queued">{{ t('musicLora.prepare.reconnecting') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <template v-if="supported && (installed || props.engine !== 'stable_audio3') && !ready">
      <p class="text-sm text-text-dim">{{ t('musicLora.prepare.hint') }}</p>
      <button v-if="!plan" type="button" class="prepare-button" :disabled="!canPrepare" @click="review">{{ t(working ? 'musicLora.working' : 'musicLora.prepare.review') }}</button>
      <div v-if="plan" class="space-y-3 rounded-lg border border-border p-4">
        <h3 class="font-semibold text-text">{{ t('musicLora.prepare.title', { model: t(props.engine === 'stable_audio3' ? `musicLora.targets.${variant}` : `musicLora.engines.${props.engine}`) }) }}</h3>
        <p class="text-sm text-text-dim">{{ t('musicLora.prepare.size', { size: bytes(plan.estimated_download_bytes) }) }}</p>
        <p v-for="step in plan.steps" :key="step.module_id" class="text-sm text-text-dim">{{ step.detail }}</p>
        <p v-for="warning in plan.warnings" :key="warning" class="text-sm text-status-queued">{{ warning }}</p>
        <details class="text-sm text-text-dim">
          <summary class="min-h-11 cursor-pointer py-3 focus-visible:outline-2 focus-visible:outline-accent1">{{ t('musicLora.prepare.terms') }}</summary>
          <ul class="space-y-2"><li v-for="license in licenses" :key="license.component_id"><span class="text-text">{{ license.name }} · {{ license.declared_license ?? t('moduleWorkspace.licenseUnknown') }}</span><p>{{ license.notes }}</p><a v-if="license.source_url" :href="license.source_url" target="_blank" rel="noopener noreferrer" class="inline-flex min-h-11 items-center underline">{{ t('musicLora.prepare.source') }}</a></li></ul>
        </details>
        <button type="button" class="prepare-button" :disabled="!canPrepare" @click="review">{{ t('musicLora.prepare.reviewAgain') }}</button>
        <button type="button" class="prepare-button bg-accent1 text-white" :disabled="!canPrepare || !plan.can_install" @click="prepare">{{ t('musicLora.prepare.start') }}</button>
      </div>
    </template>
    <ModuleSetupJob v-if="currentJob" :job="currentJob" :title="t('musicLora.prepare.activity', { model: t(currentJob.stable_training_variant ? `musicLora.targets.${currentJob.stable_training_variant}` : `musicLora.engines.${currentJob.music_training_engine}`) })" :working="working" @control="control" />
  </section>
</template>

<style scoped>
@reference "../../style.css";
.prepare-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

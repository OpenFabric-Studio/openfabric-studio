<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import * as music from '../../api/localMusic'
import type { LocalMusicEngineId } from '../../api/localMusic'
import type { ConditioningSelection, MusicConditioningDraft } from '../../api/musicConditioning'
import type { LocalMusicEngines, LocalMusicJob, LocalMusicRequest, MusicAdapterSelection } from '../../api/generated'
import { ApiError } from '../../api/http'
import MusicTermsCard from '../../components/shared/MusicTermsCard.vue'
import { pendingMusicAdapter, pendingMusicConditioning } from '../../composables/generationDrafts'
import { createPollingLoop } from '../../composables/polling'
import StemsPanel from '../../components/shared/StemsPanel.vue'
import MusicAdapterSelect from '../../components/shared/MusicAdapterSelect.vue'
import MusicTrainingSetup from './MusicTrainingSetup.vue'
import MusicConditioningControls from './MusicConditioningControls.vue'

const props = defineProps<{ engine: LocalMusicEngineId }>()
const { t, te } = useI18n()
const engineStatus = ref<LocalMusicEngines['engines'][number] | null>(null)
const jobs = ref<LocalMusicJob[]>([]), historyIncomplete = ref(false), busy = ref(false), error = ref('')
const title = ref(''), prompt = ref(''), duration = ref(30), seed = ref(0)
const variant = ref<NonNullable<LocalMusicRequest['variant']>>('small-music')
const adapter = ref<MusicAdapterSelection | null>(null)
const adapterValid = ref(true)
const conditioning = ref<ConditioningSelection | null>(null), conditioningValid = ref(true)
const conditioningDraft = ref<MusicConditioningDraft | null>(null)
const baseRequest = computed<LocalMusicRequest>(() => ({ engine: props.engine, title: title.value.trim(), prompt: prompt.value.trim(),
  lyrics: '', duration_seconds: duration.value, seed: seed.value, variant: variant.value, adapter: adapter.value }))
const selectedJobs = computed(() => jobs.value.filter(job => job.request.engine === props.engine))
const active = computed(() => selectedJobs.value.some(job => ongoing(job)))
const weightsReady = computed(() => (variant.value === 'medium' ? engineStatus.value?.medium_weights_ready : engineStatus.value?.small_weights_ready) === true)
const ready = computed(() => !!engineStatus.value?.supported && engineStatus.value.installed && weightsReady.value)
const minDuration = 5
const maxDuration = computed(() => variant.value === 'medium' ? 380 : 120)
const termsBlocked = ref(false)
const valid = computed(() => !termsBlocked.value && ready.value && !active.value && adapterValid.value && conditioningValid.value && prompt.value.trim().length > 0 && prompt.value.length <= 4000
  && title.value.length <= 500
  && Number.isInteger(duration.value) && duration.value >= minDuration && duration.value <= maxDuration.value
  && Number.isInteger(seed.value) && seed.value >= 0 && seed.value <= 2147483647)
const readinessKey = computed(() => !engineStatus.value?.supported ? 'unsupported' : !engineStatus.value.installed ? 'missingEngine' : !weightsReady.value ? 'missingSelectedWeights' : 'ready')
let alive = true, generation = 0, controller: AbortController | undefined
function ongoing(job: LocalMusicJob): boolean { return job.status === 'queued' || job.status === 'running' }
function errorText(code: string | null | undefined, fallback: string): string {
  if (code && te(`musicConditioning.errors.${code}`)) return t(`musicConditioning.errors.${code}`)
  switch (code) {
    case 'music_terms_required':
    case 'music_noncommercial_disabled': return t('musicTerms.required')
    case 'insufficient_disk': return t('localMusic.insufficientDisk')
    case 'local_music_output_exists': return t('localMusic.outputExists')
    case 'music_lora_adapter_not_found': return t('musicLora.errors.missing')
    case 'music_lora_adapter_changed': return t('musicLora.errors.changed')
    case 'music_lora_target_mismatch':
    case 'music_lora_format_invalid': return t('musicLora.errors.format')
    case 'local_music_weights_missing': return t('localMusic.missingSelectedWeights')
    case 'local_music_engine_not_installed': return t('localMusic.missingEngine')
    case 'local_music_runtime_unsupported': return t('localMusic.unsupported')
    case 'module_setup_busy':
    case 'local_music_busy': return t('localMusic.busy')
    default: return t(fallback)
  }
}

const poll = createPollingLoop(async context => {
  const engine = props.engine
  try {
    const response = await music.listLocalMusic(context.signal)
    if (!context.isCurrent() || !alive || engine !== props.engine) return false
    jobs.value = response.jobs; historyIncomplete.value = response.history_incomplete ?? false
  } catch {
    if (context.isCurrent() && alive && engine === props.engine) error.value = t('localMusic.loadFailed')
  }
  return active.value
}, 2500)

async function action(work: (signal: AbortSignal, isCurrent: () => boolean) => Promise<void>, failure = 'localMusic.actionFailed') {
  if (!alive || busy.value) return
  poll.stop()
  const token = ++generation, request = new AbortController()
  const isCurrent = () => alive && token === generation && !request.signal.aborted
  controller = request; busy.value = true; error.value = ''
  try { await work(request.signal, isCurrent) }
  catch (cause) { if (isCurrent()) error.value = errorText(cause instanceof ApiError ? cause.message : null, failure) }
  finally {
    if (isCurrent()) { busy.value = false; controller = undefined; if (active.value) poll.start(false) }
  }
}
function load() {
  return action(async (signal, isCurrent) => {
    const [setup, history] = await Promise.allSettled([music.localMusicEngines(signal), music.listLocalMusic(signal)])
    if (!isCurrent()) return
    if (setup.status === 'fulfilled') engineStatus.value = setup.value.engines.find(engine => engine.id === props.engine) ?? null
    if (history.status === 'fulfilled') { jobs.value = history.value.jobs; historyIncomplete.value = history.value.history_incomplete ?? false }
    if (setup.status === 'rejected' || history.status === 'rejected') error.value = t('localMusic.loadFailed')
  }, 'localMusic.loadFailed')
}
function history() {
  return action(async (signal, isCurrent) => {
    const response = await music.listLocalMusic(signal)
    if (isCurrent()) { jobs.value = response.jobs; historyIncomplete.value = response.history_incomplete ?? false }
  }, 'localMusic.loadFailed')
}
function generate() {
  if (!valid.value) return
  const request: LocalMusicRequest = conditioning.value ? { ...baseRequest.value, conditioning: conditioning.value } : baseRequest.value
  return action(async (signal, isCurrent) => {
    const response = await music.submitLocalMusic(request, signal)
    if (isCurrent()) jobs.value = [response, ...jobs.value.filter(job => job.id !== response.id)]
  })
}
function cancel(job: LocalMusicJob) {
  return action(async (signal, isCurrent) => {
    const response = await music.cancelLocalMusic(job.id, signal)
    if (isCurrent()) jobs.value = jobs.value.map(existing => existing.id === response.id ? response : existing)
  })
}
watch(variant, () => { adapter.value = null; adapterValid.value = true }, { flush: 'sync' })
watch(pendingMusicConditioning, value => { if (value && alive) conditioningDraft.value = value }, { immediate: true })
function conditioningDraftApplied() {
  if (!alive) return
  if (pendingMusicConditioning.value === conditioningDraft.value) pendingMusicConditioning.value = null
  conditioningDraft.value = null
}
watch(() => [pendingMusicAdapter.value, props.engine], async (_value, _previous, onCleanup) => {
  const selection = pendingMusicAdapter.value
  if (!selection || selection.engine !== props.engine) return
  let current = true
  onCleanup(() => { current = false })
  if ((selection.target === 'small-music' || selection.target === 'medium') && variant.value !== selection.target) {
    variant.value = selection.target
    await nextTick()
    if (!alive || !current) return
  }
  adapter.value = selection.selection
  pendingMusicAdapter.value = null
}, { immediate: true })
onMounted(() => { void load() })
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort(); poll.stop() })
</script>

<template>
  <section class="min-w-0 space-y-5" :aria-busy="busy">
    <header class="space-y-2 rounded-xl border border-accent1/30 bg-accent1/5 p-5">
      <div class="flex flex-wrap items-center gap-3"><h2 class="text-lg font-semibold text-text">{{ t(`localMusic.engines.${engine}`) }}</h2><span class="rounded-full border border-accent1/30 px-2 py-1 text-xs text-accent2">{{ t('localMusic.experimental') }}</span></div>
      <p class="text-sm text-text-dim">{{ t('localMusic.intro') }}</p>
      <p v-if="engineStatus" class="text-sm text-text-dim">{{ t(`localMusic.${readinessKey}`) }}<span v-if="ready"> · {{ engineStatus.runtime }}</span></p>
      <div class="flex flex-wrap items-center gap-3"><RouterLink :to="`/settings#module-${engine}`" class="inline-flex min-h-11 items-center text-sm text-accent2 underline focus-visible:outline-2 focus-visible:outline-accent2">{{ t('localMusic.setup') }}</RouterLink><button type="button" class="music-button" :disabled="busy" @click="load">{{ t('localMusic.refresh') }}</button></div>
    </header>
    <MusicTrainingSetup v-if="engineStatus" mode="generation" :variant="variant" :supported="engineStatus.supported" :installed="engineStatus.installed" :ready="ready" @refresh="load" />
    <p v-if="busy" role="status" class="text-sm text-text-dim">{{ t('localMusic.working') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="historyIncomplete" role="alert" class="text-sm text-status-queued">{{ t('localMusic.historyIncomplete') }}</p>
    <form class="space-y-4 rounded-xl border border-border bg-panel p-5" @submit.prevent="generate">
      <label class="music-label">{{ t('localMusic.prompt') }}<textarea v-model="prompt" name="prompt" rows="4" maxlength="4000" required class="music-input resize-y" :disabled="busy" aria-describedby="local-music-instrumental" /></label>
      <p id="local-music-instrumental" class="text-xs text-text-dim">{{ t('localMusic.instrumental') }}</p>
      <label class="music-label">{{ t('localMusic.title') }}<input v-model="title" name="title" maxlength="500" class="music-input" :disabled="busy" /></label>
      <div class="grid gap-4 sm:grid-cols-2">
        <label class="music-label">{{ t('localMusic.duration') }}<input v-model.number="duration" name="duration_seconds" type="number" :min="minDuration" :max="maxDuration" step="1" required class="music-input" :disabled="busy" aria-describedby="local-music-duration-hint" /></label>
        <label class="music-label">{{ t('localMusic.seed') }}<input v-model.number="seed" name="seed" type="number" min="0" max="2147483647" step="1" required class="music-input" :disabled="busy" /></label>
      </div>
      <label class="music-label">{{ t('localMusic.variant') }}<select v-model="variant" name="variant" class="music-input" :disabled="busy" aria-describedby="local-music-variant-hint"><option value="small-music">{{ t('localMusic.small') }} · {{ t(engineStatus?.small_weights_ready ? 'localMusic.weightsAvailable' : 'localMusic.weightsMissing') }}</option><option value="medium">{{ t('localMusic.medium') }} · {{ t(engineStatus?.medium_weights_ready ? 'localMusic.weightsAvailable' : 'localMusic.weightsMissing') }}</option></select></label><p id="local-music-variant-hint" class="text-xs text-text-dim">{{ t('localMusic.variantHint') }}</p>
      <div class="space-y-2"><MusicAdapterSelect v-model="adapter" :engine="engine" :target="variant" :disabled="busy" @validity="adapterValid = $event" /><RouterLink to="/music/lora" class="inline-flex min-h-11 items-center text-xs text-accent1 underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('musicLora.manage') }}</RouterLink></div>
      <p id="local-music-duration-hint" class="text-xs text-text-dim">{{ t('localMusic.durationHint', { min: minDuration, max: maxDuration }) }}</p>
      <MusicConditioningControls :request="baseRequest" :supported="engineStatus?.supported ?? false" :installed="engineStatus?.installed ?? false" :disabled="busy" :source-draft="conditioningDraft" @change="conditioning = $event" @validity="conditioningValid = $event" @refresh="load" @draft-applied="conditioningDraftApplied" />
      <MusicTermsCard :engine="engine" operation="generation" @blocked="termsBlocked = $event" />
      <button type="submit" class="music-button accent-gradient text-white" :disabled="busy || !valid">{{ t('localMusic.generate') }}</button>
      <p v-if="active" class="text-xs text-text-dim">{{ t('localMusic.ongoing') }}</p>
    </form>
    <div class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3"><h3 class="font-semibold text-text">{{ t('localMusic.history') }}</h3><button type="button" class="music-button" :disabled="busy" @click="history">{{ t('localMusic.refreshJobs') }}</button></div>
      <p v-if="!selectedJobs.length" class="text-sm text-text-dim">{{ t('localMusic.empty') }}</p>
      <article v-for="job in selectedJobs" :key="job.id" class="space-y-3 rounded-xl border border-border bg-panel p-5">
        <div class="flex flex-wrap items-start justify-between gap-3"><div><h4 class="break-words font-medium text-text">{{ job.track?.title || job.request.title || t(`localMusic.engines.${engine}`) }}</h4><p class="mt-1 text-xs text-text-dim" role="status">{{ t(`localMusic.states.${job.status}`) }}</p></div><button v-if="ongoing(job)" type="button" class="music-button" :disabled="busy" @click="cancel(job)">{{ t('localMusic.cancel') }}</button></div>
        <p v-if="job.status === 'failed'" role="alert" class="text-sm text-status-failed">{{ errorText(job.error_code, 'localMusic.failed') }}</p>
        <p v-if="job.status === 'interrupted'" class="text-sm text-status-queued">{{ t('localMusic.interrupted') }}</p>
        <p v-if="job.conditioning_recipe" class="break-all text-xs text-text-dim">{{ t(`musicConditioning.${job.conditioning_recipe.mode}`) }} · {{ t('musicConditioning.hash') }}: {{ job.conditioning_recipe.source_sha256 }}</p>
        <template v-if="job.track"><audio :src="job.track.audio_url" controls preload="none" class="w-full" :aria-label="job.track.title" /><a :href="job.track.audio_url" :download="job.track.filename" class="inline-flex min-h-11 items-center text-sm text-accent1 underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('localMusic.download') }}</a><StemsPanel :track-id="job.track.id" :title="job.track.title" :lyrics="job.track.lyrics" :model="job.track.model" /></template>
      </article>
    </div>
  </section>
</template>

<style scoped>
@reference "../../style.css";
.music-label { @apply flex flex-col gap-2 text-sm text-text-dim; }
.music-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
.music-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

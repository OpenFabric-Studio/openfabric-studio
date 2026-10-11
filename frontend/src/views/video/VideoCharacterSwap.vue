<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import type { CharacterSwapJob, CharacterSwapReadiness, VideoSceneCut } from '../../api/contracts'
import * as api from '../../api/characterSwaps'
import { CHARACTER_SWAP_MAX_VIDEO_BYTES } from '../../api/characterSwaps'
import { ApiError } from '../../api/http'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
import { createPollingLoop } from '../../composables/polling'

const { t, te } = useI18n()
const props = defineProps<{ chosenScene?: VideoSceneCut | null }>()
const emit = defineEmits<{ clearScene: [] }>()
const jobs = ref<CharacterSwapJob[]>([]), selectedId = ref(''), readiness = ref<CharacterSwapReadiness | null>(null)
const name = ref(t('characterSwap.title')), video = ref<File | null>(null), image = ref<File | null>(null)
const startSec = ref(0), seconds = ref(2), busy = ref(false), preparing = ref(false), loading = ref(false), error = ref('')
const consent = ref(false), transfers = ref(false), clock = ref(Date.now()), uncertainIds = ref(new Set<string>())
const selected = computed(() => jobs.value.find(job => job.id === selectedId.value))
const legacy = computed(() => selected.value?.provider === 'fal')
const stage = computed(() => selected.value?.stage ?? 'frame')
const swapSteps = ['prepare', 'frame', 'video'] as const
const currentStep = computed(() => !selected.value || legacy.value ? 'prepare' : stage.value === 'frame' ? 'frame' : 'video')
const tracking = computed(() => selected.value && !legacy.value && ['submitting', 'queued', 'running'].includes(selected.value.status ?? ''))
const expired = computed(() => { const expiry = (selected.value?.quote?.expires_at ?? 0) * 1000; return !Number.isFinite(expiry) || expiry <= clock.value })
const valid = computed(() => name.value.trim().length > 0 && name.value.trim().length <= 120 && (props.chosenScene ? props.chosenScene.status === 'ready' && (props.chosenScene.bytes ?? 0) <= CHARACTER_SWAP_MAX_VIDEO_BYTES && startSec.value + seconds.value <= props.chosenScene.seconds : video.value) && image.value
  && Number.isFinite(startSec.value) && startSec.value >= 0 && startSec.value <= 21600 && Number.isInteger(seconds.value) && seconds.value >= 2 && seconds.value <= 8)
const canQuote = computed(() => selected.value && !legacy.value && selected.value.status === 'prepared' && !busy.value && !loading.value
  && readiness.value?.configured && readiness.value.ffmpeg_ready && (stage.value === 'frame' || selected.value.edited_frame_url)
  && !uncertainIds.value.has(submissionKey(selected.value)))
const canSubmit = computed(() => canQuote.value && selected.value?.quote && selected.value.quote.model_id === selected.value.model_id
  && selected.value.quote.kind === (stage.value === 'frame' ? 'image' : 'video') && !expired.value && transfers.value && consent.value)
const canResume = computed(() => selected.value?.resume_available && !legacy.value && selected.value.error_code !== 'worker_unresolved')
let alive = true, mounted = false, generation = 0, controller: AbortController | undefined
let clockTimer: ReturnType<typeof setInterval> | undefined
const players = new Set<HTMLVideoElement>()
const loop = createPollingLoop(async ({ signal, isCurrent }) => {
  const id = selectedId.value, token = generation
  if (!tracking.value || busy.value || loading.value) return false
  try {
    const job = await api.getCharacterSwap(id, signal)
    if (isCurrent() && alive && token === generation && selectedId.value === id && job.id === id) { saveJob(job); error.value = '' }
  } catch { if (isCurrent() && alive && token === generation) error.value = t('characterSwap.errors.load_failed') }
  return Boolean(tracking.value)
}, 4000)

function invalidate() { generation++; controller?.abort(); controller = undefined; loop.stop() }
function current(token: number, request: AbortController) { return alive && generation === token && !request.signal.aborted }
function submissionKey(job: CharacterSwapJob) { return `${job.id}:${job.stage ?? 'frame'}` }
function saveJob(job: CharacterSwapJob) {
  const old = selected.value
  if (old?.id === job.id && (old.revision !== job.revision || old.stage !== job.stage || old.quote?.id !== job.quote?.id)) { consent.value = false; transfers.value = false }
  jobs.value = [job, ...jobs.value.filter(value => value.id !== job.id)]
}
function actionError(value: unknown) { const key = value instanceof ApiError ? `characterSwap.errors.${value.message}` : ''; return t(key && te(key) ? key : 'characterSwap.errors.unknown') }
function schedulePoll() { if (alive && tracking.value) loop.start(false) }
function stopPlayers() { for (const player of players) { player.pause(); releasePlaybackIfCurrent(player) }; players.clear() }
function selectJob(id: string) { invalidate(); stopPlayers(); selectedId.value = id; busy.value = false; preparing.value = false; loading.value = false; consent.value = false; transfers.value = false; error.value = ''; schedulePoll() }
async function load() {
  if (busy.value || loading.value) return
  invalidate(); const token = generation, request = new AbortController(); controller = request; loading.value = true; error.value = ''; consent.value = false; transfers.value = false
  try {
    const [available, saved] = await Promise.all([api.characterSwapReadiness(request.signal), api.listCharacterSwaps(request.signal)])
    if (!current(token, request)) return
    readiness.value = available; jobs.value = saved.jobs ?? []
    if (!jobs.value.some(job => job.id === selectedId.value)) selectedId.value = props.chosenScene ? '' : jobs.value[0]?.id ?? ''
    for (const job of jobs.value) if (job.status !== 'prepared') uncertainIds.value.delete(submissionKey(job))
  } catch { if (current(token, request)) error.value = t('characterSwap.errors.load_failed') }
  finally { if (current(token, request)) { loading.value = false; schedulePoll() } }
}
function chooseFile(event: Event, kind: 'video' | 'image') {
  if (!(event.target instanceof HTMLInputElement)) return
  const file = event.target.files?.[0] ?? null
  const supported = !file || (kind === 'video' ? /\.(avi|mkv|mp4|mov|webm)$/i.test(file.name) && file.size <= CHARACTER_SWAP_MAX_VIDEO_BYTES
    : /\.(png|jpe?g|webp)$/i.test(file.name) && file.size <= 15 * 1024 * 1024)
  if (kind === 'video') video.value = supported ? file : null
  else image.value = supported ? file : null
  error.value = supported ? '' : t(`characterSwap.errors.invalid_${kind}`)
}
async function prepare() {
  if (busy.value || loading.value || !readiness.value?.ffmpeg_ready || !valid.value || !image.value || !props.chosenScene && !video.value) return
  invalidate(); const token = generation, request = new AbortController(); controller = request; busy.value = true; preparing.value = true; error.value = ''
  try {
    const common = { image: image.value, name: name.value.trim(), startSec: startSec.value, seconds: seconds.value }
    const job = props.chosenScene ? await api.prepareCharacterSwapFromScene({ ...common, sceneId: props.chosenScene.source_id, cutId: props.chosenScene.id }, request.signal)
      : video.value ? await api.prepareCharacterSwap({ ...common, video: video.value }, request.signal) : null
    if (!job) return
    if (current(token, request)) { stopPlayers(); saveJob(job); selectedId.value = job.id; consent.value = false; transfers.value = false }
  } catch (value) { if (current(token, request)) error.value = actionError(value) }
  finally { if (current(token, request)) { busy.value = false; preparing.value = false; schedulePoll() } }
}
async function action(kind: 'quote' | 'submit' | 'resume' | 'stop') {
  const job = selected.value
  if (!job || legacy.value || busy.value || loading.value || kind === 'quote' && !canQuote.value || kind === 'submit' && (!canSubmit.value || !job.quote || job.quote.expires_at * 1000 <= Date.now())
    || kind === 'resume' && !canResume.value || kind === 'stop' && !tracking.value) return
  invalidate(); const token = generation, request = new AbortController(); controller = request; busy.value = true; error.value = ''
  if (kind === 'quote') { consent.value = false; transfers.value = false }
  if (kind === 'submit') uncertainIds.value.add(submissionKey(job))
  try {
    const result = kind === 'quote' ? await api.quoteCharacterSwap(job.id, { revision: job.revision ?? 1 }, request.signal)
      : kind === 'submit' && job.quote ? await api.submitCharacterSwap(job.id, { revision: job.revision ?? 1, quote_id: job.quote.id, transfers_confirmed: transfers.value, consent_confirmed: consent.value }, request.signal)
      : kind === 'resume' ? await api.resumeCharacterSwap(job.id, { revision: job.revision ?? 1 }, request.signal)
        : await api.stopCharacterSwap(job.id, { revision: job.revision ?? 1 }, request.signal)
    if (current(token, request) && result.id === job.id) { saveJob(result); uncertainIds.value.delete(submissionKey(job)); consent.value = false; transfers.value = false }
  } catch (value) { if (current(token, request)) error.value = actionError(value) }
  finally { if (current(token, request)) { busy.value = false; schedulePoll() } }
}
function playing(event: Event) { if (event.target instanceof HTMLVideoElement) { players.add(event.target); claimPlayback(event.target) } }
function stopped(event: Event) { if (event.target instanceof HTMLVideoElement) releasePlaybackIfCurrent(event.target) }
watch(() => props.chosenScene, () => {
  invalidate(); stopPlayers(); selectedId.value = ''; busy.value = false; preparing.value = false; loading.value = false; consent.value = false; transfers.value = false; error.value = ''; video.value = null; startSec.value = 0; seconds.value = 2
  if (mounted && alive) void load()
}, { immediate: true, flush: 'sync' })
onMounted(() => { mounted = true; void load(); clockTimer = setInterval(() => { clock.value = Date.now() }, 1000) })
onBeforeUnmount(() => { alive = false; invalidate(); if (clockTimer) clearInterval(clockTimer); stopPlayers() })
</script>

<template>
  <section class="swap-panel space-y-5" aria-labelledby="character-swap-heading" :aria-busy="busy || loading">
    <header><h2 id="character-swap-heading" tabindex="-1" class="scroll-mt-20 text-xl font-semibold focus-visible:outline-2 focus-visible:outline-accent1">{{ t('characterSwap.title') }}</h2><p class="mt-2 text-sm text-text-dim">{{ t('characterSwap.intro') }}</p></header>
    <ol class="grid gap-3 sm:grid-cols-3" :aria-label="t('characterSwap.process')">
      <li v-for="(item, index) in swapSteps" :key="item" :aria-current="currentStep === item ? 'step' : undefined" class="swap-stage rounded-lg border border-border bg-panel p-4"><p class="text-sm font-semibold">{{ index + 1 }}. {{ t(`characterSwap.steps.${item}.title`) }}</p><p class="mt-1 text-xs text-text-dim">{{ t(`characterSwap.steps.${item}.hint`) }}</p></li>
    </ol>
    <details class="text-sm text-text-dim"><summary class="min-h-11 cursor-pointer py-2">{{ t('characterSwap.qualityDetails') }}</summary><p class="mt-2 leading-relaxed">{{ t('characterSwap.quality') }}</p></details>
    <div v-if="readiness && !readiness.configured" role="status" class="text-sm"><p>{{ t('characterSwap.setup') }}</p><RouterLink data-swap-setup to="/settings#providers" class="inline-flex min-h-11 items-center text-text underline">{{ t('cloudProviders.setup') }}</RouterLink></div>
    <p v-if="readiness && !readiness.ffmpeg_ready" role="status" class="text-sm text-status-failed">{{ t('characterSwap.ffmpeg') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <form class="video-card space-y-4" @submit.prevent="prepare">
      <h3 class="font-semibold">{{ t('characterSwap.prepareTitle') }}</h3>
      <div v-if="chosenScene" data-swap-chosen-scene class="space-y-2"><p class="font-medium">{{ t('videoScenes.chosenScene') }} · {{ chosenScene.name }} · {{ chosenScene.seconds.toFixed(3) }}s</p><p class="text-sm text-text-dim">{{ t('videoScenes.chosenSceneHint') }}</p><p v-if="(chosenScene.bytes ?? 0) > CHARACTER_SWAP_MAX_VIDEO_BYTES" role="status" class="text-sm text-status-failed">{{ t('videoScenes.swapTooLarge') }}</p><button type="button" @click="emit('clearScene')">{{ t('videoScenes.clearScene') }}</button></div>
      <p class="text-sm text-text-dim">{{ t('characterSwap.prepareHint') }}</p>
      <details class="text-sm text-text-dim"><summary class="min-h-11 cursor-pointer py-2">{{ t('characterSwap.fileDetails') }}</summary><p class="mt-2 leading-relaxed">{{ t('characterSwap.limits') }}</p></details>
      <fieldset :disabled="busy || loading" class="grid min-w-0 gap-4 sm:grid-cols-2">
        <label class="sm:col-span-2">{{ t('characterSwap.name') }}<input v-model="name" type="text" maxlength="120" required></label>
        <label v-if="!chosenScene">{{ t('characterSwap.video') }}<input data-swap-video type="file" accept=".avi,.mkv,.mp4,.mov,.webm,video/x-msvideo,video/x-matroska,video/mp4,video/quicktime,video/webm" required @change="chooseFile($event, 'video')"></label>
        <label>{{ t('characterSwap.image') }}<input data-swap-image type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp" required @change="chooseFile($event, 'image')"></label>
        <label>{{ t('characterSwap.start') }}<input v-model.number="startSec" data-swap-start type="number" min="0" :max="chosenScene?.seconds ?? 21600" step="any" required></label>
        <label>{{ t('characterSwap.seconds') }}<input v-model.number="seconds" data-swap-seconds type="number" min="2" max="8" step="1" required></label>
      </fieldset>
      <p class="text-sm text-text-dim">{{ t('characterSwap.local') }}</p>
      <button data-swap-prepare type="submit" :disabled="!valid || !readiness?.ffmpeg_ready || busy || loading">{{ t('characterSwap.prepare') }}</button>
      <p v-if="preparing" role="status">{{ t('characterSwap.preparing') }}</p>
    </form>
    <div class="grid min-w-0 gap-5 lg:grid-cols-[240px_minmax(0,1fr)]">
      <aside class="min-w-0 space-y-3" :aria-label="t('characterSwap.library')">
        <h3 class="font-semibold">{{ t('characterSwap.library') }}</h3>
        <button type="button" :disabled="busy || loading" @click="load">{{ t('characterSwap.refresh') }}</button>
        <p v-if="loading" role="status">{{ t('characterSwap.loading') }}</p>
        <p v-else-if="!jobs.length" class="text-sm text-text-dim">{{ t('characterSwap.empty') }}</p>
        <ul class="space-y-2"><li v-for="job in jobs" :key="job.id"><button :data-swap-job="job.id" type="button" :aria-pressed="selectedId === job.id" class="w-full text-left" @click="selectJob(job.id)"><span class="block break-words font-medium">{{ job.name }}</span><span class="block text-xs">{{ t(`characterSwap.status.${job.status}`) }}</span></button></li></ul>
      </aside>
      <article v-if="selected" class="video-card min-w-0 space-y-4" :aria-label="selected.name">
        <div class="flex flex-wrap items-center justify-between gap-2"><h3 class="break-words font-semibold">{{ selected.name }}</h3><p role="status" class="text-sm">{{ t(`characterSwap.status.${selected.status}`) }}</p></div>
        <p v-if="legacy" role="status" class="text-sm text-text-dim">{{ t('characterSwap.legacy') }}</p>
        <p v-else class="text-sm font-medium">{{ t(stage === 'frame' ? 'characterSwap.frameStep' : 'characterSwap.videoStep') }}</p>
        <p class="text-sm text-text-dim">{{ t('characterSwap.duration', { seconds: selected.seconds, width: selected.width, height: selected.height }) }}</p>
        <p v-if="selected.error_code" role="alert" class="text-sm text-status-failed">{{ t(te(`characterSwap.errors.${selected.error_code}`) ? `characterSwap.errors.${selected.error_code}` : 'characterSwap.errors.unknown') }}</p>
        <p v-if="!legacy && (selected.status === 'submission_unknown' || uncertainIds.has(submissionKey(selected)) && !busy)" role="alert" class="text-sm text-status-failed">{{ t(selected.status === 'submission_unknown' ? 'characterSwap.unknown' : 'characterSwap.unconfirmed') }} <a href="https://openrouter.ai/activity" target="_blank" rel="noopener noreferrer" class="underline">{{ t('characterSwap.activity') }}</a></p>
        <div class="grid min-w-0 gap-4 md:grid-cols-2">
          <figure class="min-w-0 space-y-2"><figcaption class="text-sm font-medium">{{ t('characterSwap.source') }}</figcaption><video v-if="selected.clip_url" :key="selected.clip_url" :src="selected.clip_url" controls preload="metadata" playsinline class="aspect-video w-full rounded-lg bg-panel-2" :aria-label="t('characterSwap.source')" @play="playing" @pause="stopped" @ended="stopped"></video><p v-if="!selected.has_audio" class="text-xs text-text-dim">{{ t('characterSwap.noAudio') }}</p></figure>
          <figure v-if="selected.output_url && selected.status === 'ready'" class="min-w-0 space-y-2"><figcaption class="text-sm font-medium">{{ t('characterSwap.replacement') }}</figcaption><video :key="selected.output_url" :src="selected.output_url" controls preload="metadata" playsinline class="aspect-video w-full rounded-lg bg-panel-2" :aria-label="t('characterSwap.replacement')" @play="playing" @pause="stopped" @ended="stopped"></video><a :href="selected.output_url" data-swap-download download class="inline-flex min-h-11 items-center text-text underline">{{ t('characterSwap.download') }}</a></figure>
          <figure v-else-if="!legacy && stage === 'video' && selected.edited_frame_url" class="min-w-0 space-y-2"><figcaption class="text-sm font-medium">{{ t('characterSwap.editedFrame') }}</figcaption><img data-swap-edited-frame :src="selected.edited_frame_url" :alt="t('characterSwap.editedFrame')" class="aspect-video w-full rounded-lg bg-panel-2 object-contain"><p class="text-sm text-text-dim">{{ t('characterSwap.frameReview') }}</p></figure>
          <figure v-else-if="!legacy && selected.source_frame_url" class="min-w-0 space-y-2"><figcaption class="text-sm font-medium">{{ t('characterSwap.sourceFrame') }}</figcaption><img data-swap-source-frame :src="selected.source_frame_url" :alt="t('characterSwap.sourceFrame')" class="aspect-video w-full rounded-lg bg-panel-2 object-contain"></figure>
          <figure v-if="selected.image_url && (legacy || stage === 'frame')" class="min-w-0 space-y-2"><figcaption class="text-sm font-medium">{{ t('characterSwap.reference') }}</figcaption><img data-swap-reference :src="selected.image_url" :alt="t('characterSwap.reference')" loading="lazy" class="aspect-video w-full rounded-lg bg-panel-2 object-contain"></figure>
        </div>
        <a v-if="selected.source_url" :href="selected.source_url" download class="inline-flex min-h-11 items-center text-sm text-text underline">{{ t('characterSwap.original') }}</a>
        <div v-if="!legacy && selected.status === 'prepared'" data-swap-review class="space-y-3 rounded-lg border border-border p-4">
          <h4 class="font-semibold">{{ t('characterSwap.review') }}</h4>
          <button data-swap-quote type="button" :disabled="!canQuote" @click="action('quote')">{{ t(stage === 'frame' ? 'characterSwap.quoteFrame' : 'characterSwap.quoteVideo') }}</button>
          <template v-if="selected.quote">
            <p class="font-medium">{{ t('characterSwap.estimate', { cost: selected.quote.estimated_usd.toFixed(3) }) }}</p>
            <p class="text-xs text-text-dim">{{ t('characterSwap.estimateHint') }}</p>
            <p class="text-sm text-text-dim">{{ t(stage === 'frame' ? 'characterSwap.frameTransfers' : 'characterSwap.videoTransfers') }}</p>
            <label class="inline-check"><input v-model="transfers" type="checkbox" :disabled="busy || expired">{{ t('characterSwap.transferConfirm') }}</label>
            <label class="inline-check"><input v-model="consent" type="checkbox" :disabled="busy || expired">{{ t('characterSwap.consentConfirm') }}</label>
            <p v-if="expired" role="status" class="text-sm text-status-failed">{{ t('characterSwap.expired') }}</p>
            <button data-swap-submit type="button" class="primary" :disabled="!canSubmit" @click="action('submit')">{{ t(stage === 'frame' ? 'characterSwap.submitFrame' : 'characterSwap.submitVideo') }}</button>
          </template>
        </div>
        <p v-if="selected.remote_id" class="break-all text-xs text-text-dim">{{ t('characterSwap.remote', { id: selected.remote_id }) }}</p>
        <button v-if="canResume" data-swap-resume type="button" :disabled="busy || loading" @click="action('resume')">{{ t('characterSwap.resume') }}</button>
        <div v-if="tracking" class="space-y-2"><button data-swap-stop type="button" :disabled="busy || loading" @click="action('stop')">{{ t('characterSwap.stop') }}</button><p class="text-xs text-text-dim">{{ t('characterSwap.stopHint') }}</p></div>
      </article>
    </div>
    <p class="text-xs text-text-dim">{{ t('characterSwap.background') }}</p>
  </section>
</template>

<style scoped>
.swap-panel label { display: flex; flex-direction: column; gap: .35rem; font-size: .875rem; }
.swap-panel input:not([type=checkbox]) { width: 100%; min-width: 0; min-height: 44px; padding: .6rem .75rem; background: var(--color-panel-2); border: 1px solid var(--color-border); border-radius: .5rem; }
.swap-panel button { min-height: 44px; padding: .5rem .75rem; border-radius: .5rem; background: var(--color-panel-2); }
.swap-panel button.primary, .swap-panel button[aria-pressed=true] { background: var(--color-accent1); color: white; }
.swap-panel button:disabled { opacity: .45; cursor: not-allowed; }
.swap-stage[aria-current=step] { border-color: var(--color-accent2); }
.swap-panel summary:focus-visible { outline: 2px solid var(--color-accent1); outline-offset: 2px; }
.swap-panel button:focus-visible, .swap-panel input:focus-visible, .swap-panel a:focus-visible { outline: 2px solid var(--color-accent1); outline-offset: 2px; }
.swap-panel label.inline-check { flex-direction: row; align-items: center; min-height: 44px; gap: .5rem; }
.swap-panel input[type=checkbox] { width: 18px; height: 18px; flex-shrink: 0; }
.video-card { min-width: 0; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-panel); padding: 20px; }
</style>

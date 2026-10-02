<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listAudioVersions, createAudioVersion, cancelAudioVersion, retryAudioVersion } from '../../api/audioVersions'
import { listAudioExports, createAudioExport, cancelAudioExport, retryAudioExport, type AudioExportFormat } from '../../api/audioExports'
import { listVoices, type VoiceProfile } from '../../api/voices'
import { ApiError } from '../../api/http'
import type { AudioVersion, AudioExportResponse } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'
import WaveformPlayer from './WaveformPlayer.vue'
import StatusBadge from './StatusBadge.vue'
import VoiceApplyStatus from './VoiceApplyStatus.vue'
import TaggedAudioDownload from './TaggedAudioDownload.vue'
const props = defineProps<{ trackId: number; fallbackAudioUrl?: string | null; fallbackFilename?: string | null; voiceApplying?: boolean }>()
const emit = defineEmits<{ playing: [value: boolean]; processing: [value: boolean] }>()
const { t, te } = useI18n()
const versions = ref<AudioVersion[]>([])
const exports = ref<AudioExportResponse[]>([])
const voices = ref<VoiceProfile[]>([])
const voicesUnavailable = ref(false)
const selectedId = ref('')
const playback = ref<{ version: AudioVersion; export: AudioExportResponse | null } | null>(null)
const player = ref<InstanceType<typeof WaveformPlayer> | null>(null)
const playerPlaying = ref(false)
const playbackStarting = ref(false)
// Changing src pauses the existing media before the explicitly selected file
// can start. Keep its card owned through that transition, then use real media
// state again when the request starts, fails, or is cancelled.
watch(() => playerPlaying.value || playbackStarting.value, value => emit('playing', value), { flush: 'sync' })
const voiceId = ref('')
const format = ref<AudioExportFormat>('mp3')
const originalAvailable = ref(false)
const loaded = ref(false)
const loading = ref(true)
const pending = ref(false)
const error = ref('')
let disposed = false
let session = 0
let playbackRequest = 0
let actionController: AbortController | undefined
const selected = computed(() => versions.value.find(item => item.id === selectedId.value))
const playbackFile = computed(() => playback.value?.export ?? playback.value?.version)
const playable = computed(() => playbackFile.value?.audio_url ?? (!loaded.value ? props.fallbackAudioUrl : null))
const playbackFilename = computed(() => playbackFile.value?.filename ?? (!loaded.value ? props.fallbackFilename : null))
const readyExports = computed(() => exports.value.filter(item => item.version_id === selectedId.value && item.status === 'done' && item.audio_url))
const playbackFormat = computed(() => playback.value?.export?.format.toUpperCase() ?? nativeFormat(playbackFilename.value, playable.value) ?? t('trackAudio.source'))
const playbackLabel = computed(() => [playback.value ? label(playback.value.version) : '', playbackFormat.value].filter(Boolean).join(' · '))
const readyVoices = computed(() => voices.value.filter(item => item.usable))
const active = (status: string) => status === 'queued' || status === 'running'
watch(() => versions.value.some(version => active(version.status)) || exports.value.some(item => active(item.status)), value => emit('processing', value), { immediate: true })
const visibleVoiceJobs = computed(() => versions.value.filter(item => item.kind === 'voice' && (active(item.status) || item.id === selectedId.value && item.job_progress)))
function label(version: AudioVersion) { return version.kind === 'original' ? t('trackAudio.original') : version.voice_name || t('trackAudio.unknownVoice') }
function statusLabel(version: AudioVersion): string {
  const progress = version.job_progress
  if (progress && active(version.status)) {
    const key = progress.status === 'queued' || progress.phase === 'waiting_gpu' || progress.phase === 'queued'
      ? `trackAudio.progress.queue.${progress.queue_reason || (progress.phase === 'waiting_gpu' ? 'gpu_busy' : 'queued')}`
      : `trackAudio.progress.phase.${progress.phase ?? ''}`
    if (te(key)) return t(key)
  }
  return t(`jobStatus.${version.status}`)
}
function nativeFormat(filename?: string | null, url?: string | null): string | undefined {
  const path = (filename || url || '').split(/[?#]/, 1)[0] ?? ''
  return /\.([a-z0-9]{1,8})$/i.exec(path)?.[1]?.toUpperCase()
}
function sourceLabel(version: AudioVersion): string {
  const format = nativeFormat(version.filename, version.audio_url)
  return format ? t('trackAudio.sourceFormat', { format }) : t('trackAudio.source')
}
function reconcilePlayback(): void {
  const version = versions.value.find(item => item.id === playback.value?.version.id)
  if (version?.status === 'done' && version.audio_url && playback.value) {
    playback.value = { version, export: playback.value.export }
  } else {
    const available = selected.value?.status === 'done' && selected.value.audio_url ? selected.value
      : [...versions.value].reverse().find(item => item.status === 'done' && item.audio_url)
    playback.value = available ? { version: available, export: null } : null
  }
}
async function playFile(version: AudioVersion, exported: AudioExportResponse | null = null): Promise<void> {
  if (disposed || pending.value || version.status !== 'done' || !version.audio_url || (exported && (exported.status !== 'done' || !exported.audio_url || exported.version_id !== version.id))) return
  const token = session, request = ++playbackRequest
  playbackStarting.value = true
  playback.value = { version, export: exported }
  const url = playable.value
  try {
    await nextTick()
    if (disposed || token !== session || request !== playbackRequest || playable.value !== url) return
    await player.value?.play()
  } finally {
    if (!disposed && token === session && request === playbackRequest) playbackStarting.value = false
  }
}
function notePlaying(value: boolean): void {
  playerPlaying.value = value
  if (value) playbackStarting.value = false
}
function errorText(cause: unknown) {
  const code = cause instanceof ApiError ? cause.message : ''
  const key = `trackAudio.errors.${code}`
  return code && te(key) ? t(key) : t('trackAudio.errors.unknown')
}
function upsertVersion(version: AudioVersion) { versions.value = [...versions.value.filter(item => item.id !== version.id), version] }
function upsertExport(item: AudioExportResponse) { exports.value = [...exports.value.filter(existing => existing.id !== item.id), item] }
const poll = createPollingLoop(async context => {
  const trackId = props.trackId
  try {
    const response = await listAudioVersions(trackId, context.signal)
    if (!context.isCurrent() || trackId !== props.trackId) return false
    versions.value = response.versions ?? []
    originalAvailable.value = response.original_available
    loaded.value = true
    if (!versions.value.some(item => item.id === selectedId.value)) {
      selectedId.value = [...versions.value].reverse().find(item => item.status === 'done' && item.audio_url)?.id ?? versions.value[0]?.id ?? ''
    }
    reconcilePlayback()
    const id = selectedId.value
    if (id) {
      const response = await listAudioExports(trackId, id, context.signal)
      if (!context.isCurrent() || id !== selectedId.value || trackId !== props.trackId) return false
      exports.value = response.exports ?? []
      if (playback.value?.version.id === id && playback.value.export) {
        const exported = readyExports.value.find(item => item.id === playback.value?.export?.id)
        playback.value = { version: playback.value.version, export: exported ?? null }
      }
    }
  } catch (cause) {
    if (!context.isCurrent()) return false
    error.value = errorText(cause)
    return false
  } finally {
    if (context.isCurrent()) loading.value = false
  }
  return props.voiceApplying || versions.value.some(item => active(item.status)) || exports.value.some(item => active(item.status))
}, 2500)
async function loadVoices(token: number, signal: AbortSignal) {
  try {
    const response = await listVoices(signal)
    if (disposed || token !== session || signal.aborted) return
    voices.value = response
    voicesUnavailable.value = false
    if (!readyVoices.value.some(item => item.id === voiceId.value)) voiceId.value = readyVoices.value[0]?.id ?? ''
  } catch {
    if (!disposed && token === session && !signal.aborted) { voices.value = []; voiceId.value = ''; voicesUnavailable.value = true }
  }
}
function refresh() {
  if (pending.value || disposed) return
  poll.stop()
  error.value = ''
  loading.value = true
  actionController?.abort()
  actionController = new AbortController()
  void loadVoices(session, actionController.signal)
  poll.start()
}
function choose(version: AudioVersion) {
  if (pending.value || disposed) return
  if (version.status !== 'done' || !version.audio_url) {
    playbackRequest++
    playbackStarting.value = false
  }
  if (version.id !== selectedId.value) {
    selectedId.value = version.id
    exports.value = []
    poll.stop()
    poll.start()
  }
  void playFile(version)
}
async function perform(action: (signal: AbortSignal) => Promise<void>) {
  if (pending.value || disposed) return
  poll.stop()
  actionController?.abort()
  const controller = new AbortController()
  actionController = controller
  const token = session
  pending.value = true
  error.value = ''
  try { await action(controller.signal) } catch (cause) {
    if (!disposed && token === session && !controller.signal.aborted) error.value = errorText(cause)
  } finally {
    if (!disposed && token === session && !controller.signal.aborted) {
      pending.value = false
      // Mutations already returned their authoritative record; defer the next
      // refresh so an older GET cannot immediately erase the just-created row.
      poll.start(false)
    }
  }
}
function current(signal: AbortSignal, trackId: number) { return !disposed && !signal.aborted && trackId === props.trackId }
function addVoice() {
  if (!originalAvailable.value || !readyVoices.value.some(item => item.id === voiceId.value)) return
  const trackId = props.trackId, chosenVoice = voiceId.value
  void perform(async signal => {
    const version = await createAudioVersion(trackId, chosenVoice, signal)
    if (!current(signal, trackId)) return
    upsertVersion(version)
    selectedId.value = version.id
    exports.value = []
  })
}
function versionAction(version: AudioVersion, kind: 'cancel' | 'retry') {
  const trackId = props.trackId
  void perform(async signal => {
    const result = await (kind === 'cancel' ? cancelAudioVersion : retryAudioVersion)(trackId, version.id, signal)
    if (current(signal, trackId)) upsertVersion(result)
  })
}
function addExport() {
  const version = selected.value, trackId = props.trackId, chosenFormat = format.value
  if (!version || version.status !== 'done') return
  void perform(async signal => {
    const result = await createAudioExport(trackId, version.id, chosenFormat, signal)
    if (current(signal, trackId) && selectedId.value === version.id) upsertExport(result)
  })
}
function exportAction(item: AudioExportResponse, kind: 'cancel' | 'retry') {
  const trackId = props.trackId
  void perform(async signal => {
    const result = await (kind === 'cancel' ? cancelAudioExport : retryAudioExport)(trackId, item.version_id, item.id, signal)
    if (current(signal, trackId) && selectedId.value === item.version_id) upsertExport(result)
  })
}
function quality(item: AudioExportResponse) {
  const profile = item.settings[item.format]
  const rate = profile?.sample_rate ? `${profile.sample_rate / 1000} kHz` : ''
  const channels = profile?.channels ? t(profile.channels === 1 ? 'trackAudio.mono' : 'trackAudio.stereo') : ''
  const mp3 = item.settings.mp3
  const detail = item.format === 'mp3'
    ? mp3?.mode === 'vbr' ? `VBR q${mp3.vbr_quality ?? '?'}` : `${mp3?.bitrate_kbps ?? '?'} kbps CBR`
    : `${(item.format === 'wav' ? item.settings.wav : item.settings.flac)?.bit_depth ?? '?'}-bit${item.format === 'wav' && item.settings.wav?.bit_depth === 32 ? ' float' : ''}`
  const compression = item.format === 'flac' ? t('trackAudio.compression', { level:item.settings.flac?.compression_level ?? '?' }) : ''
  return [item.format.toUpperCase(), detail, compression, rate, channels].filter(Boolean).join(' · ')
}
watch(() => props.trackId, () => {
  session++
  playbackRequest++
  playerPlaying.value = false
  playbackStarting.value = false
  poll.stop()
  actionController?.abort()
  pending.value = false
  selectedId.value = ''
  playback.value = null
  versions.value = []
  exports.value = []
  originalAvailable.value = false
  loaded.value = false
  refresh()
}, { immediate: true })
watch(() => props.voiceApplying, () => { if (!pending.value) { poll.stop(); poll.start() } })
onBeforeUnmount(() => { disposed = true; session++; playbackRequest++; playerPlaying.value = false; playbackStarting.value = false; poll.stop(); actionController?.abort(); emit('playing', false); emit('processing', false) })
</script>

<template>
  <section class="space-y-3" :aria-label="t('trackAudio.title')">
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-xs font-medium text-text-dim">{{ t('trackAudio.title') }}</span>
      <button v-for="version in versions" :key="version.id" type="button" :aria-pressed="selectedId === version.id" :disabled="pending" class="rounded-lg border border-border px-3 py-1 text-xs disabled:opacity-50" :class="selectedId === version.id ? 'bg-accent/20 text-accent1' : 'bg-panel-2 text-text-dim'" @click="choose(version)">
        {{ label(version) }}<span v-if="version.status !== 'done'"> · {{ statusLabel(version) }}</span>
      </button>
      <button type="button" :disabled="pending || loading" class="ml-auto text-xs text-accent1 hover:underline disabled:opacity-50" @click="refresh">{{ t('trackAudio.refresh') }}</button>
    </div>
    <p v-if="loading && !loaded" class="text-xs text-text-dim">{{ t('common.loading') }}</p>
    <p v-if="error" role="alert" class="text-xs text-status-failed">{{ error }}</p>
    <p v-if="loaded && !originalAvailable" class="text-xs text-text-dim">{{ t('trackAudio.originalMissing') }}</p>
    <div v-for="version in visibleVoiceJobs" :key="`progress-${version.id}`" class="space-y-2 rounded-lg border border-border bg-panel-2 p-3" :data-voice-version-progress="version.id">
      <VoiceApplyStatus :voice-name="label(version)" :phase="version.status === 'queued' ? 'queued' : undefined" :job-progress="version.job_progress" />
      <button v-if="active(version.status)" type="button" :disabled="pending" :aria-label="t('trackAudio.progress.cancelVoice', { name: label(version) })" class="text-xs text-accent1 hover:underline disabled:opacity-50" @click="versionAction(version, 'cancel')">{{ t('common.cancel') }}</button>
    </div>
    <div v-if="selected?.status === 'done' && selected.audio_url" role="group" :aria-label="t('trackAudio.playbackFormat')" class="flex flex-wrap items-center gap-2">
      <span class="text-xs text-text-dim">{{ t('trackAudio.playbackFormat') }}</span>
      <button type="button" :aria-pressed="playback?.version.id === selected.id && !playback.export" :disabled="pending" class="rounded-lg border border-border px-3 py-1 text-xs disabled:opacity-50" :class="playback?.version.id === selected.id && !playback.export ? 'bg-accent/20 text-accent1' : 'bg-panel-2 text-text-dim'" @click="playFile(selected)">{{ sourceLabel(selected) }}</button>
      <button v-for="item in readyExports" :key="item.id" type="button" :aria-pressed="playback?.export?.id === item.id" :disabled="pending" class="rounded-lg border border-border px-3 py-1 text-xs disabled:opacity-50" :class="playback?.export?.id === item.id ? 'bg-accent/20 text-accent1' : 'bg-panel-2 text-text-dim'" @click="playFile(selected, item)">{{ quality(item) }}</button>
    </div>
    <p v-if="playable" class="text-xs text-text-dim">{{ t('trackAudio.selectedAudio', { selection: playbackLabel }) }}</p>
    <WaveformPlayer v-if="playable" ref="player" :src="playable" @playing="notePlaying" />
    <a v-if="playable" :href="playable" :download="playbackFilename ?? ''" class="inline-block text-xs text-accent1 hover:underline">{{ t('trackAudio.downloadSelected', { format: playbackFormat }) }}</a>
    <TaggedAudioDownload v-if="playback" class="ml-3" :track-id="trackId" :version-id="playback.version.id" :export-id="playback.export?.id" />
    <div v-if="selected && selected.status !== 'done' && !active(selected.status)" class="flex flex-wrap items-center gap-2 text-xs" aria-live="polite">
      <StatusBadge :status="selected.status" />
      <span v-if="selected.error_code" class="text-status-failed">{{ te(`trackAudio.errors.${selected.error_code}`) ? t(`trackAudio.errors.${selected.error_code}`) : t('trackAudio.errors.unknown') }}</span>
      <button v-if="selected.kind === 'voice' && selected.voice_id" type="button" :disabled="pending" class="text-accent1 hover:underline disabled:opacity-50" @click="versionAction(selected, 'retry')">{{ t('trackAudio.retry') }}</button>
    </div>
    <details class="rounded-lg border border-border bg-panel-2 p-2">
      <summary class="cursor-pointer text-xs font-medium text-text">{{ t('trackAudio.manage') }}</summary>
      <div class="mt-3 space-y-3">
        <p class="text-xs text-text-dim">{{ t('trackAudio.voiceHint') }}</p>
        <p v-if="voicesUnavailable" role="status" class="text-xs text-status-failed">{{ t('trackAudio.voicesUnavailable') }}</p>
        <div class="flex flex-wrap items-end gap-2">
          <label class="min-w-36 flex-1 space-y-1 text-xs text-text-dim"><span class="block">{{ t('trackAudio.voice') }}</span><select v-model="voiceId" :disabled="pending || !originalAvailable" class="w-full rounded-lg border border-border bg-panel p-2 text-text"><option v-if="!readyVoices.length" value="">{{ t('trackAudio.noVoices') }}</option><option v-for="voice in readyVoices" :key="voice.id" :value="voice.id">{{ voice.name }}</option></select></label>
          <button type="button" :disabled="pending || !originalAvailable || !voiceId" class="rounded-lg border border-border bg-panel px-3 py-2 text-xs text-accent1 disabled:opacity-50" @click="addVoice">{{ t('trackAudio.addVoice') }}</button>
          <RouterLink to="/voice-clone" class="text-xs text-accent1 hover:underline">{{ t('voiceClone.manage') }}</RouterLink>
        </div>
        <div class="flex flex-wrap items-end gap-2">
          <label class="space-y-1 text-xs text-text-dim"><span class="block">{{ t('trackAudio.format') }}</span><select v-model="format" :disabled="pending" class="rounded-lg border border-border bg-panel p-2 text-text"><option value="mp3">MP3</option><option value="wav">WAV</option><option value="flac">FLAC</option></select></label>
          <button type="button" :disabled="pending || selected?.status !== 'done'" class="rounded-lg border border-border bg-panel px-3 py-2 text-xs text-accent1 disabled:opacity-50" @click="addExport">{{ t('trackAudio.export') }}</button>
          <RouterLink to="/settings" class="text-xs text-accent1 hover:underline">{{ t('trackAudio.settings') }}</RouterLink>
        </div>
        <p class="text-xs text-text-dim">{{ t('trackAudio.exportHint') }}</p>
        <ul v-if="exports.length" class="space-y-2">
          <li v-for="item in exports" :key="item.id" class="flex flex-wrap items-center gap-2 text-xs">
            <span class="text-text-dim">{{ quality(item) }}</span>
            <a v-if="item.status === 'done' && item.audio_url" :href="item.audio_url" :download="item.filename ?? ''" class="text-accent1 hover:underline">{{ t('common.download') }}</a>
            <template v-else><StatusBadge :status="item.status" /><span v-if="item.error_code" class="text-status-failed">{{ te(`trackAudio.errors.${item.error_code}`) ? t(`trackAudio.errors.${item.error_code}`) : t('trackAudio.errors.unknown') }}</span><button type="button" :disabled="pending" class="text-accent1 hover:underline disabled:opacity-50" @click="exportAction(item, active(item.status) ? 'cancel' : 'retry')">{{ active(item.status) ? t('common.cancel') : t('trackAudio.retry') }}</button></template>
          </li>
        </ul>
      </div>
    </details>
  </section>
</template>

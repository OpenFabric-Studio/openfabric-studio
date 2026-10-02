<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/references'
import * as tracksApi from '../../api/tracks'
import { languageLabel } from '../../utils/languageLabel'
import { ApiError } from '../../api/http'
import type { ReferenceCapabilities, ReferenceImport, ReferenceSource, ReferenceToolCapability, ReferenceImportRequest, ReferenceLyricsRequest, SavedTrack } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'
import TrackAudioVersions from './TrackAudioVersions.vue'
import HelpModal from './HelpModal.vue'
const props = withDefaults(defineProps<{ engine: 'ace_step' | 'yue2'; melodyDefault?: boolean }>(), { melodyDefault: false })
const emit = defineEmits<{ applyLyrics: [text: string, importId: string | null]; applyAbc: [text: string, importId: string | null] }>()
const { t, te, locale } = useI18n()
const tab = ref<'reference' | 'lyricsTool' | 'abcTool'>('reference')
const sourceKind = ref<'url' | 'upload' | 'saved'>('url')
const url = ref(''); const source = ref<ReferenceSource | null>(null); const title = ref(''); const file = ref<File | null>(null)
const trackId = ref<number | null>(null); const tracks = ref<SavedTrack[]>([])
const caps = ref<ReferenceCapabilities | null>(null); const jobs = ref<ReferenceImport[]>([])
const lyricsSource = ref<NonNullable<ReferenceImportRequest['lyrics_source']>>('none')
const subtitleLanguage = ref(''); const separation = ref<NonNullable<ReferenceImportRequest['separation']>>('none'); const melody = ref(props.melodyDefault)
const selectedId = ref<string | null>(null); const reviewId = ref<string | null>(null); const reviewedLyrics = ref(''); const reviewedAbc = ref('')
const lyricsReviewed = ref(false); const abcReviewed = ref(false)
const deleting = ref<ReferenceImport | null>(null)
const text = ref(''); const format = ref<NonNullable<ReferenceLyricsRequest['format']>>('plain'); const language = ref(''); const sectionSize = ref(4)
const abc = ref(''); const transpose = ref(0); const tempo = ref<number | null>(null); const minMidi = ref<number | null>(null); const maxMidi = ref<number | null>(null)
const busy = ref(false); const error = ref(''); const toolSummary = ref('')
let alive = true; let generation = 0; let controller: AbortController | null = null
const active = (row: ReferenceImport) => row.status === 'queued' || row.status === 'running'
const selected = computed(() => jobs.value.find(row => row.id === selectedId.value))
const validSource = computed(() => sourceKind.value === 'url' ? !!source.value && caps.value?.source_import.available : sourceKind.value === 'upload' ? !!file.value : trackId.value !== null)
const canPrepare = computed(() => validSource.value && !!caps.value && (lyricsSource.value !== 'subtitles' || sourceKind.value === 'url' && !!subtitleLanguage.value) && (!melody.value || caps.value.melody.available) && (lyricsSource.value !== 'whisper' || caps.value.whisper.available) && (lyricsSource.value !== 'subtitles' || caps.value.subtitles.available) && (separation.value === 'none' || caps.value.separation.some(tool => tool.id === separation.value && tool.available)))
function errorText(cause: unknown) { const code = cause instanceof ApiError ? cause.message : ''; return code && te(`referenceWorkspace.errors.${code}`) ? t(`referenceWorkspace.errors.${code}`) : t('referenceWorkspace.error') }
function reason(capability: ReferenceToolCapability | undefined) { return capability?.reason && te(`referenceWorkspace.errors.${capability.reason}`) ? t(`referenceWorkspace.errors.${capability.reason}`) : '' }
function upsert(row: ReferenceImport) { jobs.value = [row, ...jobs.value.filter(item => item.id !== row.id)] }
const poll = createPollingLoop(async context => {
  const token = generation
  try { const response = await api.list(context.signal); if (!context.isCurrent() || !alive || token !== generation) return false; jobs.value = response }
  catch (cause) { if (context.isCurrent() && alive && token === generation) error.value = errorText(cause) }
  return jobs.value.some(active)
}, 2000)
async function perform(action: (signal: AbortSignal, current: () => boolean) => Promise<void>) {
  if (busy.value || !alive) return
  const token = ++generation; poll.stop(); controller?.abort(); const request = new AbortController(); controller = request
  const current = () => alive && token === generation && !request.signal.aborted
  busy.value = true; error.value = ''
  try { await action(request.signal, current) } catch (cause) { if (current()) error.value = errorText(cause) }
  finally { if (current()) { busy.value = false; if (jobs.value.some(active)) poll.start(false) } }
}
async function load() {
  await perform(async (signal, current) => {
    const results = await Promise.allSettled([api.capabilities(signal), api.list(signal), tracksApi.listTracks(undefined, signal)])
    if (!current()) return
    const [capabilities, imports, savedTracks] = results
    if (capabilities.status === 'fulfilled') { caps.value = capabilities.value; if (!caps.value.melody.available) melody.value = false } else error.value = t('referenceWorkspace.capabilityFailed')
    if (imports.status === 'fulfilled') jobs.value = imports.value; else error.value = errorText(imports.reason)
    if (savedTracks.status === 'fulfilled') tracks.value = savedTracks.value; else error.value = errorText(savedTracks.reason)
  })
}
function onFile(event: Event) { file.value = event.target instanceof HTMLInputElement ? event.target.files?.[0] ?? null : null }
function inspect() {
  const requested = url.value.trim()
  void perform(async (signal, current) => { const result = await api.probe(requested, signal); if (!current() || requested !== url.value.trim()) return; source.value = result; title.value = result.title; subtitleLanguage.value = result.subtitle_languages?.[0] ?? '' })
}
function prepare() {
  if (!canPrepare.value) return
  const kind = sourceKind.value; const pickedFile = file.value; const chosenTrack = trackId.value; const originalSource = source.value
  const settings = { lyrics_source: lyricsSource.value, separation: separation.value, melody: melody.value, subtitle_language: subtitleLanguage.value || null }
  void perform(async (signal, current) => {
    let result: ReferenceImport
    if (kind === 'url' && originalSource) result = await api.submit({ kind: 'url', url: originalSource.canonical_url, title: title.value.trim(), ...settings }, signal)
    else {
      let savedId = chosenTrack
      if (kind === 'upload' && pickedFile) { const track = await tracksApi.uploadTrack(pickedFile, title.value.trim() || undefined, signal); if (!current()) return; savedId = track.id; tracks.value = [track, ...tracks.value.filter(row => row.id !== track.id)]; trackId.value = track.id }
      if (savedId === null || settings.lyrics_source === 'subtitles') return
      result = await api.prepare({ kind: 'track', track_id: savedId, ...settings, lyrics_source: settings.lyrics_source }, signal)
    }
    if (!current()) return
    upsert(result); selectedId.value = result.id
  })
}
function jobAction(row: ReferenceImport, kind: 'cancel' | 'retry' | 'delete') {
  void perform(async (signal, current) => { if (kind === 'delete') { await api.remove(row.id, signal); if (current()) { jobs.value = jobs.value.filter(item => item.id !== row.id); if (selectedId.value === row.id) selectedId.value = null; deleting.value = null } } else { const result = await api[kind](row.id, signal); if (current()) upsert(result) } })
}
function review(row: ReferenceImport) { selectedId.value = row.id; reviewId.value = row.id; reviewedLyrics.value = row.lyrics?.lyrics ?? ''; reviewedAbc.value = row.abc ?? ''; lyricsReviewed.value = !!row.lyrics; abcReviewed.value = !!row.abc; toolSummary.value = '' }
function transformLyrics() { void perform(async (signal, current) => { const result = await api.transformLyrics({ text: text.value, format: format.value, language: language.value || null, section_size: sectionSize.value }, signal); if (!current()) return; reviewId.value = null; selectedId.value = null; reviewedLyrics.value = result.lyrics; reviewedAbc.value = ''; lyricsReviewed.value = true; abcReviewed.value = false; toolSummary.value = result.warnings?.join(' · ') ?? '' }) }
function optionalNumber(value: number | null): number | null { return typeof value === 'number' && Number.isFinite(value) ? value : null }
function transformAbc() { void perform(async (signal, current) => { const result = await api.transformAbc({ abc: abc.value, transpose_semitones: transpose.value, tempo_bpm: optionalNumber(tempo.value), target_min_midi: optionalNumber(minMidi.value), target_max_midi: optionalNumber(maxMidi.value) }, signal); if (!current()) return; reviewId.value = null; selectedId.value = null; reviewedLyrics.value = ''; reviewedAbc.value = result.abc; lyricsReviewed.value = false; abcReviewed.value = true; toolSummary.value = `${result.note_count} · MIDI ${result.min_midi}–${result.max_midi} · ${result.applied_transpose_semitones >= 0 ? '+' : ''}${result.applied_transpose_semitones}` }) }
watch(url, () => { source.value = null })
watch(sourceKind, kind => { if (kind !== 'url' && lyricsSource.value === 'subtitles') lyricsSource.value = 'none' })
onMounted(() => { void load() })
onBeforeUnmount(() => { alive = false; generation++; controller?.abort(); poll.stop() })
</script>
<template>
  <section class="space-y-4" :aria-busy="busy">
    <div class="flex flex-wrap gap-2" role="group" :aria-label="t('referenceWorkspace.title')"><button v-for="item in (['reference', 'lyricsTool', 'abcTool'] as const)" :key="item" type="button" :disabled="busy || item === 'abcTool' && engine !== 'yue2'" :aria-pressed="tab === item" class="rounded-lg border border-border px-3 py-2 text-xs disabled:opacity-40" :class="tab === item ? 'text-accent1 bg-panel-2' : 'text-text-dim'" @click="tab = item">{{ t(`referenceWorkspace.${item}`) }}</button></div>
    <p v-if="error" role="alert" class="text-xs text-status-failed">{{ error }}</p>
    <div v-if="tab === 'reference'" class="space-y-3">
      <p class="text-xs text-text-dim">{{ t('referenceWorkspace.originalRetained') }}</p>
      <label class="block text-xs text-text-dim">{{ t('referenceWorkspace.source') }}<select v-model="sourceKind" :disabled="busy" class="ml-2 rounded border border-border bg-panel-2 p-2"><option value="url">{{ t('referenceWorkspace.url') }}</option><option value="upload">{{ t('referenceWorkspace.upload') }}</option><option value="saved">{{ t('referenceWorkspace.saved') }}</option></select></label>
      <div v-if="sourceKind === 'url'" class="space-y-2">
        <label class="block text-xs text-text-dim">{{ t('referenceWorkspace.url') }}<input v-model="url" type="url" maxlength="2048" :disabled="busy" class="mt-1 block w-full rounded-lg border border-border bg-panel-2 p-2 text-text" placeholder="https://www.youtube.com/watch?v=…" /></label>
        <button type="button" :disabled="busy || !url.trim() || !caps?.source_import.available" class="text-xs text-accent1 underline disabled:opacity-50" @click="inspect">{{ t('referenceWorkspace.inspect') }}</button>
        <p v-if="!caps?.source_import.available" class="text-xs text-text-dim">{{ reason(caps?.source_import) }} {{ caps?.source_import.setup_hint }}</p>
        <p v-if="source" class="text-xs text-text">{{ source.title }} · {{ t('referenceWorkspace.duration', { seconds: Math.round(source.duration_seconds) }) }}</p>
      </div>
      <div v-else-if="sourceKind === 'upload'" class="space-y-2"><input type="file" accept="audio/*" :disabled="busy" :aria-label="t('referenceWorkspace.upload')" class="w-full text-xs text-text-dim" @change="onFile" /><p class="text-xs text-text-dim">{{ t('referenceWorkspace.uploadHint') }}</p></div>
      <label v-else class="block text-xs text-text-dim">{{ t('referenceWorkspace.saved') }}<select v-model="trackId" :disabled="busy" class="mt-1 block w-full rounded border border-border bg-panel-2 p-2 text-text"><option :value="null">{{ t('referenceWorkspace.none') }}</option><option v-for="track in tracks" :key="track.id" :value="track.id">{{ track.title }}</option></select></label>
      <label class="block text-xs text-text-dim">{{ t('referenceWorkspace.sourceTitle') }}<input v-model="title" maxlength="500" :disabled="busy" class="mt-1 block w-full rounded border border-border bg-panel-2 p-2 text-text" /></label>
      <label class="block text-xs text-text-dim">{{ t('referenceWorkspace.lyricsSource') }}<select v-model="lyricsSource" :disabled="busy" class="ml-2 rounded border border-border bg-panel-2 p-2 text-text"><option value="none">{{ t('referenceWorkspace.none') }}</option><option value="subtitles" :disabled="sourceKind !== 'url' || !caps?.subtitles.available || !source?.subtitle_languages?.length">{{ t('referenceWorkspace.subtitles') }}</option><option value="whisper" :disabled="!caps?.whisper.available">{{ t('referenceWorkspace.whisper') }}</option></select></label>
      <label v-if="lyricsSource === 'subtitles'" class="block text-xs text-text-dim">{{ t('referenceWorkspace.language') }}<select v-model="subtitleLanguage" :disabled="busy" class="ml-2 rounded border border-border bg-panel-2 p-2 text-text"><option v-for="lang in source?.subtitle_languages ?? []" :key="lang" :value="lang">{{ languageLabel(lang, locale, lang) }}</option></select></label>
      <p v-if="caps && !caps.whisper.available" class="text-xs text-text-dim">{{ reason(caps.whisper) }} {{ caps.whisper.setup_hint }}</p>
      <label class="block text-xs text-text-dim">{{ t('referenceWorkspace.separation') }}<select v-model="separation" :disabled="busy" class="ml-2 rounded border border-border bg-panel-2 p-2 text-text"><option value="none">{{ t('referenceWorkspace.none') }}</option><option v-for="tool in caps?.separation ?? []" :key="tool.id" :value="tool.id" :disabled="!tool.available">{{ tool.id }}</option></select></label>
      <p v-for="tool in caps?.separation.filter(item => !item.available) ?? []" :key="tool.id" class="text-xs text-text-dim">{{ tool.id }}: {{ reason(tool) }} {{ tool.setup_hint }}</p>
      <label v-if="engine === 'yue2'" class="flex items-center gap-2 text-xs text-text-dim"><input v-model="melody" type="checkbox" :disabled="busy || !caps?.melody.available" />{{ t('referenceWorkspace.melody') }}</label>
      <p v-if="engine === 'yue2' && caps && !caps.melody.available" class="text-xs text-text-dim">{{ reason(caps.melody) }} {{ caps.melody.setup_hint }}</p>
      <button type="button" :disabled="busy || !canPrepare" class="accent-gradient rounded-lg px-3 py-2 text-sm text-white disabled:opacity-50" @click="prepare">{{ t('referenceWorkspace.prepare') }}</button>
      <button type="button" :disabled="busy" class="ml-2 text-xs text-accent1 underline disabled:opacity-50" @click="load">{{ t('generationWorkspace.reload') }}</button>
      <ul class="space-y-2"><li v-for="row in jobs" :key="row.id" class="space-y-2 rounded-lg border border-border bg-panel-2 p-3"><p class="text-sm text-text">{{ row.source?.title || ('url' in row.request ? row.request.title || row.request.url : tracks.find(track => track.id === row.track_id)?.title || `#${row.request.track_id}`) }} · {{ t(`referenceWorkspace.${row.status === 'done' ? 'ready' : row.status}`) }}</p><ul class="text-xs text-text-dim"><li v-for="stage in row.stages" :key="stage.name">{{ t(`referenceWorkspace.stages.${stage.name}`) }}: {{ t(`referenceWorkspace.stageStatus.${stage.status}`) }}<span v-if="stage.error_code" class="text-status-failed"> · {{ t(`referenceWorkspace.errors.${stage.error_code}`) }}</span></li></ul><div class="flex flex-wrap gap-3 text-xs"><button type="button" :disabled="busy" class="text-accent1 underline disabled:opacity-50" @click="review(row)">{{ t('referenceWorkspace.review') }}</button><button v-if="active(row)" type="button" :disabled="busy" class="text-accent1 underline disabled:opacity-50" @click="jobAction(row, 'cancel')">{{ t('generationWorkspace.cancel') }}</button><template v-else><button v-if="row.status !== 'done'" type="button" :disabled="busy" class="text-accent1 underline disabled:opacity-50" @click="jobAction(row, 'retry')">{{ t('trackAudio.retry') }}</button><button type="button" :disabled="busy" class="text-accent1 underline disabled:opacity-50" @click="deleting = row">{{ t('referenceWorkspace.delete') }}</button></template></div></li></ul>
    </div>
    <form v-else-if="tab === 'lyricsTool'" class="space-y-3" @submit.prevent="transformLyrics"><label class="block text-xs text-text-dim">{{ t('referenceWorkspace.text') }}<textarea v-model="text" rows="7" maxlength="100000" :disabled="busy" class="mt-1 block w-full rounded-lg border border-border bg-panel-2 p-2 font-mono text-text"></textarea></label><div class="flex flex-wrap gap-3"><label class="text-xs">{{ t('referenceWorkspace.format') }}<select v-model="format" class="ml-2 rounded border border-border bg-panel-2 p-2 text-text"><option value="plain">Plain text</option><option value="srt">SRT</option><option value="vtt">VTT</option></select></label><label class="text-xs">{{ t('referenceWorkspace.sectionSize') }}<input v-model.number="sectionSize" type="number" min="1" max="32" class="ml-2 w-16 rounded border border-border bg-panel-2 p-2 text-text" /></label><label class="text-xs">{{ t('referenceWorkspace.languageHint') }}<input v-model="language" maxlength="35" class="ml-2 w-24 rounded border border-border bg-panel-2 p-2 text-text" /></label></div><button type="submit" :disabled="busy || !text.trim()" class="accent-gradient rounded-lg px-3 py-2 text-sm text-white disabled:opacity-50">{{ t('referenceWorkspace.transform') }}</button></form>
    <form v-else class="space-y-3" @submit.prevent="transformAbc"><label class="block text-xs">ABC<textarea v-model="abc" rows="7" maxlength="100000" :disabled="busy" class="mt-1 block w-full rounded-lg border border-border bg-panel-2 p-2 font-mono text-text"></textarea></label><div class="grid grid-cols-2 gap-3"><label class="text-xs">{{ t('referenceWorkspace.transpose') }}<input v-model.number="transpose" type="number" min="-24" max="24" class="mt-1 block w-full rounded border border-border bg-panel-2 p-2 text-text" /></label><label class="text-xs">{{ t('referenceWorkspace.tempo') }}<input v-model.number="tempo" type="number" min="20" max="300" class="mt-1 block w-full rounded border border-border bg-panel-2 p-2 text-text" /></label><label class="text-xs">{{ t('referenceWorkspace.range') }} min<input v-model.number="minMidi" type="number" min="24" max="96" class="mt-1 block w-full rounded border border-border bg-panel-2 p-2 text-text" /></label><label class="text-xs">{{ t('referenceWorkspace.range') }} max<input v-model.number="maxMidi" type="number" min="24" max="96" class="mt-1 block w-full rounded border border-border bg-panel-2 p-2 text-text" /></label></div><p class="text-xs">{{ t('referenceWorkspace.rangeHint') }}</p><button type="submit" :disabled="busy || !abc.trim()" class="accent-gradient rounded-lg px-3 py-2 text-sm text-white disabled:opacity-50">{{ t('referenceWorkspace.transform') }}</button></form>
    <div v-if="selected || lyricsReviewed || abcReviewed" class="space-y-3 border-t border-border pt-3">
      <h3 class="text-sm font-semibold text-text">{{ t('referenceWorkspace.output') }}</h3><p class="text-xs">{{ t('referenceWorkspace.reviewHint') }}</p>
      <TrackAudioVersions v-if="selected?.track_id && selected.audio_url" :key="selected.track_id" :track-id="selected.track_id" :fallback-audio-url="selected.audio_url" />
      <p v-else-if="selected?.status === 'done' || selected?.status === 'partial'" class="text-xs text-text-dim">{{ t('referenceWorkspace.sourceMissing') }}</p>
      <details v-if="selected?.lyrics?.lines.length" class="text-xs"><summary>{{ t('referenceWorkspace.subtitles') }}</summary><p v-for="(line, index) in selected.lyrics.lines" :key="index" class="py-1"><span v-if="line.start_seconds != null && line.end_seconds != null">{{ line.start_seconds.toFixed(2) }}–{{ line.end_seconds.toFixed(2) }}s · </span><span v-if="line.language">{{ line.language }} · </span>{{ line.text }}</p></details>
      <p v-if="toolSummary" class="text-xs text-text-dim">{{ toolSummary }}</p>
      <div v-if="lyricsReviewed" class="space-y-2"><label class="block text-xs text-text-dim">{{ t('referenceWorkspace.reviewedLyrics') }}<textarea v-model="reviewedLyrics" :aria-label="t('referenceWorkspace.reviewedLyrics')" rows="6" maxlength="100000" class="mt-1 block w-full rounded-lg border border-border bg-panel-2 p-2 font-mono text-text"></textarea></label><button type="button" :disabled="busy || !reviewedLyrics.trim()" class="text-xs text-accent1 underline disabled:opacity-50" @click="emit('applyLyrics', reviewedLyrics, reviewId)">{{ t('referenceWorkspace.applyLyrics') }}</button></div>
      <div v-if="abcReviewed && engine === 'yue2'" class="space-y-2"><label class="block text-xs text-text-dim">{{ t('referenceWorkspace.reviewedAbc') }}<textarea v-model="reviewedAbc" :aria-label="t('referenceWorkspace.reviewedAbc')" rows="6" maxlength="100000" class="mt-1 block w-full rounded-lg border border-border bg-panel-2 p-2 font-mono text-text"></textarea></label><button type="button" :disabled="busy || !reviewedAbc.trim()" class="text-xs text-accent1 underline disabled:opacity-50" @click="emit('applyAbc', reviewedAbc, reviewId)">{{ t('referenceWorkspace.applyAbc') }}</button></div>
    </div>
    <HelpModal :open="deleting !== null" :title="t('referenceWorkspace.confirmDelete')" @close="deleting = null"><p>{{ t('referenceWorkspace.retained') }}</p><button type="button" :disabled="busy" class="rounded-lg border border-border px-3 py-2 text-status-failed" @click="deleting && jobAction(deleting, 'delete')">{{ t('referenceWorkspace.delete') }}</button></HelpModal>
  </section>
</template>

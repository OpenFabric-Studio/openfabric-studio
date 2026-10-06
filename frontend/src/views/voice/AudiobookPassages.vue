<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/audiobookWorkflow'
import * as videosApi from '../../api/videos'
import { ApiError } from '../../api/http'
import type { AudiobookPassagesResponse, AudiobookRepair, AudiobookPassage } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'
import PassageAsrReview from './PassageAsrReview.vue'
import SpeakerPassageReview from './SpeakerPassageReview.vue'
import PauseAnalysisSettings from './PauseAnalysisSettings.vue'
import CloudSpeechCost from './CloudSpeechCost.vue'
import {quoteRepair} from '../../api/cloudSpeech'
import type { CloudSpeechApproval } from '../../api/contracts'
const props = withDefaults(defineProps<{ bookId: string; chapterIndex: number; active?: boolean; playbackSeconds?: number; chapterRevision?: number }>(), { active: true, playbackSeconds: 0 })
const emit = defineEmits<{ updated: [] }>()
const { t } = useI18n()
const expanded = ref(false), data = ref<AudiobookPassagesResponse | null>(null), selectedId = ref('')
const restoring = ref(false)
const seconds = ref(0), text = ref(''), loading = ref(false), busy = ref(false), accepting = ref(false), error = ref(''), notice = ref('')
const repair = ref<AudiobookRepair | null>(null), mediaRoot = ref<HTMLElement | null>(null)
type ReelClip = { passage_id: string; start: number; end: number; prompt: string; caption: string }
const reelClips = ref<ReelClip[]>([]), reelName = ref(''), reelId = ref(''), reelBusy = ref(false)
const cloudApproval=ref<CloudSpeechApproval|null>(null),costNonce=ref(0)
function quoteTake(signal:AbortSignal){return quoteRepair(props.bookId,props.chapterIndex,selectedId.value,{revision:data.value?.revision??0,text:text.value.trim()},signal)}
const selected = computed(() => data.value?.passages.find(passage => passage.id === selectedId.value))
const running = computed(() => repair.value?.status === 'queued' || repair.value?.status === 'running')
let alive = true, generation = 0
let controller: AbortController | undefined
const controllers = new Set<AbortController>()
function pause() { for (const player of mediaRoot.value?.querySelectorAll('audio') ?? []) player.pause() }
function context() {
  const token = generation, request = new AbortController(); controllers.add(request)
  return { signal: request.signal, current: () => alive && !request.signal.aborted && token === generation, finish: () => controllers.delete(request) }
}
const polling = createPollingLoop(async ({ signal, isCurrent }) => {
  const candidate = repair.value, token = generation
  if (!candidate || !props.active || !expanded.value) return false
  try {
    const result = await api.getRepair(candidate.id, signal)
    if (!alive || token !== generation || !isCurrent() || result.id !== candidate.id) return false
    cloudApproval.value=null
    repair.value = result
    if (result.status === 'failed') error.value = t('audiobookReview.repairFailed')
    return result.status === 'queued' || result.status === 'running'
  } catch { if (alive && token === generation && isCurrent()) error.value = t('audiobookReview.repairFailed'); return false }
}, 2000)
function choose(passage: AudiobookPassage) {
  if (busy.value || accepting.value || running.value) return
  controller?.abort(); polling.stop(); pause()
  selectedId.value = passage.id; text.value = passage.text; repair.value = null; error.value = ''; notice.value = ''
  void restoreTakes(passage)
}
async function restoreTakes(passage: AudiobookPassage) {
  const revision = data.value?.revision
  if (!revision) return
  const token = generation, request = new AbortController(); controller = request; restoring.value = true
  try {
    const rows = await api.listPassageRepairs(props.bookId, props.chapterIndex, passage.id, revision, request.signal)
    if (!alive || token !== generation || request.signal.aborted || selectedId.value !== passage.id || data.value?.revision !== revision) return
    repair.value = rows.filter(item => item.book_id === props.bookId && item.passage_id === passage.id && item.revision === revision && ['ready', 'queued', 'running'].includes(item.status)).sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null
    if (running.value && props.active && expanded.value) polling.start(false)
  } catch { if (alive && token === generation && !request.signal.aborted) error.value = t('audiobookReview.loadFailed') }
  finally { if (alive && token === generation && !request.signal.aborted) restoring.value = false }
}
function find() {
  const time = seconds.value * 1000
  const passage = data.value?.passages.find(item => item.status === 'done' && time >= item.start_ms && time < item.end_ms)
  if (passage) choose(passage)
  else error.value = t('audiobookReview.seekFailed')
}
async function load() {
  if (loading.value || busy.value || accepting.value) return
  const action = context(); loading.value = true; error.value = ''
  try {
    const result = await api.listPassages(props.bookId, props.chapterIndex, action.signal)
    if (!action.current() || result.book_id !== props.bookId || result.chapter_index !== props.chapterIndex) return
    data.value = result
    const passage = result.passages.find(item => item.id === selectedId.value) ?? result.passages.find(item => item.status === 'done')
    if (passage) choose(passage)
  } catch { if (action.current()) error.value = t('audiobookReview.loadFailed') }
  finally { action.finish(); if (action.current()) loading.value = false }
}
function toggle() {
  expanded.value = !expanded.value
  if (expanded.value) { if (!data.value) void load(); else if (running.value && props.active) polling.start() }
  else { pause(); polling.stop() }
}
async function freshTake() {
  const passage = selected.value, revision = data.value?.revision
  if (!passage || !revision || passage.status !== 'done' || busy.value || accepting.value || running.value || restoring.value || !text.value.trim()) return
  const action = context(); busy.value = true; error.value = ''; notice.value = ''; repair.value = null; pause()
  try {
    if(passage.renderer==='openrouter'&&!cloudApproval.value){error.value=t('cloudSpeech.approvalRequired');return}
    const approval=cloudApproval.value
    if(passage.renderer==='openrouter')costNonce.value++
    const result = await api.createRepair(props.bookId, props.chapterIndex, passage.id, { revision, text: text.value.trim(),...(approval?{cloud_approval:approval}:{}) }, action.signal)
    if (!action.current() || result.book_id !== props.bookId || result.passage_id !== passage.id || result.revision !== revision) return
    repair.value = result
    if (result.status === 'failed') error.value = t('audiobookReview.repairFailed')
    if (running.value && props.active && expanded.value) polling.start(false)
  } catch (err) { if (action.current()) error.value = t(err instanceof ApiError && err.message==='cloud_speech_submission_unknown'?'cloudSpeech.unknown':err instanceof ApiError && err.message.startsWith('cloud_')?'cloudSpeech.changed':err instanceof ApiError && err.status === 409 ? 'audiobookReview.stale' : 'audiobookReview.repairFailed') }
  finally { action.finish(); if (action.current()) busy.value = false }
}
async function accept() {
  const candidate = repair.value
  if (!candidate || candidate.status !== 'ready' || accepting.value || busy.value || !data.value) return
  const action = context(); accepting.value = true; error.value = ''; pause()
  try {
    const result = await api.acceptRepair(candidate.id, data.value.revision, action.signal)
    if (!action.current() || result.book_id !== props.bookId || result.chapter_index !== props.chapterIndex) return
    data.value = result; repair.value = null; reelClips.value = []; reelId.value = ''
    const passage = result.passages.find(item => item.id === selectedId.value)
    if (passage) text.value = passage.text
    notice.value = t('audiobookReview.accepted'); emit('updated')
  } catch (err) { if (action.current()) error.value = t(err instanceof ApiError && err.status === 409 ? 'audiobookReview.stale' : 'audiobookReview.acceptFailed') }
  finally { action.finish(); if (action.current()) accepting.value = false }
}
async function keep() {
  const candidate = repair.value
  if (busy.value || accepting.value) return
  if (!candidate || candidate.status !== 'ready' && !running.value) { repair.value = null; pause(); return }
  const action = context(); busy.value = true; polling.stop(); pause()
  try { const result = await api.cancelRepair(candidate.id, action.signal); if (action.current() && result.id === candidate.id) repair.value = result.status === 'cancelled' ? null : result }
  catch { if (action.current()) error.value = t('audiobookReview.discardFailed') }
  finally { action.finish(); if (action.current()) { busy.value = false; if (running.value && props.active && expanded.value) polling.start(false) } }
}
function toggleClip(passage: AudiobookPassage) {
  const index = reelClips.value.findIndex(item => item.passage_id === passage.id)
  if (index >= 0) reelClips.value.splice(index, 1)
  else if (reelClips.value.length < 4) reelClips.value.push({ passage_id: passage.id, start: 0, end: Math.min(6, (passage.end_ms - passage.start_ms) / 1000), prompt: '', caption: (passage.end_ms - passage.start_ms) <= 6000 && passage.text.length <= 500 ? passage.text : '' })
}
async function createReel() {
  if (!data.value || !reelClips.value.length || reelBusy.value) return
  const action = context(); reelBusy.value = true; error.value = ''; reelId.value = ''
  try {
    const result = await videosApi.createDialogueReel({ book_id: props.bookId, chapter_index: props.chapterIndex, revision: data.value.revision, name: reelName.value.trim() || 'Dialogue reel', selections: reelClips.value.map(clip => ({ passage_id: clip.passage_id, clip_start_ms: Math.round(clip.start * 1000), clip_end_ms: Math.round(clip.end * 1000), prompt: clip.prompt.trim() || 'A character speaking naturally', ...(clip.caption.trim() ? { caption: clip.caption.trim() } : {}) })) }, action.signal)
    if (action.current()) reelId.value = result.id
  } catch { if (action.current()) error.value = t('audiobookReview.reelFailed') }
  finally { action.finish(); if (action.current()) reelBusy.value = false }
}
watch(() => props.playbackSeconds, value => { seconds.value = value })
watch(() => [props.bookId, props.chapterIndex, props.chapterRevision] as const, () => {
  if (data.value?.book_id === props.bookId && data.value.chapter_index === props.chapterIndex && data.value.revision === props.chapterRevision) return
  generation++; controller?.abort(); for (const request of controllers) request.abort(); controllers.clear(); polling.stop(); pause()
  expanded.value = false; data.value = null; selectedId.value = ''; repair.value = null; loading.value = false; busy.value = false; accepting.value = false; restoring.value = false
  error.value = ''; notice.value = ''; reelClips.value = []; reelId.value = ''; reelBusy.value = false; seconds.value = props.playbackSeconds
})
watch(() => props.active, active => { if (!active) { pause(); polling.stop() } else if (running.value && expanded.value) polling.start() })
onBeforeUnmount(() => { alive = false; generation++; for (const request of controllers) request.abort(); controllers.clear(); controller?.abort(); polling.stop(); pause() })
</script>
<template>
  <section ref="mediaRoot" class="mt-3 rounded-lg border border-border bg-panel p-3">
    <button type="button" class="min-h-11 text-sm font-medium text-accent1 focus-visible:outline-2 focus-visible:outline-accent1" :aria-expanded="expanded" @click="toggle">{{ t(expanded ? 'audiobookReview.close' : 'audiobookReview.open') }}</button>
    <div v-if="expanded" class="space-y-4">
      <p class="text-xs text-text-dim">{{ t('audiobookReview.intro') }}</p>
      <div class="flex flex-wrap items-end gap-3">
        <label class="space-y-1"><span class="block text-xs text-text-dim">{{ t('audiobookReview.timestamp') }}</span><input v-model.number="seconds" type="number" min="0" step="0.1" :aria-label="t('audiobookReview.timestamp')" class="min-h-11 w-36 rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"></label>
        <button type="button" class="min-h-11 rounded-lg border border-border px-3 text-xs text-text disabled:opacity-50" :disabled="busy || accepting || running || !data" @click="find">{{ t('audiobookReview.find') }}</button>
        <button type="button" class="min-h-11 px-3 text-xs text-text-dim disabled:opacity-50" :disabled="loading || busy || accepting || running" @click="load">{{ t('audiobookReview.reload') }}</button>
      </div>
      <p v-if="loading" role="status" class="text-xs text-text-dim">{{ t('common.loading') }}</p>
      <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
      <p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>
      <p v-if="data && !data.passages.length" class="text-xs text-text-dim">{{ t('audiobookReview.empty') }}</p>
      <div v-if="data?.passages.length" class="grid gap-4 xl:grid-cols-[13rem_minmax(0,1fr)]">
        <ol class="max-h-80 space-y-2 overflow-y-auto">
          <li v-for="passage in data.passages" :key="passage.id"><button type="button" class="w-full rounded-lg border p-3 text-left disabled:opacity-50" :class="selectedId === passage.id ? 'border-accent1/60 bg-accent1/10' : 'border-border bg-panel-2'" :aria-pressed="selectedId === passage.id" :disabled="busy || accepting || running || passage.status !== 'done'" @click="choose(passage)"><span class="block text-xs text-text-dim">{{ (passage.start_ms / 1000).toFixed(1) }}–{{ (passage.end_ms / 1000).toFixed(1) }}s · {{ passage.speaker }}</span><span class="mt-1 block truncate text-sm text-text">{{ passage.text }}</span></button></li>
        </ol>
        <div v-if="selected" class="min-w-0 space-y-3">
          <p class="text-sm font-medium text-text">{{ selected.speaker }} · {{ selected.language }}</p>
          <p class="text-xs text-text-dim">{{ t('audiobookReview.kept') }}</p>
          <div><p class="text-xs text-text-dim">{{ t('audiobookReview.original') }}</p><audio v-if="api.workflowAudioUrl(selected.audio_url, data?.revision)" controls preload="none" :aria-label="t('audiobookReview.original')" :src="api.workflowAudioUrl(selected.audio_url, data?.revision)" class="mt-2 h-10 w-full" /></div>
          <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookReview.text') }}</span><textarea v-model="text" maxlength="1200" rows="4" :disabled="busy || running || accepting" :aria-label="t('audiobookReview.text')" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" /></label>
          <CloudSpeechCost :enabled="selected.renderer==='openrouter'" :input-key="JSON.stringify([bookId,chapterIndex,data?.revision,selectedId,text,costNonce])" :load="quoteTake" :active="active&&expanded" :disabled="busy||running||accepting||!text.trim()" @approval="value=>cloudApproval=value" />
          <p v-if="selected.cloud_provenance" class="text-xs text-text-dim">{{t('cloudSpeech.provenance',{model:selected.cloud_provenance.model,receipt:selected.cloud_provenance.receipt_id})}}</p>
          <button type="button" class="min-h-11 rounded-lg border border-accent1/50 px-3 text-sm text-accent1 disabled:opacity-50" :disabled="busy || running || accepting || restoring || !text.trim() || selected.renderer==='openrouter'&&!cloudApproval" @click="freshTake">{{ busy || running ? t('audiobookReview.working') : t('audiobookReview.repair') }}</button>
          <div v-if="repair" class="space-y-2 rounded-lg border border-accent1/30 bg-accent1/5 p-3">
            <p class="text-sm font-medium text-text">{{ t('audiobookReview.candidate') }}</p><p class="text-sm text-text-dim">{{ repair.text }}</p>
            <p v-if="repair.mock" class="text-xs text-status-queued">{{ t('audiobookReview.auditionMock') }}</p>
            <audio v-if="api.workflowAudioUrl(repair.audio_url)" controls preload="none" :aria-label="t('audiobookReview.candidate')" :src="api.workflowAudioUrl(repair.audio_url)" class="h-10 w-full" />
            <div class="flex flex-wrap gap-3"><button v-if="repair.status === 'ready'" type="button" :disabled="accepting || busy" class="min-h-11 rounded-lg bg-accent1 px-3 text-sm text-white disabled:opacity-50" @click="accept">{{ accepting ? t('audiobookReview.accepting') : t('audiobookReview.accept') }}</button><button type="button" :disabled="accepting || busy" class="min-h-11 px-3 text-xs text-text-dim" @click="keep">{{ t('audiobookReview.discard') }}</button></div>
          </div>
          <PassageAsrReview v-if="selected.render_identity && data" :key="selected.id + data.revision" :book-id="bookId" :chapter-index="chapterIndex" :passage-id="selected.id" :revision="data.revision" :render-identity="selected.render_identity" :active="active" />
          <SpeakerPassageReview v-if="selected.render_identity && data" :key="selected.id + data.revision+'speaker'" :book-id="bookId" :chapter-index="chapterIndex" :passage-id="selected.id" :revision="data.revision" :render-identity="selected.render_identity" :active="active" />
        </div>
      </div>
      <PauseAnalysisSettings />
      <details v-if="data?.passages.some(passage => passage.status === 'done')" class="rounded-lg border border-border p-3">
        <summary class="cursor-pointer text-sm font-medium text-text">{{ t('audiobookReview.reel') }}</summary><p class="my-3 text-xs text-text-dim">{{ t('audiobookReview.reelHint') }}</p>
        <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookReview.reelName') }}</span><input v-model="reelName" maxlength="120" :disabled="reelBusy" :aria-label="t('audiobookReview.reelName')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"></label>
        <div v-for="passage in data?.passages.filter(item => item.status === 'done')" :key="passage.id" class="mt-3 space-y-2 border-t border-border pt-3">
          <label class="flex gap-2 text-sm text-text"><input type="checkbox" :checked="reelClips.some(item => item.passage_id === passage.id)" :disabled="reelBusy || reelClips.length >= 4 && !reelClips.some(item => item.passage_id === passage.id)" :aria-label="t('audiobookReview.reelSelect') + ': ' + passage.speaker + ' ' + (passage.section_index + 1)" @change="toggleClip(passage)"><span>{{ passage.speaker }}: {{ passage.text }}</span></label>
          <div v-for="clip in reelClips.filter(item => item.passage_id === passage.id)" :key="clip.passage_id" class="grid gap-2 sm:grid-cols-2">
            <label class="space-y-1 text-xs text-text-dim"><span>{{ t('audiobookReview.clipStart') }}</span><input v-model.number="clip.start" type="number" min="0" :max="(passage.end_ms - passage.start_ms) / 1000" step="0.1" :disabled="reelBusy" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-text"></label>
            <label class="space-y-1 text-xs text-text-dim"><span>{{ t('audiobookReview.clipEnd') }}</span><input v-model.number="clip.end" type="number" min="0.1" :max="(passage.end_ms - passage.start_ms) / 1000" step="0.1" :disabled="reelBusy" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-text"></label>
            <label class="space-y-1 text-xs text-text-dim sm:col-span-2"><span>{{ t('audiobookReview.reelPrompt') }}</span><textarea v-model="clip.prompt" rows="2" maxlength="2000" :disabled="reelBusy" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-text" /></label>
            <label class="space-y-1 text-xs text-text-dim sm:col-span-2"><span>{{ t('audiobookReview.reelCaption') }}</span><textarea v-model="clip.caption" rows="2" maxlength="500" :disabled="reelBusy" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-text" /></label>
          </div>
        </div>
        <button type="button" class="mt-3 min-h-11 rounded-lg bg-accent1 px-3 text-sm text-white disabled:opacity-50" :disabled="reelBusy || !reelClips.length || busy || accepting || running" @click="createReel">{{ t(reelBusy ? 'audiobookReview.reelBusy' : 'audiobookReview.reel') }}</button>
        <p v-if="reelId" role="status" class="mt-3 text-sm text-status-done">{{ t('audiobookReview.reelReady') }} <a :href="'/video?project=' + reelId" class="underline">{{ t('audiobookReview.reelOpen') }}</a></p>
      </details>
    </div>
  </section>
</template>

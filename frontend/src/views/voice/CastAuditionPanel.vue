<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/audiobookWorkflow'
import type { AudiobookAudition, CreateAudiobookRequest } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'
import { ApiError } from '../../api/http'
import CloudSpeechCost from './CloudSpeechCost.vue'
import * as cloudApi from '../../api/cloudSpeech'
import type { CloudSpeechApproval } from '../../api/contracts'
const props = withDefaults(defineProps<{ draft?: CreateAudiobookRequest | null; bookId?: string; chapterIndex?: number; active?: boolean; disabled?: boolean; revision?: number; cloud?: boolean }>(), { chapterIndex: 0, active: true, disabled: false,cloud:false })
const emit = defineEmits<{ busy: [value: boolean] }>()
const { t } = useI18n()
const audition = ref<AudiobookAudition | null>(null), submitting = ref(false), error = ref('')
const restored = ref(false)
const cloudApproval=ref<CloudSpeechApproval|null>(null),quoteMode=ref<'cast'|'scene'>('cast'),costNonce=ref(0)
function quotePreview(signal:AbortSignal){const options={chapter_index:props.chapterIndex,mode:quoteMode.value,max_chars:600};return props.bookId?cloudApi.quoteSavedAudition(props.bookId,options,signal):cloudApi.quoteAudition({...props.draft, title:props.draft?.title??'',profile_id:props.draft?.profile_id??'',chapters:props.draft?.chapters??[],...options},signal)}
const mediaRoot = ref<HTMLElement | null>(null)
const running = computed(() => audition.value?.status === 'queued' || audition.value?.status === 'running')
watch([submitting, running], ([pending, generating]) => emit('busy', pending || generating))
const enabled = computed(() => !props.disabled && (props.bookId || props.draft?.profile_id && props.draft.chapters[0]?.text.trim()))
const inputKey = computed(() => JSON.stringify([props.draft, props.bookId, props.chapterIndex, props.revision]))
let alive = true, generation = 0
let controller: AbortController | undefined
function pause() { for (const player of mediaRoot.value?.querySelectorAll('audio') ?? []) player.pause() }
function failure(code = '') {
  return t(code==='cloud_speech_submission_unknown'?'cloudSpeech.unknown':code.startsWith('cloud_')?'cloudSpeech.changed':code === 'reference_transcript_required' ? 'audiobookReview.referenceRequired' : code === 'speech_language_unsupported' ? 'audiobookReview.languageUnsupported' : 'audiobookReview.auditionFailed')
}
const polling = createPollingLoop(async ({ signal, isCurrent }) => {
  const target = audition.value, token = generation
  if (!target || !props.active) return false
  try {
    const result = await api.getAudition(target.id, signal)
    if (!alive || token !== generation || !isCurrent() || result.id !== target.id) return false
    audition.value = result
    cloudApproval.value=null
    if (result.status === 'failed') error.value = failure(result.detail)
    return result.status === 'queued' || result.status === 'running'
  } catch { if (alive && token === generation && isCurrent()) error.value = t('audiobookReview.auditionFailed'); return false }
}, 2000)
async function restore() {
  if (!props.bookId || !props.active || props.disabled || submitting.value) return
  const token = generation, request = new AbortController(); controller?.abort(); controller = request; submitting.value = true
  try {
    const rows = await api.listBookAuditions(props.bookId, props.chapterIndex, request.signal)
    if (!alive || token !== generation || request.signal.aborted) return
    audition.value = rows.sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null
    restored.value = audition.value !== null
    if (running.value && props.active) polling.start(false)
  } catch { if (alive && token === generation && !request.signal.aborted) error.value = t('audiobookReview.auditionFailed') }
  finally { if (alive && token === generation && !request.signal.aborted) submitting.value = false }
}
watch(inputKey, () => { generation++; controller?.abort(); polling.stop(); pause(); audition.value = null; restored.value = false; submitting.value = false; error.value = ''; void restore() }, { immediate: true })
watch(() => props.active, active => { if (!active) { pause(); polling.stop() } else if (running.value) polling.start(); else if (!audition.value) void restore() })
watch(() => props.disabled, disabled => { if (!disabled && !audition.value) void restore() })
async function start(mode: 'cast' | 'scene') {
  if (submitting.value || running.value || !enabled.value || !props.active) return
  if(props.cloud&&(!cloudApproval.value||mode!==quoteMode.value)){error.value=t('cloudSpeech.approvalRequired');return}
  const approval=cloudApproval.value
  if(props.cloud)costNonce.value++
  const token = ++generation, request = new AbortController(); controller?.abort(); controller = request; submitting.value = true; error.value = ''; restored.value = false; pause(); audition.value = null
  try {
    const result = props.bookId
      ? await api.auditionBook(props.bookId, { chapter_index: props.chapterIndex, mode, max_chars: 600,...(approval?{cloud_approval:approval}:{}) }, request.signal)
      : props.draft ? await api.auditionDraft({ ...props.draft, chapter_index: props.chapterIndex, mode, max_chars: 600,...(approval?{cloud_approval:approval}:{}) }, request.signal) : null
    if (!alive || token !== generation || request.signal.aborted || !result) return
    audition.value = result
    if (result.status === 'failed') error.value = failure(result.detail)
    if (running.value && props.active) polling.start(false)
  } catch (err) { if (alive && token === generation && !request.signal.aborted) error.value = failure(err instanceof ApiError ? err.message : '') }
  finally { if (alive && token === generation && !request.signal.aborted) submitting.value = false }
}
async function cancel() {
  const target = audition.value
  if (!target || submitting.value) return
  const token = generation, request = new AbortController(); controller = request; submitting.value = true; polling.stop()
  try {
    const result = await api.cancelAudition(target.id, request.signal)
    if (alive && token === generation && !request.signal.aborted && result.id === target.id) audition.value = result
  } catch { if (alive && token === generation && !request.signal.aborted) error.value = t('audiobookReview.auditionFailed') }
  finally { if (alive && token === generation && !request.signal.aborted) { submitting.value = false; if (running.value && props.active) polling.start(false) } }
}
onBeforeUnmount(() => { alive = false; generation++; controller?.abort(); polling.stop(); pause(); emit('busy', false) })
</script>
<template>
  <section ref="mediaRoot" class="space-y-3 rounded-lg border border-accent1/30 bg-accent1/5 p-4" :aria-label="t('audiobookReview.audition')">
    <p class="text-xs text-text-dim">{{ t('audiobookReview.auditionIntro') }}</p>
    <label v-if="cloud" class="block space-y-1"><span class="text-xs text-text-dim">{{t('cloudSpeech.control')}}</span><select v-model="quoteMode" :disabled="running||submitting" :aria-label="t('cloudSpeech.control')" class="min-h-11 rounded-lg border border-border bg-panel p-2 text-sm text-text"><option value="cast">{{t('audiobookReview.audition')}}</option><option value="scene">{{t('audiobookReview.scene')}}</option></select></label>
    <CloudSpeechCost :enabled="cloud" :input-key="inputKey+quoteMode+costNonce" :load="quotePreview" :active="active" :disabled="!enabled||running||submitting" @approval="value=>cloudApproval=value" />
    <div class="flex flex-wrap items-center gap-3">
      <button type="button" class="min-h-11 rounded-lg border border-border bg-panel px-3 text-sm text-text disabled:opacity-50" :disabled="!enabled || submitting || running || !active || cloud&&(!cloudApproval||quoteMode!=='cast')" @click="start('cast')">{{ t('audiobookReview.audition') }}</button>
      <button type="button" class="min-h-11 rounded-lg border border-border bg-panel px-3 text-sm text-text disabled:opacity-50" :disabled="!enabled || submitting || running || !active || cloud&&(!cloudApproval||quoteMode!=='scene')" @click="start('scene')">{{ t('audiobookReview.scene') }}</button>
      <button v-if="running" type="button" class="min-h-11 px-3 text-xs text-text-dim" :disabled="submitting" @click="cancel">{{ t('audiobookReview.cancel') }}</button>
      <span v-if="submitting || running" role="status" class="text-xs text-text-dim">{{ t('audiobookReview.auditionBusy') }}</span>
    </div>
    <p v-if="!enabled" class="text-xs text-text-dim">{{ t('audiobookReview.auditionEmpty') }}</p>
    <p v-if="running" class="text-xs text-text-dim">{{ t('audiobookReview.auditionPending') }}</p>
    <p v-if="restored" class="text-xs text-text-dim">{{ t('audiobookReview.restoredAudition') }}</p>
    <p v-if="audition?.skipped_speakers?.length" class="text-xs text-text-dim">{{ t('audiobookReview.skippedSpeakers', { names: audition.skipped_speakers.join(', ') }) }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="audition?.clips?.some(clip => clip.mock)" class="text-xs text-status-queued">{{ t('audiobookReview.auditionMock') }}</p>
    <div v-if="api.workflowAudioUrl(audition?.scene_audio_url)"><p class="text-sm font-medium text-text">{{ t('audiobookReview.auditionScene') }}</p><audio controls preload="none" :aria-label="t('audiobookReview.auditionScene')" :src="api.workflowAudioUrl(audition?.scene_audio_url)" class="mt-2 h-10 w-full" /></div>
    <ol v-else-if="audition" class="space-y-3">
      <li v-for="clip in audition.clips" :key="clip.index" class="rounded-lg border border-border bg-panel p-3">
        <div class="flex justify-between gap-3"><span class="text-sm font-medium text-text">{{ clip.speaker }}</span><span class="text-xs text-text-dim">{{ t(`audiobookReview.status.${clip.status}`) }} · {{ clip.language }}</span></div>
        <p class="mt-1 text-sm text-text-dim">{{ clip.text }}</p>
        <audio v-if="api.workflowAudioUrl(clip.audio_url)" controls preload="none" :aria-label="clip.speaker" :src="api.workflowAudioUrl(clip.audio_url)" class="mt-2 h-10 w-full" />
      </li>
    </ol>
  </section>
</template>

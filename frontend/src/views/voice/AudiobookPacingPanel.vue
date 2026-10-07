<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import { updatePacing } from '../../api/narrationTiming'
import { listPassages } from '../../api/audiobookWorkflow'
import type { AudiobookBook, AudiobookJob, AudiobookPassage, AudiobookPassagesResponse } from '../../api/contracts'
const props = withDefaults(defineProps<{ book: AudiobookBook; jobs: AudiobookJob[]; active?: boolean }>(), { active: true })
const emit = defineEmits<{ updated: [book: AudiobookBook] }>()
const { t } = useI18n()
const passageGap = ref(0), speakerGap = ref(0), chapter = ref(0), data = ref<AudiobookPassagesResponse | null>(null)
const overrides = ref<Record<string, number | null>>({}), edits = ref<Record<string, number | null>>({})
const editChapters = ref<Record<string, number>>({})
const loading = ref(false), saving = ref(false), error = ref(''), notice = ref('')
const ready = computed(() => props.book.status === 'done' && props.jobs.length === props.book.chapter_count && props.jobs.every(job => job.status === 'done'))
const valid = computed(() => Number.isInteger(passageGap.value) && passageGap.value >= 0 && passageGap.value <= 5000 && Number.isInteger(speakerGap.value) && speakerGap.value >= 0 && speakerGap.value <= 5000 && Object.values(edits.value).every(value => value === null || Number.isInteger(value) && value >= 0 && value <= 10000))
let alive = true, generation = 0, controller: AbortController | undefined
function stop() { generation++; controller?.abort(); controller = undefined; loading.value = false; saving.value = false }
function reset() {
  stop(); data.value = null; overrides.value = {}; edits.value = {}; editChapters.value = {}; error.value = ''; notice.value = ''
  passageGap.value = props.book.passage_gap_ms ?? 0; speakerGap.value = props.book.speaker_change_gap_ms ?? 0
  if (!props.jobs.some(job => job.chapter_index === chapter.value)) chapter.value = props.jobs[0]?.chapter_index ?? 0
}
async function load() {
  if (!props.active || !ready.value || loading.value || saving.value) return
  controller?.abort(); const request = new AbortController(), token = ++generation; controller = request
  const index = chapter.value, revision = props.jobs.find(job => job.chapter_index === index)?.revision ?? 1
  loading.value = true; error.value = ''
  try {
    const result = await listPassages(props.book.id, index, request.signal)
    if (!alive || !props.active || request.signal.aborted || token !== generation || index !== chapter.value) return
    if (result.book_id !== props.book.id || result.chapter_index !== index || result.revision !== revision) { error.value = t('audiobookReview.pacingStale'); return }
    data.value = result
    for (const passage of result.passages) overrides.value[passage.id] = passage.id in edits.value ? edits.value[passage.id] : passage.gap_after_ms ?? null
  } catch { if (alive && token === generation && !request.signal.aborted) error.value = t('audiobookReview.pacingFailed') }
  finally { if (alive && token === generation && !request.signal.aborted) { loading.value = false; controller = undefined } }
}
function defaultGap(passage: AudiobookPassage) {
  const index = data.value?.passages.findIndex(item => item.id === passage.id) ?? -1
  const next = index >= 0 ? data.value?.passages[index + 1] : undefined
  return next ? passageGap.value + (next.speaker !== passage.speaker ? speakerGap.value : 0) : 0
}
function useDefault(passage: AudiobookPassage, event: Event) {
  if (!(event.target instanceof HTMLInputElement)) return
  const value = event.target.checked ? null : defaultGap(passage)
  overrides.value[passage.id] = value; edits.value[passage.id] = value
  editChapters.value[passage.id] = chapter.value
}
function changeGap(passage: AudiobookPassage, event: Event) {
  if (!(event.target instanceof HTMLInputElement)) return
  const value = event.target.valueAsNumber
  overrides.value[passage.id] = value; edits.value[passage.id] = value
  editChapters.value[passage.id] = chapter.value
}
async function save() {
  if (!props.active || !ready.value || !valid.value || loading.value || saving.value) return
  controller?.abort(); const request = new AbortController(), token = ++generation; controller = request
  const current = () => alive && props.active && token === generation && !request.signal.aborted
  saving.value = true; error.value = ''; notice.value = ''
  try {
    const chapters = props.jobs.map(job => ({ chapter_index: job.chapter_index, revision: job.revision ?? 1,
      passages: Object.entries(edits.value).filter(([passage_id]) => editChapters.value[passage_id] === job.chapter_index).map(([passage_id, gap_after_ms]) => ({ passage_id, gap_after_ms })) }))
    const result = await updatePacing(props.book.id, { passage_gap_ms: passageGap.value, speaker_change_gap_ms: speakerGap.value, chapters }, request.signal)
    if (current() && result.id === props.book.id) { notice.value = t('audiobookReview.pacingSaved'); emit('updated', result) }
  } catch (err) { if (current()) error.value = t(err instanceof ApiError && err.status === 409 ? 'audiobookReview.pacingStale' : 'audiobookReview.pacingFailed') }
  finally { if (current()) { saving.value = false; controller = undefined } }
}
const contextKey = computed(() => JSON.stringify([props.book.id, props.book.status, props.book.passage_gap_ms, props.book.speaker_change_gap_ms, props.jobs.map(job => [job.chapter_index, job.revision, job.status])]))
watch(contextKey, reset, { immediate: true })
watch(() => props.active, active => { if (!active) stop() })
watch(chapter, () => { stop(); data.value = null; overrides.value = {}; error.value = '' })
onBeforeUnmount(() => { alive = false; stop() })
</script>
<template>
  <details class="rounded-lg border border-border bg-panel p-3" data-audiobook-pacing>
    <summary class="cursor-pointer text-sm font-medium text-text">{{ t('audiobookReview.pacingTitle') }}</summary>
    <div class="mt-3 space-y-3">
      <p class="text-xs text-text-dim">{{ t('audiobookReview.pacingHint') }}</p>
      <p v-if="!ready" class="text-xs text-text-dim">{{ t('audiobookReview.pacingComplete') }}</p>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="space-y-1"><span class="block text-xs text-text-dim">{{ t('audiobookReview.passageGap') }}</span><input v-model.number="passageGap" type="number" min="0" max="5000" step="50" :disabled="!ready || saving || !active" :aria-label="t('audiobookReview.passageGap')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text disabled:opacity-50"></label>
        <label class="space-y-1"><span class="block text-xs text-text-dim">{{ t('audiobookReview.speakerGap') }}</span><input v-model.number="speakerGap" type="number" min="0" max="5000" step="50" :disabled="!ready || saving || !active" :aria-label="t('audiobookReview.speakerGap')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text disabled:opacity-50"></label>
      </div>
      <p class="text-xs text-text-dim">{{ t('audiobookReview.passageGapHint') }}</p>
      <div class="flex flex-wrap items-end gap-3">
        <label class="space-y-1"><span class="block text-xs text-text-dim">{{ t('audiobookReview.pacingChapter') }}</span><select v-model.number="chapter" :disabled="!ready || saving || loading || !active" :aria-label="t('audiobookReview.pacingChapter')" class="min-h-11 rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"><option v-for="job in jobs" :key="job.id" :value="job.chapter_index">{{ job.chapter_title || job.chapter_index + 1 }}</option></select></label>
        <button type="button" :disabled="!ready || loading || saving || !active" class="min-h-11 rounded-lg border border-border px-3 text-xs text-text disabled:opacity-50" @click="load">{{ t('audiobookReview.pacingLoad') }}</button>
      </div>
      <ol v-if="data" class="max-h-80 space-y-3 overflow-y-auto">
        <li v-for="passage in data.passages" :key="passage.id" class="space-y-2 rounded-lg border border-border bg-panel-2 p-3">
          <p class="text-xs text-text-dim">{{ passage.speaker }} · {{ (passage.start_ms / 1000).toFixed(1) }}s</p><p class="truncate text-sm text-text">{{ passage.display_text || passage.text }}</p>
          <label class="flex min-h-11 items-center gap-2 text-xs text-text"><input type="checkbox" :checked="overrides[passage.id] == null" :disabled="saving || !active" :aria-label="t('audiobookReview.pacingUseDefault') + ': ' + (passage.section_index + 1)" @change="useDefault(passage, $event)">{{ t('audiobookReview.pacingUseDefault') }}</label>
          <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookReview.pacingOverride') }}</span><input data-passage-gap type="number" min="0" max="10000" step="50" :value="overrides[passage.id] ?? defaultGap(passage)" :disabled="saving || overrides[passage.id] == null || !active" :aria-label="t('audiobookReview.pacingOverride') + ': ' + (passage.section_index + 1)" class="min-h-11 w-full rounded-lg border border-border bg-panel p-2 text-sm text-text disabled:opacity-50" @input="changeGap(passage, $event)"></label>
          <p class="text-xs text-text-dim">{{ t('audiobookReview.pacingEffective', { milliseconds: passage.effective_gap_after_ms ?? 0 }) }}</p>
        </li>
      </ol>
      <button type="button" :disabled="!ready || !valid || saving || loading || !active" class="min-h-11 rounded-lg border border-accent1/50 px-3 text-sm text-accent1 disabled:opacity-50" @click="save">{{ t(saving ? 'audiobookReview.pacingSaving' : 'audiobookReview.pacingSave') }}</button>
      <p v-if="error" role="alert" class="text-xs text-status-failed">{{ error }}</p><p v-if="notice" role="status" class="text-xs text-status-done">{{ notice }}</p>
    </div>
  </details>
</template>

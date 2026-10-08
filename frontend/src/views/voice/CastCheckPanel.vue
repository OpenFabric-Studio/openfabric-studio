<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import * as api from '../../api/audiobookWorkflow'
import type { AudiobookCastCheck, AudiobookJob, CreateAudiobookRequest } from '../../api/contracts'

const props = withDefaults(defineProps<{ draft?: CreateAudiobookRequest | null; bookId?: string; jobs?: AudiobookJob[]; editKey?: string; active?: boolean; disabled?: boolean }>(), { active: true, disabled: false })
const { t } = useI18n()
const chapterIndex = ref(0), checking = ref(false), report = ref<AudiobookCastCheck | null>(null), error = ref(''), shown = ref(50)
const chapters = computed(() => props.bookId
  ? (props.jobs ?? []).map(job => ({ index: job.chapter_index, title: job.chapter_title, revision: job.revision ?? 1 }))
  : (props.draft?.chapters ?? []).map((chapter, index) => ({ index, title: chapter.title, revision: null })))
const selected = computed(() => chapters.value.find(chapter => chapter.index === chapterIndex.value))
watch(chapters, value => { if (!value.some(chapter => chapter.index === chapterIndex.value)) chapterIndex.value = value[0]?.index ?? 0 })
const enabled = computed(() => props.active && !props.disabled && !!selected.value && !!(props.bookId || props.draft))
const inputKey = computed(() => JSON.stringify([props.draft, props.bookId, chapters.value, chapterIndex.value, props.editKey]))
let alive = true, generation = 0, controller: AbortController | undefined
function invalidate() { generation++; controller?.abort(); checking.value = false; report.value = null; error.value = ''; shown.value = 50 }
watch([inputKey, () => props.active, () => props.disabled], invalidate)
async function check() {
  if (!enabled.value || checking.value || !selected.value) return
  const token = ++generation, request = new AbortController(), index = chapterIndex.value, revision = selected.value.revision, bookId = props.bookId
  controller?.abort(); controller = request; checking.value = true; report.value = null; error.value = ''; shown.value = 50
  try {
    const result = bookId && revision !== null
      ? await api.checkBookCast(bookId, { chapter_index: index, revision }, request.signal)
      : props.draft ? await api.checkDraftCast({ ...props.draft, chapter_index: index }, request.signal) : null
    if (!alive || token !== generation || request.signal.aborted || !props.active || props.disabled || !result) return
    if (result.chapter_index !== index || (result.book_id ?? undefined) !== bookId || bookId && result.revision !== revision) throw new Error('Mismatched cast check')
    report.value = result
  } catch (err) {
    if (alive && token === generation && !request.signal.aborted) error.value = t(err instanceof ApiError && err.message === 'chapter_changed' ? 'audiobookReview.castCheckStale' : 'audiobookReview.castCheckFailed')
  } finally { if (alive && token === generation && !request.signal.aborted) checking.value = false }
}
onBeforeUnmount(() => { alive = false; invalidate() })
</script>
<template>
  <section class="space-y-3 rounded-lg border border-border bg-panel p-4" :aria-label="t('audiobookReview.castCheck')">
    <p class="text-xs text-text-dim">{{ t('audiobookReview.castCheckHint') }}</p>
    <div class="flex flex-wrap items-center gap-3">
      <label v-if="chapters.length > 1" class="space-x-2 text-xs text-text-dim"><span>{{ t('audiobookReview.castCheckChapter') }}</span><select v-model.number="chapterIndex" :aria-label="t('audiobookReview.castCheckChapter')" :disabled="!active || disabled" class="min-h-11 rounded-lg border border-border bg-panel-2 p-2 text-text"><option v-for="chapter in chapters" :key="chapter.index" :value="chapter.index">{{ chapter.index + 1 }} · {{ chapter.title }}</option></select></label>
      <button type="button" class="min-h-11 rounded-lg border border-border bg-panel-2 px-3 text-sm text-text disabled:opacity-50" :disabled="!enabled || checking" @click="check">{{ t('audiobookReview.castCheck') }}</button>
      <span v-if="checking" role="status" class="text-xs text-text-dim">{{ t('audiobookReview.castChecking') }}</span>
    </div>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <div v-if="report" class="space-y-3" aria-live="polite">
      <p class="text-xs text-text-dim">{{ t('audiobookReview.castChecked', { count: report.turns.length }) }}<span v-if="report.revision"> · {{ t('audiobookReview.castCheckRevision', { revision: report.revision }) }}</span></p>
      <ul v-if="report.warnings.length" class="space-y-1 text-xs text-status-queued"><li v-for="(warning, index) in report.warnings" :key="index">{{ t(`audiobookReview.castWarnings.${warning.code}`, { speaker: warning.speaker, line: warning.line_number }) }}</li></ul>
      <p v-else class="text-xs text-text-dim">{{ t('audiobookReview.castCheckClear') }}</p>
      <ol class="space-y-2"><li v-for="(turn, index) in report.turns.slice(0, shown)" :key="index" class="rounded-lg border border-border p-3"><p class="text-sm font-medium text-text">{{ turn.speaker }} · {{ turn.profile_name ?? t('audiobookReview.castVoiceMissing') }}</p><p class="mt-1 whitespace-pre-line text-sm text-text-dim">{{ turn.text }}</p></li></ol>
      <button v-if="shown < report.turns.length" type="button" class="min-h-11 px-3 text-sm text-text underline" @click="shown += 50">{{ t('audiobookReview.castShowMore') }}</button>
    </div>
  </section>
</template>

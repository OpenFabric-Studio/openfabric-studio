<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/audiobooks'
import { ApiError } from '../../api/http'
import type { EbookDraft } from '../../api/audiobooks'
import type { EbookDraftSummary, SubtitleSourceCue } from '../../api/contracts'

const props = defineProps<{ disabled: boolean }>()
const emit = defineEmits<{ useDraft: [draft: EbookDraft]; deletedDraft: [id: string]; deleting: [active: boolean] }>()
const { t } = useI18n()
const drafts = ref<EbookDraftSummary[]>([]), pending = ref<EbookDraft | null>(null), working = ref(false), phase = ref<'importing' | 'openingDraft' | 'deletingDraft'>('importing'), error = ref(''), confirmingDelete = ref(false)
const pasteTitle = ref(''), pasteBody = ref(''), pasting = ref(false)
const sourceCues = computed(() => {
  const preview: SubtitleSourceCue[] = []
  for (const chapter of pending.value?.chapters ?? []) for (const cue of chapter.source_cues ?? []) {
    if (preview.length >= 50) return preview
    preview.push(cue)
  }
  return preview
})
const cueCount = computed(() => pending.value?.chapters.reduce((count, chapter) => count + (chapter.source_cues?.length ?? 0), 0) ?? 0)
let alive = true, generation = 0, controller: AbortController | null = null
function errorText(err: unknown) {
  const code = err instanceof ApiError ? err.message : ''
  const allowed = ['ebook_converter_missing', 'ebook_encrypted', 'ebook_too_large', 'invalid_mobi', 'unsupported_ebook_format', 'invalid_text', 'unsafe_ebook', 'ebook_conversion_timeout', 'ebook_text_empty', 'ebook_import_busy', 'ebook_draft_limit', 'ebook_draft_in_use']
  return t(allowed.includes(code) ? `audiobookWorkspace.importErrors.${code}` : 'audiobookWorkspace.importFailed')
}
function remember(draft: EbookDraft) {
  pending.value = draft
  drafts.value = [{ id: draft.id, title: draft.title, source_filename: draft.source_filename, chapter_count: draft.chapters.length, revision: draft.revision, created_at: draft.created_at, updated_at: draft.updated_at }, ...drafts.value.filter(item => item.id !== draft.id)]
}
async function reload() {
  if (working.value) return
  const token = ++generation; controller?.abort(); const request = new AbortController(); controller = request
  try { const response = await api.listEbookDrafts(request.signal); if (alive && token === generation) drafts.value = response }
  catch { if (alive && token === generation) error.value = t('audiobookWorkspace.draftsFailed') }
  finally { if (alive && token === generation) controller = null }
}
async function upload(event: Event) {
  if (props.disabled || working.value || !(event.target instanceof HTMLInputElement)) return
  const file = event.target.files?.[0]; event.target.value = ''
  if (!file) return
  const lower = file.name.toLowerCase()
  if (!/\.(mobi|epub|txt|docx|srt|vtt)$/.test(lower)) { error.value = t('audiobookWorkspace.importErrors.unsupported_ebook_format'); return }
  if (file.size > 50 * 1024 * 1024) { error.value = t('audiobookWorkspace.importErrors.ebook_too_large'); return }
  const token = ++generation; controller?.abort(); const request = new AbortController(); controller = request
  working.value = true; phase.value = 'importing'; error.value = ''; pending.value = null; confirmingDelete.value = false
  try { const draft = await api.importEbook(file, request.signal); if (alive && token === generation) remember(draft) }
  catch (err) { if (alive && token === generation) error.value = errorText(err) }
  finally { if (alive && token === generation) { working.value = false; controller = null } }
}
async function paste() {
  const title = pasteTitle.value.trim(), text = pasteBody.value.trim()
  if (props.disabled || working.value || !title || !text) return
  const token = ++generation; controller?.abort(); const request = new AbortController(); controller = request
  working.value = true; phase.value = 'importing'; error.value = ''; pending.value = null; confirmingDelete.value = false
  try { const draft = await api.importPastedText({ title, text }, request.signal); if (alive && token === generation) { remember(draft); pasteBody.value = '' } }
  catch (err) { if (alive && token === generation) error.value = errorText(err) }
  finally { if (alive && token === generation) { working.value = false; controller = null } }
}
function cancel() { generation++; controller?.abort(); controller = null; working.value = false; error.value = '' }
async function choose(event: Event) {
  if (!(event.target instanceof HTMLSelectElement) || props.disabled) return
  const id = event.target.value; pending.value = null; confirmingDelete.value = false; error.value = ''
  const token = ++generation; controller?.abort(); working.value = false; controller = null; if (!id) return
  const request = new AbortController(); controller = request; working.value = true; phase.value = 'openingDraft'
  try { const draft = await api.getEbookDraft(id, request.signal); if (alive && token === generation) pending.value = draft }
  catch { if (alive && token === generation) error.value = t('audiobookWorkspace.draftsFailed') }
  finally { if (alive && token === generation) { working.value = false; controller = null } }
}
async function removeDraft() {
  const draft = pending.value
  if (!draft || working.value || props.disabled) return
  const token = ++generation; controller?.abort(); const request = new AbortController(); controller = request; working.value = true; phase.value = 'deletingDraft'; error.value = ''; emit('deleting', true)
  try { await api.deleteEbookDraft(draft.id, request.signal); if (alive && token === generation) { drafts.value = drafts.value.filter(item => item.id !== draft.id); pending.value = null; confirmingDelete.value = false; emit('deletedDraft', draft.id) } }
  catch (err) { if (alive && token === generation) error.value = errorText(err) }
  finally { if (alive && token === generation) { working.value = false; controller = null; emit('deleting', false) } }
}
onMounted(() => { void reload() })
onBeforeUnmount(() => { alive = false; cancel() })
</script>
<template>
  <section class="space-y-3 rounded-xl border border-accent1/30 bg-accent1/5 p-4" :aria-busy="working" :aria-label="t('audiobookWorkspace.importTitle')">
    <div><h3 class="text-sm font-semibold text-text">{{ t('audiobookWorkspace.importTitle') }}</h3><p class="mt-1 text-xs text-text-dim">{{ t('audiobookWorkspace.importHint') }}</p></div>
    <p class="text-xs text-text-dim">{{ t('audiobookWorkspace.importFormats') }}</p>
    <div class="flex flex-wrap gap-3"><label class="flex min-h-11 min-w-0 max-w-full cursor-pointer flex-wrap items-center gap-3 rounded-lg border border-border bg-panel px-3 py-2 text-sm text-text"><span>{{ t('audiobookWorkspace.upload') }}</span><input type="file" accept=".txt,.epub,.mobi,.docx,.srt,.vtt,text/plain,application/epub+zip,application/x-mobipocket-ebook,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/vtt" :aria-label="t('audiobookWorkspace.upload')" :disabled="props.disabled || working" class="min-w-0 max-w-full text-xs sm:max-w-48 file:mr-2 file:rounded file:border-0 file:bg-panel-2 file:px-2 file:py-1 file:text-text" @change="upload"></label><button v-if="working && phase !== 'deletingDraft'" type="button" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text" @click="cancel">{{ t('audiobookWorkspace.cancelImport') }}</button></div>
    <button type="button" class="min-h-11 text-xs text-text-dim hover:text-text hover:underline" :disabled="props.disabled || working" @click="pasting = !pasting">{{ t('audiobookWorkspace.pasteToggle') }}</button>
    <div v-if="pasting" class="space-y-2 rounded-lg border border-border bg-panel p-3" @keydown.enter.prevent="paste">
      <p class="text-xs text-text-dim">{{ t('audiobookWorkspace.pasteHint') }}</p>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookWorkspace.pasteTitle') }}</span><input v-model="pasteTitle" type="text" maxlength="200" :aria-label="t('audiobookWorkspace.pasteTitle')" :disabled="props.disabled || working" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 text-sm text-text"></label>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookWorkspace.pasteText') }}</span><textarea v-model="pasteBody" rows="4" maxlength="2000000" :aria-label="t('audiobookWorkspace.pasteText')" :disabled="props.disabled || working" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"></textarea></label>
      <button type="button" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" :disabled="props.disabled || working || !pasteTitle.trim() || !pasteBody.trim()" @click="paste">{{ t('audiobookWorkspace.pasteImport') }}</button>
    </div>
    <p v-if="working" role="status" class="text-sm text-text-dim">{{ t(`audiobookWorkspace.${phase}`) }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <div v-if="pending" class="space-y-2">
      <p v-for="warning in pending.warnings" :key="warning.code" role="note" class="text-xs text-text-dim">{{ warning.message }}</p>
      <p v-if="pending.cast_review_required" class="text-sm text-text">{{ t('audiobookWorkspace.subtitleCastReview') }}</p>
      <details v-if="cueCount" class="rounded-lg border border-border p-3">
        <summary class="cursor-pointer text-sm text-text">{{ t('audiobookWorkspace.sourceCues', { count: cueCount }) }}</summary>
        <p class="my-2 text-xs text-text-dim">{{ t('audiobookWorkspace.sourceTimingHint') }}</p>
        <div class="max-h-64 overflow-auto"><table class="w-full text-left text-xs text-text-dim"><thead><tr><th scope="col" class="p-2">{{ t('audiobookWorkspace.cue') }}</th><th scope="col" class="p-2">{{ t('audiobookWorkspace.speaker') }}</th><th scope="col" class="p-2">{{ t('audiobookWorkspace.sourceTime') }}</th><th scope="col" class="p-2">{{ t('audiobookWorkspace.pasteText') }}</th></tr></thead><tbody><tr v-for="cue in sourceCues" :key="cue.order"><td class="p-2">{{ cue.cue_id }}</td><td class="p-2">{{ cue.speaker || t('audiobooks.narrator') }}</td><td class="whitespace-nowrap p-2">{{ (cue.start_ms / 1000).toFixed(3) }}–{{ (cue.end_ms / 1000).toFixed(3) }} s</td><td class="p-2">{{ cue.text }}</td></tr></tbody></table></div>
      </details>
    </div>
    <a v-if="error" href="/settings#module-ebooks" class="inline-block min-h-11 py-3 text-sm text-text underline decoration-accent2">{{ t('audiobookWorkspace.converterSetup') }}</a>
    <label v-if="drafts.length" class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookWorkspace.savedImports') }}</span><select :value="pending?.id ?? ''" :disabled="props.disabled || working" :aria-label="t('audiobookWorkspace.savedImports')" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3 text-sm text-text" @change="choose"><option value="">{{ t('audiobookWorkspace.chooseImport') }}</option><option v-for="draft in drafts" :key="draft.id" :value="draft.id">{{ draft.title }} · {{ draft.source_filename }}</option></select></label>
    <div v-if="pending" class="space-y-2"><p class="text-sm text-text">{{ pending.title }} · {{ t('audiobooks.chapterCount', { count: pending.chapters.length }) }}</p><p class="text-xs text-text-dim">{{ t('audiobookWorkspace.replaceHint') }}</p><div class="flex flex-wrap gap-3"><button type="button" :disabled="props.disabled || working" class="min-h-11 rounded-lg bg-accent1 px-3 text-sm text-white disabled:opacity-50" @click="emit('useDraft', pending)">{{ t('audiobookWorkspace.useImport') }}</button><button type="button" :disabled="props.disabled || working" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="confirmingDelete = !confirmingDelete">{{ t('audiobookWorkspace.deleteDraft') }}</button></div><div v-if="confirmingDelete" class="rounded-lg border border-status-failed/30 p-3"><p class="text-sm text-text-dim">{{ t('audiobookWorkspace.deleteHint') }}</p><button type="button" class="mt-2 min-h-11 rounded-lg border border-status-failed px-3 text-sm text-status-failed" :disabled="props.disabled || working" @click="removeDraft">{{ t('audiobookWorkspace.confirmDelete') }}</button></div></div>
  </section>
</template>

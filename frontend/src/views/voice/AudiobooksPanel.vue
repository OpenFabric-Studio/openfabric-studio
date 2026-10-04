<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import * as audiobooksApi from '../../api/audiobooks'
import * as profilesApi from '../../api/voiceProfiles'
import type { AudiobookBook, AudiobookJob, CreateAudiobookRequest } from '../../api/audiobooks'
import type { SpeechVoiceProfile } from '../../api/voiceProfiles'
import { createPollingLoop } from '../../composables/polling'
import EbookImportPanel from './EbookImportPanel.vue'
import type { EbookDraft, SpeechCloneTrialResponse } from '../../api/contracts'

const emit = defineEmits<{ activity: [message: string] }>()
const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true })
const { t } = useI18n()
type ChapterDraft = { id: number; title: string; text: string; included: boolean }
const importedDraft = ref<EbookDraft | null>(null), draftSaving = ref(false), deletingImport = ref(false), previewing = ref(false), controlling = ref(false)
const preview = ref<SpeechCloneTrialResponse | null>(null), previewError = ref('')
const chapterEditorIndex = ref(0)
let draftController: AbortController | undefined, previewController: AbortController | undefined, controlController: AbortController | undefined
let draftGeneration = 0, previewGeneration = 0
const profiles = ref<SpeechVoiceProfile[]>([])
const books = ref<AudiobookBook[]>([])
const jobs = ref<AudiobookJob[]>([])
const selectedBookId = ref('')
const search = ref('')
const creating = ref(false)
const loading = ref(false)
const loadingJobs = ref(false)
const loadingProfiles = ref(false)
const saving = ref(false)
const retrying = ref(false)
const error = ref('')
const jobsError = ref('')
const notice = ref('')
const title = ref('')
const author = ref('')
type SpeechPair = { written: string; spoken: string }
const pronunciations = ref<SpeechPair[]>([])
const bookSpeech = ref<SpeechPair[]>([])
const bookLanguage = ref('')
const chapterLanguages = ref<Record<string, string>>({})
const savingLanguages = ref(false)
const savingSpeech = ref(false)
const regeneratingIndex = ref<number | null>(null)
const titleInput = ref<HTMLInputElement | null>(null)
const newBookButton = ref<HTMLButtonElement | null>(null)
const audioPlayers = ref<HTMLAudioElement[]>([])
const profileId = ref('')
let chapterSequence = 0
function newChapter(number: number): ChapterDraft {
  return { id: ++chapterSequence, title: t('audiobookWorkspace.chapterName', { number }), text: '', included: true }
}
const chapters = ref<ChapterDraft[]>([newChapter(1)])
let mounted = true
let selectionGeneration = 0
let loadController: AbortController | undefined
let jobsController: AbortController | undefined
let createController: AbortController | undefined
let retryController: AbortController | undefined
let profilesController: AbortController | undefined

const selectedBook = computed(() => books.value.find(book => book.id === selectedBookId.value))
const narrators = computed(() => profiles.value.filter(profile => profile.consent_confirmed))
const draftNarratorAvailable = computed(() => narrators.value.some(profile => profile.id === profileId.value))
const filteredBooks = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return books.value.filter(book => book.title.toLocaleLowerCase().includes(query))
})
const activeBooks = computed(() => books.value.filter(book => book.status === 'queued' || book.status === 'running'))
const selectedNarrator = computed(() => profiles.value.find(profile => profile.id === selectedBook.value?.profile_id)?.name)
const completedChapters = computed(() => jobs.value.filter(job => job.status === 'done').length)
const finishingSection = computed(() => ['paused', 'cancelled'].includes(selectedBook.value?.status ?? '') && jobs.value.some(job => job.status === 'running'))
const includedChapters = computed(() => chapters.value.filter(chapter => chapter.included))
const previewUrl = computed(() => preview.value ? profilesApi.speechTrialAudioUrl(preview.value.trial_id) : null)
watch([profileId, () => includedChapters.value[0]?.text], () => { previewGeneration++; previewController?.abort(); preview.value = null; previewing.value = false; previewError.value = '' })
const activity = computed(() => {
  if (saving.value) return t('audiobookWorkspace.creating')
  if (retrying.value) return t('audiobookWorkspace.retrying')
  if (loadingProfiles.value) return t('audiobookWorkspace.loadingNarrators')
  if (error.value || jobsError.value) return error.value || jobsError.value
  if (selectedBook.value?.status === 'failed') return t('audiobookWorkspace.bookFailed', { title: selectedBook.value.title })
  if (activeBooks.value.length) return t('audiobookWorkspace.background', { count: activeBooks.value.length })
  return ''
})
watch(activity, message => { if (mounted) emit('activity', message) }, { immediate: true })
function pauseAudio() { for (const player of audioPlayers.value) player.pause() }
watch([() => props.active, creating, selectedBookId], pauseAudio)
watch(() => props.active, active => { if (active && creating.value) void refreshNarrators() })

function safeError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    switch (err.message) {
      case 'consent_required': return t('audiobookWorkspace.errors.consent')
      case 'profile_not_found':
      case 'invalid_profile_id': return t('audiobookWorkspace.errors.profileMissing')
      case 'book_not_found':
      case 'invalid_book_id': return t('audiobookWorkspace.errors.bookMissing')
      case 'nothing_to_retry': return t('audiobookWorkspace.errors.nothingToRetry')
      case 'duplicate_pronunciation': return t('audiobookWorkspace.errors.duplicate_pronunciation')
      case 'audiobook_busy': return t('audiobookWorkspace.errors.audiobook_busy')
      case 'export_codec_missing': return t('audiobookWorkspace.errors.export_codec_missing')
      case 'unsupported_cover': return t('audiobookWorkspace.errors.unsupported_cover')
      case 'cover_too_large': return t('audiobookWorkspace.errors.cover_too_large')
      case 'ebook_draft_conflict': return t('audiobookWorkspace.errors.draftConflict')
    }
  }
  return t(fallback)
}

function chapterHint(job: AudiobookJob): string {
  // Worker detail may contain local paths, server addresses or raw exceptions.
  if (job.detail?.startsWith('Dry-run speech clone wrote a silent placeholder WAV')) return t('audiobookWorkspace.mockAudio')
  if (job.status === 'failed') {
    if (job.detail === 'consent_required') return t('audiobookWorkspace.errors.consent')
    if (job.detail === 'engine_not_installed' || job.detail?.startsWith('engine_not_installed:')) return t('audiobookWorkspace.errors.engineMissing')
    if (job.detail === 'api_unavailable' || job.detail?.startsWith('api_unavailable:')) return t('audiobookWorkspace.errors.apiUnavailable')
    return t('audiobookWorkspace.chapterFailed')
  }
  return ''
}

async function loadJobs(bookId: string) {
  if (!mounted) return
  polling.stop()
  jobsController?.abort()
  const controller = new AbortController()
  jobsController = controller
  const generation = selectionGeneration
  const isCurrent = () => mounted && !controller.signal.aborted && generation === selectionGeneration && selectedBookId.value === bookId
  loadingJobs.value = true
  jobsError.value = ''
  try {
    const result = await audiobooksApi.listAudiobookJobs(bookId, controller.signal)
    if (isCurrent()) jobs.value = result
  } catch (err) {
    if (isCurrent()) jobsError.value = safeError(err, 'audiobookWorkspace.errors.jobs')
  } finally {
    if (isCurrent()) { loadingJobs.value = false; jobsController = undefined; updatePolling() }
  }
}

const polling = createPollingLoop(async ({ signal, isCurrent }) => {
  const current = () => mounted && isCurrent()
  try {
    const updated = await audiobooksApi.listAudiobooks(signal)
    if (!current()) return false
    books.value = updated
    const id = selectedBookId.value
    const generation = selectionGeneration
    if (id && selectedBook.value && !loadingJobs.value) {
      const updatedJobs = await audiobooksApi.listAudiobookJobs(id, signal)
      if (!current()) return false
      if (generation === selectionGeneration && selectedBookId.value === id) {
        jobs.value = updatedJobs
        jobsError.value = ''
      }
    }
    return activeBooks.value.length > 0 || finishingSection.value
  } catch (err) {
    if (current()) error.value = safeError(err, 'audiobookWorkspace.errors.refresh')
    return false
  }
}, 2000)

function updatePolling() {
  if (!mounted || loading.value || loadingJobs.value || saving.value || retrying.value || controlling.value || (!activeBooks.value.length && !finishingSection.value)) polling.stop()
  else polling.start(false)
}

async function loadWorkspace() {
  if (!mounted || saving.value || retrying.value || loadingProfiles.value || controlling.value) return
  loadController?.abort()
  polling.stop()
  const controller = new AbortController()
  loadController = controller
  const isCurrent = () => mounted && !controller.signal.aborted
  loading.value = true
  error.value = ''
  try {
    const [profileResult, bookResult] = await Promise.allSettled([
      profilesApi.listSpeechVoiceProfiles(controller.signal),
      audiobooksApi.listAudiobooks(controller.signal),
    ])
    if (!isCurrent()) return
    if (profileResult.status === 'fulfilled') {
      profiles.value = profileResult.value
      if (!profileId.value) profileId.value = narrators.value[0]?.id ?? ''
    } else error.value = t('audiobookWorkspace.errors.profiles')
    if (bookResult.status === 'fulfilled') {
      books.value = bookResult.value
      if (!selectedBook.value) {
        selectionGeneration++
        jobsController?.abort()
        jobs.value = []
        selectedBookId.value = books.value[0]?.id ?? ''
      }
      if (selectedBookId.value) void loadJobs(selectedBookId.value)
    } else error.value = safeError(bookResult.reason, 'audiobooks.err.load')
  } finally {
    if (isCurrent()) { loading.value = false; loadController = undefined; updatePolling() }
  }
}

async function onSelectBook(book: AudiobookBook) {
  if (!mounted || deletingImport.value) return
  creating.value = false
  notice.value = ''
  if (selectedBookId.value === book.id) return
  selectionGeneration++
  selectedBookId.value = book.id
  jobs.value = []
  error.value = ''
  polling.stop()
  await loadJobs(book.id)
  if (mounted) updatePolling()
}

async function openCreation() {
  creating.value = true
  notice.value = ''
  void refreshNarrators()
  await nextTick()
  if (mounted && props.active && creating.value) titleInput.value?.focus()
}
function speechRows(rows: SpeechPair[]): SpeechPair[] | null {
  const cleaned = rows.map(row => ({ written: row.written.trim(), spoken: row.spoken.trim() })).filter(row => row.written || row.spoken)
  if (cleaned.some(row => !row.written || !row.spoken)) return null
  return cleaned
}
function useDraft(draft: EbookDraft) {
  if (saving.value || draftSaving.value) return
  draftGeneration++; draftController?.abort(); importedDraft.value = draft; title.value = draft.title
  author.value = draft.author ?? ''
  pronunciations.value = (draft.pronunciations ?? []).map(item => ({ written: item.written, spoken: item.spoken }))
  chapters.value = draft.chapters.map(chapter => ({ title: chapter.title ?? '', text: chapter.text, included: chapter.included ?? true, id: ++chapterSequence }))
  chapterEditorIndex.value = 0
  error.value = ''; notice.value = ''; preview.value = null
}
async function saveReviewedDraft(signal?: AbortSignal): Promise<EbookDraft | null> {
  const draft = importedDraft.value
  if (!draft) return null
  const speech = speechRows(pronunciations.value)
  const updated = await audiobooksApi.saveEbookDraft(draft.id, { title: title.value.trim(), revision: draft.revision, author: author.value.trim(), pronunciations: speech ?? [], chapters: chapters.value.map(({ title, text, included }) => ({ title: title.trim(), text: text.trim(), included })) }, signal)
  if (mounted && !signal?.aborted && importedDraft.value?.id === draft.id) importedDraft.value = updated
  return updated
}
async function onSaveDraft() {
  if (!importedDraft.value || saving.value || draftSaving.value || deletingImport.value) return
  if (speechRows(pronunciations.value) === null) { error.value = t('audiobookWorkspace.errors.pronunciation'); return }
  const token = ++draftGeneration, controller = new AbortController(); draftController = controller; draftSaving.value = true; error.value = ''
  try { await saveReviewedDraft(controller.signal); if (mounted && token === draftGeneration) notice.value = t('audiobookWorkspace.draftSaved') }
  catch (err) { if (mounted && token === draftGeneration) error.value = safeError(err, 'audiobookWorkspace.errors.draftSave') }
  finally { if (mounted && token === draftGeneration) { draftSaving.value = false; draftController = undefined } }
}
async function onPreview() {
  const text = includedChapters.value[0]?.text.trim().slice(0, 500)
  if (!text || previewing.value || !draftNarratorAvailable.value) return
  const token = ++previewGeneration, controller = new AbortController(); previewController = controller; previewing.value = true; previewError.value = ''; preview.value = null
  try {
    const response = await profilesApi.startSpeechCloneTrial(profileId.value, text, controller.signal)
    if (!mounted || token !== previewGeneration || controller.signal.aborted) return
    if (response.status === 'completed' || response.status === 'mock_completed') preview.value = response
    else previewError.value = t('audiobookWorkspace.previewFailed')
  } catch { if (mounted && token === previewGeneration) previewError.value = t('audiobookWorkspace.previewFailed') }
  finally { if (mounted && token === previewGeneration) { previewing.value = false; previewController = undefined } }
}
async function onControl(action: 'pause' | 'resume' | 'cancel') {
  const book = selectedBook.value
  if (!book || controlling.value || saving.value || loading.value || retrying.value) return
  const generation = selectionGeneration, controller = new AbortController(); controlController = controller; controlling.value = true; polling.stop(); error.value = ''
  try {
    const updated = await audiobooksApi.controlAudiobook(book.id, action, controller.signal)
    if (!mounted || controller.signal.aborted) return
    upsertBook(updated)
    if (generation === selectionGeneration && selectedBookId.value === book.id) void loadJobs(book.id)
  } catch (err) { if (mounted && !controller.signal.aborted && generation === selectionGeneration) error.value = safeError(err, 'audiobookWorkspace.errors.control') }
  finally { if (mounted && !controller.signal.aborted) { controlling.value = false; controlController = undefined; updatePolling() } }
}
async function closeCreation() {
  if (deletingImport.value) return
  creating.value = false
  await nextTick()
  if (mounted && props.active && !creating.value) newBookButton.value?.focus()
}
async function refreshNarrators() {
  if (!mounted || loading.value || loadingProfiles.value || saving.value || retrying.value) return
  const controller = new AbortController()
  profilesController = controller
  const isCurrent = () => mounted && !controller.signal.aborted
  loadingProfiles.value = true
  try {
    const result = await profilesApi.listSpeechVoiceProfiles(controller.signal)
    if (!isCurrent()) return
    profiles.value = result
    if (!profileId.value) profileId.value = narrators.value[0]?.id ?? ''
  } catch {
    if (isCurrent()) error.value = t('audiobookWorkspace.errors.profiles')
  } finally {
    if (isCurrent()) { loadingProfiles.value = false; profilesController = undefined }
  }
}
function addChapter() {
  if (saving.value || chapters.value.length >= 100) return
  chapters.value.push(newChapter(chapters.value.length + 1))
  chapterEditorIndex.value = chapters.value.length - 1
}
function removeChapter(index: number) {
  if (saving.value || chapters.value.length <= 1) return
  chapters.value.splice(index, 1)
  if (index < chapterEditorIndex.value) chapterEditorIndex.value--
  chapterEditorIndex.value = Math.min(chapterEditorIndex.value, chapters.value.length - 1)
}
function upsertBook(book: AudiobookBook) {
  const existing = books.value.findIndex(item => item.id === book.id)
  if (existing < 0) books.value.unshift(book)
  else books.value.splice(existing, 1, book)
}

async function onCreate() {
  if (!mounted || loading.value || saving.value || retrying.value || loadingProfiles.value || draftSaving.value || deletingImport.value || previewing.value) return
  error.value = ''
  notice.value = ''
  const trimmedTitle = title.value.trim()
  if (!trimmedTitle) { error.value = t('audiobooks.err.title'); return }
  if (!narrators.value.some(profile => profile.id === profileId.value)) { error.value = t('audiobooks.err.profile'); return }
  const payloadChapters = includedChapters.value.map(chapter => ({ title: chapter.title.trim(), text: chapter.text.trim() }))
  if (!payloadChapters.length) { error.value = t('audiobookWorkspace.errors.noIncluded'); return }
  if (payloadChapters.some(chapter => !chapter.text)) { error.value = t('audiobookWorkspace.errors.emptyChapter'); return }
  const speech = speechRows(pronunciations.value)
  if (!speech) { error.value = t('audiobookWorkspace.errors.pronunciation'); return }
  const body: CreateAudiobookRequest = { title: trimmedTitle, profile_id: profileId.value, chapters: payloadChapters }
  if (author.value.trim()) body.author = author.value.trim()
  if (speech.length) body.pronunciations = speech
  const controller = new AbortController()
  createController = controller
  const generation = selectionGeneration
  const isCurrent = () => mounted && !controller.signal.aborted
  saving.value = true
  polling.stop()
  try {
    const reviewedDraft = await saveReviewedDraft(controller.signal)
    if (!isCurrent()) return
    const created = reviewedDraft ? await audiobooksApi.createAudiobookFromDraft(reviewedDraft, body.profile_id, controller.signal) : await audiobooksApi.createAudiobook(body, controller.signal)
    if (!isCurrent()) return
    upsertBook(created.book)
    title.value = ''
    author.value = ''
    pronunciations.value = []
    chapters.value = [newChapter(1)]
    importedDraft.value = null; preview.value = null
    notice.value = t('audiobooks.created')
    if (selectionGeneration === generation && creating.value) {
      jobsController?.abort()
      selectionGeneration++
      selectedBookId.value = created.book.id
      jobs.value = created.jobs
      jobsError.value = ''
      loadingJobs.value = false
      creating.value = false
    }
  } catch (err) {
    if (isCurrent()) error.value = safeError(err, 'audiobooks.err.create')
  } finally {
    if (isCurrent()) { saving.value = false; createController = undefined; updatePolling() }
  }
}

function exportNote(note: string | undefined): string {
  if (!note) return ''
  if (note.includes('export_codec_missing')) return t('audiobookWorkspace.codecMissing')
  return t('audiobookWorkspace.exportFailed')
}
async function onSaveSpeech() {
  const book = selectedBook.value
  const speech = speechRows(bookSpeech.value)
  if (!book || !speech || savingSpeech.value || retrying.value || regeneratingIndex.value !== null) return
  if (book.status === 'queued' || book.status === 'running') return
  savingSpeech.value = true; error.value = ''; notice.value = ''
  try {
    const updated = await audiobooksApi.setAudiobookPronunciations(book.id, speech)
    if (!mounted) return
    upsertBook(updated)
    notice.value = t('audiobookWorkspace.pronunciationsSaved')
  } catch (err) { if (mounted) error.value = safeError(err, 'audiobookWorkspace.errors.pronunciation') }
  finally { if (mounted) savingSpeech.value = false }
}
async function onRegenerate(index: number) {
  const book = selectedBook.value
  if (!book || regeneratingIndex.value !== null || retrying.value || saving.value) return
  if (book.status !== 'done' && book.status !== 'failed') return
  const controller = new AbortController(); regeneratingIndex.value = index; error.value = ''; notice.value = ''; polling.stop()
  try {
    const updated = await audiobooksApi.regenerateAudiobookChapter(book.id, index, controller.signal)
    if (!mounted) return
    upsertBook(updated)
    notice.value = t('audiobookWorkspace.regenerated')
    void loadJobs(book.id)
  } catch (err) { if (mounted) error.value = safeError(err, 'audiobookWorkspace.errors.control') }
  finally { if (mounted) { regeneratingIndex.value = null; updatePolling() } }
}
async function onCover(event: Event) {
  const book = selectedBook.value
  if (!book || !(event.target instanceof HTMLInputElement)) return
  const file = event.target.files?.[0]; event.target.value = ''
  if (!file) return
  error.value = ''; notice.value = ''
  try {
    const updated = await audiobooksApi.uploadAudiobookCover(book.id, file)
    if (!mounted) return
    upsertBook(updated)
    notice.value = t('audiobookWorkspace.coverSaved')
  } catch (err) { if (mounted) error.value = safeError(err, 'audiobookWorkspace.errors.unsupported_cover') }
}
watch(selectedBookId, () => {
  const book = books.value.find(item => item.id === selectedBookId.value)
  bookSpeech.value = (book?.pronunciations ?? []).map(item => ({ written: item.written, spoken: item.spoken }))
  bookLanguage.value = book?.language ?? ''
  chapterLanguages.value = {}
})
watch(jobs, rows => {
  const next = { ...chapterLanguages.value }
  for (const job of rows) {
    if (!(job.id in next)) next[job.id] = job.language ?? ''
  }
  chapterLanguages.value = next
})
async function onSaveLanguages() {
  const book = selectedBook.value
  if (!book || savingLanguages.value || book.status === 'queued' || book.status === 'running') return
  savingLanguages.value = true; error.value = ''; notice.value = ''
  try {
    const chapters = jobs.value.map(job => ({ chapter_index: job.chapter_index, language: (chapterLanguages.value[job.id] ?? '').trim() }))
    const updated = await audiobooksApi.setAudiobookLanguages(book.id, bookLanguage.value.trim(), chapters)
    if (!mounted) return
    upsertBook(updated)
    bookLanguage.value = updated.language ?? ''
    chapterLanguages.value = {}
    notice.value = t('audiobookWorkspace.languagesSaved')
    void loadJobs(book.id)
  } catch (err) { if (mounted) error.value = safeError(err, 'audiobookWorkspace.errors.control') }
  finally { if (mounted) savingLanguages.value = false }
}
async function onRetry() {
  const book = selectedBook.value
  if (!mounted || loading.value || saving.value || retrying.value || book?.status !== 'failed') return
  const controller = new AbortController()
  retryController = controller
  const generation = selectionGeneration
  const isCurrent = () => mounted && !controller.signal.aborted
  retrying.value = true
  error.value = ''
  notice.value = ''
  polling.stop()
  try {
    const updated = await audiobooksApi.retryAudiobook(book.id, controller.signal)
    if (!isCurrent()) return
    upsertBook(updated)
    if (selectionGeneration === generation && selectedBookId.value === book.id) {
      notice.value = t('audiobooks.retried')
      void loadJobs(book.id)
    }
  } catch (err) {
    if (isCurrent() && selectionGeneration === generation) error.value = safeError(err, 'audiobooks.err.retry')
  } finally {
    if (isCurrent()) { retrying.value = false; retryController = undefined; updatePolling() }
  }
}

onMounted(() => { void loadWorkspace() })
onBeforeUnmount(() => {
  mounted = false
  selectionGeneration++
  polling.stop()
  loadController?.abort(); jobsController?.abort(); createController?.abort(); retryController?.abort()
  profilesController?.abort()
  draftGeneration++; previewGeneration++; draftController?.abort(); previewController?.abort(); controlController?.abort()
  pauseAudio()
  emit('activity', '')
})
</script>

<template>
  <section class="grid min-w-0 gap-5 lg:grid-cols-[15rem_minmax(0,1fr)]" :aria-label="t('audiobooks.title')">
    <aside class="min-w-0 space-y-4 rounded-xl border border-border bg-panel p-4" :aria-label="t('audiobooks.booksTitle')">
      <div class="flex items-center justify-between gap-2">
        <h2 class="text-sm font-semibold text-text">{{ t('audiobooks.booksTitle') }}</h2>
        <button ref="newBookButton" type="button" class="min-h-11 rounded-lg bg-accent1 px-3 py-2 text-xs text-white hover:brightness-110 focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="loading" @click="openCreation">{{ t('audiobookWorkspace.new') }}</button>
      </div>
      <label class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('audiobookWorkspace.search') }}</span>
        <input v-model="search" type="search" :aria-label="t('audiobookWorkspace.search')" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" />
      </label>
      <p v-if="loading" role="status" class="text-sm text-text-dim">{{ t('common.loading') }}</p>
      <p v-if="!books.length && !loading" class="text-sm text-text-dim">{{ t('audiobooks.empty') }}</p>
      <p v-else-if="books.length && !filteredBooks.length" class="text-sm text-text-dim">{{ t('audiobookWorkspace.noMatches') }}</p>
      <ul class="max-h-96 space-y-2 overflow-y-auto lg:max-h-[32rem]">
        <li v-for="book in filteredBooks" :key="book.id">
          <button type="button" :disabled="deletingImport" :data-select-book="book.id" :aria-current="book.id === selectedBookId && !creating ? 'true' : undefined" class="w-full rounded-lg border p-3 text-left focus-visible:outline-2 focus-visible:outline-accent1" :class="book.id === selectedBookId && !creating ? 'border-accent1/60 bg-accent1/10' : 'border-border bg-panel-2 hover:border-accent1/40'" @click="onSelectBook(book)">
            <span class="block truncate text-sm font-medium text-text">{{ book.title }}</span>
            <span class="mt-1 block text-xs text-text-dim">{{ t(`audiobookWorkspace.status.${book.status}`) }} · {{ t('audiobooks.chapterCount', { count: book.chapter_count }) }}</span>
          </button>
        </li>
      </ul>
      <button type="button" class="min-h-11 rounded-md px-2 text-xs text-text-dim hover:text-text hover:underline focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="loading || loadingProfiles || saving || retrying || controlling" @click="loadWorkspace">{{ t('audiobookWorkspace.reload') }}</button>
      <p v-if="activeBooks.length" role="status" class="text-xs text-text-dim">{{ t('audiobookWorkspace.background', { count: activeBooks.length }) }}</p>
    </aside>

    <div class="min-w-0 space-y-4 rounded-xl border border-border bg-panel p-5">
      <p v-if="error" role="alert" class="rounded-lg border border-status-failed/40 bg-status-failed/10 px-3 py-2 text-sm text-status-failed">{{ error }}</p>
      <p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>
      <p v-if="saving && !creating" role="status" class="text-sm text-text-dim">{{ t('audiobookWorkspace.creating') }}</p>

      <form v-if="creating" class="space-y-4" :aria-label="t('audiobookWorkspace.new')" :aria-busy="saving" @submit.prevent="onCreate">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h2 class="text-lg font-semibold text-text">{{ t('audiobookWorkspace.new') }}</h2>
          <button type="button" class="min-h-11 rounded-md px-2 text-xs text-text-dim hover:text-text hover:underline focus-visible:outline-2 focus-visible:outline-accent1" :disabled="deletingImport" @click="closeCreation">{{ t('audiobookWorkspace.back') }}</button>
        </div>
        <p class="text-sm text-text-dim">{{ t('audiobookWorkspace.intro') }}</p>
        <EbookImportPanel :disabled="saving || draftSaving" @deleting="active => deletingImport = active" @use-draft="useDraft" @deleted-draft="id => { if (importedDraft?.id === id) importedDraft = null }" />
        <div v-if="importedDraft" class="space-y-2 rounded-lg border border-border bg-panel-2 p-3"><p class="text-sm text-text">{{ t('audiobookWorkspace.importedFrom', { filename: importedDraft.source_filename }) }}</p><a :href="audiobooksApi.ebookSourceUrl(importedDraft.id)" download class="inline-block min-h-11 py-3 text-xs text-text underline decoration-accent2">{{ t('audiobookWorkspace.source') }}</a><ul v-if="importedDraft.warnings?.length" class="list-disc space-y-1 pl-5 text-sm text-status-queued"><li v-for="warning in importedDraft.warnings ?? []" :key="warning.code + warning.message">{{ warning.message }}</li></ul></div>
        <fieldset class="space-y-4" :disabled="saving || draftSaving">
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="block space-y-1">
              <span class="text-xs text-text-dim">{{ t('audiobooks.titleLabel') }}</span>
              <input ref="titleInput" v-model="title" type="text" maxlength="200" required :aria-label="t('audiobooks.titleLabel')" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" :placeholder="t('audiobooks.titlePlaceholder')" />
            </label>
            <label class="block space-y-1">
              <span class="text-xs text-text-dim">{{ t('audiobookWorkspace.author') }}</span>
              <input v-model="author" type="text" maxlength="200" :aria-label="t('audiobookWorkspace.author')" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" :placeholder="t('audiobookWorkspace.authorPlaceholder')" />
            </label>
            <label class="block space-y-1 sm:col-span-2">
              <span class="text-xs text-text-dim">{{ t('audiobookWorkspace.narrator') }}</span>
              <select v-model="profileId" required :aria-label="t('audiobookWorkspace.narrator')" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1">
                <option disabled value="">{{ t('audiobooks.profilePlaceholder') }}</option>
                <option v-if="profileId && !draftNarratorAvailable" disabled :value="profileId">{{ t('audiobookWorkspace.unavailableNarrator') }}</option>
                <option v-for="profile in narrators" :key="profile.id" :value="profile.id">{{ profile.name }}</option>
              </select>
            </label>
          </div>
          <p v-if="loadingProfiles" role="status" class="text-xs text-text-dim">{{ t('audiobookWorkspace.loadingNarrators') }}</p>
          <p v-if="!narrators.length" class="text-sm text-text-dim">{{ t('audiobookWorkspace.noProfiles') }}</p>
          <p v-else-if="profileId && !draftNarratorAvailable" class="text-sm text-status-failed">{{ t('audiobookWorkspace.errors.profileMissing') }}</p>
          <div class="flex flex-wrap items-center gap-3"><button type="button" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" :disabled="previewing || !draftNarratorAvailable || !includedChapters[0]?.text.trim()" @click="onPreview">{{ previewing ? t('audiobookWorkspace.previewing') : t('audiobookWorkspace.preview') }}</button><span v-if="preview?.status === 'mock_completed'" class="text-xs text-status-queued">{{ t('audiobookWorkspace.mockPreview') }}</span></div>
          <p v-if="previewError" role="alert" class="text-sm text-status-failed">{{ previewError }}</p>
          <audio v-if="previewUrl" ref="audioPlayers" :src="previewUrl" controls preload="none" :aria-label="t('audiobookWorkspace.previewReady')" class="h-10 w-full" />
          <div class="space-y-2 rounded-lg border border-border bg-panel-2 p-3">
            <p class="text-sm font-medium text-text">{{ t('audiobookWorkspace.pronunciations') }}</p>
            <p class="text-xs text-text-dim">{{ t('audiobookWorkspace.pronunciationHint') }}</p>
            <div v-for="(row, index) in pronunciations" :key="index" class="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
              <input v-model="row.written" type="text" maxlength="80" :aria-label="t('audiobookWorkspace.written')" class="min-h-11 rounded-lg border border-border bg-panel px-3 text-sm text-text" :placeholder="t('audiobookWorkspace.written')">
              <input v-model="row.spoken" type="text" maxlength="200" :aria-label="t('audiobookWorkspace.spoken')" class="min-h-11 rounded-lg border border-border bg-panel px-3 text-sm text-text" :placeholder="t('audiobookWorkspace.spoken')">
              <button type="button" class="min-h-11 rounded-md px-2 text-xs text-text-dim" :aria-label="t('audiobookWorkspace.removePronunciation')" @click="pronunciations.splice(index, 1)">{{ t('common.delete') }}</button>
            </div>
            <button type="button" class="min-h-11 rounded-md px-2 text-xs text-text-dim hover:text-text hover:underline" :disabled="pronunciations.length >= 100" @click="pronunciations.push({ written: '', spoken: '' })">{{ t('audiobookWorkspace.addPronunciation') }}</button>
          </div>
          <div class="space-y-3">
            <p v-if="importedDraft" class="text-xs text-text-dim">{{ t('audiobookWorkspace.included', { count: includedChapters.length }) }}</p>
            <label v-if="importedDraft" class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookWorkspace.reviewChapter') }}</span><select v-model.number="chapterEditorIndex" :aria-label="t('audiobookWorkspace.reviewChapter')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 text-sm text-text"><option v-for="(chapter, index) in chapters" :key="chapter.id" :value="index">{{ index + 1 }}. {{ chapter.title }}{{ chapter.included ? '' : ' · ' + t('audiobookWorkspace.excluded') }}</option></select></label>
            <div v-for="(chapter, index) in chapters" v-show="!importedDraft || index === chapterEditorIndex" :key="chapter.id" class="space-y-2 rounded-lg border border-border bg-panel-2 p-3">
              <label v-if="importedDraft || chapters.some(chapter => !chapter.included)" class="flex min-h-11 items-center gap-2 text-xs text-text"><input v-model="chapter.included" type="checkbox" class="size-4 accent-accent1">{{ t('audiobookWorkspace.include', { number: index + 1 }) }}</label>
              <div class="flex items-center justify-between gap-2">
                <label class="min-w-0 flex-1 space-y-1">
                  <span class="text-xs text-text-dim">{{ t('audiobookWorkspace.chapterTitle', { number: index + 1 }) }}</span>
                  <input v-model="chapter.title" type="text" maxlength="200" :aria-label="t('audiobookWorkspace.chapterTitle', { number: index + 1 })" class="w-full rounded-lg border border-border bg-panel p-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" :placeholder="t('audiobooks.chapterTitlePlaceholder')" />
                </label>
                <button type="button" class="min-h-11 shrink-0 rounded-md px-2 text-xs text-text-dim hover:underline focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-40" :disabled="chapters.length <= 1" :aria-describedby="chapters.length <= 1 ? 'audiobook-chapter-minimum' : undefined" :aria-label="t('audiobookWorkspace.removeChapter', { number: index + 1 })" @click="removeChapter(index)">{{ t('common.delete') }}</button>
              </div>
              <label class="block space-y-1">
                <span class="text-xs text-text-dim">{{ t('audiobookWorkspace.chapterText', { number: index + 1 }) }}</span>
                <textarea v-model="chapter.text" rows="5" maxlength="20000" :required="chapter.included" :disabled="!chapter.included" :aria-label="t('audiobookWorkspace.chapterText', { number: index + 1 })" class="w-full rounded-lg border border-border bg-panel p-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" :placeholder="t('audiobooks.chapterTextPlaceholder')" />
              </label>
              <p class="text-xs text-text-dim">{{ t('audiobookWorkspace.characters', { count: chapter.text.length }) }}</p>
            </div>
            <button type="button" class="min-h-11 rounded-md px-2 text-xs font-medium text-text-dim hover:text-text hover:underline focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-40" :disabled="chapters.length >= 100" @click="addChapter">{{ t('audiobooks.addChapter') }}</button>
            <p v-if="chapters.length <= 1" id="audiobook-chapter-minimum" class="text-xs text-text-dim">{{ t('audiobookWorkspace.chapterMinimum') }}</p>
            <p v-if="chapters.length >= 100" class="text-xs text-text-dim">{{ t('audiobookWorkspace.chapterLimit') }}</p>
          </div>
        </fieldset>
        <p class="text-xs text-text-dim">{{ t(importedDraft ? 'audiobookWorkspace.importedDraftHint' : 'audiobookWorkspace.draftHint') }}</p>
        <button v-if="importedDraft" type="button" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" :disabled="draftSaving || deletingImport || saving || !title.trim() || chapters.some(chapter => !chapter.text.trim())" @click="onSaveDraft">{{ t('audiobookWorkspace.saveDraft') }}</button>
        <p v-if="retrying" role="status" class="text-xs text-text-dim">{{ t('audiobookWorkspace.retrying') }}</p>
        <button type="submit" class="min-h-11 rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="loading || saving || retrying || loadingProfiles || draftSaving || deletingImport || previewing || !draftNarratorAvailable || !includedChapters.length">{{ saving ? t('audiobookWorkspace.creating') : t('audiobooks.create') }}</button>
      </form>

      <div v-else-if="selectedBook" data-book-editor class="space-y-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 class="break-words text-lg font-semibold text-text">{{ selectedBook.title }}</h2>
            <p class="mt-1 text-sm text-text-dim">{{ t(`audiobookWorkspace.status.${selectedBook.status}`) }} · {{ t('audiobooks.chapterCount', { count: selectedBook.chapter_count }) }}<span v-if="selectedBook.author"> · {{ selectedBook.author }}</span></p>
            <p class="mt-1 text-xs text-text-dim">{{ t('audiobookWorkspace.narrator') }}: {{ selectedNarrator || t('audiobookWorkspace.unavailableNarrator') }}</p>
          </div>
          <div class="flex flex-wrap gap-3">
            <button v-if="selectedBook.status === 'queued' || selectedBook.status === 'running'" type="button" class="min-h-11 rounded-lg border border-border px-3 text-xs text-text disabled:opacity-50" :disabled="controlling || loading || retrying" @click="onControl('pause')">{{ t('audiobookWorkspace.pause') }}</button>
            <button v-if="selectedBook.status === 'paused' || selectedBook.status === 'cancelled'" type="button" class="min-h-11 rounded-lg border border-border px-3 text-xs text-text disabled:opacity-50" :disabled="controlling || loading || retrying" @click="onControl('resume')">{{ t('audiobookWorkspace.resume') }}</button>
            <button v-if="['queued', 'running', 'paused'].includes(selectedBook.status)" type="button" class="min-h-11 rounded-lg border border-border px-3 text-xs text-text disabled:opacity-50" :disabled="controlling || loading || retrying" @click="onControl('cancel')">{{ t('audiobookWorkspace.cancel') }}</button>
            <button v-if="selectedBook.status === 'failed'" type="button" class="min-h-11 rounded-lg border border-accent1/50 px-3 py-2 text-xs font-medium text-accent1 focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="loading || retrying || saving" :aria-label="t('audiobooks.retry')" @click="onRetry">{{ retrying ? t('audiobookWorkspace.retrying') : t('audiobooks.retry') }}</button>
            <a v-if="selectedBook.status === 'done'" data-export-book class="rounded-lg bg-accent1 px-3 py-2 text-xs font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1" :href="audiobooksApi.audiobookExportUrl(selectedBook.id)" download>{{ t('audiobooks.downloadExport') }}</a>
            <a v-if="selectedBook.mp3_ready" class="rounded-lg border border-border px-3 py-2 text-xs font-medium text-text" :href="audiobooksApi.audiobookExportFormatUrl(selectedBook.id, 'mp3')" download>{{ t('audiobookWorkspace.downloadMp3') }}</a>
            <a v-if="selectedBook.m4b_ready" class="rounded-lg border border-border px-3 py-2 text-xs font-medium text-text" :href="audiobooksApi.audiobookExportFormatUrl(selectedBook.id, 'm4b')" download>{{ t('audiobookWorkspace.downloadM4b') }}</a>
            <a v-if="selectedBook.status === 'done'" class="rounded-lg border border-border px-3 py-2 text-xs font-medium text-text" :href="audiobooksApi.audiobookCueUrl(selectedBook.id)" download>{{ t('audiobookWorkspace.downloadCue') }}</a>
            <a v-if="selectedBook.status === 'done'" class="rounded-lg border border-border px-3 py-2 text-xs font-medium text-text" :href="audiobooksApi.audiobookCollectionUrl(selectedBook.id)" download>{{ t('audiobookWorkspace.downloadCollection') }}</a>
          </div>
        </div>
        <p v-if="selectedBook.status !== 'done'" class="text-xs text-text-dim">{{ t('audiobookWorkspace.boundaryHint') }}</p>
        <p v-if="selectedBook.status !== 'done'" class="text-xs text-text-dim">{{ t('audiobookWorkspace.exportHint') }}</p>
        <p v-if="exportNote(selectedBook.export_note)" class="text-xs text-status-queued">{{ exportNote(selectedBook.export_note) }}</p>
        <label v-if="selectedBook.status === 'done' || selectedBook.status === 'failed'" class="flex min-h-11 flex-wrap items-center gap-3 text-sm text-text"><span>{{ t('audiobookWorkspace.cover') }}</span><input type="file" accept="image/png,image/jpeg" :aria-label="t('audiobookWorkspace.cover')" class="text-xs" @change="onCover"></label>
        <div v-if="selectedBook.status !== 'queued' && selectedBook.status !== 'running'" class="space-y-2 rounded-lg border border-border p-3">
          <p class="text-sm font-medium text-text">{{ t('audiobookWorkspace.pronunciations') }}</p>
          <p class="text-xs text-text-dim">{{ t('audiobookWorkspace.pronunciationHint') }}</p>
          <div v-for="(row, index) in bookSpeech" :key="index" class="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <input v-model="row.written" type="text" maxlength="80" :aria-label="t('audiobookWorkspace.written')" class="min-h-11 rounded-lg border border-border bg-panel-2 px-3 text-sm text-text">
            <input v-model="row.spoken" type="text" maxlength="200" :aria-label="t('audiobookWorkspace.spoken')" class="min-h-11 rounded-lg border border-border bg-panel-2 px-3 text-sm text-text">
            <button type="button" class="min-h-11 text-xs text-text-dim" @click="bookSpeech.splice(index, 1)">{{ t('common.delete') }}</button>
          </div>
          <div class="flex flex-wrap gap-3">
            <button type="button" class="min-h-11 text-xs text-text-dim hover:underline" @click="bookSpeech.push({ written: '', spoken: '' })">{{ t('audiobookWorkspace.addPronunciation') }}</button>
            <button type="button" class="min-h-11 rounded-lg border border-border px-3 text-xs text-text disabled:opacity-50" :disabled="savingSpeech" @click="onSaveSpeech">{{ t('audiobookWorkspace.savePronunciations') }}</button>
          </div>
        </div>
        <div class="space-y-2 rounded-lg border border-border p-3">
          <p class="text-sm font-medium text-text">{{ t('audiobookWorkspace.language') }}</p>
          <p class="text-xs text-text-dim">{{ t('audiobookWorkspace.languageHint') }}</p>
          <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookWorkspace.language') }}</span><input v-model="bookLanguage" maxlength="35" type="text" :aria-label="t('audiobookWorkspace.language')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 text-sm text-text" :placeholder="t('audiobookWorkspace.languagePlaceholder')"></label>
          <button type="button" class="min-h-11 rounded-lg border border-border px-3 text-xs text-text disabled:opacity-50" :disabled="savingLanguages || selectedBook.status === 'queued' || selectedBook.status === 'running'" @click="onSaveLanguages">{{ t('audiobookWorkspace.saveLanguages') }}</button>
        </div>
        <div class="border-t border-border pt-4">
          <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 class="text-sm font-semibold text-text">{{ t('audiobooks.jobsTitle') }}</h3>
            <span v-if="jobs.length" class="text-xs text-text-dim">{{ t('audiobookWorkspace.completed', { done: completedChapters, total: selectedBook.chapter_count }) }}</span>
          </div>
          <p v-if="loadingJobs" role="status" class="text-sm text-text-dim">{{ t('common.loading') }}</p>
          <p v-if="jobsError" role="alert" class="text-sm text-status-failed">{{ jobsError }}</p>
          <button v-if="jobsError" type="button" class="mt-2 rounded-md text-xs text-text-dim hover:text-text hover:underline focus-visible:outline-2 focus-visible:outline-accent1" :disabled="loadingJobs" @click="loadJobs(selectedBook.id).then(updatePolling)">{{ t('audiobookWorkspace.reloadChapters') }}</button>
          <p v-if="!loadingJobs && !jobsError && !jobs.length" class="text-sm text-text-dim">{{ t('audiobookWorkspace.noJobs') }}</p>
          <ol class="space-y-3">
            <li v-for="job in jobs" :key="job.id" class="rounded-lg border border-border bg-panel-2 p-3">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <h4 class="text-sm font-medium text-text">{{ job.chapter_title || t('audiobookWorkspace.chapterName', { number: job.chapter_index + 1 }) }}</h4>
                <span class="text-xs" :class="job.status === 'failed' ? 'text-status-failed' : job.status === 'done' ? 'text-status-done' : 'text-text-dim'">{{ t(`audiobookWorkspace.status.${job.status}`) }}</span>
                <span data-language-chip class="rounded-full border border-border px-2 py-0.5 text-xs text-text-dim">{{ (chapterLanguages[job.id] || job.language) || t('audiobookWorkspace.languageMissing') }} · {{ job.language_ready ? t('audiobookWorkspace.languageReady') : t('audiobookWorkspace.languageNotReady') }}</span>
              </div>
              <label class="mt-2 block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookWorkspace.chapterLanguage', { number: job.chapter_index + 1 }) }}</span><input v-model="chapterLanguages[job.id]" maxlength="35" type="text" :aria-label="t('audiobookWorkspace.chapterLanguage', { number: job.chapter_index + 1 })" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3 text-sm text-text" :placeholder="t('audiobookWorkspace.languagePlaceholder')"></label>
              <p v-if="chapterHint(job)" class="mt-2 text-xs text-text-dim">{{ chapterHint(job) }}</p>
              <p v-if="job.total_sections" class="mt-2 text-xs text-text-dim">{{ t('audiobookWorkspace.sections', { done: job.completed_sections ?? 0, total: job.total_sections }) }}</p>
              <button v-if="selectedBook.status === 'done' || selectedBook.status === 'failed'" type="button" class="mt-3 min-h-11 rounded-lg border border-accent1/50 px-3 text-xs font-medium text-accent1 disabled:opacity-50" :disabled="regeneratingIndex !== null || retrying" :aria-label="t('audiobookWorkspace.regenerateChapter')" @click="onRegenerate(job.chapter_index)">{{ regeneratingIndex === job.chapter_index ? t('audiobookWorkspace.regenerating') : t('audiobookWorkspace.regenerateChapter') }}</button>
              <audio v-if="job.status === 'done'" ref="audioPlayers" class="mt-3 h-9 w-full" controls preload="none" :aria-label="t('audiobookWorkspace.playChapter', { title: job.chapter_title || t('audiobookWorkspace.chapterName', { number: job.chapter_index + 1 }) })" :src="audiobooksApi.audiobookChapterAudioUrl(selectedBook.id, job.chapter_index)" />
            </li>
          </ol>
        </div>
      </div>
      <div v-else class="space-y-2 py-6">
        <h2 class="text-lg font-semibold text-text">{{ t('audiobooks.title') }}</h2>
        <p class="text-sm text-text-dim">{{ t('audiobookWorkspace.chooseBook') }}</p>
      </div>
    </div>
  </section>
</template>

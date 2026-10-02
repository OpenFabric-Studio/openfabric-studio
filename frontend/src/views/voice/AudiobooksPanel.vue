<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import * as audiobooksApi from '../../api/audiobooks'
import * as profilesApi from '../../api/voiceProfiles'
import type { AudiobookBook, AudiobookJob, CreateAudiobookRequest } from '../../api/audiobooks'
import type { SpeechVoiceProfile } from '../../api/voiceProfiles'
import { createPollingLoop } from '../../composables/polling'

const emit = defineEmits<{ activity: [message: string] }>()
const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true })
const { t } = useI18n()
type ChapterDraft = { id: number; title: string; text: string }
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
const titleInput = ref<HTMLInputElement | null>(null)
const newBookButton = ref<HTMLButtonElement | null>(null)
const audioPlayers = ref<HTMLAudioElement[]>([])
const profileId = ref('')
let chapterSequence = 0
function newChapter(number: number): ChapterDraft {
  return { id: ++chapterSequence, title: t('audiobookWorkspace.chapterName', { number }), text: '' }
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
    }
  }
  return t(fallback)
}

function chapterHint(job: AudiobookJob): string {
  // Worker detail may contain local paths, server addresses or raw exceptions.
  if (job.detail?.startsWith('Dry-run speech clone wrote a silent placeholder WAV')) return t('audiobookWorkspace.mockAudio')
  if (job.status === 'failed') {
    if (job.detail === 'consent_required') return t('audiobookWorkspace.errors.consent')
    if (job.detail?.startsWith('engine_not_installed:')) return t('audiobookWorkspace.errors.engineMissing')
    if (job.detail?.startsWith('api_unavailable:')) return t('audiobookWorkspace.errors.apiUnavailable')
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
    return activeBooks.value.length > 0
  } catch (err) {
    if (current()) error.value = safeError(err, 'audiobookWorkspace.errors.refresh')
    return false
  }
}, 2000)

function updatePolling() {
  if (!mounted || loading.value || loadingJobs.value || saving.value || retrying.value || !activeBooks.value.length) polling.stop()
  else polling.start(false)
}

async function loadWorkspace() {
  if (!mounted || saving.value || retrying.value || loadingProfiles.value) return
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
  if (!mounted) return
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
async function closeCreation() {
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
function addChapter() { if (!saving.value && chapters.value.length < 100) chapters.value.push(newChapter(chapters.value.length + 1)) }
function removeChapter(index: number) { if (!saving.value && chapters.value.length > 1) chapters.value.splice(index, 1) }
function upsertBook(book: AudiobookBook) {
  const existing = books.value.findIndex(item => item.id === book.id)
  if (existing < 0) books.value.unshift(book)
  else books.value.splice(existing, 1, book)
}

async function onCreate() {
  if (!mounted || loading.value || saving.value || retrying.value || loadingProfiles.value) return
  error.value = ''
  notice.value = ''
  const trimmedTitle = title.value.trim()
  if (!trimmedTitle) { error.value = t('audiobooks.err.title'); return }
  if (!narrators.value.some(profile => profile.id === profileId.value)) { error.value = t('audiobooks.err.profile'); return }
  const payloadChapters = chapters.value.map(chapter => ({ title: chapter.title.trim(), text: chapter.text.trim() }))
  if (payloadChapters.some(chapter => !chapter.text)) { error.value = t('audiobookWorkspace.errors.emptyChapter'); return }
  const body: CreateAudiobookRequest = { title: trimmedTitle, profile_id: profileId.value, chapters: payloadChapters }
  const controller = new AbortController()
  createController = controller
  const generation = selectionGeneration
  const isCurrent = () => mounted && !controller.signal.aborted
  saving.value = true
  polling.stop()
  try {
    const created = await audiobooksApi.createAudiobook(body, controller.signal)
    if (!isCurrent()) return
    upsertBook(created.book)
    title.value = ''
    chapters.value = [newChapter(1)]
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
          <button type="button" :data-select-book="book.id" :aria-current="book.id === selectedBookId && !creating ? 'true' : undefined" class="w-full rounded-lg border p-3 text-left focus-visible:outline-2 focus-visible:outline-accent1" :class="book.id === selectedBookId && !creating ? 'border-accent1/60 bg-accent1/10' : 'border-border bg-panel-2 hover:border-accent1/40'" @click="onSelectBook(book)">
            <span class="block truncate text-sm font-medium text-text">{{ book.title }}</span>
            <span class="mt-1 block text-xs text-text-dim">{{ t(`audiobookWorkspace.status.${book.status}`) }} · {{ t('audiobooks.chapterCount', { count: book.chapter_count }) }}</span>
          </button>
        </li>
      </ul>
      <button type="button" class="min-h-11 rounded-md px-2 text-xs text-text-dim hover:text-text hover:underline focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="loading || loadingProfiles || saving || retrying" @click="loadWorkspace">{{ t('audiobookWorkspace.reload') }}</button>
      <p v-if="activeBooks.length" role="status" class="text-xs text-text-dim">{{ t('audiobookWorkspace.background', { count: activeBooks.length }) }}</p>
    </aside>

    <div class="min-w-0 space-y-4 rounded-xl border border-border bg-panel p-5">
      <p v-if="error" role="alert" class="rounded-lg border border-status-failed/40 bg-status-failed/10 px-3 py-2 text-sm text-status-failed">{{ error }}</p>
      <p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>
      <p v-if="saving && !creating" role="status" class="text-sm text-text-dim">{{ t('audiobookWorkspace.creating') }}</p>

      <form v-if="creating" class="space-y-4" :aria-label="t('audiobookWorkspace.new')" :aria-busy="saving" @submit.prevent="onCreate">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h2 class="text-lg font-semibold text-text">{{ t('audiobookWorkspace.new') }}</h2>
          <button type="button" class="min-h-11 rounded-md px-2 text-xs text-text-dim hover:text-text hover:underline focus-visible:outline-2 focus-visible:outline-accent1" @click="closeCreation">{{ t('audiobookWorkspace.back') }}</button>
        </div>
        <p class="text-sm text-text-dim">{{ t('audiobookWorkspace.intro') }}</p>
        <fieldset class="space-y-4" :disabled="saving">
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="block space-y-1">
              <span class="text-xs text-text-dim">{{ t('audiobooks.titleLabel') }}</span>
              <input ref="titleInput" v-model="title" type="text" maxlength="200" required :aria-label="t('audiobooks.titleLabel')" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" :placeholder="t('audiobooks.titlePlaceholder')" />
            </label>
            <label class="block space-y-1">
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
          <div class="space-y-3">
            <div v-for="(chapter, index) in chapters" :key="chapter.id" class="space-y-2 rounded-lg border border-border bg-panel-2 p-3">
              <div class="flex items-center justify-between gap-2">
                <label class="min-w-0 flex-1 space-y-1">
                  <span class="text-xs text-text-dim">{{ t('audiobookWorkspace.chapterTitle', { number: index + 1 }) }}</span>
                  <input v-model="chapter.title" type="text" maxlength="200" :aria-label="t('audiobookWorkspace.chapterTitle', { number: index + 1 })" class="w-full rounded-lg border border-border bg-panel p-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" :placeholder="t('audiobooks.chapterTitlePlaceholder')" />
                </label>
                <button type="button" class="min-h-11 shrink-0 rounded-md px-2 text-xs text-status-failed hover:underline focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-40" :disabled="chapters.length <= 1" :aria-describedby="chapters.length <= 1 ? 'audiobook-chapter-minimum' : undefined" :aria-label="t('audiobookWorkspace.removeChapter', { number: index + 1 })" @click="removeChapter(index)">{{ t('common.delete') }}</button>
              </div>
              <label class="block space-y-1">
                <span class="text-xs text-text-dim">{{ t('audiobookWorkspace.chapterText', { number: index + 1 }) }}</span>
                <textarea v-model="chapter.text" rows="5" maxlength="20000" required :aria-label="t('audiobookWorkspace.chapterText', { number: index + 1 })" class="w-full rounded-lg border border-border bg-panel p-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" :placeholder="t('audiobooks.chapterTextPlaceholder')" />
              </label>
            </div>
            <button type="button" class="min-h-11 rounded-md px-2 text-xs font-medium text-text-dim hover:text-text hover:underline focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-40" :disabled="chapters.length >= 100" @click="addChapter">{{ t('audiobooks.addChapter') }}</button>
            <p v-if="chapters.length <= 1" id="audiobook-chapter-minimum" class="text-xs text-text-dim">{{ t('audiobookWorkspace.chapterMinimum') }}</p>
            <p v-if="chapters.length >= 100" class="text-xs text-text-dim">{{ t('audiobookWorkspace.chapterLimit') }}</p>
          </div>
        </fieldset>
        <p class="text-xs text-text-dim">{{ t('audiobookWorkspace.draftHint') }}</p>
        <p v-if="retrying" role="status" class="text-xs text-text-dim">{{ t('audiobookWorkspace.retrying') }}</p>
        <button type="submit" class="min-h-11 rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="loading || saving || retrying || loadingProfiles || !draftNarratorAvailable">{{ saving ? t('audiobookWorkspace.creating') : t('audiobooks.create') }}</button>
      </form>

      <div v-else-if="selectedBook" data-book-editor class="space-y-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 class="break-words text-lg font-semibold text-text">{{ selectedBook.title }}</h2>
            <p class="mt-1 text-sm text-text-dim">{{ t(`audiobookWorkspace.status.${selectedBook.status}`) }} · {{ t('audiobooks.chapterCount', { count: selectedBook.chapter_count }) }}</p>
            <p class="mt-1 text-xs text-text-dim">{{ t('audiobookWorkspace.narrator') }}: {{ selectedNarrator || t('audiobookWorkspace.unavailableNarrator') }}</p>
          </div>
          <div class="flex flex-wrap gap-3">
            <button v-if="selectedBook.status === 'failed'" type="button" class="min-h-11 rounded-lg border border-accent1/50 px-3 py-2 text-xs font-medium text-accent1 focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="loading || retrying || saving" :aria-label="t('audiobooks.retry')" @click="onRetry">{{ retrying ? t('audiobookWorkspace.retrying') : t('audiobooks.retry') }}</button>
            <a v-if="selectedBook.status === 'done'" data-export-book class="rounded-lg bg-accent1 px-3 py-2 text-xs font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1" :href="audiobooksApi.audiobookExportUrl(selectedBook.id)" download>{{ t('audiobooks.downloadExport') }}</a>
          </div>
        </div>
        <p v-if="selectedBook.status !== 'done'" class="text-xs text-text-dim">{{ t('audiobookWorkspace.exportHint') }}</p>
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
              </div>
              <p v-if="chapterHint(job)" class="mt-2 text-xs text-text-dim">{{ chapterHint(job) }}</p>
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

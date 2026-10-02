<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import * as audiobooksApi from '../../api/audiobooks'
import * as profilesApi from '../../api/voiceProfiles'
import type { AudiobookBook, AudiobookJob } from '../../api/audiobooks'
import type { SpeechVoiceProfile } from '../../api/voiceProfiles'

const { t } = useI18n()

type ChapterDraft = { title: string; text: string }

const profiles = ref<SpeechVoiceProfile[]>([])
const books = ref<AudiobookBook[]>([])
const jobs = ref<AudiobookJob[]>([])
const selectedBookId = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const title = ref('')
const profileId = ref('')
const chapters = ref<ChapterDraft[]>([{ title: 'Chapter 1', text: '' }])
let pollTimer: number | null = null

async function refreshProfiles() {
  profiles.value = await profilesApi.listSpeechVoiceProfiles()
  if (!profileId.value && profiles.value.length) {
    profileId.value = profiles.value[0].id
  }
}

async function refreshBooks() {
  books.value = await audiobooksApi.listAudiobooks()
  if (!selectedBookId.value && books.value.length) {
    selectedBookId.value = books.value[0].id
  }
  if (selectedBookId.value) {
    await refreshJobs(selectedBookId.value)
  } else {
    jobs.value = []
  }
}

async function refreshJobs(bookId: string) {
  jobs.value = await audiobooksApi.listAudiobookJobs(bookId)
}

function addChapter() {
  chapters.value.push({ title: `Chapter ${chapters.value.length + 1}`, text: '' })
}

function removeChapter(index: number) {
  if (chapters.value.length <= 1) return
  chapters.value.splice(index, 1)
}

async function onCreate() {
  error.value = ''
  notice.value = ''
  const trimmedTitle = title.value.trim()
  if (!trimmedTitle) {
    error.value = t('audiobooks.err.title')
    return
  }
  if (!profileId.value) {
    error.value = t('audiobooks.err.profile')
    return
  }
  const payloadChapters = chapters.value
    .map((chapter) => ({ title: chapter.title.trim(), text: chapter.text.trim() }))
    .filter((chapter) => chapter.text.length > 0)
  if (!payloadChapters.length) {
    error.value = t('audiobooks.err.chapters')
    return
  }
  saving.value = true
  try {
    const created = await audiobooksApi.createAudiobook({
      title: trimmedTitle,
      profile_id: profileId.value,
      chapters: payloadChapters,
    })
    title.value = ''
    chapters.value = [{ title: 'Chapter 1', text: '' }]
    selectedBookId.value = created.book.id
    notice.value = t('audiobooks.created')
    await refreshBooks()
    startPolling()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : t('audiobooks.err.create')
  } finally {
    saving.value = false
  }
}

async function onSelectBook(book: AudiobookBook) {
  selectedBookId.value = book.id
  error.value = ''
  await refreshJobs(book.id)
  startPolling()
}

async function onRetry() {
  if (!selectedBookId.value) return
  error.value = ''
  try {
    await audiobooksApi.retryAudiobook(selectedBookId.value)
    notice.value = t('audiobooks.retried')
    await refreshBooks()
    startPolling()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : t('audiobooks.err.retry')
  }
}

function exportHref(): string {
  return selectedBookId.value ? audiobooksApi.audiobookExportUrl(selectedBookId.value) : '#'
}

function selectedBook(): AudiobookBook | undefined {
  return books.value.find((book) => book.id === selectedBookId.value)
}

function startPolling() {
  stopPolling()
  const book = selectedBook()
  if (!book || book.status === 'done' || book.status === 'failed') return
  pollTimer = window.setInterval(() => {
    void (async () => {
      try {
        await refreshBooks()
        const current = selectedBook()
        if (!current || current.status === 'done' || current.status === 'failed') {
          stopPolling()
        }
      } catch {
        stopPolling()
      }
    })()
  }, 2000)
}

function stopPolling() {
  if (pollTimer != null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(async () => {
  loading.value = true
  error.value = ''
  try {
    await refreshProfiles()
    await refreshBooks()
    startPolling()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : t('audiobooks.err.load')
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(() => stopPolling())
</script>

<template>
  <section class="space-y-4 rounded-xl border border-border bg-panel p-5">
    <div>
      <h2 class="text-lg font-semibold text-text">{{ t('audiobooks.title') }}</h2>
      <p class="mt-1 text-sm text-text-dim">{{ t('audiobooks.intro') }}</p>
    </div>

    <p v-if="error" class="rounded-lg border border-status-failed/40 bg-status-failed/10 px-3 py-2 text-sm text-status-failed">
      {{ error }}
    </p>
    <p v-if="notice" class="text-sm text-status-done">{{ notice }}</p>
    <p v-if="loading" class="text-sm text-text-dim">{{ t('common.loading') }}</p>

    <form class="space-y-3" @submit.prevent="onCreate">
      <label class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('audiobooks.titleLabel') }}</span>
        <input
          v-model="title"
          type="text"
          maxlength="200"
          class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"
          :placeholder="t('audiobooks.titlePlaceholder')"
        />
      </label>
      <label class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('audiobooks.profileLabel') }}</span>
        <select
          v-model="profileId"
          class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"
        >
          <option disabled value="">{{ t('audiobooks.profilePlaceholder') }}</option>
          <option v-for="profile in profiles" :key="profile.id" :value="profile.id">
            {{ profile.name }}
          </option>
        </select>
      </label>

      <div class="space-y-3">
        <div
          v-for="(chapter, index) in chapters"
          :key="index"
          class="space-y-2 rounded-lg border border-border bg-panel-2 p-3"
        >
          <div class="flex items-center justify-between gap-2">
            <input
              v-model="chapter.title"
              type="text"
              maxlength="200"
              class="w-full rounded-lg border border-border bg-panel p-2 text-sm text-text"
              :placeholder="t('audiobooks.chapterTitlePlaceholder')"
            />
            <button
              type="button"
              class="shrink-0 text-xs text-status-failed hover:underline disabled:opacity-40"
              :disabled="chapters.length <= 1"
              @click="removeChapter(index)"
            >
              {{ t('common.delete') }}
            </button>
          </div>
          <textarea
            v-model="chapter.text"
            rows="4"
            maxlength="20000"
            class="w-full rounded-lg border border-border bg-panel p-2 text-sm text-text"
            :placeholder="t('audiobooks.chapterTextPlaceholder')"
          />
        </div>
        <button
          type="button"
          class="text-xs font-medium text-accent1 hover:underline"
          @click="addChapter"
        >
          {{ t('audiobooks.addChapter') }}
        </button>
      </div>

      <button
        type="submit"
        class="rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        :disabled="saving || !profiles.length"
      >
        {{ saving ? t('common.loading') : t('audiobooks.create') }}
      </button>
    </form>

    <div class="space-y-3 border-t border-border pt-4">
      <h3 class="text-sm font-semibold text-text">{{ t('audiobooks.booksTitle') }}</h3>
      <p v-if="!books.length" class="text-sm text-text-dim">{{ t('audiobooks.empty') }}</p>
      <ul v-else class="space-y-2">
        <li
          v-for="book in books"
          :key="book.id"
          class="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2"
          :class="book.id === selectedBookId ? 'border-accent1/60 bg-panel-2' : 'border-border bg-panel-2'"
        >
          <button type="button" class="min-w-0 text-left" @click="onSelectBook(book)">
            <p class="truncate text-sm font-medium text-text">{{ book.title }}</p>
            <p class="text-xs text-text-dim">
              {{ book.status }} · {{ t('audiobooks.chapterCount', { count: book.chapter_count }) }}
            </p>
          </button>
          <a
            v-if="book.status === 'done'"
            class="text-xs font-medium text-accent1 hover:underline"
            :href="audiobooksApi.audiobookExportUrl(book.id)"
            :download="`${book.title || 'audiobook'}.wav`"
          >
            {{ t('common.download') }}
          </a>
        </li>
      </ul>

      <div v-if="selectedBookId" class="space-y-2">
        <div class="flex flex-wrap items-center gap-3">
          <h4 class="text-xs font-semibold uppercase tracking-wide text-text-dim">
            {{ t('audiobooks.jobsTitle') }}
          </h4>
          <button
            v-if="selectedBook()?.status === 'failed'"
            type="button"
            class="text-xs font-medium text-accent1 hover:underline"
            @click="onRetry"
          >
            {{ t('audiobooks.retry') }}
          </button>
          <a
            v-if="selectedBook()?.status === 'done'"
            class="text-xs font-medium text-accent1 hover:underline"
            :href="exportHref()"
            download
          >
            {{ t('audiobooks.downloadExport') }}
          </a>
        </div>
        <ul class="space-y-1">
          <li
            v-for="job in jobs"
            :key="job.id"
            class="rounded-lg border border-border bg-panel px-3 py-2 text-xs text-text-dim"
          >
            <span class="font-medium text-text">{{ job.chapter_title || `#${job.chapter_index + 1}` }}</span>
            · {{ job.status }}
            <span v-if="job.detail"> — {{ job.detail }}</span>
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>

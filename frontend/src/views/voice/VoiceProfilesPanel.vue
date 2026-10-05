<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as profilesApi from '../../api/voiceProfiles'
import type { SpeechCloneEngineStatus, SpeechCloneTrialResponse, SpeechVoiceProfile } from '../../api/voiceProfiles'
import { useDialogA11y } from '../../composables/useDialogA11y'
import StarterSpeechVoices from './StarterSpeechVoices.vue'
import SpeechAudioPreview from './SpeechAudioPreview.vue'
import LocalEnginePanel from './LocalEnginePanel.vue'
import { ApiError } from '../../api/http'
import SpeechReferenceDetails from './SpeechReferenceDetails.vue'

const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true })
const emit = defineEmits<{ activity: [message: string] }>()
const { t } = useI18n()
const workspace = ref<HTMLElement | null>(null)
const profiles = ref<SpeechVoiceProfile[]>([])
const search = ref('')
const selectedId = ref('')
const selectedProfile = computed(() => profiles.value.find(profile => profile.id === selectedId.value))
const filteredProfiles = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return profiles.value.filter(profile => `${profile.name} ${profile.notes ?? ''}`.toLocaleLowerCase().includes(query))
})
const loading = ref(false)
const engineLoading = ref(false)
const saving = ref(false)
const cloning = ref(false)
const deleting = ref(false)
const importingId = ref('')
const starterCatalogExpanded = ref<boolean | null>(null)
const error = ref('')
const notice = ref('')
const engine = ref<SpeechCloneEngineStatus | null>(null)
const engineError = ref('')
const createVisited = ref(false)
const createOpen = ref(false)
const name = ref('')
const notes = ref('')
const referenceTranscript = ref(''), referenceLanguage = ref('en')
const consent = ref(false)
const file = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const nameInput = ref<HTMLInputElement | null>(null)
const trialText = ref('')
const trialLanguage = ref('en')
const trialTextInput = ref<HTMLTextAreaElement | null>(null)
const trialResult = ref<SpeechCloneTrialResponse | null>(null)
const trialAudioUrl = computed(() => {
  const result = trialResult.value
  return result && (result.status === 'completed' || result.status === 'mock_completed') ? profilesApi.speechTrialAudioUrl(result.trial_id) : null
})
const setupHints = computed(() => [...new Set([...(engine.value?.install_hints ?? []), ...(trialResult.value?.install_hints ?? [])])])
const deleteTarget = ref<SpeechVoiceProfile | null>(null)
const deleteDialog = ref<HTMLElement | null>(null)
useDialogA11y(deleteDialog, () => props.active && deleteTarget.value !== null, closeDelete)
watch(() => props.active, async active => {
  if (active) return
  const focused = document.activeElement
  const outside = focused instanceof HTMLElement && !workspace.value?.contains(focused) ? focused : null
  if (!deleting.value) deleteTarget.value = null
  await nextTick()
  if (alive && !props.active && outside?.isConnected) outside.focus()
})

let alive = true
let selectionGeneration = 0
let trialGeneration = 0
let loadController: AbortController | null = null
let createController: AbortController | null = null
let deleteController: AbortController | null = null
let trialController: AbortController | null = null
let importController: AbortController | null = null

const activity = computed(() => {
  if (saving.value) return t('speechWorkspace.createPending')
  if (importingId.value) return t('speechWorkspace.starters.importPending')
  if (deleting.value) return t('speechWorkspace.deletePending')
  if (cloning.value) return t('speechWorkspace.trialPending')
  if (loading.value) return t('speechWorkspace.loadPending')
  return error.value || engineError.value || (engineLoading.value ? t('speechWorkspace.enginePending') : '')
})
watch(activity, message => { if (alive) emit('activity', message) }, { immediate: true })

async function refresh() {
  if (loading.value || engineLoading.value || saving.value || deleting.value || importingId.value || !alive) return
  const controller = new AbortController()
  loadController = controller
  loading.value = true
  engineLoading.value = true
  error.value = ''
  engineError.value = ''
  try {
    const isCurrent = () => alive && loadController === controller && !controller.signal.aborted
    const profileRequest = profilesApi.listSpeechVoiceProfiles(controller.signal).then(response => {
      if (!isCurrent()) return
      profiles.value = response
      if (!profiles.value.some(profile => profile.id === selectedId.value)) selectProfile(profiles.value[0]?.id ?? '')
    }).catch(() => {
      if (isCurrent()) error.value = t('voiceProfiles.err.load')
    }).finally(() => {
      if (isCurrent()) loading.value = false
    })
    const engineRequest = profilesApi.getSpeechCloneEngine(controller.signal).then(response => {
      if (isCurrent()) engine.value = response
    }).catch(() => {
      if (isCurrent()) { engine.value = null; engineError.value = t('speechWorkspace.engineLoadError') }
    }).finally(() => {
      if (isCurrent()) engineLoading.value = false
    })
    await Promise.allSettled([profileRequest, engineRequest])
  } finally {
    if (alive && loadController === controller) { loading.value = false; engineLoading.value = false; loadController = null }
  }
}

function selectProfile(id: string) {
  if (id && !profiles.value.some(profile => profile.id === id)) return
  ++selectionGeneration
  if (id !== selectedId.value) {
    ++trialGeneration
    trialController?.abort()
    trialController = null
    cloning.value = false
    selectedId.value = id
    trialResult.value = null
    error.value = ''
    notice.value = ''
    if (!deleting.value) deleteTarget.value = null
  }
  createOpen.value = false
}

async function selectStarterProfile(id: string) {
  if (!alive || !profiles.value.some(profile => profile.id === id)) return
  selectProfile(id)
  starterCatalogExpanded.value = false
  const generation = selectionGeneration
  await nextTick()
  if (alive && props.active && !createOpen.value && generation === selectionGeneration && selectedId.value === id) trialTextInput.value?.focus()
}

async function openCreate() {
  if (loading.value || !alive) return
  ++selectionGeneration
  createVisited.value = true
  createOpen.value = true
  error.value = ''
  notice.value = ''
  await nextTick()
  if (alive && props.active && createOpen.value) nameInput.value?.focus()
}

function onFilePicked(event: Event) {
  if (event.target instanceof HTMLInputElement) file.value = event.target.files?.[0] ?? null
}

async function onCreate() {
  if (saving.value || loading.value || importingId.value || !alive) return
  error.value = ''
  notice.value = ''
  if (!consent.value) { error.value = t('voiceProfiles.err.consent'); return }
  if (!file.value) { error.value = t('voiceProfiles.err.audio'); return }
  const trimmed = name.value.trim()
  if (!trimmed || trimmed.length > 120) { error.value = t('voiceProfiles.err.name'); return }
  const controller = new AbortController()
  const selection = selectionGeneration
  createController = controller
  saving.value = true
  try {
    const created = await profilesApi.createSpeechVoiceProfile({
      name: trimmed, consentConfirmed: true, audio: file.value, notes: notes.value.trim() || undefined,
      referenceTranscript: referenceTranscript.value.trim(), referenceLanguage: referenceLanguage.value.trim(),
    }, controller.signal)
    if (!alive || controller.signal.aborted) return
    profiles.value = [...profiles.value.filter(profile => profile.id !== created.id), created]
    name.value = ''; notes.value = ''; referenceTranscript.value = ''; referenceLanguage.value = 'en'; consent.value = false; file.value = null
    if (fileInput.value) fileInput.value.value = ''
    createOpen.value = false
    if (selection === selectionGeneration) selectProfile(created.id)
    notice.value = t('voiceProfiles.created')
  } catch {
    if (alive && !controller.signal.aborted) error.value = t('voiceProfiles.err.create')
  } finally {
    if (alive && createController === controller) { saving.value = false; createController = null }
  }
}

async function importStarter(id: string) {
  if (!alive || loading.value || saving.value || deleting.value || importingId.value) return
  const controller = new AbortController()
  const generation = selectionGeneration
  const draftWasOpen = createOpen.value
  importController = controller
  importingId.value = id
  error.value = ''
  notice.value = ''
  try {
    const imported = await profilesApi.importStarterSpeechVoice(id, controller.signal)
    if (!alive || controller.signal.aborted) return
    if (imported.starter_voice_id !== id) throw new TypeError('Starter import identifier mismatch')
    profiles.value = [...profiles.value.filter(profile => profile.id !== imported.id), imported]
    if (selectionGeneration === generation && !draftWasOpen && !createOpen.value) void selectStarterProfile(imported.id)
    notice.value = t('speechWorkspace.starters.imported')
  } catch {
    if (alive && !controller.signal.aborted) error.value = t('speechWorkspace.starters.importError')
  } finally {
    if (alive && importController === controller) { importingId.value = ''; importController = null }
  }
}

function closeDelete() { if (!deleting.value) deleteTarget.value = null }
function requestDelete() {
  if (deleting.value || loading.value || !props.active || !selectedProfile.value) return
  deleteTarget.value = selectedProfile.value
}
async function onDelete() {
  if (deleting.value || loading.value || !deleteTarget.value || !alive) return
  const target = deleteTarget.value
  const controller = new AbortController()
  deleteController = controller
  deleting.value = true
  error.value = ''
  notice.value = ''
  try {
    await profilesApi.deleteSpeechVoiceProfile(target.id, controller.signal)
    if (!alive || controller.signal.aborted) return
    profiles.value = profiles.value.filter(profile => profile.id !== target.id)
    if (selectedId.value === target.id) selectProfile(profiles.value[0]?.id ?? '')
    deleteTarget.value = null
    notice.value = t('voiceProfiles.deleted')
  } catch {
    if (alive && !controller.signal.aborted) error.value = t('voiceProfiles.err.delete')
  } finally {
    if (alive && deleteController === controller) { deleting.value = false; deleteController = null }
  }
}

async function onTrial() {
  if (cloning.value || !alive || deleting.value && deleteTarget.value?.id === selectedId.value) return
  error.value = ''
  notice.value = ''
  trialResult.value = null
  const profile = selectedProfile.value
  const text = trialText.value.trim()
  if (!profile) { error.value = t('voiceProfiles.err.trialProfile'); return }
  if (!profile.consent_confirmed) { error.value = t('speechWorkspace.consentRequired'); return }
  if (!text || text.length > 8000) { error.value = t('voiceProfiles.err.trialText'); return }
  const controller = new AbortController()
  const generation = ++trialGeneration
  trialController = controller
  cloning.value = true
  const current = () => alive && !controller.signal.aborted && generation === trialGeneration && selectedId.value === profile.id
  try {
    const result = await profilesApi.startSpeechCloneTrial(profile.id, text, controller.signal, trialLanguage.value)
    if (!current()) return
    if (result.profile_id !== profile.id) { error.value = t('voiceProfiles.err.trial'); return }
    trialResult.value = result
    switch (result.status) {
      case 'mock_completed': notice.value = t('speechWorkspace.trialMock'); break
      case 'completed': notice.value = t('speechWorkspace.trialCompleted'); break
      case 'engine_ready': notice.value = t('speechWorkspace.trialEngineReady'); break
      case 'api_unavailable': error.value = t('speechWorkspace.trialApiUnavailable'); break
      case 'engine_not_installed': error.value = t('speechWorkspace.trialMissing'); break
      case 'failed': error.value = t(result.detail === 'reference_transcript_required' ? 'audiobookReview.referenceRequired' : result.detail === 'speech_language_unsupported' ? 'audiobookReview.languageUnsupported' : 'voiceProfiles.err.trial'); break
    }
  } catch (err) {
    if (current()) error.value = t(err instanceof ApiError && err.message === 'reference_transcript_required' ? 'audiobookReview.referenceRequired' : err instanceof ApiError && err.message === 'speech_language_unsupported' ? 'audiobookReview.languageUnsupported' : 'voiceProfiles.err.trial')
  } finally {
    if (current()) { cloning.value = false; trialController = null }
  }
}

onMounted(() => { void refresh() })
onBeforeUnmount(() => {
  alive = false
  ++trialGeneration
  ++selectionGeneration
  loadController?.abort(); createController?.abort(); deleteController?.abort(); trialController?.abort()
  importController?.abort()
})
</script>

<template>
  <section ref="workspace" class="grid min-w-0 gap-5 lg:grid-cols-[15rem_minmax(0,1fr)]" :aria-label="t('voiceProfiles.title')">
    <aside class="self-start rounded-xl border border-border bg-panel p-4" :aria-label="t('speechWorkspace.library')">
      <div class="mb-4 flex items-center justify-between gap-3">
        <h2 class="text-sm font-semibold text-text">{{ t('voiceProfiles.title') }}</h2>
        <button type="button" :disabled="loading" class="min-h-11 shrink-0 rounded-lg bg-accent1 px-3 py-2 text-xs font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50" @click="openCreate">{{ t('speechWorkspace.newProfile') }}</button>
      </div>
      <label class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('speechWorkspace.search') }}</span>
        <input v-model="search" type="search" :aria-label="t('speechWorkspace.search')" class="w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" />
      </label>
      <p v-if="loading" role="status" class="mt-4 text-sm text-text-dim">{{ t('common.loading') }}</p>
      <ul v-if="filteredProfiles.length" class="mt-3 max-h-80 space-y-1 overflow-y-auto lg:max-h-[65vh]">
        <li v-for="profile in filteredProfiles" :key="profile.id">
          <button type="button" :aria-label="profile.name" :aria-pressed="selectedId === profile.id" class="w-full rounded-lg border px-3 py-3 text-left focus-visible:outline-2 focus-visible:outline-accent1" :class="selectedId === profile.id ? 'border-accent1 bg-accent1/10' : 'border-transparent hover:bg-panel-2'" @click="selectProfile(profile.id)">
            <span class="block truncate text-sm font-medium text-text">{{ profile.name }}</span>
            <span class="mt-1 block text-xs text-text-dim">{{ !profile.consent_confirmed ? t('voiceProfiles.consentMissing') : profile.starter_voice_id ? t('speechWorkspace.licensedReference') : t('voiceProfiles.consentOk') }}</span>
          </button>
        </li>
      </ul>
      <p v-else-if="!loading" class="mt-4 text-sm text-text-dim">{{ profiles.length ? t('speechWorkspace.noMatches') : t('voiceProfiles.empty') }}</p>
    </aside>

    <div class="min-w-0 space-y-4">
      <p v-if="error" role="alert" class="rounded-lg border border-status-failed/40 bg-status-failed/10 px-4 py-3 text-sm text-status-failed">{{ error }}</p>
      <p v-if="notice" role="status" class="rounded-lg border border-status-done/30 bg-status-done/5 px-4 py-3 text-sm text-status-done">{{ notice }}</p>
      <StarterSpeechVoices v-model:expanded="starterCatalogExpanded" :profiles="profiles" :active="active" :disabled="loading || saving || deleting" :importing-id="importingId" @import="importStarter" @select="selectStarterProfile" />

      <form v-if="createVisited" v-show="createOpen" :aria-label="t('speechWorkspace.createTitle')" class="space-y-4 rounded-xl border border-border bg-panel p-5" @submit.prevent="onCreate">
        <div class="flex items-start justify-between gap-3">
          <div><h2 class="text-lg font-semibold text-text">{{ t('speechWorkspace.createTitle') }}</h2><p class="mt-1 text-sm text-text-dim">{{ t('speechWorkspace.createIntro') }}</p></div>
          <button type="button" class="rounded-lg px-2 py-1 text-sm text-text-dim hover:bg-panel-2 focus-visible:outline-2 focus-visible:outline-accent1" :disabled="saving" @click="createOpen = false">{{ t('common.close') }}</button>
        </div>
        <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('voiceProfiles.nameLabel') }}</span><input ref="nameInput" v-model="name" type="text" maxlength="120" :aria-label="t('voiceProfiles.nameLabel')" :placeholder="t('voiceProfiles.namePlaceholder')" :disabled="saving" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" /></label>
        <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('voiceProfiles.notesLabel') }}</span><textarea v-model="notes" rows="2" maxlength="2000" :aria-label="t('voiceProfiles.notesLabel')" :placeholder="t('voiceProfiles.notesPlaceholder')" :disabled="saving" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" /><span class="block text-xs text-text-dim">{{ t('speechWorkspace.notesHelp') }}</span></label>
        <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookReview.transcript') }}</span><textarea v-model="referenceTranscript" rows="3" maxlength="2000" :aria-label="t('audiobookReview.transcript')" :disabled="saving" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" /></label>
        <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookReview.referenceLanguage') }}</span><input v-model="referenceLanguage" maxlength="16" :aria-label="t('audiobookReview.referenceLanguage')" :disabled="saving" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" /></label>
        <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('voiceProfiles.audioLabel') }}</span><input ref="fileInput" type="file" accept=".wav,.flac,audio/wav,audio/flac" :aria-label="t('voiceProfiles.audioLabel')" :disabled="saving" class="block w-full text-sm text-text-dim file:mr-3 file:rounded-lg file:border-0 file:bg-panel-2 file:px-3 file:py-2 file:text-sm file:text-text focus-visible:outline-2 focus-visible:outline-accent1" @change="onFilePicked" /></label>
        <label class="flex items-start gap-2 text-sm text-text"><input v-model="consent" type="checkbox" :aria-label="t('voiceProfiles.consentLabel')" :disabled="saving" class="mt-1 focus-visible:outline-2 focus-visible:outline-accent1" /><span>{{ t('voiceProfiles.consentLabel') }}</span></label>
        <p v-if="!consent" class="text-xs text-text-dim">{{ t('voiceProfiles.err.consent') }}</p>
        <button type="submit" class="rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="saving || !!importingId || !consent">{{ saving ? t('speechWorkspace.createPending') : t('voiceProfiles.create') }}</button>
      </form>

      <form v-show="!createOpen" :aria-label="t('speechWorkspace.synthesis')" class="space-y-4 rounded-xl border border-border bg-panel p-5" @submit.prevent="onTrial">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div><h2 class="text-lg font-semibold text-text">{{ selectedProfile?.name ?? t('speechWorkspace.synthesis') }}</h2><p class="mt-1 text-sm text-text-dim">{{ t('speechWorkspace.synthesisIntro') }}</p></div>
          <button v-if="selectedProfile" type="button" :disabled="deleting || loading" class="rounded-lg border border-border px-3 py-1.5 text-xs text-text-dim hover:border-status-failed hover:text-status-failed focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" @click="requestDelete">{{ t('speechWorkspace.deleteProfile') }}</button>
        </div>
        <template v-if="selectedProfile">
          <p class="text-xs" :class="selectedProfile.consent_confirmed ? 'text-text-dim' : 'text-status-failed'">{{ !selectedProfile.consent_confirmed ? t('voiceProfiles.consentMissing') : selectedProfile.starter_voice_id ? t('speechWorkspace.licensedReference') : t('voiceProfiles.consentOk') }}</p>
          <p v-if="selectedProfile.notes" class="whitespace-pre-wrap break-words text-sm text-text-dim">{{ selectedProfile.notes }}</p>
          <SpeechReferenceDetails :key="selectedProfile.id" :profile="selectedProfile" :disabled="cloning || deleting" @saved="updated => { profiles = profiles.map(profile => profile.id === updated.id ? updated : profile) }" />
          <label class="block space-y-2"><span class="text-sm font-medium text-text">{{ t('voiceProfiles.trialTextLabel') }}</span><textarea ref="trialTextInput" v-model="trialText" rows="7" maxlength="8000" :aria-label="t('voiceProfiles.trialTextLabel')" :placeholder="t('voiceProfiles.trialTextPlaceholder')" class="w-full rounded-lg border border-border bg-panel-2 p-3 text-sm leading-relaxed text-text focus-visible:outline-2 focus-visible:outline-accent1" /></label>
          <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookReview.outputLanguage') }}</span><select v-model="trialLanguage" :disabled="cloning" :aria-label="t('audiobookReview.outputLanguage')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"><option value="en">English</option><option value="zh">中文</option><option value="ja">日本語</option><option value="ko">한국어</option><option value="yue">粵語</option></select></label>
          <p v-if="!selectedProfile.consent_confirmed" class="text-xs text-status-failed">{{ t('speechWorkspace.consentRequired') }}</p>
          <p v-else-if="cloning" role="status" class="text-xs text-text-dim">{{ t('speechWorkspace.trialPendingHelp') }}</p>
          <p v-else-if="deleting && deleteTarget?.id === selectedId" class="text-xs text-text-dim">{{ t('speechWorkspace.deletePending') }}</p>
          <p v-else-if="!trialText.trim()" class="text-xs text-text-dim">{{ t('voiceProfiles.err.trialText') }}</p>
          <button type="submit" class="rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="cloning || !selectedProfile.consent_confirmed || !trialText.trim() || deleting && deleteTarget?.id === selectedId">{{ cloning ? t('speechWorkspace.trialPending') : t('voiceProfiles.trialRun') }}</button>
          <div v-if="trialAudioUrl" data-speech-trial class="space-y-3 rounded-lg border border-border bg-panel-2 p-3">
            <p class="text-sm text-text-dim">{{ t(trialResult?.status === 'mock_completed' ? 'speechWorkspace.trialMock' : 'speechWorkspace.trialCompleted') }}</p>
            <SpeechAudioPreview :src="trialAudioUrl" :label="t(trialResult?.status === 'mock_completed' ? 'speechWorkspace.mockTrialAudio' : 'speechWorkspace.trialAudio')" :active="active && !createOpen" />
            <a :href="trialAudioUrl" download class="inline-block min-h-11 rounded-lg px-2 py-3 text-sm text-text underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('speechWorkspace.downloadTrial') }}</a>
          </div>
          <p v-else-if="trialResult?.output_path && (trialResult.status === 'completed' || trialResult.status === 'mock_completed')" class="rounded-lg border border-border bg-panel-2 p-3 text-sm text-text-dim">{{ t('speechWorkspace.playbackUnavailable') }}</p>
        </template>
        <p v-else class="text-sm text-text-dim">{{ t('speechWorkspace.selectOrCreate') }}</p>
      </form>

      <LocalEnginePanel kind="speech" :active="active" />

      <section class="rounded-xl border border-border bg-panel p-4" :aria-label="t('speechWorkspace.engineStatus')">
        <div class="flex items-start justify-between gap-3"><h3 class="text-sm font-semibold text-text">{{ t('speechWorkspace.engineStatus') }}</h3><button type="button" :disabled="loading || engineLoading || saving || deleting || !!importingId" class="text-xs text-text-dim hover:text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" @click="refresh">{{ t('speechWorkspace.refresh') }}</button></div>
        <p v-if="engineLoading" role="status" class="mt-3 text-xs text-text-dim">{{ t('speechWorkspace.enginePending') }}</p>
        <dl class="mt-3 grid gap-3 text-xs sm:grid-cols-3">
          <div><dt class="text-text-dim">{{ t('speechWorkspace.engineInstalled') }}</dt><dd class="mt-1 font-medium text-text">{{ engine ? (engine.installed ? t('speechWorkspace.installed') : t('speechWorkspace.missing')) : t('speechWorkspace.unknown') }}</dd></div>
          <div><dt class="text-text-dim">{{ t('speechWorkspace.engineMock') }}</dt><dd class="mt-1 font-medium text-text">{{ engine ? (engine.mock ? t('speechWorkspace.enabled') : t('speechWorkspace.disabled')) : t('speechWorkspace.unknown') }}</dd></div>
          <div><dt class="text-text-dim">{{ t('speechWorkspace.engineApi') }}</dt><dd class="mt-1 font-medium text-text">{{ engine?.api_reachable === undefined ? t('speechWorkspace.unknown') : (engine.api_reachable ? t('speechWorkspace.reachable') : t('speechWorkspace.unreachable')) }}</dd></div>
        </dl>
        <p v-if="engine?.mock" class="mt-3 text-xs text-text-dim">{{ t('speechWorkspace.mockHelp') }}</p>
        <p v-if="engineError" role="status" class="mt-3 text-xs text-status-failed">{{ engineError }}</p>
        <details class="mt-4 border-t border-border pt-3 text-xs text-text-dim"><summary class="cursor-pointer rounded focus-visible:outline-2 focus-visible:outline-accent1">{{ t('speechWorkspace.engineHelp') }}</summary><p class="mt-3">{{ t('speechWorkspace.engineHelpIntro') }}</p><ul v-if="setupHints.length" class="mt-2 list-disc space-y-1 break-words pl-4"><li v-for="hint in setupHints" :key="hint">{{ hint }}</li></ul></details>
      </section>
    </div>

    <div v-if="deleteTarget && active" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" @click.self="closeDelete">
      <div ref="deleteDialog" data-voice-confirmation role="dialog" aria-modal="true" aria-labelledby="speech-delete-heading" aria-describedby="speech-delete-description" tabindex="-1" class="w-full max-w-md space-y-4 rounded-xl border border-border bg-panel p-5 shadow-2xl">
        <h3 id="speech-delete-heading" class="break-words text-lg font-semibold text-text">{{ t('speechWorkspace.deleteConfirm', { name: deleteTarget.name }) }}</h3>
        <p id="speech-delete-description" class="text-sm text-text-dim">{{ t('speechWorkspace.deleteDescription') }}</p>
        <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
        <div class="flex justify-end gap-3"><button type="button" :disabled="deleting" class="rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" @click="closeDelete">{{ t('common.cancel') }}</button><button type="button" :disabled="deleting" class="rounded-lg bg-status-failed px-4 py-2 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-status-failed disabled:opacity-50" @click="onDelete">{{ deleting ? t('speechWorkspace.deletePending') : t('common.delete') }}</button></div>
      </div>
    </div>
  </section>
</template>

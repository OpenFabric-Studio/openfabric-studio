<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import * as profilesApi from '../../api/voiceProfiles'
import type { SpeechCloneTrialResponse, SpeechVoiceProfile } from '../../api/voiceProfiles'

const { t } = useI18n()

const profiles = ref<SpeechVoiceProfile[]>([])
const loading = ref(false)
const saving = ref(false)
const cloning = ref(false)
const error = ref('')
const notice = ref('')
const name = ref('')
const notes = ref('')
const consent = ref(false)
const file = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const trialProfileId = ref('')
const trialText = ref('')
const trialResult = ref<SpeechCloneTrialResponse | null>(null)
const engineHint = ref('')

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    profiles.value = await profilesApi.listSpeechVoiceProfiles()
    if (!trialProfileId.value && profiles.value.length) {
      trialProfileId.value = profiles.value[0].id
    }
    try {
      const engine = await profilesApi.getSpeechCloneEngine()
      if (engine.mock) engineHint.value = t('voiceProfiles.engineMock')
      else if (engine.installed) engineHint.value = t('voiceProfiles.engineReady')
      else engineHint.value = t('voiceProfiles.engineMissing')
    } catch {
      engineHint.value = ''
    }
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : t('voiceProfiles.err.load')
  } finally {
    loading.value = false
  }
}

function onFilePicked(event: Event) {
  const input = event.target as HTMLInputElement
  file.value = input.files?.[0] ?? null
}

async function onCreate() {
  error.value = ''
  notice.value = ''
  if (!consent.value) {
    error.value = t('voiceProfiles.err.consent')
    return
  }
  if (!file.value) {
    error.value = t('voiceProfiles.err.audio')
    return
  }
  const trimmed = name.value.trim()
  if (!trimmed) {
    error.value = t('voiceProfiles.err.name')
    return
  }
  saving.value = true
  try {
    const created = await profilesApi.createSpeechVoiceProfile({
      name: trimmed,
      consentConfirmed: true,
      audio: file.value,
      notes: notes.value.trim() || undefined,
    })
    name.value = ''
    notes.value = ''
    consent.value = false
    file.value = null
    if (fileInput.value) fileInput.value.value = ''
    notice.value = t('voiceProfiles.created')
    trialProfileId.value = created.id
    await refresh()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : t('voiceProfiles.err.create')
  } finally {
    saving.value = false
  }
}

async function onDelete(profile: SpeechVoiceProfile) {
  error.value = ''
  try {
    await profilesApi.deleteSpeechVoiceProfile(profile.id)
    notice.value = t('voiceProfiles.deleted')
    if (trialProfileId.value === profile.id) trialProfileId.value = ''
    await refresh()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : t('voiceProfiles.err.delete')
  }
}

async function onTrial() {
  error.value = ''
  notice.value = ''
  trialResult.value = null
  const text = trialText.value.trim()
  if (!trialProfileId.value) {
    error.value = t('voiceProfiles.err.trialProfile')
    return
  }
  if (!text) {
    error.value = t('voiceProfiles.err.trialText')
    return
  }
  cloning.value = true
  try {
    const result = await profilesApi.startSpeechCloneTrial(trialProfileId.value, text)
    trialResult.value = result
    if (result.status === 'mock_completed') notice.value = t('voiceProfiles.trialMock')
    else if (result.status === 'engine_ready') notice.value = t('voiceProfiles.trialReady')
    else if (result.status === 'engine_not_installed') notice.value = t('voiceProfiles.trialMissing')
    else notice.value = result.detail
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : t('voiceProfiles.err.trial')
  } finally {
    cloning.value = false
  }
}

onMounted(() => { void refresh() })
</script>

<template>
  <section class="space-y-4 rounded-xl border border-border bg-panel p-5">
    <div>
      <h2 class="text-lg font-semibold text-text">{{ t('voiceProfiles.title') }}</h2>
      <p class="mt-1 text-sm text-text-dim">{{ t('voiceProfiles.intro') }}</p>
      <p v-if="engineHint" class="mt-1 text-xs text-text-dim">{{ engineHint }}</p>
    </div>

    <p v-if="error" class="rounded-lg border border-status-failed/40 bg-status-failed/10 px-3 py-2 text-sm text-status-failed">
      {{ error }}
    </p>
    <p v-if="notice" class="text-sm text-status-done">{{ notice }}</p>
    <p v-if="loading" class="text-sm text-text-dim">{{ t('common.loading') }}</p>

    <ul v-if="profiles.length" class="space-y-2">
      <li
        v-for="profile in profiles"
        :key="profile.id"
        class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-panel-2 px-3 py-2"
      >
        <div class="min-w-0">
          <p class="truncate text-sm font-medium text-text">{{ profile.name }}</p>
          <p class="text-xs text-text-dim">
            {{ profile.consent_confirmed ? t('voiceProfiles.consentOk') : t('voiceProfiles.consentMissing') }}
            <span v-if="profile.notes"> · {{ profile.notes }}</span>
          </p>
        </div>
        <button type="button" class="text-xs text-status-failed hover:underline" @click="onDelete(profile)">
          {{ t('common.delete') }}
        </button>
      </li>
    </ul>
    <p v-else-if="!loading" class="text-sm text-text-dim">{{ t('voiceProfiles.empty') }}</p>

    <form class="space-y-3 border-t border-border pt-4" @submit.prevent="onCreate">
      <label class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('voiceProfiles.nameLabel') }}</span>
        <input
          v-model="name"
          type="text"
          maxlength="120"
          class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"
          :placeholder="t('voiceProfiles.namePlaceholder')"
        />
      </label>
      <label class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('voiceProfiles.notesLabel') }}</span>
        <input
          v-model="notes"
          type="text"
          maxlength="2000"
          class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"
          :placeholder="t('voiceProfiles.notesPlaceholder')"
        />
      </label>
      <label class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('voiceProfiles.audioLabel') }}</span>
        <input
          ref="fileInput"
          type="file"
          accept=".wav,.flac,audio/wav,audio/flac"
          class="block w-full text-sm text-text-dim file:mr-3 file:rounded-lg file:border-0 file:bg-accent1 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-white"
          @change="onFilePicked"
        />
      </label>
      <label class="flex items-start gap-2 text-sm text-text">
        <input v-model="consent" type="checkbox" class="mt-1" required />
        <span>{{ t('voiceProfiles.consentLabel') }}</span>
      </label>
      <button
        type="submit"
        class="rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        :disabled="saving || !consent"
      >
        {{ saving ? t('common.loading') : t('voiceProfiles.create') }}
      </button>
    </form>

    <form class="space-y-3 border-t border-border pt-4" @submit.prevent="onTrial">
      <h3 class="text-sm font-semibold text-text">{{ t('voiceProfiles.trialTitle') }}</h3>
      <p class="text-xs text-text-dim">{{ t('voiceProfiles.trialIntro') }}</p>
      <label class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('voiceProfiles.trialProfileLabel') }}</span>
        <select
          v-model="trialProfileId"
          class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"
          :disabled="!profiles.length"
        >
          <option disabled value="">{{ t('voiceProfiles.trialProfilePlaceholder') }}</option>
          <option v-for="profile in profiles" :key="profile.id" :value="profile.id">
            {{ profile.name }}
          </option>
        </select>
      </label>
      <label class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('voiceProfiles.trialTextLabel') }}</span>
        <textarea
          v-model="trialText"
          rows="3"
          maxlength="8000"
          class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"
          :placeholder="t('voiceProfiles.trialTextPlaceholder')"
        />
      </label>
      <button
        type="submit"
        class="rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        :disabled="cloning || !profiles.length"
      >
        {{ cloning ? t('common.loading') : t('voiceProfiles.trialRun') }}
      </button>
      <div v-if="trialResult" class="rounded-lg border border-border bg-panel-2 p-3 text-xs text-text-dim space-y-1">
        <p><span class="font-medium text-text">{{ trialResult.status }}</span> — {{ trialResult.detail }}</p>
        <p v-if="trialResult.output_path">{{ t('voiceProfiles.trialOutput') }}: {{ trialResult.output_path }}</p>
        <ul v-if="trialResult.install_hints?.length" class="list-disc pl-4">
          <li v-for="(hint, index) in trialResult.install_hints" :key="index">{{ hint }}</li>
        </ul>
      </div>
    </form>
  </section>
</template>

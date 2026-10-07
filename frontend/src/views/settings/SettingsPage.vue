<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { routeLocationKey } from 'vue-router'
import { useI18n } from 'vue-i18n'
import LibraryFolder from '../../components/shared/LibraryFolder.vue'
import ArtistSettings from './ArtistSettings.vue'
import GenerationSettings from './GenerationSettings.vue'
import ModuleSetupPanel from './ModuleSetupPanel.vue'
import ProvidersPanel from './ProvidersPanel.vue'
import EngineSupportPanel from './EngineSupportPanel.vue'
import LoudnessControls from '../../components/shared/LoudnessControls.vue'
import { getAudioSettings, saveAudioSettings, type CompleteAudioEncodingSettings } from '../../api/audioSettings'
import { parseAudioEncodingSettings } from '../../api/contracts'

const { t } = useI18n()
type SettingsTab = 'setup' | 'providers' | 'audio' | 'library' | 'preferences' | 'support'
const tab = ref<SettingsTab>('setup')
const tabs: SettingsTab[] = ['setup', 'providers', 'library', 'audio', 'preferences', 'support']
const route = inject(routeLocationKey, undefined)
watch(() => route?.hash, hash => { if (hash?.startsWith('#module-')) tab.value = 'setup'; else if (hash === '#providers') tab.value = 'providers'; else if (hash === '#support') tab.value = 'support' }, { immediate: true })
function tabLabel(value: SettingsTab) { return value === 'setup' ? t('moduleWorkspace.title') : value === 'providers' ? t('cloudProviders.title') : value === 'support' ? t('supportWorkspace.tab') : t(`settingsWorkspace.tabs.${value}`) }
function moveTab(event: KeyboardEvent, index: number) {
  let next: number
  if (event.key === 'ArrowRight') next = (index + 1) % tabs.length
  else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = tabs.length - 1
  else return
  event.preventDefault(); const value = tabs[next]; if (value) { tab.value = value; document.getElementById(`settings-tab-${value}`)?.focus() }
}
const draft = ref<CompleteAudioEncodingSettings | null>(null)
const saved = ref<CompleteAudioEncodingSettings | null>(null)
const defaults = ref<CompleteAudioEncodingSettings | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const dirty = computed(() => !!draft.value && JSON.stringify(draft.value) !== JSON.stringify(saved.value))
const valid = computed(() => {
  if (!draft.value) return false
  try { parseAudioEncodingSettings(draft.value); return true } catch { return false }
})
const sampleRates: CompleteAudioEncodingSettings['mp3']['sample_rate'][] = [44100, 48000]
const bitrates: CompleteAudioEncodingSettings['mp3']['bitrate_kbps'][] = [128, 192, 256, 320]
const wavBits: CompleteAudioEncodingSettings['wav']['bit_depth'][] = [16, 24, 32]
const flacBits: CompleteAudioEncodingSettings['flac']['bit_depth'][] = [16, 24]
let active = true
let operation = 0
let revision = 0
let controller: AbortController | null = null

function copy(settings: CompleteAudioEncodingSettings): CompleteAudioEncodingSettings {
  return { mp3: { ...settings.mp3 }, wav: { ...settings.wav }, flac: { ...settings.flac }, loudness: { ...settings.loudness } }
}
watch(draft, () => { revision++ }, { deep: true, flush: 'sync' })

async function load() {
  if (saving.value || loading.value) return
  const token = ++operation
  const initialRevision = revision
  const preserveDraft = dirty.value
  controller?.abort()
  const request = new AbortController(); controller = request
  loading.value = true; error.value = ''; notice.value = ''
  try {
    const response = await getAudioSettings(request.signal)
    if (!active || token !== operation) return
    const edited = preserveDraft || revision !== initialRevision
    saved.value = copy(response.settings); defaults.value = copy(response.defaults)
    if (!edited) draft.value = copy(response.settings)
    else notice.value = t('settingsWorkspace.reloadedKeepingDraft')
  } catch {
    if (active && token === operation) error.value = t('settingsWorkspace.loadFailed')
  } finally {
    if (active && token === operation) { loading.value = false; controller = null }
  }
}

async function save() {
  if (saving.value || loading.value || !draft.value || !dirty.value || !valid.value) return
  const submitted = copy(draft.value)
  const initialRevision = revision
  const token = ++operation
  controller?.abort()
  const request = new AbortController(); controller = request
  saving.value = true; error.value = ''; notice.value = ''
  try {
    const response = await saveAudioSettings(submitted, request.signal)
    if (!active || token !== operation) return
    saved.value = copy(response.settings); defaults.value = copy(response.defaults)
    if (revision === initialRevision) draft.value = copy(response.settings)
    notice.value = t(dirty.value ? 'settingsWorkspace.savedWithDraft' : 'settingsWorkspace.saved')
  } catch {
    if (active && token === operation) error.value = t('settingsWorkspace.saveFailed')
  } finally {
    if (active && token === operation) { saving.value = false; controller = null }
  }
}

function resetDefaults() {
  if (!defaults.value || saving.value || loading.value) return
  draft.value = copy(defaults.value); error.value = ''
  notice.value = t(dirty.value ? 'settingsWorkspace.defaultsStaged' : 'settingsWorkspace.defaultsAlreadySaved')
}
onMounted(() => { void load() })
onBeforeUnmount(() => { active = false; operation++; controller?.abort(); controller = null })
</script>

<template>
  <div class="mx-auto max-w-5xl space-y-8">
    <div>
      <h1 class="text-2xl font-semibold text-text">{{ t('settingsWorkspace.title') }}</h1>
      <p class="mt-2 text-sm text-text-dim">{{ t('settingsWorkspace.intro') }}</p>
    </div>
    <div role="tablist" :aria-label="t('settingsWorkspace.title')" class="flex flex-wrap gap-1 border-b border-border pb-2"><button v-for="(value, index) in tabs" :id="`settings-tab-${value}`" :key="value" type="button" role="tab" :aria-selected="tab === value" :aria-controls="`settings-panel-${value}`" :tabindex="tab === value ? 0 : -1" class="settings-button border-transparent" :class="tab === value ? 'bg-accent1/15 text-accent2' : 'text-text-dim'" @click="tab = value" @keydown="moveTab($event, index)">{{ tabLabel(value) }}</button></div>
    <div v-show="tab === 'setup'" id="settings-panel-setup" role="tabpanel" aria-labelledby="settings-tab-setup"><ModuleSetupPanel /></div>
    <div v-show="tab === 'providers'" id="settings-panel-providers" role="tabpanel" aria-labelledby="settings-tab-providers"><ProvidersPanel v-if="tab === 'providers'" :active="true" /></div>
    <div v-if="tab === 'support'" id="settings-panel-support" role="tabpanel" aria-labelledby="settings-tab-support"><EngineSupportPanel /></div>
    <section v-show="tab === 'audio'" id="settings-panel-audio" role="tabpanel" aria-labelledby="settings-tab-audio" class="space-y-4 rounded-xl border border-border bg-panel p-5" :aria-busy="loading || saving">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-lg font-semibold text-text">{{ t('settingsWorkspace.audioTitle') }}</h2>
          <p class="mt-2 max-w-3xl text-sm text-text-dim">{{ t('settingsWorkspace.futureExports') }}</p>
        </div>
        <button v-if="draft" type="button" class="settings-button" :disabled="loading || saving" @click="load">{{ t('settingsWorkspace.reload') }}</button>
      </div>
      <p class="rounded-lg border border-border bg-panel-2 p-3 text-sm text-text-dim">{{ t('settingsWorkspace.sourceLimits') }}</p>
      <p v-if="loading" role="status" class="text-sm text-text-dim">{{ t('settingsWorkspace.loading') }}</p>
      <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
      <button v-if="!draft && error" type="button" class="settings-button" :disabled="loading" @click="load">{{ t('settingsWorkspace.retryLoad') }}</button>
      <form v-if="draft" class="space-y-5" @submit.prevent="save">
        <LoudnessControls v-model="draft.loudness" :disabled="saving || loading" />
        <div class="grid gap-4 lg:grid-cols-3">
          <fieldset class="settings-format">
            <legend class="px-1 text-sm font-semibold text-text">MP3</legend>
            <p class="text-xs text-text-dim">{{ t('settingsWorkspace.mp3Hint') }}</p>
            <label class="settings-label">
              <span>{{ t('settingsWorkspace.mp3Mode') }}</span>
              <select v-model="draft.mp3.mode" :aria-label="t('settingsWorkspace.mp3Mode')" class="settings-select"><option value="cbr">{{ t('settingsWorkspace.cbr') }}</option><option value="vbr">{{ t('settingsWorkspace.vbr') }}</option></select>
            </label>
            <label v-if="draft.mp3.mode === 'cbr'" class="settings-label">
              <span>{{ t('settingsWorkspace.mp3Bitrate') }}</span>
              <select v-model.number="draft.mp3.bitrate_kbps" :aria-label="t('settingsWorkspace.mp3Bitrate')" class="settings-select"><option v-for="rate in bitrates" :key="rate" :value="rate">{{ rate }} kbps</option></select>
            </label>
            <label v-else class="settings-label">
              <span>{{ t('settingsWorkspace.mp3Quality') }}</span>
              <select v-model.number="draft.mp3.vbr_quality" :aria-label="t('settingsWorkspace.mp3Quality')" class="settings-select"><option v-for="quality in 10" :key="quality" :value="quality - 1">{{ quality - 1 }}</option></select>
              <span class="text-xs text-text-dim">{{ t('settingsWorkspace.vbrHint') }}</span>
            </label>
            <label class="settings-label">
              <span>{{ t('settingsWorkspace.sampleRate', { format: 'MP3' }) }}</span>
              <select v-model.number="draft.mp3.sample_rate" :aria-label="t('settingsWorkspace.sampleRate', { format: 'MP3' })" class="settings-select"><option v-for="rate in sampleRates" :key="rate" :value="rate">{{ rate / 1000 }} kHz</option></select>
            </label>
            <label class="settings-label">
              <span>{{ t('settingsWorkspace.channels', { format: 'MP3' }) }}</span>
              <select v-model.number="draft.mp3.channels" :aria-label="t('settingsWorkspace.channels', { format: 'MP3' })" class="settings-select"><option :value="1">{{ t('settingsWorkspace.mono') }}</option><option :value="2">{{ t('settingsWorkspace.stereo') }}</option></select>
            </label>
          </fieldset>
          <fieldset class="settings-format">
            <legend class="px-1 text-sm font-semibold text-text">WAV</legend>
            <p class="text-xs text-text-dim">{{ t('settingsWorkspace.wavHint') }}</p>
            <label class="settings-label">
              <span>{{ t('settingsWorkspace.bitDepth', { format: 'WAV' }) }}</span>
              <select v-model.number="draft.wav.bit_depth" :aria-label="t('settingsWorkspace.bitDepth', { format: 'WAV' })" class="settings-select"><option v-for="bits in wavBits" :key="bits" :value="bits">{{ bits === 32 ? t('settingsWorkspace.float32') : t('settingsWorkspace.pcmBits', { bits }) }}</option></select>
            </label>
            <label class="settings-label">
              <span>{{ t('settingsWorkspace.sampleRate', { format: 'WAV' }) }}</span>
              <select v-model.number="draft.wav.sample_rate" :aria-label="t('settingsWorkspace.sampleRate', { format: 'WAV' })" class="settings-select"><option v-for="rate in sampleRates" :key="rate" :value="rate">{{ rate / 1000 }} kHz</option></select>
            </label>
            <label class="settings-label">
              <span>{{ t('settingsWorkspace.channels', { format: 'WAV' }) }}</span>
              <select v-model.number="draft.wav.channels" :aria-label="t('settingsWorkspace.channels', { format: 'WAV' })" class="settings-select"><option :value="1">{{ t('settingsWorkspace.mono') }}</option><option :value="2">{{ t('settingsWorkspace.stereo') }}</option></select>
            </label>
          </fieldset>
          <fieldset class="settings-format">
            <legend class="px-1 text-sm font-semibold text-text">FLAC</legend>
            <p class="text-xs text-text-dim">{{ t('settingsWorkspace.flacHint') }}</p>
            <label class="settings-label">
              <span>{{ t('settingsWorkspace.bitDepth', { format: 'FLAC' }) }}</span>
              <select v-model.number="draft.flac.bit_depth" :aria-label="t('settingsWorkspace.bitDepth', { format: 'FLAC' })" class="settings-select"><option v-for="bits in flacBits" :key="bits" :value="bits">{{ t('settingsWorkspace.pcmBits', { bits }) }}</option></select>
            </label>
            <label class="settings-label">
              <span>{{ t('settingsWorkspace.flacCompression') }}</span>
              <select v-model.number="draft.flac.compression_level" :aria-label="t('settingsWorkspace.flacCompression')" class="settings-select"><option v-for="level in 9" :key="level" :value="level - 1">{{ level - 1 }}</option></select>
              <span class="text-xs text-text-dim">{{ t('settingsWorkspace.flacCompressionHint') }}</span>
            </label>
            <label class="settings-label">
              <span>{{ t('settingsWorkspace.sampleRate', { format: 'FLAC' }) }}</span>
              <select v-model.number="draft.flac.sample_rate" :aria-label="t('settingsWorkspace.sampleRate', { format: 'FLAC' })" class="settings-select"><option v-for="rate in sampleRates" :key="rate" :value="rate">{{ rate / 1000 }} kHz</option></select>
            </label>
            <label class="settings-label">
              <span>{{ t('settingsWorkspace.channels', { format: 'FLAC' }) }}</span>
              <select v-model.number="draft.flac.channels" :aria-label="t('settingsWorkspace.channels', { format: 'FLAC' })" class="settings-select"><option :value="1">{{ t('settingsWorkspace.mono') }}</option><option :value="2">{{ t('settingsWorkspace.stereo') }}</option></select>
            </label>
          </fieldset>
        </div>
        <p v-if="!valid" role="alert" class="text-sm text-status-failed">{{ t('settingsWorkspace.invalid') }}</p>
        <p v-else-if="dirty" class="text-sm text-status-queued">{{ t('settingsWorkspace.unsaved') }}</p>
        <div class="flex flex-wrap gap-3">
          <button type="submit" class="settings-button accent-gradient border-transparent text-white" :disabled="saving || loading || !dirty || !valid">{{ saving ? t('settingsWorkspace.saving') : t('settingsWorkspace.save') }}</button>
          <button type="button" class="settings-button" :disabled="saving || loading || !defaults" @click="resetDefaults">{{ t('settingsWorkspace.reset') }}</button>
        </div>
      </form>
      <p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>
    </section>
    <div v-show="tab === 'library'" id="settings-panel-library" role="tabpanel" aria-labelledby="settings-tab-library"><LibraryFolder /></div>
    <div v-show="tab === 'preferences'" id="settings-panel-preferences" role="tabpanel" aria-labelledby="settings-tab-preferences" class="space-y-8"><GenerationSettings /><ArtistSettings /></div>
  </div>
</template>

<style scoped>
@reference "../../style.css";
.settings-format { @apply space-y-3 rounded-lg border border-border bg-panel-2/40 p-4; }
.settings-label { @apply block space-y-1 text-xs text-text-dim; }
.settings-select { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent1; }
.settings-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

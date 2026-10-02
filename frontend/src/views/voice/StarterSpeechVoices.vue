<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { listStarterSpeechVoices } from '../../api/voiceProfiles'
import type { SpeechVoiceProfile, StarterSpeechVoice } from '../../api/contracts'
import SpeechAudioPreview from './SpeechAudioPreview.vue'

const props = withDefaults(defineProps<{ profiles: SpeechVoiceProfile[]; active?: boolean; disabled?: boolean; importingId?: string; expanded?: boolean | null }>(), { active: true, disabled: false, importingId: '', expanded: null })
const emit = defineEmits<{ import: [id: string]; select: [id: string]; 'update:expanded': [value: boolean] }>()
const { t } = useI18n()
const voices = ref<StarterSpeechVoice[]>([])
const loading = ref(false)
const error = ref(false)
const expanded = computed(() => props.expanded ?? props.profiles.length === 0)
let alive = true
let controller: AbortController | undefined
function toggle() { emit('update:expanded', !expanded.value) }
function existingProfile(id: string) { return props.profiles.find(profile => profile.starter_voice_id === id) }
function choose(voice: StarterSpeechVoice) {
  const existing = existingProfile(voice.id)
  if (existing) {
    emit('update:expanded', false)
    emit('select', existing.id)
  } else if (!props.disabled && !props.importingId) {
    emit('update:expanded', true)
    emit('import', voice.id)
  }
}
async function load() {
  if (!alive || loading.value) return
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = false
  try {
    const result = await listStarterSpeechVoices(request.signal)
    if (alive && !request.signal.aborted) voices.value = result
  } catch {
    if (alive && !request.signal.aborted) error.value = true
  } finally {
    if (alive && controller === request) { loading.value = false; controller = undefined }
  }
}
onMounted(() => { void load() })
onBeforeUnmount(() => { alive = false; controller?.abort() })
</script>
<template>
  <section class="space-y-4 rounded-xl border border-border bg-panel p-4" :aria-label="t('speechWorkspace.starters.title')">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h2 class="text-sm font-semibold text-text">{{ t('speechWorkspace.starters.title') }}</h2>
      <button type="button" aria-controls="starter-voice-catalog" :aria-expanded="expanded" class="min-h-11 rounded-lg border border-border px-3 py-2 text-xs text-text hover:bg-panel-2 focus-visible:outline-2 focus-visible:outline-accent1" @click="toggle">{{ t(expanded ? 'speechWorkspace.starters.hide' : 'speechWorkspace.starters.browse') }}</button>
    </div>
    <p v-if="loading" role="status" class="text-sm text-text-dim">{{ t('speechWorkspace.starters.loading') }}</p>
    <div v-if="error" class="space-y-2">
      <p role="alert" class="text-sm text-status-failed">{{ t('speechWorkspace.starters.loadError') }}</p>
      <button type="button" class="min-h-11 rounded-lg border border-border px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" @click="load">{{ t('speechWorkspace.starters.reload') }}</button>
    </div>
    <div v-show="expanded" id="starter-voice-catalog" class="space-y-4">
      <template v-if="expanded">
      <p class="text-sm text-text-dim">{{ t('speechWorkspace.starters.intro') }}</p>
      <div class="grid gap-4 sm:grid-cols-2">
        <article v-for="voice in voices" :key="voice.id" :data-starter-voice="voice.id" class="min-w-0 space-y-3 rounded-lg border border-border bg-panel-2 p-4">
          <div><h3 class="text-sm font-semibold text-text">{{ voice.accent }}</h3><p class="mt-1 text-xs text-text-dim">{{ voice.id }}</p></div>
          <p class="text-xs text-text-dim">{{ t('speechWorkspace.starters.properties', { duration: voice.duration_seconds.toFixed(1), rate: voice.sample_rate_hz / 1000 }) }}</p>
          <SpeechAudioPreview :src="voice.audio_url" :label="t('speechWorkspace.starters.preview', { name: voice.name })" :active="active" />
          <p class="text-xs text-text-dim">{{ t('speechWorkspace.licensedReference') }} · <a data-starter-license :href="voice.license_url" target="_blank" rel="noopener" class="underline focus-visible:outline-2 focus-visible:outline-accent1">{{ voice.license_name }}</a></p>
          <details class="text-xs text-text-dim">
            <summary class="cursor-pointer rounded focus-visible:outline-2 focus-visible:outline-accent1">{{ t('speechWorkspace.starters.details') }}</summary>
            <p class="mt-3 font-medium">{{ t('speechWorkspace.starters.transcript') }}</p>
            <p class="mt-2 whitespace-pre-wrap break-words">{{ voice.transcript }}</p>
            <p class="mt-3">{{ voice.attribution }} · <a data-starter-source :href="voice.source_url" target="_blank" rel="noopener" class="underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('speechWorkspace.starters.source') }}</a></p>
          </details>
          <button type="button" :aria-label="t(existingProfile(voice.id) ? 'speechWorkspace.starters.useLabel' : 'speechWorkspace.starters.addLabel', { name: voice.name })" :disabled="!existingProfile(voice.id) && (disabled || !!importingId)" class="min-h-11 rounded-lg bg-accent1 px-3 py-2 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50" @click="choose(voice)">{{ t(existingProfile(voice.id) ? 'speechWorkspace.starters.use' : importingId === voice.id ? 'speechWorkspace.starters.adding' : 'speechWorkspace.starters.add') }}</button>
        </article>
      </div>
      </template>
    </div>
  </section>
</template>

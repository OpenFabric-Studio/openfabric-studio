<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import SpeechAudioPreview from '../voice/SpeechAudioPreview.vue'
import { useI18n } from 'vue-i18n'
import { listSpeechVoiceProfiles } from '../../api/voiceProfiles'
import type { SpeechVoiceProfile, VideoProject } from '../../api/contracts'
import type { VideoDraft } from './useVideoWorkspace'

const props = defineProps<{ project: VideoProject; draft: VideoDraft; readOnly: boolean }>()
const emit = defineEmits<{ upload: [file: File]; clear: []; speak: [profileId: string, text: string] }>()
const { t } = useI18n()
const voices = ref<SpeechVoiceProfile[]>([])
const voiceId = ref('')
const line = ref('')
const voiceError = ref(false)

const lifetime = new AbortController()
let alive = true
onBeforeUnmount(() => { alive = false; lifetime.abort() })
onMounted(async () => {
  try {
    const response = await listSpeechVoiceProfiles(lifetime.signal)
    if (!alive) return
    voices.value = response.filter((voice) => voice.consent_confirmed && voice.renderer !== 'openrouter')
    const bound = props.project.speech_clip?.voice_profile_id
    voiceId.value = bound && voices.value.some((voice) => voice.id === bound) ? bound : (voices.value[0]?.id ?? '')
  } catch {
    if (alive) voiceError.value = true
  }
})

function chosen(event: Event) {
  if (!(event.target instanceof HTMLInputElement)) return
  const file = event.target.files?.[0]
  event.target.value = ''
  if (file) emit('upload', file)
}
function speak() {
  if (voiceId.value && line.value.trim()) emit('speak', voiceId.value, line.value.trim())
}
</script>

<template>
  <section data-video-soundtrack class="min-w-0 space-y-3 rounded-xl border border-border bg-panel p-5">
    <h3 class="font-semibold">{{ t('videoExperience.soundtrackTitle') }}</h3>
    <p class="text-sm leading-relaxed text-text-dim">{{ t('videoExperience.soundtrackHint') }}</p>
    <label>{{ t('videoExperience.speechFile') }}<input data-speech-file type="file" accept="audio/wav,audio/mpeg,audio/mp4,audio/flac,audio/ogg,.wav,.mp3,.m4a,.flac,.ogg" :disabled="readOnly" @change="chosen"></label>
    <label>{{ t('videoExperience.savedVoice') }}
      <select v-model="voiceId" data-speech-voice :disabled="readOnly || !voices.length">
        <option v-if="!voices.length" value="">{{ t('videoExperience.noVoices') }}</option>
        <option v-for="voice in voices" :key="voice.id" :value="voice.id">{{ voice.name }}</option>
      </select>
    </label>
    <p class="text-xs text-text-dim">{{t('videoCloud.cloudSpeechHint')}}</p>
    <p v-if="voiceError" class="text-sm text-text-dim">{{ t('videoExperience.noVoices') }}</p>
    <label>{{ t('videoExperience.speakLine') }}<textarea v-model="line" data-speech-line rows="2" maxlength="500" :disabled="readOnly"></textarea></label>
    <button type="button" data-speak-line :disabled="readOnly || !voiceId || !line.trim()" @click="speak">{{ t('videoExperience.speak') }}</button>
    <p v-if="project.speech_clip">{{ project.speech_clip.name }} · {{ project.speech_clip.kind === 'voice' ? t('videoExperience.spokenLine') : t('videoExperience.uploadedClip') }}</p>
    <p v-if="project.speech_clip?.line" class="text-sm text-text-dim">{{ project.speech_clip.line }}</p>
    <SpeechAudioPreview v-if="project.speech_clip" :key="project.speech_clip.id" :src="`/api/videos/projects/${project.id}/speech?clip=${project.speech_clip.id}`" :label="t('videoExperience.speechFile')" />
    <p v-if="project.warnings?.includes('speech_mock')" role="status" class="text-sm text-text-dim">{{ t('video.err.speech_mock') }}</p>
    <label class="inline-check"><input v-model="draft.export_settings.attach_speech" data-attach-speech type="checkbox" :disabled="readOnly || !project.speech_clip">{{ t('videoExperience.speechAttach') }}</label>
    <p class="text-sm text-text-dim">{{ draft.export_settings.attach_speech && project.speech_clip ? t('videoExperience.soundtrackOn') : t('videoExperience.soundtrackOff') }}</p>
    <button type="button" :disabled="readOnly || !project.speech_clip" @click="emit('clear')">{{ t('videoExperience.speechClear') }}</button>
  </section>
</template>

<style scoped>
label { display: flex; flex-direction: column; gap: .35rem; font-size: .875rem; }
label.inline-check { flex-direction: row; align-items: center; min-height: 44px; }
input:not([type=checkbox]):not([type=file]), select, textarea { width: 100%; min-height: 44px; padding: .6rem .75rem; background: var(--color-panel-2); border: 1px solid var(--color-border); border-radius: .5rem; }
button { min-height: 44px; padding: .5rem .75rem; border-radius: .5rem; background: var(--color-panel-2); }
</style>

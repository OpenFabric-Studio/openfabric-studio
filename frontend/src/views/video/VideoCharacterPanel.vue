<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { listSpeechVoiceProfiles } from '../../api/voiceProfiles'
import { createVideoCharacter, listVideoCharacters, videoRequestError } from '../../api/videos'
import type { SpeechVoiceProfile, VideoCharacter, VideoProject } from '../../api/contracts'

const props = defineProps<{ project: VideoProject; readOnly: boolean }>()
const emit = defineEmits<{ apply: [characterId: string] }>()
const { t, te } = useI18n()
const voices = ref<SpeechVoiceProfile[]>([])
const characters = ref<VideoCharacter[]>([])
const name = ref('')
const voiceId = ref('')
const consent = ref(false)
const error = ref('')

onMounted(async () => {
  try {
    const [profiles, saved] = await Promise.all([listSpeechVoiceProfiles(), listVideoCharacters()])
    voices.value = profiles.filter((voice) => voice.consent_confirmed)
    characters.value = saved.characters
    voiceId.value = voices.value[0]?.id ?? ''
  } catch (cause) {
    error.value = videoRequestError(cause)
  }
})

async function save(event: Event) {
  const form = event.target
  if (!(form instanceof HTMLFormElement)) return
  const file = form.querySelector('input[type=file]')
  const still = file instanceof HTMLInputElement ? file.files?.[0] : undefined
  if (!still || !name.value.trim() || !voiceId.value || !consent.value) {
    error.value = 'consent_required'
    return
  }
  error.value = ''
  try {
    const created = await createVideoCharacter({ name: name.value.trim(), voiceProfileId: voiceId.value, consentConfirmed: true, still })
    characters.value = [created, ...characters.value.filter((item) => item.id !== created.id)]
    name.value = ''
    consent.value = false
    form.reset()
  } catch (cause) {
    error.value = videoRequestError(cause)
  }
}
</script>

<template>
  <section data-video-character class="min-w-0 space-y-3 rounded-xl border border-border bg-panel p-5">
    <h3 class="font-semibold">{{ t('videoExperience.characterTitle') }}</h3>
    <p class="text-sm leading-relaxed text-text-dim">{{ t('videoExperience.characterHint') }}</p>
    <p class="text-sm text-text-dim">{{ t('videoExperience.characterLook') }}</p>
    <ul v-if="characters.length" class="space-y-2">
      <li v-for="character in characters" :key="character.id" class="flex flex-wrap items-center gap-3 text-sm">
        <img :src="character.still_url" :alt="character.still_name" class="h-14 w-14 rounded object-cover">
        <span>{{ character.name }} · {{ character.voice_name }}</span>
        <span v-if="!character.voice_ready" class="text-status-failed">{{ t('video.err.consent_required') }}</span>
        <button type="button" :disabled="readOnly || !character.voice_ready || project.character_id === character.id" @click="emit('apply', character.id)">{{ project.character_id === character.id ? t('videoExperience.characterUsing') : t('videoExperience.characterUse') }}</button>
      </li>
    </ul>
    <form class="space-y-3" @submit.prevent="save">
      <label>{{ t('videoExperience.characterName') }}<input v-model="name" maxlength="80" :disabled="readOnly" required></label>
      <label>{{ t('videoExperience.savedVoice') }}
        <select v-model="voiceId" :disabled="readOnly || !voices.length">
          <option v-if="!voices.length" value="">{{ t('videoExperience.noVoices') }}</option>
          <option v-for="voice in voices" :key="voice.id" :value="voice.id">{{ voice.name }}</option>
        </select>
      </label>
      <label>{{ t('videoExperience.characterStill') }}<input data-character-still type="file" accept="image/png,image/jpeg,image/webp" :disabled="readOnly" required></label>
      <label class="inline-check"><input v-model="consent" data-character-consent type="checkbox" :disabled="readOnly">{{ t('videoExperience.characterConsent') }}</label>
      <button type="submit" :disabled="readOnly || !consent || !voices.length">{{ t('videoExperience.characterSave') }}</button>
    </form>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ te(`video.err.${error}`) ? t(`video.err.${error}`) : t('video.err.unknown') }}</p>
  </section>
</template>

<style scoped>
label { display: flex; flex-direction: column; gap: .35rem; font-size: .875rem; }
label.inline-check { flex-direction: row; align-items: center; min-height: 44px; }
input:not([type=checkbox]):not([type=file]), select, textarea { width: 100%; min-height: 44px; padding: .6rem .75rem; background: var(--color-panel-2); border: 1px solid var(--color-border); border-radius: .5rem; }
button { min-height: 44px; padding: .5rem .75rem; border-radius: .5rem; background: var(--color-panel-2); }
</style>

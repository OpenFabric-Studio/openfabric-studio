<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { patchSpeechVoiceProfile } from '../../api/voiceProfiles'
import type { SpeechVoiceProfile } from '../../api/contracts'
const props = defineProps<{ profile: SpeechVoiceProfile; disabled?: boolean }>()
const emit = defineEmits<{ saved: [profile: SpeechVoiceProfile] }>()
const { t } = useI18n()
const transcript = ref(''), language = ref('en'), busy = ref(false), error = ref(''), saved = ref(false)
let alive = true, generation = 0
let controller: AbortController | undefined
watch(() => props.profile, profile => {
  generation++; controller?.abort(); busy.value = false; error.value = ''; saved.value = false
  transcript.value = profile.reference_transcript ?? ''
  language.value = profile.reference_language ?? 'en'
}, { immediate: true })
async function save() {
  if (busy.value || props.disabled) return
  const token = generation, target = props.profile.id, request = new AbortController(); controller = request
  busy.value = true; error.value = ''; saved.value = false
  try {
    const result = await patchSpeechVoiceProfile(target, { reference_transcript: transcript.value.trim(), reference_language: language.value.trim() }, request.signal)
    if (!alive || request.signal.aborted || token !== generation || result.id !== target) return
    emit('saved', result)
    await nextTick()
    if (alive && props.profile.id === target && props.profile.reference_transcript === result.reference_transcript && props.profile.reference_language === result.reference_language) saved.value = true
  } catch { if (alive && token === generation && !request.signal.aborted) error.value = t('audiobookReview.referenceFailed') }
  finally { if (alive && token === generation && !request.signal.aborted) busy.value = false }
}
onBeforeUnmount(() => { alive = false; generation++; controller?.abort() })
</script>
<template>
  <details :open="!profile.reference_transcript?.trim()" class="rounded-lg border border-border bg-panel-2 p-3">
    <summary class="cursor-pointer text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-accent1">{{ t('audiobookReview.referenceTitle') }}</summary>
    <p class="my-3 text-xs text-text-dim">{{ t('audiobookReview.referenceHint') }}</p>
    <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookReview.transcript') }}</span><textarea v-model="transcript" rows="3" maxlength="2000" :disabled="disabled || busy" :aria-label="t('audiobookReview.transcript')" class="w-full rounded-lg border border-border bg-panel p-2 text-sm text-text" /></label>
    <label class="mt-3 block space-y-1"><span class="text-xs text-text-dim">{{ t('audiobookReview.referenceLanguage') }}</span><input v-model="language" type="text" minlength="2" maxlength="16" :disabled="disabled || busy" :aria-label="t('audiobookReview.referenceLanguage')" class="min-h-11 w-full rounded-lg border border-border bg-panel p-2 text-sm text-text" /></label>
    <button type="button" :disabled="disabled || busy || !language.trim()" class="mt-3 min-h-11 rounded-lg border border-border px-3 text-xs text-text disabled:opacity-50" @click="save">{{ t('audiobookReview.referenceSave') }}</button>
    <p v-if="error" role="alert" class="mt-2 text-xs text-status-failed">{{ error }}</p>
    <p v-if="saved" role="status" class="mt-2 text-xs text-status-done">{{ t('audiobookReview.referenceSaved') }}</p>
  </details>
</template>

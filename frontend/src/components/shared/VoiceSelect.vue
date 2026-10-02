<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getActiveVoiceId, listVoices, setActiveVoiceId } from '../../api/voices'
import type { VoiceProfile } from '../../api/voices'
import { createPollingLoop } from '../../composables/polling'

defineProps<{ link?: boolean; label?: string; hint?: string }>()
const emit = defineEmits<{ select: [voiceId: string | null] }>()
const { t } = useI18n()
const voices = ref<VoiceProfile[]>([])
const selected = ref(getActiveVoiceId() ?? '')
const usable = computed(() => voices.value.filter((voice) => voice.usable))
watch(() => [selected.value, usable.value], () => {
  emit('select', usable.value.some((voice) => voice.id === selected.value) ? selected.value : null)
}, { immediate: true })

const poll = createPollingLoop(async (context) => {
  try {
    const response = await listVoices(context.signal)
    if (!context.isCurrent()) return
    voices.value = response
  } catch {
    return
  }
  const current = getActiveVoiceId() ?? ''
  if (current && !usable.value.some((voice) => voice.id === current)) {
    setActiveVoiceId(null)
    selected.value = ''
    return
  }
  selected.value = current
}, 4000)

function onChange() {
  const value = selected.value
  if (value && !usable.value.some((voice) => voice.id === value)) return
  setActiveVoiceId(value || null)
}

function onExternalChange() {
  selected.value = getActiveVoiceId() ?? ''
}

onMounted(() => {
  poll.start()
  window.addEventListener('remiqora-voice', onExternalChange)
  window.addEventListener('storage', onExternalChange)
})
onBeforeUnmount(() => {
  poll.stop()
  window.removeEventListener('remiqora-voice', onExternalChange)
  window.removeEventListener('storage', onExternalChange)
})
</script>

<template>
  <div class="space-y-1 text-left">
    <label v-if="usable.length" class="block space-y-1">
      <span class="text-xs text-text-dim">{{ label ?? t('voiceClone.useOnSongs') }}</span>
      <select
        v-model="selected"
        class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"
        @change="onChange"
      >
        <option value="">{{ t('voiceClone.none') }}</option>
        <option v-for="voice in usable" :key="voice.id" :value="voice.id">{{ voice.name }}</option>
      </select>
    </label>
    <p class="text-xs text-text-dim">{{ usable.length ? hint ?? t('voiceClone.useHint') : t('voiceClone.noneReady') }}</p>
    <RouterLink v-if="link" to="/voice-clone" class="inline-block text-xs text-accent1 hover:underline">{{ t('voiceClone.manage') }}</RouterLink>
  </div>
</template>

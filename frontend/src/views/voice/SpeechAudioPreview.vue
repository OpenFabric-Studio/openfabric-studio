<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'

const props = withDefaults(defineProps<{ src: string; label: string; active?: boolean }>(), { active: true })
const { t } = useI18n()
const audio = ref<HTMLAudioElement | null>(null)
const failed = ref(false)
let alive = true
function release() { if (audio.value) releasePlaybackIfCurrent(audio.value) }
function stop() { audio.value?.pause(); release() }
function onPlay() {
  if (!alive || !props.active) { stop(); return }
  failed.value = false
  if (audio.value) claimPlayback(audio.value)
}
function onError() { if (alive) failed.value = true; stop() }
watch(() => props.active, active => { if (!active) stop() }, { flush: 'sync' })
watch(() => props.src, () => { stop(); failed.value = false }, { flush: 'sync' })
onBeforeUnmount(() => { alive = false; stop() })
</script>
<template>
  <div class="space-y-2">
    <audio ref="audio" controls preload="metadata" class="h-10 w-full" :src="src" :aria-label="label" @play="onPlay" @pause="release" @ended="release" @error="onError" />
    <p v-if="failed" role="alert" class="text-xs text-status-failed">{{ t('speechWorkspace.audioError') }}</p>
  </div>
</template>

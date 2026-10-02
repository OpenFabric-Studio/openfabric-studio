<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
const props = defineProps<{ src: string; poster?: string; label: string }>()
const { t } = useI18n()
const element = ref<HTMLVideoElement | null>(null)
const failed = ref(false)
function stop() { if (element.value) { element.value.pause(); releasePlaybackIfCurrent(element.value) } }
function started() { if (element.value) claimPlayback(element.value) }
function released() { if (element.value) releasePlaybackIfCurrent(element.value) }
watch(() => props.src, () => { stop(); failed.value = false }, { flush: 'sync' })
onBeforeUnmount(stop)
</script>
<template>
  <div><video ref="element" controls preload="none" :src="src" :poster="poster" :aria-label="label" class="w-full rounded-lg bg-black" @play="started" @pause="released" @ended="released" @error="failed = true; released()"></video>
    <p v-if="failed" role="alert" class="mt-2 text-sm text-status-failed">{{ t('videoWorkspace.playbackFailed') }} <a :href="src" download class="underline">{{ t('common.download') }}</a></p>
  </div>
</template>

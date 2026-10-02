<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { downloadTrackAudio } from '../../api/trackDownload'
const props = defineProps<{ trackId: number; versionId: string; exportId?: string }>()
const { t } = useI18n()
const pending = ref(false), failed = ref(false)
let active = true, generation = 0
let controller: AbortController | null = null
const urls = new Map<string, ReturnType<typeof setTimeout>>()
function release(url: string) { clearTimeout(urls.get(url)); urls.delete(url); URL.revokeObjectURL(url) }
watch(() => [props.trackId, props.versionId, props.exportId], () => { ++generation; controller?.abort(); pending.value = false; failed.value = false })
async function download() {
  if (pending.value) return
  const token = ++generation
  const trackId = props.trackId, versionId = props.versionId, exportId = props.exportId
  controller?.abort(); controller = new AbortController(); pending.value = true; failed.value = false
  try {
    const file = await downloadTrackAudio(trackId, versionId, exportId, undefined, controller.signal)
    if (!active || token !== generation) return
    const url = URL.createObjectURL(file.blob)
    urls.set(url, setTimeout(() => release(url), 1000))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = file.filename
    document.body.append(anchor)
    try { anchor.click() } finally { anchor.remove() }
  } catch { if (active && token === generation) failed.value = true }
  finally { if (active && token === generation) pending.value = false }
}
onBeforeUnmount(() => { active = false; ++generation; controller?.abort(); for (const url of urls.keys()) release(url) })
</script>
<template>
  <span class="inline-flex flex-wrap items-center gap-2 text-xs">
    <button type="button" :disabled="pending" :aria-busy="pending" class="text-accent1 hover:underline focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" @click="download">{{ pending ? t('upstreamWorkspace.downloading') : t('upstreamWorkspace.taggedDownload') }}</button>
    <span v-if="failed" role="alert" class="text-status-failed">{{ t('upstreamWorkspace.downloadFailed') }}</span>
  </span>
</template>

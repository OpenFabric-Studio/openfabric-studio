<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/stemExports'
import type { StemAudioExportResponse } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'
const props = defineProps<{ trackId: number; stemName: string }>()
const { t } = useI18n()
const row = ref<StemAudioExportResponse | null>(null)
const pending = ref(false)
const error = ref('')
const active = computed(() => row.value?.status === 'queued' || row.value?.status === 'running')
let alive = true
let generation = 0
let actionController: AbortController | null = null
const poll = createPollingLoop(async context => {
  const token = generation; const current = row.value
  if (!current || !active.value) return false
  try {
    const result = await api.get(props.trackId, props.stemName, current.id, context.signal)
    if (!context.isCurrent() || !alive || token !== generation) return false
    row.value = result
  } catch { if (context.isCurrent() && alive && token === generation) error.value = t('generationWorkspace.stemExportFailed') }
  return active.value
}, 2000)
async function load() {
  const token = ++generation; const trackId = props.trackId; const stem = props.stemName
  poll.stop(); actionController?.abort(); const request = new AbortController(); actionController = request
  row.value = null; error.value = ''
  try {
    const rows = await api.list(trackId, stem, request.signal)
    if (!alive || token !== generation) return
    const newest = [...rows].sort((a, b) => b.created_at.localeCompare(a.created_at))
    row.value = newest.find(item => item.status === 'queued' || item.status === 'running') ?? newest[0] ?? null
    if (active.value) poll.start(false)
  } catch { if (alive && token === generation) error.value = t('generationWorkspace.stemExportFailed') }
}
async function action(kind: 'create' | 'cancel' | 'retry') {
  if (pending.value) return
  const token = ++generation; const trackId = props.trackId; const stem = props.stemName; const id = row.value?.id
  poll.stop(); actionController?.abort(); const request = new AbortController(); actionController = request
  pending.value = true; error.value = ''
  try {
    const result = kind === 'create' ? await api.create(trackId, stem, request.signal) : id ? await api[kind](trackId, stem, id, request.signal) : null
    if (!alive || token !== generation) return
    row.value = result
    if (active.value) poll.start(false)
  } catch { if (alive && token === generation) { error.value = t('generationWorkspace.stemExportFailed'); if (active.value) poll.start(false) } }
  finally { if (alive && token === generation) pending.value = false }
}
watch(() => [props.trackId, props.stemName], () => { pending.value = false; void load() })
onMounted(() => { void load() })
onBeforeUnmount(() => { alive = false; generation++; actionController?.abort(); poll.stop() })
</script>
<template>
  <div class="flex flex-wrap items-center gap-2 text-xs text-text-dim">
    <a v-if="row?.status === 'done' && row.audio_url" :href="row.audio_url" :download="row.filename ?? `${stemName}_${trackId}.mp3`" class="text-accent1 hover:underline">{{ t('generationWorkspace.downloadMp3') }}</a>
    <button v-if="row?.status === 'done'" type="button" :disabled="pending" class="text-accent1 underline disabled:opacity-50" @click="action('create')">{{ t('generationWorkspace.prepareMp3Current') }}</button>
    <template v-else-if="active"><span role="status">{{ t('generationWorkspace.preparingMp3') }}</span><button type="button" :disabled="pending" class="text-accent1 underline disabled:opacity-50" @click="action('cancel')">{{ t('generationWorkspace.cancel') }}</button></template>
    <button v-else type="button" :disabled="pending" class="text-accent1 underline disabled:opacity-50" @click="action(row ? 'retry' : 'create')">{{ t(row ? 'generationWorkspace.retryMp3' : 'generationWorkspace.prepareMp3') }}</button>
    <p v-if="error || row?.status === 'failed'" role="alert" class="text-status-failed">{{ error || t('generationWorkspace.stemExportFailed') }}</p>
  </div>
</template>

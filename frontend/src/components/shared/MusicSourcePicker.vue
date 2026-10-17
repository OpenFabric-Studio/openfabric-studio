<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listAudioVersions } from '../../api/audioVersions'
import { inspectConditioningSource } from '../../api/musicConditioning'
import type { AudioVersion, MusicConditioningSourceInfo, SelectedSource } from '../../api/generated'
import { ApiError } from '../../api/http'
import LibraryPicker from '../editor/LibraryPicker.vue'

const props = defineProps<{ modelValue: SelectedSource | null; label: string; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: SelectedSource | null]; inspected: [value: MusicConditioningSourceInfo | null]; busy: [value: boolean]; validity: [value: boolean] }>()
const { t, te } = useI18n()
const pickerOpen = ref(false), working = ref(false), error = ref(''), trackLabel = ref('')
const versions = ref<AudioVersion[]>([]), versionId = ref(''), trackId = ref<number | null>(null)
const info = ref<MusicConditioningSourceInfo | null>(null)
let alive = true, generation = 0, controller: AbortController | undefined, hydrating = false, emittedValue: string | undefined
function cancelRead() {
  ++generation; controller?.abort(); controller = undefined; working.value = false; error.value = ''; emit('busy', false)
}
function publish(value: SelectedSource | null) { emittedValue = JSON.stringify(value); emit('update:modelValue', value) }
function invalidate() {
  cancelRead(); info.value = null
  publish(null); emit('inspected', null); emit('validity', false)
}
function hydrateVersion(value: string) { hydrating = true; versionId.value = value; hydrating = false }
async function loadVersions(identifier: number, pinnedVersion?: string) {
  const token = generation, request = new AbortController(); controller = request; working.value = true; emit('busy', true)
  try {
    const response = await listAudioVersions(identifier, request.signal)
    if (!alive || token !== generation || request.signal.aborted) return
    const retained = response.versions ?? []
    if (response.track_id !== identifier || retained.some(version => version.track_id !== identifier)) throw new TypeError('Mismatched saved versions')
    const completed = retained.filter(version => version.status === 'done'), choices = completed.slice(0, 100)
    const pinned = completed.find(version => version.id === pinnedVersion)
    if (pinned && !choices.some(version => version.id === pinned.id)) choices.push(pinned)
    versions.value = choices
  } catch { if (alive && token === generation) error.value = t('musicConditioning.failed') }
  finally { if (alive && token === generation) { working.value = false; controller = undefined; emit('busy', false) } }
}
async function pick(track: { trackId?: number; sourceLabel: string }) {
  pickerOpen.value = false
  if (props.disabled || track.trackId === undefined || !Number.isSafeInteger(track.trackId) || track.trackId < 1) return
  invalidate(); versions.value = []; hydrateVersion(''); trackId.value = track.trackId; trackLabel.value = track.sourceLabel
  await loadVersions(track.trackId)
}
const candidate = computed<SelectedSource['source'] | null>(() => {
  if (trackId.value !== null && versions.value.some(value => value.id === versionId.value && value.status === 'done')) {
    return { kind: 'version', track_id: trackId.value, version_id: versionId.value }
  }
  return props.modelValue?.source ?? null
})
const pinnedLabel = computed(() => {
  const value = props.modelValue?.source
  if (!value) return ''
  if (value.kind === 'version') return t('musicConditioning.pinnedVersion', { track: value.track_id, version: value.version_id })
  if (value.kind === 'stem') return t('musicConditioning.pinnedStem', { track: value.track_id, stem: value.stem })
  return t('musicConditioning.pinnedAsset', { asset: value.asset_id })
})
async function inspect() {
  const requested = candidate.value
  if (props.disabled || working.value || !requested) return
  invalidate()
  const token = generation, request = new AbortController(); controller = request; working.value = true; emit('busy', true)
  try {
    const result = await inspectConditioningSource(requested, request.signal)
    if (!alive || token !== generation || request.signal.aborted) return
    if (result.source.kind === 'beat_render') throw new TypeError('Mismatched saved source')
    info.value = result
    publish({ source: result.source, audio_sha256: result.source_sha256 }); emit('inspected', result); emit('validity', true)
  } catch (cause) {
    if (alive && token === generation) {
      const key = cause instanceof ApiError ? `musicConditioning.errors.${cause.message}` : ''
      error.value = t(te(key) ? key : 'musicConditioning.failed')
    }
  } finally { if (alive && token === generation) { working.value = false; controller = undefined; emit('busy', false) } }
}
watch(versionId, () => { if (!hydrating) invalidate() }, { flush: 'sync' })
watch(() => props.modelValue, async (value, previous) => {
  const key = JSON.stringify(value)
  if (key === emittedValue) { emittedValue = undefined; return }
  emittedValue = undefined; cancelRead(); info.value = null; versions.value = []; hydrateVersion(''); trackId.value = null; trackLabel.value = ''
  if (previous !== undefined || value !== null) emit('inspected', null)
  emit('validity', value !== null)
  if (value?.source.kind === 'version') {
    trackId.value = value.source.track_id; hydrateVersion(value.source.version_id)
    await loadVersions(value.source.track_id, value.source.version_id)
  }
}, { immediate: true, flush: 'sync' })
watch(() => props.disabled, value => { if (value && working.value) cancelRead() })
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort() })
</script>

<template>
  <section class="space-y-3" :aria-label="label" :aria-busy="working">
    <h4 class="text-sm font-medium text-text">{{ label }}</h4>
    <button type="button" class="source-button" :disabled="disabled || working" @click="pickerOpen = true">{{ t('musicConditioning.choose') }}</button>
    <p v-if="trackLabel" class="text-sm text-text-dim">{{ trackLabel }}</p>
    <p v-if="pinnedLabel" class="break-all text-xs text-text-dim">{{ pinnedLabel }}</p>
    <p v-if="modelValue && !info" class="break-all text-xs text-text-dim">{{ t('musicConditioning.hash') }}: {{ modelValue.audio_sha256 }}</p>
    <label v-if="trackId !== null" class="flex flex-col gap-2 text-sm text-text-dim">{{ t('musicConditioning.version') }}<select v-model="versionId" class="source-input" :disabled="disabled || working"><option value="">{{ t('musicConditioning.selectVersion') }}</option><option v-for="version in versions" :key="version.id" :value="version.id">{{ version.kind === 'original' ? t('musicConditioning.original') : version.voice_name || t('musicConditioning.voice') }} · {{ version.created_at }}</option></select></label>
    <button v-if="trackId !== null || modelValue" type="button" class="source-button" :disabled="disabled || working || !candidate" @click="inspect">{{ t('musicConditioning.inspect') }}</button>
    <p v-if="working" role="status" class="text-sm text-text-dim">{{ t('musicConditioning.inspecting') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <template v-if="info"><p class="text-xs text-text-dim">{{ t('musicConditioning.metadata', { seconds: info.duration_sec, rate: info.source_rate_hz, channels: info.source_channels }) }}</p><p class="break-all text-xs text-text-dim">{{ t('musicConditioning.hash') }}: {{ info.source_sha256 }}</p></template>
    <LibraryPicker v-if="pickerOpen" track-only :full-mix-label="t('musicConditioning.pick')" @pick="pick" @close="pickerOpen = false" />
  </section>
</template>

<style scoped>
@reference "../../style.css";
.source-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
.source-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
</style>

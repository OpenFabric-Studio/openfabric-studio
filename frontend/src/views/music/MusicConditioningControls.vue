<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/musicConditioning'
import type { ConditioningSelection, MusicConditioningDraft } from '../../api/musicConditioning'
import { parseLocalMusicRequest } from '../../api/generated'
import type { LocalMusicRequest, MusicConditioningCapabilities, MusicConditioningPreview, MusicConditioningSourceInfo, SelectedSource } from '../../api/generated'
import { ApiError } from '../../api/http'
import MusicSourcePicker from '../../components/shared/MusicSourcePicker.vue'
import MusicTrainingSetup from './MusicTrainingSetup.vue'

const props = defineProps<{ request: LocalMusicRequest; supported: boolean; installed: boolean; disabled?: boolean; sourceDraft?: MusicConditioningDraft | null }>()
const emit = defineEmits<{ change: [value: ConditioningSelection | null]; validity: [value: boolean]; refresh: []; draftApplied: [] }>()
const { t, te } = useI18n()
const mode = ref<ConditioningSelection['mode'] | 'off'>('off'), noise = ref(.6), start = ref(0), end = ref(1), crossfade = ref(.02)
const capabilities = ref<MusicConditioningCapabilities | null>(null), source = ref<MusicConditioningSourceInfo | null>(null)
const selected = ref<SelectedSource | null>(null), draftLabel = ref(''), error = ref(''), working = ref(false), checked = ref(false)
const preview = ref<MusicConditioningPreview | null>(null), reviewedKey = ref('')
let alive = true, generation = 0, controller: AbortController | undefined, capabilityGeneration = 0, capabilityController: AbortController | undefined
function available(value: ConditioningSelection['mode']): boolean {
  const row = capabilities.value?.modes.find(item => item.engine === 'stable_audio3' && item.mode === value && item.proof_status === 'source_verified')
  return !!row && (props.request.variant === 'medium' ? row.enabled_medium : row.enabled_small)
}
const setupReady = computed(() => available('audio_to_audio') && available('inpaint'))
const selection = computed<ConditioningSelection | null>(() => {
  if (mode.value === 'off' || !source.value) return null
  const common = { source: source.value.source, source_sha256: source.value.source_sha256 }
  const value = mode.value === 'audio_to_audio' ? { ...common, mode: mode.value, noise_level: noise.value }
    : { ...common, mode: mode.value, start_sec: start.value, end_sec: end.value, crossfade_sec: crossfade.value }
  try { return parseLocalMusicRequest({ ...props.request, conditioning: value }).conditioning ?? null }
  catch { return null }
})
const canonical = computed<LocalMusicRequest | null>(() => {
  if (!selection.value || mode.value === 'off' || !available(mode.value) || !source.value || source.value.duration_sec > 380
    || mode.value === 'inpaint' && source.value.duration_sec !== props.request.duration_seconds) return null
  try { return parseLocalMusicRequest({ ...props.request, conditioning: selection.value }) }
  catch { return null }
})
const requestKey = computed(() => canonical.value ? JSON.stringify(canonical.value) : '')
const valid = computed(() => mode.value === 'off' || !!requestKey.value && reviewedKey.value === requestKey.value && preview.value?.can_start === true && checked.value)
function invalidate() {
  ++generation; controller?.abort(); controller = undefined; working.value = false; checked.value = false
  preview.value = null; reviewedKey.value = ''; error.value = ''
}
function message(cause: unknown): string {
  const key = cause instanceof ApiError ? `musicConditioning.errors.${cause.message}` : ''
  return t(te(key) ? key : 'musicConditioning.failed')
}
function bytes(value: number | null | undefined): string { return value == null ? t('musicConditioning.unknown') : `${(value / 1024**2).toFixed(1)} MiB` }
async function loadCapabilities() {
  capabilityController?.abort()
  const token = ++capabilityGeneration, request = new AbortController(); capabilityController = request
  try {
    const response = await api.conditioningCapabilities(request.signal)
    if (alive && token === capabilityGeneration && !request.signal.aborted) capabilities.value = response
  } catch (cause) { if (alive && token === capabilityGeneration && !request.signal.aborted) { capabilities.value = null; error.value = message(cause) } }
  finally { if (alive && token === capabilityGeneration) capabilityController = undefined }
}
async function review() {
  const request = canonical.value
  if (props.disabled || working.value || !request) return
  invalidate()
  const key = JSON.stringify(request), token = generation, controllerValue = new AbortController(); controller = controllerValue; working.value = true
  try {
    const response = await api.previewConditioning(request, controllerValue.signal)
    if (!alive || token !== generation || controllerValue.signal.aborted || requestKey.value !== key) return
    preview.value = response; reviewedKey.value = key
    if (!response.can_start) error.value = t(`musicConditioning.errors.${response.error_code ?? 'conditioning_runtime_unavailable'}`)
  } catch (cause) { if (alive && token === generation) error.value = message(cause) }
  finally { if (alive && token === generation) { working.value = false; controller = undefined } }
}
function inspected(value: MusicConditioningSourceInfo | null) { if (value === null && working.value && draftLabel.value) return; invalidate(); source.value = value; draftLabel.value = '' }
function refresh() { invalidate(); void loadCapabilities(); emit('refresh') }
watch(requestKey, invalidate, { flush: 'sync' })
watch(selection, value => emit('change', value), { immediate: true, flush: 'sync' })
watch(valid, value => emit('validity', value), { immediate: true, flush: 'sync' })
watch(() => props.sourceDraft, async draft => {
  if (!draft) return
  invalidate(); selected.value = null; source.value = null; draftLabel.value = draft.label; mode.value = 'audio_to_audio'
  const token = generation, request = new AbortController(); controller = request; working.value = true
  emit('draftApplied')
  try {
    const response = await api.inspectConditioningSource(draft.source, request.signal)
    if (!alive || token !== generation || request.signal.aborted) return
    // Setting source recomputes and invalidates its review; handoffs never approve storage.
    source.value = response
  } catch (cause) { if (alive && token === generation) error.value = message(cause) }
  finally { if (alive && token === generation) { working.value = false; controller = undefined } }
}, { immediate: true })
onMounted(() => { void loadCapabilities() })
onBeforeUnmount(() => { alive = false; ++generation; ++capabilityGeneration; controller?.abort(); capabilityController?.abort() })
</script>

<template>
  <section class="space-y-3 rounded-lg border border-border p-4" :aria-label="t('musicConditioning.title')" :aria-busy="working">
    <h3 class="font-medium text-text">{{ t('musicConditioning.title') }}</h3>
    <p class="text-xs text-status-queued">{{ t('musicConditioning.proof') }}</p>
    <label class="conditioning-label">{{ t('musicConditioning.mode') }}<select v-model="mode" name="conditioning-mode" class="conditioning-input" :disabled="disabled || working"><option value="off">{{ t('musicConditioning.off') }}</option><option value="audio_to_audio" :disabled="!available('audio_to_audio')">{{ t('musicConditioning.audio_to_audio') }}</option><option value="inpaint" :disabled="!available('inpaint')">{{ t('musicConditioning.inpaint') }}</option></select></label>
    <MusicTrainingSetup mode="conditioning" :variant="request.variant ?? 'small-music'" :supported="supported" :installed="installed" :ready="setupReady" @refresh="refresh" />
    <template v-if="mode !== 'off'">
      <p class="text-sm text-text-dim">{{ t(mode === 'inpaint' ? 'musicConditioning.regionHint' : 'musicConditioning.mixture') }}</p>
      <p v-if="!available(mode)" role="status" class="text-sm text-status-queued">{{ t('musicConditioning.unavailable') }}</p>
      <p v-if="draftLabel" class="text-sm text-text">{{ draftLabel }}</p>
      <MusicSourcePicker v-model="selected" :label="t('musicConditioning.source')" :disabled="disabled || working" @inspected="inspected" />
      <p v-if="source && source.source.kind !== 'version'" class="break-all text-xs text-text-dim">{{ t('musicConditioning.metadata', { seconds: source.duration_sec, rate: source.source_rate_hz, channels: source.source_channels }) }} · {{ source.source_sha256 }}</p>
      <template v-if="mode === 'audio_to_audio'"><label class="conditioning-label">{{ t('musicConditioning.noise') }}<input v-model.number="noise" name="conditioning-noise" type="number" min="0.01" max="0.99" step="any" class="conditioning-input" :disabled="disabled || working" /></label><p class="text-xs text-text-dim">{{ t('musicConditioning.noiseHint') }}</p></template>
      <div v-else class="grid gap-3 sm:grid-cols-3"><label class="conditioning-label">{{ t('musicConditioning.start') }}<input v-model.number="start" name="conditioning-start" type="number" min="0" :max="request.duration_seconds" step="any" class="conditioning-input" :disabled="disabled || working" /></label><label class="conditioning-label">{{ t('musicConditioning.end') }}<input v-model.number="end" name="conditioning-end" type="number" min="0" :max="request.duration_seconds" step="any" class="conditioning-input" :disabled="disabled || working" /></label><label class="conditioning-label">{{ t('musicConditioning.crossfade') }}<input v-model.number="crossfade" name="conditioning-crossfade" type="number" min="0" max="0.05" step="any" class="conditioning-input" :disabled="disabled || working" /></label></div>
      <p class="text-xs text-text-dim">{{ t('musicConditioning.reviewHint') }}</p>
      <button type="button" class="conditioning-button" :disabled="disabled || working || !canonical" @click="review">{{ t('musicConditioning.review') }}</button>
      <p v-if="source && !canonical && !working" role="status" class="text-xs text-status-queued">{{ t('musicConditioning.invalid') }}</p>
      <div v-if="preview" class="space-y-2"><p class="text-xs text-text-dim">{{ t('musicConditioning.required', { size: bytes(preview.required_free_bytes) }) }}</p><p class="text-xs text-text-dim">{{ t('musicConditioning.free', { size: bytes(preview.free_bytes) }) }}</p><label v-if="preview.can_start" class="flex min-h-11 items-center gap-3 text-sm text-text"><input v-model="checked" name="conditioning-reviewed" type="checkbox" :disabled="disabled || working" />{{ t('musicConditioning.confirm') }}</label><p v-if="valid" role="status" class="text-sm text-status-done">{{ t('musicConditioning.ready') }}</p></div>
    </template>
    <p v-if="working" role="status" class="text-sm text-text-dim">{{ t('musicConditioning.reviewing') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
  </section>
</template>

<style scoped>
@reference "../../style.css";
.conditioning-label { @apply flex flex-col gap-2 text-sm text-text-dim; }
.conditioning-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
.conditioning-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

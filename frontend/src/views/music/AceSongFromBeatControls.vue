<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { songFromBeatCapabilities } from '../../api/songFromBeat'
import type { AceSongConditioning, AceSongDraft } from '../../api/songFromBeat'
import { inspectConditioningSource } from '../../api/musicConditioning'
import { parseAceCompleteConditioning, parseAceLegoConditioning } from '../../api/generated'
import type { MusicConditioningPreview, MusicConditioningSourceInfo, SelectedSource, SongFromBeatCapabilities } from '../../api/generated'
import MusicSourcePicker from '../../components/shared/MusicSourcePicker.vue'
import MusicTrainingSetup from './MusicTrainingSetup.vue'

const props = defineProps<{ mode: AceSongConditioning['mode'] | 'off'; preview?: MusicConditioningPreview | null; fingerprint?: string; instrumental: boolean; supported: boolean; disabled?: boolean; sourceDraft?: AceSongDraft | null }>()
const emit = defineEmits<{ 'update:mode': [value: AceSongConditioning['mode'] | 'off']; change: [value: AceSongConditioning | null]; validity: [value: boolean]; refresh: []; draftApplied: [] }>()
const { t } = useI18n()
type Role = Extract<AceSongConditioning, { mode: 'ace_complete' }>['track_classes'][number]
const roles: Role[] = ['woodwinds', 'brass', 'fx', 'synth', 'strings', 'percussion', 'keyboard', 'guitar', 'bass', 'drums', 'backing_vocals', 'vocals']
const chosen = ref<Role[]>(['bass', 'guitar']), single = ref<Role>('guitar')
const selected = ref<SelectedSource | null>(null), source = ref<MusicConditioningSourceInfo | null>(null), draftLabel = ref('')
const capabilities = ref<SongFromBeatCapabilities | null>(null), working = ref(false), error = ref(''), checked = ref(false)
let alive = true, generation = 0, controller: AbortController | undefined, capabilityGeneration = 0, capabilityController: AbortController | undefined
const state = computed(() => capabilities.value?.modes.find(row => row.mode === props.mode))
const vocalConflict = computed(() => props.instrumental && (props.mode === 'ace_lego' ? single.value === 'vocals' || single.value === 'backing_vocals' : props.mode === 'ace_complete' && chosen.value.some(role => role === 'vocals' || role === 'backing_vocals')))
const selection = computed<AceSongConditioning | null>(() => {
  const info = source.value
  if (props.mode === 'off' || !info || info.source.kind !== 'version' && info.source.kind !== 'beat_render' || vocalConflict.value) return null
  const common = { source: info.source, source_sha256: info.source_sha256 }
  try { return props.mode === 'ace_complete' ? parseAceCompleteConditioning({ ...common, mode: props.mode, track_classes: chosen.value }) : parseAceLegoConditioning({ ...common, mode: props.mode, track_name: single.value }) }
  catch { return null }
})
const sourceFits = computed(() => !!source.value && source.value.duration_sec >= 10 && source.value.duration_sec <= 300 && source.value.source_bytes <= 512 * 1024**2)
const reviewed = computed(() => !!selection.value && sourceFits.value && !!props.fingerprint && props.preview?.source_sha256 === selection.value.source_sha256 && props.preview.can_start === true && state.value?.ready === true)
const valid = computed(() => props.mode === 'off' || reviewed.value && checked.value)
function bytes(value: number | null | undefined) { return value == null ? t('musicConditioning.unknown') : `${(value / 1024**2).toFixed(1)} MiB` }
function cancelSource() { ++generation; controller?.abort(); controller = undefined; working.value = false }
function inspected(info: MusicConditioningSourceInfo | null) { if (info === null && working.value && draftLabel.value) return; cancelSource(); source.value = info; draftLabel.value = ''; checked.value = false }
async function refresh() {
  checked.value = false; capabilityController?.abort()
  const token = ++capabilityGeneration, request = new AbortController(); capabilityController = request
  try { const result = await songFromBeatCapabilities(request.signal); if (alive && token === capabilityGeneration && !request.signal.aborted) { capabilities.value = result; error.value = '' } }
  catch { if (alive && token === capabilityGeneration) { capabilities.value = null; error.value = t('songFromBeat.failed') } }
  finally { if (alive && token === capabilityGeneration) capabilityController = undefined }
}
function modeChanged(event: Event) {
  const value = event.target instanceof HTMLSelectElement ? event.target.value : ''
  if (value === 'off' || value === 'ace_complete' || value === 'ace_lego') emit('update:mode', value)
}
watch(() => [props.fingerprint, JSON.stringify(selection.value), props.mode], () => { checked.value = false }, { flush: 'sync' })
watch(selection, value => emit('change', value), { immediate: true, flush: 'sync' })
watch(valid, value => emit('validity', value), { immediate: true, flush: 'sync' })
watch(() => props.sourceDraft, async draft => {
  if (!draft) return
  cancelSource(); selected.value = null; source.value = null; checked.value = false; draftLabel.value = draft.label
  const token = generation, request = new AbortController(); controller = request; working.value = true
  emit('draftApplied')
  try { const result = await inspectConditioningSource(draft.source, request.signal); if (alive && token === generation && !request.signal.aborted) source.value = result }
  catch { if (alive && token === generation) error.value = t('songFromBeat.failed') }
  finally { if (alive && token === generation) { working.value = false; controller = undefined } }
}, { immediate: true })
onMounted(() => { void refresh() })
onBeforeUnmount(() => { alive = false; cancelSource(); ++capabilityGeneration; capabilityController?.abort() })
</script>

<template>
  <section class="space-y-3 rounded-lg border border-border p-4" :aria-label="t('songFromBeat.title')" :aria-busy="working">
    <h3 class="font-medium text-text">{{ t('songFromBeat.title') }}</h3>
    <p class="text-xs text-text-dim">{{ t('songFromBeat.hint') }}</p>
    <label class="song-label">{{ t('songFromBeat.mode') }}<select :value="mode" name="ace-song-mode" class="song-input" :disabled="disabled || working" @change="modeChanged"><option value="off">{{ t('songFromBeat.off') }}</option><option value="ace_complete">{{ t('songFromBeat.ace_complete') }}</option><option value="ace_lego">{{ t('songFromBeat.ace_lego') }}</option></select></label>
    <template v-if="mode !== 'off'">
      <p class="text-xs text-status-queued">{{ t('songFromBeat.proof') }}</p>
      <p class="text-xs text-text-dim">{{ t('songFromBeat.duration') }}</p>
      <MusicTrainingSetup mode="song" engine="ace_step" :supported="supported" :installed="state?.installed ?? false" :ready="state?.ready ?? false" @refresh="refresh(); emit('refresh')" />
      <MusicSourcePicker v-model="selected" :label="t('songFromBeat.source')" :disabled="disabled || working" @inspected="inspected" />
      <p v-if="draftLabel" class="text-sm text-text-dim">{{ t('songFromBeat.sourceLabel', { label: draftLabel }) }}</p>
      <p v-if="source" class="break-all text-xs text-text-dim">{{ t('musicConditioning.metadata', { seconds: source.duration_sec, rate: source.source_rate_hz, channels: source.source_channels }) }} · {{ source.source_sha256 }}</p>
      <p v-if="source && !sourceFits" role="alert" class="text-sm text-status-failed">{{ t('songFromBeat.duration') }}</p>
      <fieldset v-if="mode === 'ace_complete'" class="space-y-2"><legend class="text-sm text-text">{{ t('songFromBeat.roles') }}</legend><div class="grid gap-2 sm:grid-cols-2"><label v-for="role in roles" :key="role" class="flex min-h-11 items-center gap-2 text-sm text-text-dim"><input v-model="chosen" type="checkbox" :value="role" :name="`ace-role-${role}`" :disabled="disabled" />{{ t(`songFromBeat.roleNames.${role}`) }}</label></div></fieldset>
      <label v-else class="song-label">{{ t('songFromBeat.role') }}<select v-model="single" name="ace-song-role" class="song-input" :disabled="disabled"><option v-for="role in roles" :key="role" :value="role">{{ t(`songFromBeat.roleNames.${role}`) }}</option></select></label>
      <p v-if="vocalConflict" role="alert" class="text-sm text-status-failed">{{ t('songFromBeat.vocalConflict') }}</p>
      <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
      <button type="button" class="song-button" :disabled="disabled" @click="refresh(); emit('refresh')">{{ t('songFromBeat.refresh') }}</button>
      <p class="text-xs text-text-dim">{{ t('songFromBeat.review') }}</p>
      <template v-if="preview"><p class="text-xs text-text-dim">{{ t('musicConditioning.required', { size: bytes(preview.required_free_bytes) }) }} · {{ t('musicConditioning.free', { size: bytes(preview.free_bytes) }) }}</p><p v-if="preview.error_code" role="status" class="text-sm text-status-queued">{{ t(`musicConditioning.errors.${preview.error_code}`) }}</p></template>
      <label v-if="reviewed" class="flex min-h-11 items-start gap-2 text-sm text-text"><input v-model="checked" type="checkbox" name="ace-song-reviewed" :disabled="disabled" />{{ t('songFromBeat.confirm') }}</label>
      <p v-if="valid" role="status" class="text-sm text-status-done">{{ t('songFromBeat.ready') }}</p>
    </template>
  </section>
</template>

<style scoped>
@reference "../../style.css";
.song-label { @apply flex flex-col gap-2 text-sm text-text-dim; }
.song-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
.song-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

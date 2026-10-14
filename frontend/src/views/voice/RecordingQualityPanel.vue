<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listRecordingSessions, inspectRecording } from '../../api/recordingSessions'
import { ApiError } from '../../api/http'
import type { VoiceProfile } from '../../api/voices'
import type { RecordingLabels, RecordingSession } from '../../api/contracts'

const props = withDefaults(defineProps<{ voice: VoiceProfile; active?: boolean }>(), { active: true })
const emit = defineEmits<{ labels: [value: RecordingLabels] }>()
const { t, te } = useI18n()
const sessions = ref<RecordingSession[]>([]), filename = ref('')
const labels = ref<RecordingLabels>({ language: '', register: '', articulation: '', expression: '' })
const pending = ref(false), loaded = ref(false), error = ref(''), notice = ref('')
const fields = ['language', 'register', 'articulation', 'expression'] as const
const purposes = ['training', 'held_out'] as const
let alive = true, generation = 0
let controller: AbortController | undefined
const selected = computed(() => sessions.value.find(session => session.purpose === 'training' && session.relative_path === `recordings/${filename.value}`))
const canInspect = computed(() => loaded.value && !pending.value && !selected.value && props.voice.recordings.some(recording => recording.filename === filename.value))
function current(token: number, signal: AbortSignal) { return alive && props.active && token === generation && !signal.aborted }
function errorText(cause: unknown): string {
  const key = `recordingQC.errors.${cause instanceof ApiError ? cause.message : ''}`
  return te(key) ? t(key) : t('recordingQC.errors.unknown')
}
function percent(value: number) { return `${(value * 100).toFixed(2)}%` }
async function reload() {
  if (!alive || !props.active || pending.value) return
  const token = ++generation, voiceId = props.voice.id
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  pending.value = true; loaded.value = false; error.value = ''; notice.value = ''
  try {
    const response = await listRecordingSessions(voiceId, signal)
    if (current(token, signal)) { sessions.value = response; loaded.value = true }
  } catch (cause) {
    if (current(token, signal)) { sessions.value = []; error.value = errorText(cause) }
  } finally { if (current(token, signal)) pending.value = false }
}
async function inspect() {
  if (!canInspect.value || !props.active) return
  const token = ++generation, voiceId = props.voice.id
  const request = { purpose: 'training', filename: filename.value, labels: { ...labels.value } } as const
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  pending.value = true; error.value = ''; notice.value = ''
  try {
    const response = await inspectRecording(voiceId, request, signal)
    if (current(token, signal)) {
      sessions.value = [...sessions.value.filter(session => session.id !== response.id), response]
      notice.value = t('recordingQC.measured')
    }
  } catch (cause) { if (current(token, signal)) error.value = errorText(cause) }
  finally { if (current(token, signal)) pending.value = false }
}
watch(labels, value => emit('labels', { ...value }), { deep: true, immediate: true })
watch(() => [props.voice.id, props.active, JSON.stringify(props.voice.recordings.map(recording => recording.filename))] as const, (values, previous) => {
  generation++; controller?.abort(); pending.value = false; loaded.value = false
  if (values[0] !== previous?.[0]) {
    sessions.value = []; labels.value = { language: '', register: '', articulation: '', expression: '' }; error.value = ''; notice.value = ''
  }
  if (!props.voice.recordings.some(recording => recording.filename === filename.value)) filename.value = props.voice.recordings[0]?.filename ?? ''
  if (props.active) void reload()
}, { immediate: true })
onBeforeUnmount(() => { alive = false; generation++; controller?.abort() })
</script>

<template>
  <section v-if="active" class="space-y-3 rounded-lg border border-border p-3" :aria-label="t('recordingQC.title')">
    <h4 class="font-medium text-text">{{ t('recordingQC.title') }}</h4><p class="text-sm text-text-dim">{{ t('recordingQC.intro') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p><p v-if="notice" role="status" class="text-sm text-text">{{ notice }}</p><p v-if="pending && !loaded" role="status" class="text-sm text-text-dim">{{ t('recordingQC.loading') }}</p>
    <fieldset class="space-y-3"><legend class="text-sm font-medium text-text">{{ t('recordingQC.captureLabels') }}</legend><div class="grid gap-3 sm:grid-cols-2"><label v-for="field in fields" :key="field" class="space-y-1 text-sm text-text"><span>{{ t(`recordingQC.${field}`) }}</span><input v-model="labels[field]" :aria-label="t(`recordingQC.${field}`)" maxlength="120" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label></div><p class="text-sm text-text-dim">{{ t('recordingQC.labelsHint') }}</p></fieldset>
    <div class="flex flex-wrap items-end gap-3"><label class="min-w-0 flex-1 space-y-1 text-sm text-text"><span>{{ t('recordingQC.source') }}</span><select v-model="filename" :disabled="pending" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option value="">{{ t('recordingQC.choose') }}</option><option v-for="recording in voice.recordings" :key="recording.filename" :value="recording.filename">{{ recording.filename }}</option></select></label><button type="button" :disabled="!canInspect" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="inspect">{{ t('recordingQC.inspect') }}</button><button type="button" :disabled="pending" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="reload">{{ t('recordingQC.refresh') }}</button></div>
    <p v-if="selected" class="text-sm text-text-dim">{{ t('recordingQC.alreadyInspected') }}</p><p class="text-sm text-text-dim">{{ t('recordingQC.immutable') }}</p><p class="text-sm text-text-dim">{{ t('recordingQC.measurementHint') }}</p>
    <div v-for="purpose in purposes" :key="purpose" class="space-y-3"><h5 class="text-sm font-medium text-text">{{ t(purpose === 'training' ? 'recordingQC.training' : 'recordingQC.heldOut') }}</h5><p v-if="purpose === 'held_out'" class="text-sm text-text-dim">{{ t('recordingQC.heldOutHint') }}</p><p v-if="!sessions.some(session => session.purpose === purpose)" class="text-sm text-text-dim">{{ t('recordingQC.empty') }}</p>
      <article v-for="session in sessions.filter(item => item.purpose === purpose)" :key="session.id" class="space-y-3 rounded-lg bg-panel-2 p-3 text-sm text-text"><p class="break-words font-medium">{{ session.original_filename }}</p><dl class="grid grid-cols-2 gap-x-3 gap-y-2"><dt>{{ t('recordingQC.format') }}</dt><dd class="break-words">{{ session.format }} · {{ session.sample_rate }} Hz · {{ session.channels }} ch · {{ session.subtype }}</dd><dt>{{ t('recordingQC.duration') }}</dt><dd>{{ session.duration_sec.toFixed(2) }} s</dd><dt>{{ t('recordingQC.bytes') }}</dt><dd>{{ session.file_bytes }} B</dd><dt>{{ t('recordingQC.peak') }}</dt><dd>{{ session.qc.peak.toFixed(4) }}</dd><dt>{{ t('recordingQC.rms') }}</dt><dd>{{ session.qc.rms_dbfs.toFixed(1) }} dBFS</dd><dt>{{ t('recordingQC.fullScale') }}</dt><dd>{{ percent(session.qc.clipped_fraction) }}</dd><dt>{{ t('recordingQC.silence') }}</dt><dd>{{ percent(session.qc.near_silence_fraction) }}</dd><dt>{{ t('recordingQC.noiseProxy') }}</dt><dd>{{ session.qc.noise_floor_proxy_dbfs.toFixed(1) }} dBFS</dd><dt>{{ t('recordingQC.samples') }}</dt><dd>{{ session.qc.samples_analyzed }}</dd></dl>
      <div><p class="text-text-dim">{{ t('recordingQC.sha') }}</p><p class="break-all font-mono text-xs">{{ session.original_sha256 }}</p></div><div><p class="text-text-dim">{{ t('recordingQC.labels') }}</p><dl class="grid grid-cols-2 gap-x-3 gap-y-2"><template v-for="field in fields" :key="field"><dt>{{ t(`recordingQC.${field}`) }}</dt><dd class="break-words">{{ session.labels[field] || t('recordingQC.unknownLabel') }}</dd></template></dl></div><ul class="space-y-1 text-text-dim"><li v-for="warning in session.qc.warnings" :key="warning">{{ t(`recordingQC.warnings.${warning}`) }}</li></ul></article>
    </div>
  </section>
</template>

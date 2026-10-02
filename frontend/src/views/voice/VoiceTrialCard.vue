<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import { rateVoiceTrial, voiceErrorText } from '../../api/voices'
import type { VoiceComparisonResponse, VoiceComparisonTrial, VoiceTrialRating, VoiceModelChoice, VoiceReferenceCandidate } from '../../api/contracts'
import WaveformPlayer from '../../components/shared/WaveformPlayer.vue'
import { useVoiceSession } from './useVoiceSession'
import { shortVoiceId } from './voiceLabels'

const props = defineProps<{ voiceId: string; comparisonId: string; trial: VoiceComparisonTrial; modelChoices: VoiceModelChoice[]; referenceCandidates: VoiceReferenceCandidate[] }>()
const emit = defineEmits<{ rated: [comparison: VoiceComparisonResponse] }>()
const { t } = useI18n()
const capture = useVoiceSession(() => props.voiceId)
const fields = ['identity', 'pitch', 'intelligibility', 'artifacts'] as const
const rating = ref({ identity: '', pitch: '', intelligibility: '', artifacts: '', notes: '' })
const saving = ref(false)
const error = ref('')
const modelLabel = computed(() => {
  const model = props.modelChoices.find((choice) => choice.id === props.trial.model_id)
  if (model?.kind === 'base' || props.trial.model_id === 'base') return `${t('voiceClone.review.referenceOnly')} · ${shortVoiceId(props.trial.model_id)}`
  if (model) return `${t('voiceClone.review.trainedSteps', { steps: model.steps })} · ${shortVoiceId(model.id)}`
  return t('voiceClone.compare.archivedModel', { id: shortVoiceId(props.trial.model_id) })
})
const referenceLabel = computed(() => {
  if (props.trial.reference_id === 'published') return t('voiceClone.compare.publishedReference')
  const reference = props.referenceCandidates.find((candidate) => candidate.id === props.trial.reference_id)
  if (reference) return `${reference.source_filename} · ${reference.start_sec.toFixed(1)}–${reference.end_sec.toFixed(1)} ${t('common.secondsUnit')}`
  return t('voiceClone.compare.archivedReference', { id: shortVoiceId(props.trial.reference_id) })
})
watch(() => props.trial.rating, (saved) => {
  if (!saved) return
  rating.value = { identity: String(saved.identity), pitch: String(saved.pitch), intelligibility: String(saved.intelligibility), artifacts: String(saved.artifacts), notes: saved.notes ?? '' }
}, { immediate: true })
const canSave = computed(() => !saving.value && fields.every((field) => ['1', '2', '3', '4', '5'].includes(rating.value[field])))
async function save() {
  if (!canSave.value) return
  const session = capture()
  const trialId = props.trial.id
  const comparisonId = props.comparisonId
  const request: VoiceTrialRating = { identity: Number(rating.value.identity), pitch: Number(rating.value.pitch), intelligibility: Number(rating.value.intelligibility), artifacts: Number(rating.value.artifacts), notes: rating.value.notes }
  saving.value = true
  error.value = ''
  try {
    const response = await rateVoiceTrial(session.id, comparisonId, trialId, request, session.signal)
    if (session.isCurrent() && props.trial.id === trialId && props.comparisonId === comparisonId) emit('rated', response)
  } catch (cause) {
    if (session.isCurrent()) error.value = voiceErrorText(cause instanceof ApiError ? cause.message : 'unknown')
  } finally {
    if (session.isCurrent()) saving.value = false
  }
}
</script>

<template>
  <article :aria-labelledby="`trial-${comparisonId}-${trial.id}`" class="space-y-3 rounded-lg border border-border bg-panel-2 p-3">
    <h4 :id="`trial-${comparisonId}-${trial.id}`" class="text-sm text-text"><span :title="trial.model_id">{{ modelLabel }}</span> · <span :title="trial.reference_id">{{ referenceLabel }}</span> · {{ t('voiceClone.compare.stepsValue', { steps: trial.diffusion_steps }) }}</h4>
    <p class="text-xs text-text-dim">{{ t(`voiceClone.review.status.${trial.status ?? 'queued'}`) }}</p>
    <p v-if="trial.error_code" role="alert" class="text-xs text-status-failed">{{ voiceErrorText(trial.error_code) }}</p>
    <WaveformPlayer v-if="trial.audio_url" :src="trial.audio_url" />
    <p v-if="trial.metrics" class="text-xs text-text-dim">{{ t('voiceClone.compare.metrics', { seconds: trial.metrics.duration_sec.toFixed(1), delta: trial.metrics.duration_delta_sec.toFixed(1), level: trial.metrics.level_db.toFixed(1), peak: trial.metrics.peak.toFixed(3), clipped: (trial.metrics.clipped_fraction * 100).toFixed(2) }) }}</p>
    <div v-if="trial.status === 'done'" class="space-y-2">
      <p class="text-xs text-text-dim">{{ t('voiceClone.compare.ratingHint') }}</p>
      <div class="grid gap-2 sm:grid-cols-2"><label v-for="field in fields" :key="field" class="block space-y-1 text-xs text-text"><span>{{ t(`voiceClone.compare.rating.${field}`) }}</span><select v-model="rating[field]" :disabled="saving" :aria-label="t(`voiceClone.compare.rating.${field}`)" class="block w-full rounded border border-border bg-panel p-1"><option value="">{{ t('voiceClone.compare.unrated') }}</option><option v-for="score in [1, 2, 3, 4, 5]" :key="score" :value="String(score)">{{ score }}</option></select></label></div>
      <label class="block space-y-1 text-xs text-text"><span>{{ t('voiceClone.compare.notes') }}</span><textarea v-model="rating.notes" :disabled="saving" maxlength="2000" rows="2" class="w-full rounded border border-border bg-panel p-2" /></label>
      <button type="button" :disabled="!canSave" class="rounded border border-border px-3 py-1 text-xs text-text disabled:opacity-50" @click="save">{{ t('voiceClone.compare.saveRatings') }}</button>
      <p v-if="error" role="alert" class="text-xs text-status-failed">{{ error }}</p>
    </div>
  </article>
</template>

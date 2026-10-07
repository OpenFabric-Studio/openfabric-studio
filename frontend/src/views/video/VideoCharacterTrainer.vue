<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { createCharacterComparison, reviewCharacterComparison, cancelCharacterTraining, characterTrainerStatus, listCharacterTraining, saveCharacterTrainer, startCharacterTraining, videoRequestError } from '../../api/videos'
import type { CharacterDatasetReview, VideoCharacterTrainerStatus, VideoCharacterTrainingJob, VideoProject } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'

const props = defineProps<{ project: VideoProject; readOnly: boolean }>()
const emit = defineEmits<{ apply: [trainingId: string] }>()
const { t } = useI18n()
const status = ref<VideoCharacterTrainerStatus | null>(null)
const jobs = ref<VideoCharacterTrainingJob[]>([])
const name = ref('')
const consent = ref(false)
const command = ref('')
const error = ref('')
const reviewed = ref(false), steps = ref(800), rank = ref(32)
const examples = ref<Array<{ file: File; caption: string; role: 'training' | 'held_out' }>>([])
const reviewNotes = ref<Record<string, string>>({})
const savingCommand = ref(false)
const working = ref(false)
const lifetime = new AbortController()
let alive = true
const hasActiveJob = () => jobs.value.some(job => job.status === 'queued' || job.status === 'running')

const poll = createPollingLoop(async context => {
  try {
    const [trainer, saved] = await Promise.all([characterTrainerStatus(context.signal), listCharacterTraining(context.signal)])
    if (!alive || !context.isCurrent()) return false
    status.value = trainer
    jobs.value = saved.jobs
  } catch (cause) {
    if (!alive || !context.isCurrent()) return false
    error.value = videoRequestError(cause)
  }
  return hasActiveJob()
}, 3000)
onMounted(() => poll.start())
onBeforeUnmount(() => { alive = false; lifetime.abort(); poll.stop() })

function beginAction(): boolean {
  if (!alive || working.value) return false
  working.value = true
  error.value = ''
  poll.stop()
  return true
}
function finishAction() {
  if (!alive) return
  working.value = false
  if (hasActiveJob()) poll.start(false)
}

async function saveCommand() {
  if (props.readOnly || !command.value.trim() || !beginAction()) return
  savingCommand.value = true
  try {
    const saved = await saveCharacterTrainer(command.value.trim(), lifetime.signal)
    if (!alive) return
    status.value = saved
    command.value = ''
  } catch (cause) {
    if (alive) error.value = videoRequestError(cause)
  } finally {
    if (alive) savingCommand.value = false
    finishAction()
  }
}

function filesChanged(event: Event) {
  if (!(event.target instanceof HTMLInputElement)) return
  const form = event.target.closest('form')
  if (!form) return
  const photos = form.querySelector('input[data-trainer-photos]'), clips = form.querySelector('input[data-trainer-clips]')
  const files = [...(photos instanceof HTMLInputElement ? photos.files ?? [] : []), ...(clips instanceof HTMLInputElement ? clips.files ?? [] : [])]
  examples.value = files.map(file => examples.value.find(item => item.file === file) ?? { file, caption: '', role: 'training' })
  reviewed.value = false
}
function putJob(job: VideoCharacterTrainingJob) {
  jobs.value = [job, ...jobs.value.filter(item => item.id !== job.id)]
}
async function comparison(job: VideoCharacterTrainingJob) {
  if (props.readOnly || !beginAction()) return
  try { const saved = await createCharacterComparison(job.id, lifetime.signal); if (alive) putJob(saved) }
  catch (cause) { if (alive) error.value = videoRequestError(cause) }
  finally { finishAction() }
}
async function recordReview(job: VideoCharacterTrainingJob) {
  const notes = reviewNotes.value[job.id]?.trim()
  if (props.readOnly || !job.comparison || !notes || !beginAction()) return
  try { const saved = await reviewCharacterComparison(job.id, { comparison_id: job.comparison.id, notes, reviewed: true }, lifetime.signal); if (alive) putJob(saved) }
  catch (cause) { if (alive) error.value = videoRequestError(cause) }
  finally { finishAction() }
}

async function start(event: Event) {
  if (!alive || props.readOnly || working.value) return
  const form = event.target
  if (!(form instanceof HTMLFormElement)) return
  const photos = form.querySelector('input[data-trainer-photos]')
  const clips = form.querySelector('input[data-trainer-clips]')
  const photoFiles = photos instanceof HTMLInputElement ? [...(photos.files ?? [])] : []
  const clipFiles = clips instanceof HTMLInputElement ? [...(clips.files ?? [])] : []
  if (photoFiles.length < 3 || !name.value.trim() || !consent.value) {
    error.value = photoFiles.length < 3 ? 'too_few_photos' : 'consent_required'
    return
  }
  let review: CharacterDatasetReview | undefined
  if (examples.value.length) {
    if (!reviewed.value || examples.value.some(item => !item.caption.trim())) { error.value = 'dataset_review_required'; return }
    if (examples.value.filter(item => item.role === 'training' && item.file.type.startsWith('image/')).length < 3 || !examples.value.some(item => item.role === 'held_out' && item.file.type.startsWith('image/'))) { error.value = 'held_out_required'; return }
    review = { reviewed: true, items: examples.value.map((item, upload_index) => ({ upload_index, caption: item.caption.trim(), role: item.role })), settings: { base_profile: 'ltx23', steps: steps.value, rank: rank.value } }
  }
  if (!beginAction()) return
  try {
    const created = await startCharacterTraining({ name: name.value.trim(), consentConfirmed: true, files: [...photoFiles, ...clipFiles], review }, lifetime.signal)
    if (!alive) return
    jobs.value = [created, ...jobs.value.filter((job) => job.id !== created.id)]
    name.value = ''
    consent.value = false
    form.reset()
    examples.value = []; reviewed.value = false
  } catch (cause) {
    if (alive) error.value = videoRequestError(cause)
  } finally { finishAction() }
}

async function cancel(id: string) {
  if (!beginAction()) return
  try {
    const updated = await cancelCharacterTraining(id, lifetime.signal)
    if (!alive) return
    jobs.value = jobs.value.map((job) => job.id === updated.id ? updated : job)
  } catch (cause) {
    if (alive) error.value = videoRequestError(cause)
  } finally { finishAction() }
}
</script>

<template>
  <section data-character-trainer class="min-w-0 space-y-3 rounded-xl border border-border bg-panel p-5">
    <h3 class="font-semibold">{{ t('videoExperience.trainerTitle') }}</h3>
    <p class="text-sm leading-relaxed text-text-dim">{{ t('videoExperience.trainerHint') }}</p>
    <p v-if="status && !status.configured && status.source === 'none'" class="text-sm text-text-dim">{{ t('videoExperience.trainerMissingCommand') }}</p>
    <p v-else-if="status?.missing" role="status" class="text-sm text-status-failed">{{ t('videoExperience.trainerBroken') }}</p>
    <p v-else-if="status?.configured" class="text-sm text-text-dim">{{ t('videoExperience.trainerReady', { name: status.command_name }) }}</p>
    <p v-if="status?.configured && !status.dependencies_ready" role="status" class="text-sm text-text-dim">{{ t(status.reason === 'custom_trainer_unverified' ? 'videoDialogue.customTrainer' : 'videoDialogue.dependenciesMissing') }}</p>
    <form v-if="status?.source !== 'env'" class="space-y-3" @submit.prevent="saveCommand">
      <label>{{ t('videoExperience.trainerCommand') }}<input v-model="command" data-trainer-command type="text" :disabled="readOnly" :placeholder="status?.command_name || ''"></label>
      <button type="submit" :disabled="readOnly || working || savingCommand || !command.trim()">{{ t('videoExperience.trainerCommandSave') }}</button>
    </form>
    <ul v-if="jobs.length" class="space-y-2">
      <li v-for="job in jobs" :key="job.id" class="space-y-1 text-sm">
        <div class="flex flex-wrap items-center gap-3">
          <span>{{ job.name }} · {{ t(`videoExperience.trainerStatus.${job.status}`) }}</span>
          <span class="text-text-dim">{{ t('videoExperience.trainerCounts', { photos: job.photo_count, clips: job.clip_count }) }}</span>
          <button v-if="job.status === 'queued' || job.status === 'running'" type="button" :disabled="working" @click="cancel(job.id)">{{ t('video.cancel') }}</button>
          <button v-else-if="job.mock || job.adapter_ready" type="button" :disabled="readOnly || working || project.character_adapter_id === job.id || (!job.mock && project.settings?.engine_pack === 'ltx25')" @click="emit('apply', job.id)">{{ project.character_adapter_id === job.id ? t('videoExperience.trainerUsing') : (job.mock ? t('videoExperience.trainerMockUse') : t('videoExperience.trainerUse')) }}</button>
        </div>
        <p v-if="job.mock" class="text-text-dim">{{ t('videoExperience.trainerMockNote') }}</p>
        <p v-else-if="job.adapter_ready" class="text-text-dim">{{ t('videoExperience.trainerAdapterNote') }} {{ t(job.provenance?.evaluated ? 'videoDialogue.evaluated' : 'videoDialogue.pending') }}</p>
        <details v-if="job.provenance" class="rounded-lg border border-border p-3">
          <summary>{{ t(job.provenance.recipe ? 'videoDialogue.effectiveRecipe' : 'videoDialogue.recordedSettings') }}</summary>
          <div class="mt-2 space-y-2 text-xs text-text-dim">
            <p>{{ t('videoDialogue.recipeSteps', { steps: job.provenance.settings.steps, rank: job.provenance.settings.rank }) }}</p>
            <p v-if="job.provenance.recipe">{{ t('videoDialogue.recipeDetails', { width: job.provenance.recipe.width, height: job.provenance.recipe.height, frames: job.provenance.recipe.frames, rate: job.provenance.recipe.frame_rate, learningRate: job.provenance.recipe.learning_rate }) }}</p>
            <p>{{ t('videoDialogue.recipeBase', { engine: job.provenance.engine_commit.slice(0, 12), base: job.provenance.base_revision.slice(0, 12) }) }}</p>
            <p>{{ t(job.provenance.recipe ? 'videoDialogue.recipeMemory' : 'videoDialogue.recipeUnknown') }}</p>
            <p>{{ t('videoDialogue.recipeQuality') }}</p>
            <p v-if="job.provenance.evaluation_notes">{{ job.provenance.evaluation_notes }}</p>
          </div>
        </details>
        <div v-if="job.adapter_ready && job.provenance" class="space-y-2">
          <button type="button" :disabled="readOnly || working" @click="comparison(job)">{{ t('videoDialogue.compare') }}</button>
          <template v-if="job.comparison">
            <div class="flex flex-wrap gap-3"><a :href="`/video?project=${job.comparison.baseline_project_id}`">{{ t('videoDialogue.baseline') }}</a><a :href="`/video?project=${job.comparison.adapted_project_id}`">{{ t('videoDialogue.adapted') }}</a></div>
            <p class="text-xs text-text-dim">{{ t('videoDialogue.reviewHint') }}</p>
            <label>{{ t('videoDialogue.reviewNotes') }}<textarea v-model="reviewNotes[job.id]" rows="2" maxlength="2000" :disabled="readOnly || working" /></label>
            <button type="button" :disabled="readOnly || working || !reviewNotes[job.id]?.trim()" @click="recordReview(job)">{{ t('videoDialogue.review') }}</button>
          </template>
        </div>
        <p v-if="!job.mock && !job.adapter_ready && job.error_code" class="text-status-failed">{{ t(`video.err.${job.error_code}`) }}</p>
      </li>
    </ul>
    <form class="space-y-3" @submit.prevent="start">
      <label>{{ t('videoExperience.characterName') }}<input v-model="name" maxlength="80" :disabled="readOnly" required></label>
      <label>{{ t('videoExperience.trainerPhotos') }}<input data-trainer-photos type="file" accept="image/png,image/jpeg,image/webp" multiple :disabled="readOnly" required @change="filesChanged"></label>
      <label>{{ t('videoExperience.trainerClips') }}<input data-trainer-clips type="file" accept="video/mp4,video/quicktime" multiple :disabled="readOnly" @change="filesChanged"></label>
      <fieldset v-if="examples.length" class="space-y-3 rounded border border-border p-3" :disabled="readOnly || working">
        <legend>{{ t('videoDialogue.reviewTitle') }}</legend>
        <p class="text-xs text-text-dim">{{ t('videoDialogue.captionHint') }}</p>
        <div v-for="(example, index) in examples" :key="index" class="space-y-1">
          <label>{{ example.file.name }}<textarea v-model="example.caption" rows="2" maxlength="500" required /></label>
          <label class="inline-check"><input type="checkbox" :checked="example.role === 'held_out'" @change="example.role = example.role === 'held_out' ? 'training' : 'held_out'">{{ t('videoDialogue.heldOut') }}</label>
        </div>
        <div class="grid grid-cols-2 gap-2"><label>{{ t('videoDialogue.steps') }}<input v-model.number="steps" type="number" min="100" max="3000" /></label><label>{{ t('videoDialogue.rank') }}<input v-model.number="rank" type="number" min="8" max="64" /></label></div>
        <p class="text-xs text-text-dim">{{ t('videoDialogue.recipeQuality') }}</p>
        <label class="inline-check"><input v-model="reviewed" type="checkbox">{{ t('videoDialogue.datasetReviewed') }}</label>
      </fieldset>
      <label class="inline-check"><input v-model="consent" data-trainer-consent type="checkbox" :disabled="readOnly">{{ t('videoExperience.trainerConsent') }}</label>
      <button type="submit" :disabled="readOnly || working || !consent">{{ status?.configured ? t('videoExperience.trainerStart') : t('videoExperience.trainerDryRun') }}</button>
    </form>
    <p v-if="project.warnings?.includes('character_adapter_mock')" role="status" class="text-sm text-text-dim">{{ t('video.err.character_adapter_mock') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ t(`video.err.${error}`) }}</p>
  </section>
</template>

<style scoped>
label { display: flex; flex-direction: column; gap: .35rem; font-size: .875rem; }
label.inline-check { flex-direction: row; align-items: center; min-height: 44px; }
input:not([type=checkbox]):not([type=file]) { width: 100%; min-height: 44px; padding: .6rem .75rem; background: var(--color-panel-2); border: 1px solid var(--color-border); border-radius: .5rem; }
button { min-height: 44px; padding: .5rem .75rem; border-radius: .5rem; background: var(--color-panel-2); }
</style>

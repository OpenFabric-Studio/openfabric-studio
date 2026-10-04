<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { cancelCharacterTraining, characterTrainerStatus, listCharacterTraining, saveCharacterTrainer, startCharacterTraining, videoRequestError } from '../../api/videos'
import type { VideoCharacterTrainerStatus, VideoCharacterTrainingJob, VideoProject } from '../../api/contracts'

const props = defineProps<{ project: VideoProject; readOnly: boolean }>()
const emit = defineEmits<{ apply: [trainingId: string] }>()
const { t } = useI18n()
const status = ref<VideoCharacterTrainerStatus | null>(null)
const jobs = ref<VideoCharacterTrainingJob[]>([])
const name = ref('')
const consent = ref(false)
const command = ref('')
const error = ref('')
const savingCommand = ref(false)
let timer: ReturnType<typeof setInterval> | undefined

async function refresh() {
  const [trainer, saved] = await Promise.all([characterTrainerStatus(), listCharacterTraining()])
  status.value = trainer
  jobs.value = saved.jobs
}

onMounted(async () => {
  try {
    await refresh()
  } catch (cause) {
    error.value = videoRequestError(cause)
  }
  timer = setInterval(() => {
    if (jobs.value.some((job) => job.status === 'queued' || job.status === 'running')) {
      refresh().catch((cause) => { error.value = videoRequestError(cause) })
    }
  }, 3000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })

async function saveCommand() {
  savingCommand.value = true
  error.value = ''
  try {
    status.value = await saveCharacterTrainer(command.value.trim())
    command.value = ''
  } catch (cause) {
    error.value = videoRequestError(cause)
  } finally {
    savingCommand.value = false
  }
}

async function start(event: Event) {
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
  error.value = ''
  try {
    const created = await startCharacterTraining({ name: name.value.trim(), consentConfirmed: true, files: [...photoFiles, ...clipFiles] })
    jobs.value = [created, ...jobs.value.filter((job) => job.id !== created.id)]
    name.value = ''
    consent.value = false
    form.reset()
  } catch (cause) {
    error.value = videoRequestError(cause)
  }
}

async function cancel(id: string) {
  error.value = ''
  try {
    const updated = await cancelCharacterTraining(id)
    jobs.value = jobs.value.map((job) => job.id === updated.id ? updated : job)
  } catch (cause) {
    error.value = videoRequestError(cause)
  }
}
</script>

<template>
  <section data-character-trainer class="min-w-0 space-y-3 rounded-xl border border-border bg-panel p-5">
    <h3 class="font-semibold">{{ t('videoExperience.trainerTitle') }}</h3>
    <p class="text-sm leading-relaxed text-text-dim">{{ t('videoExperience.trainerHint') }}</p>
    <p v-if="status && !status.configured && status.source === 'none'" class="text-sm text-text-dim">{{ t('videoExperience.trainerMissingCommand') }}</p>
    <p v-else-if="status?.missing" role="status" class="text-sm text-status-failed">{{ t('videoExperience.trainerBroken') }}</p>
    <p v-else-if="status?.configured" class="text-sm text-text-dim">{{ t('videoExperience.trainerReady', { name: status.command_name }) }}</p>
    <form v-if="status?.source !== 'env'" class="space-y-3" @submit.prevent="saveCommand">
      <label>{{ t('videoExperience.trainerCommand') }}<input v-model="command" data-trainer-command type="text" :disabled="readOnly" :placeholder="status?.command_name || ''"></label>
      <button type="submit" :disabled="readOnly || savingCommand || !command.trim()">{{ t('videoExperience.trainerCommandSave') }}</button>
    </form>
    <ul v-if="jobs.length" class="space-y-2">
      <li v-for="job in jobs" :key="job.id" class="space-y-1 text-sm">
        <div class="flex flex-wrap items-center gap-3">
          <span>{{ job.name }} · {{ t(`videoExperience.trainerStatus.${job.status}`) }}</span>
          <span class="text-text-dim">{{ t('videoExperience.trainerCounts', { photos: job.photo_count, clips: job.clip_count }) }}</span>
          <button v-if="job.status === 'queued' || job.status === 'running'" type="button" @click="cancel(job.id)">{{ t('video.cancel') }}</button>
          <button v-else type="button" :disabled="readOnly || project.character_adapter_id === job.id" @click="emit('apply', job.id)">{{ project.character_adapter_id === job.id ? t('videoExperience.trainerUsing') : (job.mock ? t('videoExperience.trainerMockUse') : t('videoExperience.trainerUse')) }}</button>
        </div>
        <p v-if="job.mock" class="text-text-dim">{{ t('videoExperience.trainerMockNote') }}</p>
        <p v-else-if="job.adapter_ready" class="text-text-dim">{{ t('videoExperience.trainerAdapterNote') }}</p>
        <p v-else-if="job.error_code" class="text-status-failed">{{ t(`video.err.${job.error_code}`) }}</p>
      </li>
    </ul>
    <form class="space-y-3" @submit.prevent="start">
      <label>{{ t('videoExperience.characterName') }}<input v-model="name" maxlength="80" :disabled="readOnly" required></label>
      <label>{{ t('videoExperience.trainerPhotos') }}<input data-trainer-photos type="file" accept="image/png,image/jpeg,image/webp" multiple :disabled="readOnly" required></label>
      <label>{{ t('videoExperience.trainerClips') }}<input data-trainer-clips type="file" accept="video/mp4,video/quicktime" multiple :disabled="readOnly"></label>
      <label class="inline-check"><input v-model="consent" data-trainer-consent type="checkbox" :disabled="readOnly">{{ t('videoExperience.trainerConsent') }}</label>
      <button type="submit" :disabled="readOnly || !consent">{{ status?.configured ? t('videoExperience.trainerStart') : t('videoExperience.trainerDryRun') }}</button>
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

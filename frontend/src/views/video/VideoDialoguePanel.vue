<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { CreateDialogueReelRequest, VideoProject, VideoDialogueCue } from '../../api/contracts'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
import { dialoguePassages, videoRequestError } from '../../api/videos'
const props = defineProps<{ project: VideoProject; readOnly: boolean }>()
const emit = defineEmits<{ refresh: [shotId: string, source: CreateDialogueReelRequest] }>()
const { t } = useI18n()
const working = ref(''), error = ref('')
const captions = ref<Record<string, string>>({})
const starts = ref<Record<string, number | string>>({}), ends = ref<Record<string, number | string>>({})
function clipBound(value: number | string | undefined, fallback: number | null): number | null {
  if (value === undefined || value === '') return fallback
  const result = typeof value === 'number' ? value : Number(value)
  return Number.isInteger(result) && result >= 0 && result <= 600_000 ? result : -1
}
const audio = ref<HTMLAudioElement | null>(null)
let request: AbortController | null = null
function releaseAudio() { if (audio.value) releasePlaybackIfCurrent(audio.value) }
function stopAudio() { audio.value?.pause(); releaseAudio() }
function playAudio() { if (alive && audio.value) claimPlayback(audio.value) }
let generation = 0, alive = true
watch(() => [props.project.id, props.project.speech_clip?.id], () => { stopAudio() }, { flush: 'sync' })
watch(() => [props.project.id, props.project.revision, props.project.speech_clip?.id], () => { generation++; request?.abort(); request = null; working.value = ''; error.value = ''; captions.value = {}; starts.value = {}; ends.value = {} }, { flush: 'sync' })
onBeforeUnmount(() => { alive = false; generation++; request?.abort(); stopAudio() })
function peaks(cue: VideoDialogueCue): string {
  return (cue.waveform_peaks ?? []).map((peak, index, all) => `${index / Math.max(1, all.length - 1) * 600},${30 - peak * 25}`).join(' ')
}
async function refresh(cue: VideoDialogueCue) {
  if (props.readOnly || working.value) return
  const token = generation, id = props.project.id
  const controller = new AbortController(); request = controller
  working.value = cue.shot_id; error.value = ''
  try {
    const source = await dialoguePassages(cue.book_id, cue.chapter_index, controller.signal)
    if (!alive || props.readOnly || token !== generation || id !== props.project.id) return
    const passage = source.passages.find(item => item.id === cue.passage_id)
    if (!passage || passage.status !== 'done') { error.value = 'passage_not_ready'; return }
    const explicitBounds = (starts.value[cue.shot_id] !== undefined && starts.value[cue.shot_id] !== '') || (ends.value[cue.shot_id] !== undefined && ends.value[cue.shot_id] !== '')
    const wholeLine = !explicitBounds && cue.source_start_ms === 0 && cue.source_duration_ms != null && cue.source_end_ms === cue.source_duration_ms
    const start = clipBound(starts.value[cue.shot_id], wholeLine ? 0 : cue.source_start_ms)
    const end = clipBound(ends.value[cue.shot_id], wholeLine ? null : cue.source_end_ms)
    if (start === null || start < 0 || (end !== null && (end < start + 200 || end > passage.end_ms - passage.start_ms))) { error.value = 'dialogue_clip_bounds'; return }
    const customCaption = captions.value[cue.shot_id]?.trim()
    if (!wholeLine && !customCaption) { error.value = 'dialogue_clip_caption_required'; return }
    const shot = (props.project.shots ?? []).find(item => item.id === cue.shot_id)
    emit('refresh', cue.shot_id, { book_id: cue.book_id, chapter_index: cue.chapter_index, revision: source.revision,
      name: props.project.name, selections: [{ passage_id: cue.passage_id, clip_start_ms: start,
        clip_end_ms: end, prompt: shot?.prompt ?? 'A character speaking naturally',
        caption: customCaption || passage.text }] })
  } catch (cause) { if (alive && token === generation) error.value = videoRequestError(cause) }
  finally { if (alive && token === generation) { working.value = ''; request = null } }
}
</script>
<template>
  <section class="min-w-0 space-y-4 rounded-xl border border-border bg-panel p-5" data-video-dialogue>
    <h3 class="font-semibold">{{ t('videoDialogue.title') }}</h3>
    <p class="text-sm text-text-dim">{{ t('videoDialogue.hint') }}</p>
    <audio :key="`${project.id}:${project.speech_clip?.id ?? 'none'}`" ref="audio" @play="playAudio" @pause="releaseAudio" @ended="releaseAudio" @error="stopAudio" controls preload="none" class="w-full" :src="`/api/videos/projects/${project.id}/speech?clip=${project.speech_clip?.id ?? 'none'}`" :aria-label="t('videoDialogue.soundtrack')" />
    <article v-for="cue in project.dialogue_cues" :key="cue.shot_id" class="space-y-2 border-t border-border pt-3">
      <p class="text-sm font-medium">{{ cue.speaker }} · {{ cue.start_sec.toFixed(2) }}–{{ cue.end_sec.toFixed(2) }} s</p>
      <p class="text-sm">{{ cue.text }}</p>
      <svg v-if="cue.waveform_peaks?.length" viewBox="0 0 600 35" class="h-10 w-full" role="img" :aria-label="t('videoDialogue.waveform', { speaker: cue.speaker })"><polyline :points="peaks(cue)" fill="none" stroke="currentColor" class="text-accent1" /></svg>
      <details><summary class="cursor-pointer text-sm text-text-dim">{{ t('videoDialogue.refreshTitle') }}</summary>
        <div class="mt-3 space-y-2">
          <p class="text-xs text-text-dim">{{ t('videoDialogue.refreshHint') }}</p>
          <p class="text-xs text-text-dim">{{ t('videoDialogue.boundsHint') }}</p>
          <div class="grid gap-2 sm:grid-cols-2">
            <label class="block text-sm">{{ t('videoDialogue.clipStart') }}<input v-model="starts[cue.shot_id]" data-cue-start type="number" min="0" max="600000" step="1" :placeholder="String(cue.source_start_ms)" :disabled="readOnly || Boolean(working)" class="mt-1 w-full rounded border border-border bg-panel-2 p-2" /></label>
            <label class="block text-sm">{{ t('videoDialogue.clipEnd') }}<input v-model="ends[cue.shot_id]" data-cue-end type="number" min="200" max="600000" step="1" :placeholder="String(cue.source_end_ms)" :disabled="readOnly || Boolean(working)" class="mt-1 w-full rounded border border-border bg-panel-2 p-2" /></label>
          </div>
          <label class="block text-sm">{{ t('videoDialogue.caption') }}<input v-model="captions[cue.shot_id]" :placeholder="(project.overlays ?? []).find(item => item.id === cue.shot_id)?.text || cue.text" maxlength="500" :disabled="readOnly || Boolean(working)" class="mt-1 w-full rounded border border-border bg-panel-2 p-2" /></label>
          <button type="button" :disabled="readOnly || Boolean(working)" class="rounded bg-panel-2 px-3 py-2 text-sm" @click="refresh(cue)">{{ t('videoDialogue.refresh') }}</button>
        </div>
      </details>
    </article>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ t(`video.err.${error}`) }}</p>
  </section>
</template>

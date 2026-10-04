<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '../../components/shared/AppIcon.vue'
import type { AppIconName } from '../../components/shared/appIcons'
import type { VideoProject, VideoReadinessResponse } from '../../api/contracts'
import type { VideoDraft } from './useVideoWorkspace'

const draft = defineModel<VideoDraft>({ required: true })
const props = defineProps<{ project: VideoProject; readiness: VideoReadinessResponse | null; readOnly: boolean; canAnalyze: boolean }>()
const emit = defineEmits<{ analyze: []; continue: []; duplicate: []; upload: [file: File] }>()
const { t, te } = useI18n()
const panelId = useId()
const uploadError = ref('')
watch(() => props.project.id, () => { uploadError.value = '' })
const approachChoices: { mode: VideoDraft['mode']; title: string; hint: string; icon: AppIconName }[] = [
  { mode: 'generated', title: 'videoWorkspace.generated', hint: 'videoDirection.generatedHint', icon: 'video' },
  { mode: 'cover', title: 'videoWorkspace.cover', hint: 'videoDirection.coverHint', icon: 'editor' },
  { mode: 'visualizer', title: 'videoWorkspace.visualizer', hint: 'videoDirection.visualizerHint', icon: 'yue2' },
]
const picture = computed(() => props.project.track_id == null)
const reel = computed(() => props.project.preset === 'reel')
const approaches = computed(() => picture.value ? approachChoices.filter((item) => item.mode === 'generated') : approachChoices)
const generated = computed(() => draft.value.mode === 'generated')
const references = computed(() => props.project.references ?? [])
const engineOption = computed(() => props.readiness?.options.find(option => option.id === (draft.value.settings.engine_pack ?? 'ltx23')))
const comparisonOption = computed(() => props.readiness?.options.find(option => option.id === 'ltx25'))
const canUpload = computed(() => !props.readOnly && Boolean(props.readiness?.ffmpeg_ready) && references.value.length < 6)
const analysisAllowed = computed(() => props.canAnalyze && !props.readOnly && !props.project.source_changed && Boolean(props.readiness?.analysis_ready && props.readiness.ffmpeg_ready))
const analysisReason = computed(() => {
  if (picture.value) return ''
  if (props.project.source_changed) return t('videoWorkspace.sourceChanged')
  if (!props.readiness) return t('videoWorkspace.readinessFailed')
  if (!props.readiness.analysis_ready || !props.readiness.ffmpeg_ready) return t('videoWorkspace.analysisDependencyHint')
  if (props.readOnly) return t('videoDirection.analysisReadOnly')
  if (!props.canAnalyze) return t('videoDirection.analysisBusy')
  return ''
})
const gib = (bytes: number) => (bytes / 1024 ** 3).toFixed(1)

function publicSetupText(code: string | undefined, kind: 'reason' | 'warning'): string {
  if (code === 'insufficient_disk_space') return t('videoDirection.insufficientDisk')
  if (code === 'ltx25_comparison_opt_in') return t('videoDirection.comparisonOptIn')
  const key = `video.err.${code ?? ''}`
  return code && te(key) ? t(key) : t(kind === 'reason' ? 'videoDirection.unknownSetupReason' : 'videoDirection.unknownSetupWarning')
}
function selectApproach(mode: VideoDraft['mode']) {
  if (!props.readOnly) draft.value.mode = mode
}
function setSize(event: Event) {
  if (props.readOnly || reel.value || !(event.target instanceof HTMLSelectElement)) return
  if (event.target.value === '704') { draft.value.settings.width = 704; draft.value.settings.height = 448 }
  else if (event.target.value === '768') { draft.value.settings.width = 768; draft.value.settings.height = 512 }
  else if (event.target.value === '1280') { draft.value.settings.width = 1280; draft.value.settings.height = 704 }
  else if (event.target.value === '704x1280') { draft.value.settings.width = 704; draft.value.settings.height = 1280 }
}
function filesChanged(event: Event) {
  if (!(event.target instanceof HTMLInputElement)) return
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || !canUpload.value) return
  uploadError.value = ''
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) { uploadError.value = t('video.err.invalid_reference'); return }
  if (file.size > 20 * 1024 * 1024) { uploadError.value = t('video.err.reference_too_large'); return }
  emit('upload', file)
}
function analyze() { if (analysisAllowed.value) emit('analyze') }
function duplicate() { if (!props.readOnly) emit('duplicate') }
</script>

<template>
  <div class="video-direction min-w-0 space-y-5">
    <p class="text-sm leading-relaxed text-text-dim">{{ t('videoDirection.intro') }}</p>
    <div role="group" :aria-label="t('videoWorkspace.mode')" class="approach-grid grid min-w-0 gap-3">
      <button v-for="approach in approaches" :key="approach.mode" type="button" :data-video-approach="approach.mode" :aria-label="t(approach.title)" :aria-describedby="`${panelId}-${approach.mode}-hint`" :aria-pressed="draft.mode === approach.mode" :disabled="readOnly" class="approach-card" @click="selectApproach(approach.mode)">
        <span class="flex items-center justify-between gap-3">
          <span class="flex items-center gap-2.5"><AppIcon :name="approach.icon" /><span class="font-medium">{{ t(approach.title) }}</span></span>
          <span v-if="draft.mode === approach.mode" aria-hidden="true" class="text-xs text-accent2">{{ t('videoDirection.selected') }}</span>
        </span>
        <span :id="`${panelId}-${approach.mode}-hint`" class="mt-2 block text-xs leading-relaxed text-text-dim">{{ t(approach.hint) }}</span>
      </button>
    </div>

    <fieldset :disabled="readOnly" class="min-w-0 space-y-5 rounded-xl border border-border bg-panel p-4 sm:p-5">
      <div v-if="generated" class="space-y-4">
        <label>{{ t('videoWorkspace.directionPrompt') }}
          <textarea v-model="draft.direction" data-video-direction-prompt rows="3" maxlength="2000" :placeholder="t('video.promptPlaceholder')" :aria-describedby="`${panelId}-direction-hint`" />
          <span :id="`${panelId}-direction-hint`" class="field-hint">{{ t('videoWorkspace.directionHint') }}</span>
        </label>
        <label>{{ t('videoWorkspace.engine') }}
          <select v-model="draft.settings.engine_pack" data-video-model>
            <option value="ltx23">LTX-2.3</option>
            <option value="ltx25" :disabled="!comparisonOption?.available">LTX-2.5 · {{ t('videoWorkspace.experimental') }}</option>
          </select>
          <span class="field-hint">{{ t('videoWorkspace.engineHint') }}</span>
        </label>
        <p v-if="!readiness" role="status" class="field-hint">{{ t('videoWorkspace.readinessFailed') }}</p>
        <p v-else-if="!engineOption?.available" role="status" class="field-hint">{{ engineOption?.name || t('videoWorkspace.engine') }}: {{ publicSetupText(engineOption?.reason, 'reason') }}</p>
        <p v-if="readiness && draft.settings.engine_pack !== 'ltx25' && !comparisonOption?.available" class="field-hint">LTX-2.5: {{ publicSetupText(comparisonOption?.reason, 'reason') }}</p>
      </div>
      <div class="grid min-w-0 gap-4 sm:grid-cols-2">
        <label>{{ t('video.size') }}
          <select :value="draft.settings.height === 1280 ? '704x1280' : String(draft.settings.width ?? 704)" :disabled="reel" data-video-size @change="setSize">
            <option value="704">704×448</option><option value="768">768×512</option><option value="1280">1280×704</option><option value="704x1280">704×1280</option>
          </select>
          <span class="field-hint">{{ reel ? t('videoDirection.reelSizeHint') : generated ? t('videoWorkspace.sizeHint') : t('videoDirection.imageSizeHint') }}</span>
        </label>
        <label>{{ t('videoWorkspace.newShotSeed') }}
          <input v-model.number="draft.seed" data-video-seed type="number" min="0" max="2147483647">
          <span class="field-hint">{{ t('videoWorkspace.seedHint') }}</span>
        </label>
      </div>
    </fieldset>

    <section class="min-w-0 space-y-4 rounded-xl border border-border bg-panel p-4 sm:p-5" :aria-labelledby="`${panelId}-references`">
      <div class="flex flex-wrap items-start justify-between gap-2">
        <div><h3 :id="`${panelId}-references`" class="font-medium">{{ t('videoWorkspace.references') }}</h3><p class="mt-1 text-xs text-text-dim">{{ picture && generated ? t('videoDirection.stillHint') : generated ? t('videoDirection.generatedReferences') : t('videoDirection.requiredReferences') }}</p></div>
        <span class="text-xs text-text-dim">{{ t('videoDirection.referenceCount', { count: references.length }) }}</span>
      </div>
      <p class="field-hint">{{ t('videoWorkspace.referenceHint') }}</p>
      <label>{{ t('videoWorkspace.uploadImage') }}<input type="file" accept="image/png,image/jpeg,image/webp" :disabled="!canUpload" @change="filesChanged"></label>
      <div v-if="references.length" class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <figure v-for="image in references" :key="image.id" class="min-w-0"><img :src="image.url" :alt="image.name" loading="lazy" class="aspect-[4/3] w-full rounded-lg object-cover"><figcaption :title="image.name" class="mt-1 truncate text-xs text-text-dim">{{ image.name }} · {{ image.width }}×{{ image.height }}</figcaption></figure>
      </div>
      <p v-if="!generated && !references.length" role="status" class="text-sm text-status-failed">{{ t('videoWorkspace.imageRequired') }}</p>
      <p v-if="references.length >= 6" class="field-hint">{{ t('videoDirection.referenceLimit') }}</p>
      <p v-if="!readiness?.ffmpeg_ready" class="field-hint">{{ t('videoDirection.uploadUnavailable') }}</p>
      <p v-if="uploadError" role="alert" class="text-sm text-status-failed">{{ uploadError }}</p>
      <p v-if="!generated" class="field-hint">{{ t('videoWorkspace.imageMotionHint') }}</p>
    </section>

    <details class="disclosure" data-video-advanced>
      <summary>{{ t('videoDirection.advanced') }}</summary>
      <div class="mt-4 space-y-4">
        <fieldset v-if="generated" :disabled="readOnly" class="grid min-w-0 gap-4 sm:grid-cols-2">
          <label>{{ t('video.denoiseSteps') }}<input v-model.number="draft.settings.stage1_steps" data-video-denoise type="number" min="10" max="50"></label>
          <label>{{ t('video.refineSteps') }}<input v-model.number="draft.settings.stage2_steps" data-video-refine type="number" min="1" max="3"><span class="field-hint">{{ t('videoWorkspace.refineHint') }}</span></label>
          <label>{{ t('video.guidance') }}<input v-model.number="draft.settings.cfg_scale" data-video-guidance type="number" min="1" max="8" step="0.1"></label>
          <label>{{ t('videoWorkspace.negative') }}<input v-model="draft.settings.negative_prompt" data-video-negative maxlength="400"></label>
        </fieldset>
        <div class="space-y-2 border-t border-border pt-4"><button type="button" :disabled="readOnly" @click="duplicate">{{ t('videoWorkspace.duplicateProject') }}</button><p class="field-hint">{{ t('videoDirection.duplicateHint') }}</p></div>
      </div>
    </details>

    <details class="disclosure" data-video-setup>
      <summary>{{ t('videoDirection.setup') }}</summary>
      <div class="mt-4 space-y-3 text-sm">
        <template v-if="readiness">
          <p>FFmpeg: {{ readiness.ffmpeg_ready ? t('videoDirection.toolsReady') : t('videoDirection.toolsMissing') }} · {{ t('videoWorkspace.text') }}: {{ readiness.overlay_ready ? t('videoDirection.toolsReady') : t('videoDirection.toolsMissing') }}</p>
          <template v-if="generated && engineOption">
            <p>{{ engineOption.name }} · {{ engineOption.available ? t('videoWorkspace.installed') : publicSetupText(engineOption.reason, 'reason') }}</p>
            <p class="field-hint">{{ t('videoWorkspace.disk', { total: gib(engineOption.total_bytes), missing: gib(engineOption.uncached_bytes), free: gib(engineOption.free_bytes) }) }}</p>
            <p v-for="(warning, index) in engineOption.warnings" :key="index" class="field-hint">{{ publicSetupText(warning, 'warning') }}</p>
          </template>
          <p v-for="(warning, index) in readiness.warnings" :key="index" class="field-hint">{{ publicSetupText(warning, 'warning') }}</p>
        </template>
        <p v-else>{{ t('videoWorkspace.readinessFailed') }}</p>
        <p v-if="generated && !engineOption?.available" class="field-hint">{{ t('videoWorkspace.setupHint') }}</p>
        <p v-if="!generated" class="field-hint">{{ t('videoWorkspace.cpuMode') }}</p>
      </div>
    </details>

    <footer class="space-y-3 border-t border-border pt-5">
      <p v-if="analysisReason" :id="`${panelId}-analysis-blocked`" role="status" class="text-sm text-text-dim">{{ analysisReason }}</p>
      <div class="flex flex-wrap gap-3">
        <button v-if="!picture" type="button" :disabled="!analysisAllowed" :aria-describedby="analysisReason ? `${panelId}-analysis-blocked` : `${panelId}-analysis-hint`" class="primary" @click="analyze">{{ t('video.analyze') }}</button>
        <button type="button" @click="emit('continue')">{{ t('videoWorkspace.editStoryboard') }}</button>
      </div>
      <p v-if="picture" class="field-hint">{{ t('videoDirection.pictureHint') }}</p>
      <p v-else :id="`${panelId}-analysis-hint`" class="field-hint">{{ t('videoDirection.analyzeHint') }} {{ t('videoWorkspace.analysisHint') }}</p>
      <p class="field-hint">{{ draft.shots.length ? t('videoDirection.continueHint') : t('videoDirection.emptyStoryboard') }}</p>
    </footer>
  </div>
</template>

<style scoped>
.video-direction label { display: flex; min-width: 0; flex-direction: column; gap: .4rem; font-size: .875rem; }
.video-direction input, .video-direction select, .video-direction textarea { width: 100%; min-width: 0; min-height: 44px; padding: .6rem .75rem; background: var(--color-panel-2); border: 1px solid var(--color-border); border-radius: .5rem; }
.video-direction textarea { resize: vertical; }
.approach-grid { grid-template-columns: repeat(auto-fit, minmax(min(100%, 15rem), 1fr)); }
.video-direction button { min-height: 44px; padding: .6rem 1rem; border: 1px solid var(--color-border); border-radius: .6rem; background: var(--color-panel-2); }
.video-direction button.primary { border-color: var(--color-accent1); background: var(--color-accent1); color: white; }
.video-direction button.approach-card { min-width: 0; padding: 1rem; text-align: left; background: var(--color-panel); transition: border-color 150ms, background-color 150ms; }
.video-direction button.approach-card[aria-pressed=true] { border-color: var(--color-accent2); background: color-mix(in srgb, var(--color-accent1) 10%, var(--color-panel)); }
.video-direction button:not(:disabled):hover { border-color: var(--color-accent2); }
.video-direction button:disabled, .video-direction input:disabled, .video-direction fieldset:disabled input, .video-direction fieldset:disabled select, .video-direction fieldset:disabled textarea { opacity: .5; cursor: not-allowed; }
.video-direction button:focus-visible, .video-direction input:focus-visible, .video-direction select:focus-visible, .video-direction textarea:focus-visible, .video-direction summary:focus-visible { outline: 2px solid var(--color-accent2); outline-offset: 3px; }
.field-hint { font-size: .75rem; line-height: 1.6; color: var(--color-text-dim); overflow-wrap: anywhere; }
.disclosure { min-width: 0; padding: .5rem 1rem; border: 1px solid var(--color-border); border-radius: .75rem; background: var(--color-panel); }
.disclosure summary { min-height: 44px; padding-block: .75rem; cursor: pointer; font-size: .875rem; color: var(--color-text-dim); }
@media (prefers-reduced-motion: reduce) { .video-direction button.approach-card { transition: none; } }
</style>

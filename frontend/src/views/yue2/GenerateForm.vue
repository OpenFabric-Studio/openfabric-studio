<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useYue2Store } from '../../stores/yue2'
import type { CotMode, GenerateOptions } from '../../api/yue2'
import ChipGroup from '../../components/shared/ChipGroup.vue'
import CollapsibleDetails from '../../components/shared/CollapsibleDetails.vue'
import HelpModal from '../../components/shared/HelpModal.vue'
import TagInput from '../../components/shared/TagInput.vue'
import VoiceSelect from '../../components/shared/VoiceSelect.vue'
import MusicAdapterSelect from '../../components/shared/MusicAdapterSelect.vue'
import GenerationLibraryButton from '../../components/shared/GenerationLibraryButton.vue'
import ReferencePreparationButton from '../../components/shared/ReferencePreparationButton.vue'
import { setActiveVoiceId } from '../../api/voices'
import { parseMusicAdapterSelection } from '../../api/contracts'
import { ApiError } from '../../api/http'
import { transformAbc } from '../../api/references'
import type { MusicAdapterSelection, YueGenerationSettings } from '../../api/contracts'
import { pendingYueDraft, pendingYueReference, pendingMusicAdapter } from '../../composables/generationDrafts'
import { samplingSettings, finiteNumber, SAMPLING_KEYS, type SamplingKey, type SamplingSettings } from '../../composables/generationPresets'

const props = withDefaults(defineProps<{ generationAvailable?: boolean }>(), { generationAvailable: true })
const store = useYue2Store()
const selectedVoiceId = ref<string | null>(null)
const arAdapter = ref<MusicAdapterSelection | null>(null)
const narAdapter = ref<MusicAdapterSelection | null>(null)
const arAdapterValid = ref(true), narAdapterValid = ref(true)
const referenceNotice = ref(false)
const { t, tm } = useI18n()
const MUSIC_KEYS: NonNullable<YueGenerationSettings['keyScale']>[] = [
  'C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#', 'F', 'Bb', 'Eb', 'Ab', 'Db', 'Gb', 'Cb',
  'Am', 'Em', 'Bm', 'F#m', 'C#m', 'G#m', 'D#m', 'A#m', 'Dm', 'Gm', 'Cm', 'Fm', 'Bbm', 'Ebm', 'Abm',
]
const MUSIC_METERS: NonNullable<YueGenerationSettings['timeSignature']>[] = ['2/4', '3/4', '4/4', '6/8', '9/8', '12/8']

watch(
  () => store.pendingParamsInsert,
  (params) => {
    if (!params) return
    invalidateScoreEdit()
    referenceImportId.value = null; referenceNotice.value = false
    lyrics.value = typeof params.lyrics === 'string' ? params.lyrics : ''
    style.value = typeof params.style === 'string' ? params.style : ''
    songTitle.value = typeof params.song_title === 'string' ? params.song_title : ''
    cot.value = params.cot === 'melody' || params.cot === 'full' ? params.cot : 'off'
    precision.value = params.precision === 'q4_0' ? params.precision : 'q8_0'
    seed.value = finiteNumber(params.seed) ? params.seed : 831001
    randomSeed.value = params.random_seed === true
    abc.value = typeof params.abc === 'string' ? params.abc : ''
    restoreMusicalChoices(params.bpm, params.key_scale, params.time_signature)
    cfgScale.value = finiteNumber(params.cfg_scale) ? params.cfg_scale : null
    numInferenceSteps.value = finiteNumber(params.num_inference_steps) ? params.num_inference_steps : params.num_inference_steps === null ? null : 8
    Object.assign(semantic, samplingSettings(params.semantic))
    Object.assign(abcSampling, samplingSettings(params.abc_sampling))
    arAdapter.value = copiedAdapter(params.ar_adapter)
    narAdapter.value = copiedAdapter(params.nar_adapter)
    for (const key of SAMPLING_KEYS) {
      const semanticValue = params[`semantic_${key}`]
      const abcValue = params[`abc_${key}`]
      if (finiteNumber(semanticValue)) semantic[key] = semanticValue
      if (finiteNumber(abcValue)) abcSampling[key] = abcValue
    }
    store.clearPendingParamsInsert()
  },
)

const lyrics = ref('')
const songTitle = ref('')
const style = ref('')
const cot = ref<CotMode>('off')
const precision = ref<'q8_0' | 'q4_0'>('q8_0')
const abc = ref('')
const bpmInput = ref<number | string>('')
const bpmBadInput = ref(false)
const keyScale = ref<NonNullable<YueGenerationSettings['keyScale']> | ''>('')
const timeSignature = ref<NonNullable<YueGenerationSettings['timeSignature']> | ''>('')
const bpm = computed(() => validTempo(bpmInput.value) ? bpmInput.value : null)
const invalidBpm = computed(() => bpmBadInput.value || bpmInput.value !== '' && bpm.value === null)
const externalAbc = computed(() => cot.value !== 'off' && !!abc.value.trim())
const musicalChoicesPending = computed(() => bpmInput.value !== '' || keyScale.value !== '' || timeSignature.value !== '')
const applyingScore = ref(false)
const scoreError = ref('')
const scoreApplied = ref(false)
let scoreRequestId = 0
let scoreController: AbortController | null = null
watch([abc, cot, bpmInput, bpmBadInput, keyScale, timeSignature], invalidateScoreEdit, { flush: 'sync' })

const referenceImportId = ref<string | null>(null)
let formAlive = true
onBeforeUnmount(() => { formAlive = false; invalidateScoreEdit() })

const seed = ref(831001)
const randomSeed = ref(false)
const batchSize = ref(1)

const cfgScale = ref<number | null>(null)
const numInferenceSteps = ref<number | null>(8)
const semantic = reactive<SamplingSettings>({
  temperature: null, top_p: null, top_k: null, repetition_penalty: null, penalty_window: null, min_tokens: null, max_tokens: null,
})
const abcSampling = reactive<SamplingSettings>({
  temperature: null, top_p: null, top_k: null, repetition_penalty: null, penalty_window: null, min_tokens: null, max_tokens: null,
})

const snapshot = computed<YueGenerationSettings>(() => ({
  engine: 'yue2', referenceImportId: referenceImportId.value, title: songTitle.value, lyrics: lyrics.value, style: style.value, cot: cot.value, precision: precision.value,
  abc: abc.value, cfgScale: cfgScale.value, numInferenceSteps: numInferenceSteps.value,
  bpm: bpm.value, keyScale: keyScale.value || null, timeSignature: timeSignature.value || null,
  semantic: { ...semantic }, abcSampling: { ...abcSampling }, seed: seed.value, randomSeed: randomSeed.value,
  batchSize: batchSize.value, voiceId: selectedVoiceId.value, referenceRequiresReupload: referenceNotice.value,
  arAdapter: arAdapter.value, narAdapter: narAdapter.value,
}))
watch(pendingYueDraft, saved => {
  if (!saved) return
  invalidateScoreEdit()
  lyrics.value = saved.lyrics; songTitle.value = saved.title; style.value = saved.style; cot.value = saved.cot; precision.value = saved.precision
  abc.value = saved.abc; cfgScale.value = saved.cfgScale; numInferenceSteps.value = saved.numInferenceSteps
  restoreMusicalChoices(saved.bpm, saved.keyScale, saved.timeSignature)
  Object.assign(semantic, saved.semantic); Object.assign(abcSampling, saved.abcSampling)
  seed.value = saved.seed ?? 831001; randomSeed.value = saved.randomSeed; batchSize.value = saved.batchSize
  referenceImportId.value = saved.referenceImportId ?? null; referenceNotice.value = saved.referenceRequiresReupload
  arAdapter.value = saved.arAdapter ?? null; narAdapter.value = saved.narAdapter ?? null
  setActiveVoiceId(saved.voiceId); selectedVoiceId.value = saved.voiceId; pendingYueDraft.value = null
}, { immediate: true })
watch(pendingYueReference, edit => {
  if (!edit) return
  if (edit.lyrics !== undefined) lyrics.value = edit.lyrics
  if (edit.abc !== undefined) { invalidateScoreEdit(); abc.value = edit.abc; if (cot.value === 'off') cot.value = 'melody' }
  referenceImportId.value = edit.referenceImportId ?? null; referenceNotice.value = false; pendingYueReference.value = null
}, { immediate: true })

watch(pendingMusicAdapter, selection => {
  if (!selection || selection.engine !== 'yue2') return
  arAdapter.value = selection.pair ? selection.pair.ar : selection.target === 'ar' ? selection.selection : null
  narAdapter.value = selection.pair ? selection.pair.nar : selection.target === 'nar' ? selection.selection : null
  pendingMusicAdapter.value = null
}, { immediate: true })

const submitting = ref(false)
const formError = ref('')
const helpOpen = ref<null | 'style' | 'lyrics' | 'cot' | 'advanced' | 'music'>(null)

const COT_OPTIONS: { value: CotMode; label: string }[] = [
  { value: 'off', label: 'off' },
  { value: 'melody', label: 'melody' },
  { value: 'full', label: 'full' },
]
const COT_HINTS = computed<Record<CotMode, string>>(() => ({
  off: t('yueGen.cotHints.off'),
  melody: t('yueGen.cotHints.melody'),
  full: t('yueGen.cotHints.full'),
}))
const SAMPLING_FIELDS: { key: SamplingKey; label: string; step: string }[] = [
  { key: 'temperature', label: 'Temperature', step: '0.01' },
  { key: 'top_p', label: 'Top-p', step: '0.01' },
  { key: 'top_k', label: 'Top-k', step: '1' },
  { key: 'repetition_penalty', label: 'Repetition penalty', step: '0.01' },
  { key: 'penalty_window', label: 'Penalty window', step: '1' },
  { key: 'min_tokens', label: 'Min tokens', step: '1' },
  { key: 'max_tokens', label: 'Max tokens', step: '1' },
]

watch(
  () => store.pendingAbcInsert,
  (val) => {
    if (!val) return
    invalidateScoreEdit()
    abc.value = val
    if (cot.value === 'off') cot.value = 'melody'
    store.clearPendingAbcInsert()
  },
)

function buildOptions(): GenerateOptions {
  const options: GenerateOptions = { style: style.value.trim(), cot: cot.value }
  if (!externalAbc.value) {
    if (bpm.value !== null) options.bpm = bpm.value
    if (keyScale.value) options.key_scale = keyScale.value
    if (timeSignature.value) options.time_signature = timeSignature.value
  }
  if (cfgScale.value != null) options.cfg_scale = cfgScale.value
  if (numInferenceSteps.value != null) options.num_inference_steps = numInferenceSteps.value
  for (const f of SAMPLING_FIELDS) {
    const v = semantic[f.key]
    if (v != null) options[`semantic_${f.key}`] = v
  }
  if (cot.value !== 'off') {
    if (abc.value.trim()) options.abc = abc.value.trim()
    for (const f of SAMPLING_FIELDS) {
      const v = abcSampling[f.key]
      if (v != null) options[`abc_${f.key}`] = v
    }
  }
  return options
}

function copiedAdapter(value: unknown): MusicAdapterSelection | null {
  try { return parseMusicAdapterSelection(value) } catch { return null }
}

function validTempo(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 20 && value <= 300
}

function restoreMusicalChoices(tempo: unknown, key: unknown, meter: unknown) {
  bpmBadInput.value = false
  bpmInput.value = validTempo(tempo) ? tempo : ''
  keyScale.value = MUSIC_KEYS.find(option => option === key) ?? ''
  timeSignature.value = MUSIC_METERS.find(option => option === meter) ?? ''
}

function validateTempoInput(event: Event) {
  if (event.target instanceof HTMLInputElement) bpmBadInput.value = event.target.validity.badInput
}

function invalidateScoreEdit() {
  scoreRequestId++
  scoreController?.abort()
  scoreController = null
  applyingScore.value = false
  scoreError.value = ''
  scoreApplied.value = false
}

async function applyScoreChoices() {
  if (!externalAbc.value || !musicalChoicesPending.value || invalidBpm.value || applyingScore.value) return
  const requestId = ++scoreRequestId
  const controller = new AbortController()
  scoreController = controller
  applyingScore.value = true
  scoreError.value = ''
  scoreApplied.value = false
  try {
    const result = await transformAbc({ abc: abc.value, tempo_bpm: bpm.value, target_key: keyScale.value || null, time_signature: timeSignature.value || null }, controller.signal)
    if (!formAlive || requestId !== scoreRequestId || controller.signal.aborted) return
    abc.value = result.abc
    restoreMusicalChoices(null, null, null)
    scoreApplied.value = true
  } catch (cause) {
    if (!formAlive || requestId !== scoreRequestId || controller.signal.aborted) return
    const code = cause instanceof ApiError ? cause.message : ''
    scoreError.value = t(code === 'key_mode_mismatch' ? 'yueScore.errors.mode'
      : code === 'unsupported_abc' ? 'yueScore.errors.unsupported'
        : code === 'invalid_abc' ? 'yueScore.errors.invalid' : 'yueScore.errors.failed')
  } finally {
    if (formAlive && requestId === scoreRequestId) { applyingScore.value = false; scoreController = null }
  }
}

async function submit() {
  if (!props.generationAvailable || submitting.value || !arAdapterValid.value || !narAdapterValid.value || invalidBpm.value || applyingScore.value || externalAbc.value && musicalChoicesPending.value) return
  formError.value = ''
  if (!lyrics.value.trim()) {
    formError.value = t('yueGen.enterLyrics')
    return
  }
  if (!style.value.trim()) {
    formError.value = t('yueGen.enterStyle')
    return
  }
  submitting.value = true
  try {
    await store.generateBatch({
      lyrics: lyrics.value.trim(),
      title: songTitle.value.trim(),
      style: style.value.trim(),
      cot: cot.value,
      precision: precision.value,
      baseSeed: Number.isFinite(seed.value) ? seed.value : 831001,
      randomSeed: randomSeed.value,
      batchSize: batchSize.value,
      options: buildOptions(),
      settings: snapshot.value,
      voiceId: selectedVoiceId.value,
      arAdapter: arAdapter.value,
      narAdapter: narAdapter.value,
    })
  } catch (cause) {
    if (formAlive) formError.value = t(cause instanceof ApiError && cause.message === 'music_lora_runtime_unsupported'
      ? 'musicLora.errors.runtime' : cause instanceof TypeError ? 'generationWorkspace.invalid' : 'generationWorkspace.submitFailed')
  } finally {
    if (formAlive) submitting.value = false
  }
}
</script>

<template>
  <div class="lg:sticky lg:top-20 lg:self-start">
    <div class="space-y-4 rounded-xl border border-border bg-panel p-4">
      <VoiceSelect link @select="selectedVoiceId = $event" />
      <button type="button" class="accent-gradient w-full rounded-lg py-2.5 text-sm font-semibold text-white disabled:opacity-50" :disabled="submitting || !generationAvailable || !arAdapterValid || !narAdapterValid || invalidBpm || applyingScore || externalAbc && musicalChoicesPending" @click="submit">
        {{ submitting ? t('yueGen.submitting') : t('yueGen.submit') }}
      </button>
      <p v-if="formError" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ formError }}</p>

      <GenerationLibraryButton :settings="snapshot" />
      <ReferencePreparationButton engine="yue2" />
      <p v-if="referenceNotice" class="text-xs text-status-queued">{{ t('generationWorkspace.reupload') }}</p>

      <div class="space-y-1.5">
        <div class="flex items-center justify-between">
          <label for="yue-lyrics" class="text-sm font-medium text-text">{{ t('aceGen.lyricsLabel') }}</label>
          <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'lyrics'">{{ t('common.help') }}</button>
        </div>
        <textarea id="yue-lyrics" v-model="lyrics" rows="6" class="w-full rounded-lg border border-border bg-panel-2 p-2.5 font-mono text-sm text-text" :placeholder="t('aceGen.lyricsPlaceholder')"></textarea>
      </div>

      <div class="space-y-1.5">
        <label class="text-sm font-medium text-text" for="yue-song-title">{{ t('aceGen.songTitle') }}</label>
        <input id="yue-song-title" v-model="songTitle" maxlength="500" type="text" class="w-full rounded-lg border border-border bg-panel-2 p-2.5 text-sm text-text" :placeholder="t('aceGen.songTitlePlaceholder')" :aria-label="t('aceGen.songTitle')">
        <p class="text-xs text-text-dim">{{ t('aceGen.songTitleHint') }}</p>
      </div>

      <div class="space-y-1.5">
        <div class="flex items-center justify-between">
          <label class="text-sm font-medium text-text">{{ t('aceGen.styleLabel') }}</label>
          <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'style'">{{ t('common.help') }}</button>
        </div>
        <TagInput v-model="style" :placeholder="t('aceGen.stylePlaceholder')" />
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="mb-1 flex items-center justify-between">
            <label for="yue-cot" class="text-xs text-text-dim">{{ t('yueGen.cotModeLabel') }}</label>
            <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'cot'">?</button>
          </div>
          <select id="yue-cot" v-model="cot" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
            <option v-for="o in COT_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </div>
        <div>
          <label for="yue-precision" class="mb-1 block text-xs text-text-dim">{{ t('yueGen.precisionLabel') }}</label>
          <select id="yue-precision" v-model="precision" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
            <option value="q8_0">q8_0</option>
            <option value="q4_0">q4_0</option>
          </select>
        </div>
      </div>
      <p class="text-xs text-text-dim">{{ COT_HINTS[cot] }}</p>

      <fieldset class="space-y-3 rounded-lg border border-border bg-panel-2/50 p-3" :aria-busy="applyingScore">
        <legend class="px-1 text-sm font-medium text-text">{{ t('yueScore.title') }}</legend>
        <button type="button" class="min-h-9 text-xs text-accent1 hover:underline focus-visible:outline-2 focus-visible:outline-accent1" @click="helpOpen = 'music'">{{ t('yueScore.helpLabel') }}</button>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label for="yue-bpm" class="mb-1 block text-xs text-text-dim">{{ t('yueScore.tempo') }}</label>
            <input id="yue-bpm" v-model="bpmInput" type="number" min="20" max="300" step="1" :aria-invalid="invalidBpm" :aria-describedby="invalidBpm ? 'yue-bpm-error' : 'yue-score-hint'" :placeholder="t('yueScore.keepAuto')" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" @input="validateTempoInput" />
            <p v-if="invalidBpm" id="yue-bpm-error" role="alert" class="mt-1 text-xs text-status-failed">{{ t('yueScore.errors.tempo') }}</p>
          </div>
          <div>
            <label for="yue-key-scale" class="mb-1 block text-xs text-text-dim">{{ t('yueScore.keyScale') }}</label>
            <select id="yue-key-scale" v-model="keyScale" aria-describedby="yue-score-hint" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
              <option value="">{{ t('yueScore.keepAuto') }}</option>
              <option v-for="key in MUSIC_KEYS" :key="key" :value="key">{{ t(key.endsWith('m') ? 'yueScore.minor' : 'yueScore.major', { key: key.endsWith('m') ? key.slice(0, -1) : key }) }}</option>
            </select>
          </div>
          <div>
            <label for="yue-time-signature" class="mb-1 block text-xs text-text-dim">{{ t('yueScore.meter') }}</label>
            <select id="yue-time-signature" v-model="timeSignature" aria-describedby="yue-score-hint" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
              <option value="">{{ t('yueScore.keepAuto') }}</option>
              <option v-for="meter in MUSIC_METERS" :key="meter" :value="meter">{{ meter }}</option>
            </select>
          </div>
        </div>
        <p id="yue-score-hint" class="text-xs text-text-dim">{{ t(externalAbc ? 'yueScore.scoreHint' : 'yueScore.guidanceHint') }}</p>
        <template v-if="externalAbc">
          <button type="button" :disabled="!musicalChoicesPending || invalidBpm || applyingScore" class="rounded-lg border border-border px-3 py-2 text-sm text-text hover:bg-panel-2 disabled:opacity-50" @click="applyScoreChoices">{{ t(applyingScore ? 'yueScore.applying' : 'yueScore.apply') }}</button>
          <p v-if="musicalChoicesPending" class="text-xs text-status-queued">{{ t('yueScore.pending') }}</p>
        </template>
        <p v-if="scoreError" role="alert" class="text-xs text-status-failed">{{ scoreError }}</p>
        <p v-if="scoreApplied" role="status" class="text-xs text-text-dim">{{ t('yueScore.applied') }}</p>
      </fieldset>

      <div v-if="cot !== 'off'" class="space-y-3 rounded-lg border border-border bg-panel-2/50 p-3">
        <div>
          <label for="yue-abc" class="text-xs text-text-dim">{{ t('yueGen.abcScore') }}</label>
          <textarea id="yue-abc" v-model="abc" rows="6" maxlength="100000" class="w-full rounded-lg border border-border bg-panel-2 p-2.5 font-mono text-xs text-text"></textarea>
        </div>

      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label for="yue-seed" class="text-xs text-text-dim">{{ t('aceGen.seed') }}</label>
          <input id="yue-seed" v-model.number="seed" type="number" :disabled="randomSeed" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text disabled:opacity-40" />
        </div>
        <label class="flex items-end gap-2 pb-2 text-xs text-text-dim">
          <input v-model="randomSeed" type="checkbox" class="rounded border-border" />
          {{ t('yueGen.randomSeed') }}
        </label>
      </div>

      <div>
        <label class="mb-1 block text-xs text-text-dim">{{ t('aceGen.variantCount') }}</label>
        <ChipGroup v-model="batchSize" :options="[{ value: 1, label: '1' }, { value: 2, label: '2' }, { value: 3, label: '3' }, { value: 4, label: '4' }]" />
      </div>

      <CollapsibleDetails :summary="t('aceGen.advancedSettings')">
        <div class="space-y-3">
          <p class="text-xs text-text-dim">{{ t('musicLora.yueHint') }}</p>
          <fieldset class="space-y-2">
            <legend class="text-xs text-text-dim">{{ t('musicLora.arAdapter') }}</legend>
            <MusicAdapterSelect v-model="arAdapter" engine="yue2" target="ar" :disabled="submitting || !generationAvailable" @validity="arAdapterValid = $event" />
          </fieldset>
          <fieldset class="space-y-2">
            <legend class="text-xs text-text-dim">{{ t('musicLora.narAdapter') }}</legend>
            <MusicAdapterSelect v-model="narAdapter" engine="yue2" target="nar" :disabled="submitting || !generationAvailable" @validity="narAdapterValid = $event" />
          </fieldset>
        </div>
        <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'advanced'">{{ t('aceGen.advancedWhatMeans') }}</button>
        <div class="grid grid-cols-2 gap-2">
          <div>
            <label for="yue-cfg-scale" class="text-xs text-text-dim">{{ t('yueGen.cfgScale') }}</label>
            <input id="yue-cfg-scale" v-model.number="cfgScale" type="number" step="0.1" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('yueGen.defaultPlaceholder')" />
          </div>
          <div>
            <label for="yue-inference-steps" class="text-xs text-text-dim">{{ t('aceGen.inferenceSteps') }}</label>
            <input id="yue-inference-steps" v-model.number="numInferenceSteps" type="number" min="1" max="256" :aria-label="t('aceGen.inferenceSteps')" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('yueGen.defaultPlaceholder')" />
          </div>
        </div>

        <p class="text-xs text-text-dim">{{ t('generationWorkspace.stepsHint') }}</p>
        <CollapsibleDetails :summary="t('yueGen.semanticSampling')">
          <div class="grid grid-cols-2 gap-2">
            <div v-for="f in SAMPLING_FIELDS" :key="'sem_' + f.key">
              <label :for="`yue-semantic-${f.key}`" class="text-xs text-text-dim">{{ f.label }}</label>
              <input :id="`yue-semantic-${f.key}`" v-model.number="semantic[f.key]" type="number" :step="f.step" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('yueGen.defaultPlaceholder')" />
            </div>
          </div>
        </CollapsibleDetails>

        <CollapsibleDetails v-if="cot !== 'off'" :summary="t('yueGen.abcPlannerSampling')">
          <div class="grid grid-cols-2 gap-2">
            <div v-for="f in SAMPLING_FIELDS" :key="'abc_' + f.key">
              <label :for="`yue-abc-${f.key}`" class="text-xs text-text-dim">{{ f.label }}</label>
              <input :id="`yue-abc-${f.key}`" v-model.number="abcSampling[f.key]" type="number" :step="f.step" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('yueGen.defaultPlaceholder')" />
            </div>
          </div>
        </CollapsibleDetails>
      </CollapsibleDetails>
    </div>

    <HelpModal :open="helpOpen === 'music'" :title="t('yueScore.title')" @close="helpOpen = null">
      <p>{{ t('yueScore.help.guidance') }}</p>
      <p><strong class="text-text">{{ t('yueScore.tempo') }}</strong> {{ t('yueScore.help.tempo') }}</p>
      <p><strong class="text-text">{{ t('yueScore.keyScale') }}</strong> {{ t('yueScore.help.key') }}</p>
      <p><strong class="text-text">{{ t('yueScore.meter') }}</strong> {{ t('yueScore.help.meter') }}</p>
      <p>{{ t('yueScore.help.apply') }}</p>
    </HelpModal>
    <HelpModal :open="helpOpen === 'style'" :title="t('yueGen.help.style.title')" @close="helpOpen = null">
      <p>{{ t('yueGen.help.style.intro') }}</p>
      <table class="w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-border text-left text-text">
            <th class="py-1 pr-2">{{ t('yueGen.help.style.dimHeader') }}</th>
            <th class="py-1">{{ t('yueGen.help.style.examplesHeader') }}</th>
          </tr>
        </thead>
        <tbody class="align-top">
          <tr v-for="row in (tm('yueGen.help.style.rows') as { dim: string; examples: string }[])" :key="row.dim" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-medium text-text">{{ row.dim }}</td>
            <td class="py-1.5">{{ row.examples }}</td>
          </tr>
        </tbody>
      </table>
      <p class="font-medium text-text">{{ t('yueGen.help.style.tipsTitle') }}</p>
      <ul class="list-disc space-y-1 pl-4">
        <li v-for="tip in (tm('yueGen.help.style.tips') as string[])" :key="tip">{{ tip }}</li>
      </ul>
      <p class="font-medium text-text">{{ t('yueGen.help.style.examplesTitle') }}</p>
      <p v-for="ex in (tm('yueGen.help.style.examples') as string[])" :key="ex" class="rounded bg-panel-2 p-2 font-mono text-xs">{{ ex }}</p>
    </HelpModal>
    <HelpModal :open="helpOpen === 'lyrics'" :title="t('yueGen.help.lyrics.title')" @close="helpOpen = null">
      <p>{{ t('yueGen.help.lyrics.intro') }}</p>
      <table class="w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-border text-left text-text">
            <th class="py-1 pr-2">{{ t('yueGen.help.lyrics.tagHeader') }}</th>
            <th class="py-1">{{ t('yueGen.help.lyrics.purposeHeader') }}</th>
          </tr>
        </thead>
        <tbody class="align-top">
          <tr v-for="row in (tm('yueGen.help.lyrics.rows') as { tag: string; purpose: string }[])" :key="row.tag" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-mono text-text">{{ row.tag }}</td>
            <td class="py-1.5">{{ row.purpose }}</td>
          </tr>
        </tbody>
      </table>
      <p class="font-medium text-text">{{ t('yueGen.help.lyrics.tipsTitle') }}</p>
      <ul class="list-disc space-y-1 pl-4">
        <li v-for="tip in (tm('yueGen.help.lyrics.tips') as string[])" :key="tip">{{ tip }}</li>
      </ul>
      <p class="font-medium text-text">{{ t('yueGen.help.lyrics.exampleTitle') }}</p>
      <p class="rounded bg-panel-2 p-2 font-mono text-xs whitespace-pre-line">{{ t('yueGen.help.lyrics.example') }}</p>
    </HelpModal>
    <HelpModal :open="helpOpen === 'cot'" :title="t('yueGen.help.cot.title')" @close="helpOpen = null">
      <p>{{ t('yueGen.help.cot.intro') }}</p>
      <table class="w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-border text-left text-text">
            <th class="py-1 pr-2">{{ t('yueGen.help.cot.modeHeader') }}</th>
            <th class="py-1">{{ t('yueGen.help.cot.whenHeader') }}</th>
          </tr>
        </thead>
        <tbody class="align-top">
          <tr class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-mono text-text">off</td>
            <td class="py-1.5">{{ COT_HINTS.off }} {{ t('yueGen.help.cot.offNote') }}</td>
          </tr>
          <tr class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-mono text-text">melody</td>
            <td class="py-1.5">{{ COT_HINTS.melody }} {{ t('yueGen.help.cot.melodyNote') }}</td>
          </tr>
          <tr>
            <td class="py-1.5 pr-2 font-mono text-text">full</td>
            <td class="py-1.5">{{ COT_HINTS.full }} {{ t('yueGen.help.cot.fullNote') }}</td>
          </tr>
        </tbody>
      </table>
      <p class="text-xs text-text-dim">{{ t('yueGen.help.cot.precisionNote') }}</p>

      <p class="font-medium text-text">{{ t('yueGen.help.cot.abcTitle') }}</p>
      <p>{{ t('yueGen.help.cot.abcIntro') }}</p>
      <table class="w-full border-collapse text-xs">
        <tbody class="align-top">
          <tr v-for="row in (tm('yueGen.help.cot.abcRows') as { tag: string; purpose: string }[])" :key="row.tag" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-mono text-text">{{ row.tag }}</td>
            <td class="py-1.5">{{ row.purpose }}</td>
          </tr>
        </tbody>
      </table>
      <p class="text-xs text-text-dim">{{ t('yueGen.help.cot.abcFooter') }}</p>
    </HelpModal>
    <HelpModal :open="helpOpen === 'advanced'" :title="t('yueGen.help.advanced.title')" @close="helpOpen = null">
      <table class="w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-border text-left text-text">
            <th class="py-1 pr-2">{{ t('yueGen.help.advanced.paramHeader') }}</th>
            <th class="py-1">{{ t('yueGen.help.advanced.whatHeader') }}</th>
          </tr>
        </thead>
        <tbody class="align-top">
          <tr v-for="row in (tm('yueGen.help.advanced.rows') as { param: string; what: string }[])" :key="row.param" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-medium text-text">{{ row.param }}</td>
            <td class="py-1.5">{{ row.what }}</td>
          </tr>
        </tbody>
      </table>
      <p class="font-medium text-text">{{ t('yueGen.help.advanced.samplingTitle') }}</p>
      <table class="w-full border-collapse text-xs">
        <tbody class="align-top">
          <tr v-for="row in (tm('yueGen.help.advanced.samplingRows') as { tag: string; purpose: string }[])" :key="row.tag" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-mono text-text">{{ row.tag }}</td>
            <td class="py-1.5">{{ row.purpose }}</td>
          </tr>
        </tbody>
      </table>
      <p class="text-xs text-text-dim">{{ t('yueGen.help.advanced.footer') }}</p>
    </HelpModal>
  </div>
</template>

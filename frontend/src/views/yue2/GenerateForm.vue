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
import GenerationLibraryButton from '../../components/shared/GenerationLibraryButton.vue'
import ReferencePreparationButton from '../../components/shared/ReferencePreparationButton.vue'
import { setActiveVoiceId } from '../../api/voices'
import type { YueGenerationSettings } from '../../api/contracts'
import { pendingYueDraft, pendingYueReference } from '../../composables/generationDrafts'
import { samplingSettings, finiteNumber, SAMPLING_KEYS, type SamplingKey, type SamplingSettings } from '../../composables/generationPresets'

const props = withDefaults(defineProps<{ generationAvailable?: boolean }>(), { generationAvailable: true })
const store = useYue2Store()
const selectedVoiceId = ref<string | null>(null)
const referenceNotice = ref(false)
const { t, tm } = useI18n()

watch(
  () => store.pendingParamsInsert,
  (params) => {
    if (!params) return
    referenceImportId.value = null; referenceNotice.value = false
    lyrics.value = typeof params.lyrics === 'string' ? params.lyrics : ''
    style.value = typeof params.style === 'string' ? params.style : ''
    songTitle.value = typeof params.song_title === 'string' ? params.song_title : ''
    cot.value = params.cot === 'melody' || params.cot === 'full' ? params.cot : 'off'
    precision.value = params.precision === 'q4_0' ? params.precision : 'q8_0'
    seed.value = finiteNumber(params.seed) ? params.seed : 831001
    randomSeed.value = params.random_seed === true
    abc.value = typeof params.abc === 'string' ? params.abc : ''
    cfgScale.value = finiteNumber(params.cfg_scale) ? params.cfg_scale : null
    numInferenceSteps.value = finiteNumber(params.num_inference_steps) ? params.num_inference_steps : params.num_inference_steps === null ? null : 8
    Object.assign(semantic, samplingSettings(params.semantic))
    Object.assign(abcSampling, samplingSettings(params.abc_sampling))
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

const referenceImportId = ref<string | null>(null)
let formAlive = true
onBeforeUnmount(() => { formAlive = false })

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
  semantic: { ...semantic }, abcSampling: { ...abcSampling }, seed: seed.value, randomSeed: randomSeed.value,
  batchSize: batchSize.value, voiceId: selectedVoiceId.value, referenceRequiresReupload: referenceNotice.value,
}))
watch(pendingYueDraft, saved => {
  if (!saved) return
  lyrics.value = saved.lyrics; songTitle.value = saved.title; style.value = saved.style; cot.value = saved.cot; precision.value = saved.precision
  abc.value = saved.abc; cfgScale.value = saved.cfgScale; numInferenceSteps.value = saved.numInferenceSteps
  Object.assign(semantic, saved.semantic); Object.assign(abcSampling, saved.abcSampling)
  seed.value = saved.seed ?? 831001; randomSeed.value = saved.randomSeed; batchSize.value = saved.batchSize
  referenceImportId.value = saved.referenceImportId ?? null; referenceNotice.value = saved.referenceRequiresReupload
  setActiveVoiceId(saved.voiceId); selectedVoiceId.value = saved.voiceId; pendingYueDraft.value = null
}, { immediate: true })
watch(pendingYueReference, edit => {
  if (!edit) return
  if (edit.lyrics !== undefined) lyrics.value = edit.lyrics
  if (edit.abc !== undefined) { abc.value = edit.abc; if (cot.value === 'off') cot.value = 'melody' }
  referenceImportId.value = edit.referenceImportId ?? null; referenceNotice.value = false; pendingYueReference.value = null
}, { immediate: true })

const submitting = ref(false)
const formError = ref('')
const helpOpen = ref<null | 'style' | 'lyrics' | 'cot' | 'advanced'>(null)

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
    abc.value = val
    if (cot.value === 'off') cot.value = 'melody'
    store.clearPendingAbcInsert()
  },
)

function buildOptions(): GenerateOptions {
  const options: GenerateOptions = { style: style.value.trim(), cot: cot.value }
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

async function submit() {
  if (!props.generationAvailable || submitting.value) return
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
    })
  } catch (cause) {
    if (formAlive) formError.value = t(cause instanceof TypeError ? 'generationWorkspace.invalid' : 'generationWorkspace.submitFailed')
  } finally {
    if (formAlive) submitting.value = false
  }
}
</script>

<template>
  <div class="lg:sticky lg:top-20 lg:self-start">
    <div class="space-y-4 rounded-xl border border-border bg-panel p-4">
      <VoiceSelect link @select="selectedVoiceId = $event" />
      <button type="button" class="accent-gradient w-full rounded-lg py-2.5 text-sm font-semibold text-white disabled:opacity-50" :disabled="submitting || !generationAvailable" @click="submit">
        {{ submitting ? t('yueGen.submitting') : t('yueGen.submit') }}
      </button>
      <p v-if="formError" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ formError }}</p>

      <GenerationLibraryButton :settings="snapshot" />
      <ReferencePreparationButton engine="yue2" />
      <p v-if="referenceNotice" class="text-xs text-status-queued">{{ t('generationWorkspace.reupload') }}</p>

      <div class="space-y-1.5">
        <div class="flex items-center justify-between">
          <label class="text-sm font-medium text-text">{{ t('aceGen.lyricsLabel') }}</label>
          <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'lyrics'">{{ t('common.help') }}</button>
        </div>
        <textarea v-model="lyrics" rows="6" class="w-full rounded-lg border border-border bg-panel-2 p-2.5 font-mono text-sm text-text" :placeholder="t('aceGen.lyricsPlaceholder')"></textarea>
      </div>

      <div class="space-y-1.5">
        <label class="text-sm font-medium text-text" for="yue-song-title">{{ t('yueGen.songTitle') }}</label>
        <input id="yue-song-title" v-model="songTitle" maxlength="500" type="text" class="w-full rounded-lg border border-border bg-panel-2 p-2.5 text-sm text-text" :placeholder="t('yueGen.songTitlePlaceholder')" :aria-label="t('yueGen.songTitle')">
        <p class="text-xs text-text-dim">{{ t('yueGen.songTitleHint') }}</p>
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
            <label class="text-xs text-text-dim">{{ t('yueGen.cotModeLabel') }}</label>
            <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'cot'">?</button>
          </div>
          <select v-model="cot" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
            <option v-for="o in COT_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </div>
        <div>
          <label class="mb-1 block text-xs text-text-dim">{{ t('yueGen.precisionLabel') }}</label>
          <select v-model="precision" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
            <option value="q8_0">q8_0</option>
            <option value="q4_0">q4_0</option>
          </select>
        </div>
      </div>
      <p class="text-xs text-text-dim">{{ COT_HINTS[cot] }}</p>

      <div v-if="cot !== 'off'" class="space-y-3 rounded-lg border border-border bg-panel-2/50 p-3">
        <div>
          <label class="text-xs text-text-dim">{{ t('yueGen.abcScore') }}</label>
          <textarea v-model="abc" rows="6" class="w-full rounded-lg border border-border bg-panel-2 p-2.5 font-mono text-xs text-text"></textarea>
        </div>

      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="text-xs text-text-dim">{{ t('aceGen.seed') }}</label>
          <input v-model.number="seed" type="number" :disabled="randomSeed" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text disabled:opacity-40" />
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
        <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'advanced'">{{ t('aceGen.advancedWhatMeans') }}</button>
        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="text-xs text-text-dim">{{ t('yueGen.cfgScale') }}</label>
            <input v-model.number="cfgScale" type="number" step="0.1" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('yueGen.defaultPlaceholder')" />
          </div>
          <div>
            <label class="text-xs text-text-dim">{{ t('aceGen.inferenceSteps') }}</label>
            <input v-model.number="numInferenceSteps" type="number" min="1" max="256" aria-label="YuE inference steps" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('yueGen.defaultPlaceholder')" />
          </div>
        </div>

        <p class="text-xs text-text-dim">{{ t('generationWorkspace.stepsHint') }}</p>
        <CollapsibleDetails :summary="t('yueGen.semanticSampling')">
          <div class="grid grid-cols-2 gap-2">
            <div v-for="f in SAMPLING_FIELDS" :key="'sem_' + f.key">
              <label class="text-xs text-text-dim">{{ f.label }}</label>
              <input v-model.number="semantic[f.key]" type="number" :step="f.step" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('yueGen.defaultPlaceholder')" />
            </div>
          </div>
        </CollapsibleDetails>

        <CollapsibleDetails v-if="cot !== 'off'" :summary="t('yueGen.abcPlannerSampling')">
          <div class="grid grid-cols-2 gap-2">
            <div v-for="f in SAMPLING_FIELDS" :key="'abc_' + f.key">
              <label class="text-xs text-text-dim">{{ f.label }}</label>
              <input v-model.number="abcSampling[f.key]" type="number" :step="f.step" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('yueGen.defaultPlaceholder')" />
            </div>
          </div>
        </CollapsibleDetails>
      </CollapsibleDetails>
    </div>

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

import { computed, onBeforeUnmount, onMounted, ref, watch, type UnwrapNestedRefs } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import * as api from '../../api/voices'
import type { VoiceProfile } from '../../api/voices'
import type { BuildVoiceRequest, PrepareVoiceRequest, VoicePreparationResponse, VoiceSeparationOption, VoiceSourceSelection } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'
import { useVoiceSession } from './useVoiceSession'
import { isSelectableVoiceSample } from './voiceWorkspace'

export function useVoicePreparation(voice: () => VoiceProfile, disabled: () => boolean, callbacks: { profile: (voice: VoiceProfile) => void; preparation: (response: VoicePreparationResponse) => void }) {
  const { t, te } = useI18n()
  const capture = useVoiceSession(() => voice().id)
  const preparation = ref<VoicePreparationResponse | null>(null)
  const sources = ref<Required<VoiceSourceSelection>[]>([])
  const quality = ref<NonNullable<PrepareVoiceRequest['separation_quality']>>('fast')
  const separationOptions = ref<VoiceSeparationOption[]>([])
  const clean = ref(false)
  const durationMinutes = ref(15)
  const singerConfirmed = ref(false)
  const selectedIds = ref<string[]>([])
  const cleanedIds = ref<string[]>([])
  const referenceId = ref('')
  const mode = ref<'0' | '200' | '500' | '1000' | 'compare'>('200')
  const resume = ref(false)
  const action = ref('')
  const error = ref('')
  const loading = ref(true)
  let actionToken = 0

  const preparing = computed(() => preparation.value?.status === 'queued' || preparation.value?.status === 'running')
  const building = computed(() => api.isVoiceActive(voice().status))
  const busy = computed(() => disabled() || preparing.value || building.value || !!action.value)
  const segments = computed(() => preparation.value?.segments ?? [])
  const references = computed(() => preparation.value?.references ?? [])
  const models = computed(() => voice().models ?? [])
  const canResume = computed(() => mode.value !== '0' && mode.value !== 'compare' && models.value.some((model) => model.id === voice().active_model_id && model.resume_available && model.steps < Number(mode.value)))
  const selectedSeconds = computed(() => segments.value.filter((segment) => selectedIds.value.includes(segment.id)).reduce((sum, segment) => sum + segment.duration_sec, 0))
  const budgetValid = computed(() => Number.isFinite(durationMinutes.value) && durationMinutes.value >= 1 && durationMinutes.value <= 60)
  const budgetDirty = computed(() => durationMinutes.value * 60 !== (preparation.value?.options?.max_selected_seconds ?? 900))
  const sameIds = (left: string[], right: string[]) => left.length === right.length && left.every((id) => right.includes(id))
  const selectionDirty = computed(() => budgetDirty.value || !sameIds(selectedIds.value, preparation.value?.selected_segment_ids ?? []) || !sameIds(cleanedIds.value, preparation.value?.cleaned_segment_ids ?? []) || referenceId.value !== (preparation.value?.reference_id ?? ''))
  const optionsDirty = computed(() => {
    const options = preparation.value?.options
    if (!options) return true
    const saved = options.sources ?? []
    return clean.value !== (options.clean ?? false) || quality.value !== (options.separation_quality ?? 'fast') || singerConfirmed.value !== (options.singer_confirmed ?? false) || sources.value.length !== saved.length || sources.value.some((source) => !saved.some((item) => item.filename === source.filename && (item.enabled ?? true) === source.enabled && (item.kind ?? 'song') === source.kind))
  })
  const withinBudget = computed(() => budgetValid.value && selectedSeconds.value <= durationMinutes.value * 60 + 1e-6)
  const validSelection = computed(() => withinBudget.value && selectedIds.value.length > 0 && selectedIds.value.length <= 1000 && selectedIds.value.every((id) => segments.value.some((segment) => segment.id === id && isSelectableVoiceSample(segment))))
  const canBuild = computed(() => !busy.value && !loading.value && preparation.value?.status === 'done' && !!preparation.value.revision && validSelection.value && !!referenceId.value && !!preparation.value.options?.singer_confirmed && !selectionDirty.value && !optionsDirty.value)
  const needsSeparation = computed(() => sources.value.some((source) => source.enabled && source.kind === 'song'))
  const canPrepare = computed(() => !busy.value && budgetValid.value && singerConfirmed.value && sources.value.some((source) => source.enabled) && (!needsSeparation.value || separationOptions.value.some((option) => option.id === quality.value && option.available)))
  const canAnalyzeCoverage = computed(() => !busy.value && !loading.value && !!preparation.value?.revision && validSelection.value && !!referenceId.value && !selectionDirty.value && !optionsDirty.value && (preparation.value.status === 'done' || preparation.value.operation === 'coverage' && preparation.value.error_code !== 'source_changed'))

  function message(code: string, category: 'reason' | 'warning') {
    const key = `voiceClone.review.${category}.${code}`
    return te(key) ? t(key) : t('voiceClone.review.unknownReason', { code })
  }
  function errorText(cause: unknown) { return api.voiceErrorText(cause instanceof ApiError ? cause.message : 'unknown') }
  function apply(response: VoicePreparationResponse, reset = false) {
    const completed = preparing.value && response.status === 'done'
    const changed = reset || completed || preparation.value?.revision !== response.revision
    preparation.value = response
    if (changed) {
      const savedSources = response.options?.sources ?? []
      sources.value = voice().recordings.map((recording) => {
        const saved = savedSources.find((item) => item.filename === recording.filename)
        return { filename: recording.filename, enabled: saved?.enabled ?? true, kind: saved?.kind ?? 'song' }
      })
      quality.value = response.options?.separation_quality ?? 'fast'
      clean.value = response.options?.clean ?? false
      durationMinutes.value = (response.options?.max_selected_seconds ?? 900) / 60
      singerConfirmed.value = !!response.options?.singer_confirmed && !sources.value.some((source) => source.enabled && !savedSources.some((saved) => saved.filename === source.filename))
      selectedIds.value = [...response.selected_segment_ids ?? []]
      cleanedIds.value = [...response.cleaned_segment_ids ?? []]
      referenceId.value = response.reference_id ?? ''
    }
    callbacks.preparation(response)
  }
  const poll = createPollingLoop(async (context) => {
    const session = capture()
    try {
      const response = await api.getVoicePreparation(session.id, context.signal)
      if (!context.isCurrent() || !session.isCurrent()) return false
      apply(response)
      error.value = ''
    } catch (cause) {
      if (!context.isCurrent() || !session.isCurrent()) return false
      error.value = errorText(cause)
    } finally {
      if (context.isCurrent() && session.isCurrent()) loading.value = false
    }
    return preparing.value
  }, 2000)

  async function run(name: string, request: (session: ReturnType<typeof capture>) => Promise<void>) {
    if (action.value) return
    poll.stop()
    const token = ++actionToken
    const session = capture()
    action.value = name
    error.value = ''
    try { await request(session) } catch (cause) {
      if (session.isCurrent() && token === actionToken) error.value = errorText(cause)
    } finally {
      if (session.isCurrent() && token === actionToken) {
        action.value = ''
        if (preparing.value) poll.start(false)
      }
    }
  }
  async function prepare() {
    if (!canPrepare.value) return
    await run('prepare', async (session) => {
      const response = await api.prepareVoice(session.id, { sources: sources.value.map((source) => ({ ...source })), separation_quality: quality.value, clean: clean.value, singer_confirmed: singerConfirmed.value, max_selected_seconds: durationMinutes.value * 60 }, session.signal)
      if (session.isCurrent()) apply(response)
    })
  }
  async function cancelPreparation() {
    await run('cancel', async (session) => {
      const response = await api.cancelVoicePreparation(session.id, session.signal)
      if (session.isCurrent()) apply(response)
    })
  }
  async function reload() {
    await run('reload', async (session) => {
      const [response, options] = await Promise.all([api.getVoicePreparation(session.id, session.signal), api.voiceSeparationOptions(session.signal)])
      if (!session.isCurrent()) return
      separationOptions.value = options
      apply(response, true)
      loading.value = false
    })
  }
  async function saveSelection() {
    const revision = preparation.value?.revision
    if (!revision || !referenceId.value || !validSelection.value) return
    await run('selection', async (session) => {
      const response = await api.selectVoiceSamples(session.id, { revision, segment_ids: [...selectedIds.value], reference_id: referenceId.value, cleaned_segment_ids: [...cleanedIds.value], max_selected_seconds: durationMinutes.value * 60 }, session.signal)
      if (session.isCurrent()) apply(response)
    })
  }
  async function build() {
    if (!canBuild.value) return
    const request: BuildVoiceRequest = { preparation_revision: preparation.value?.revision, training_steps: mode.value === '0' ? 0 : mode.value === '500' ? 500 : mode.value === '1000' || mode.value === 'compare' ? 1000 : 200, resume: resume.value && canResume.value, compare_checkpoints: mode.value === 'compare' }
    await run('build', async (session) => {
      const response = await api.buildVoice(session.id, request, session.signal)
      if (session.isCurrent()) callbacks.profile(response)
    })
  }
  async function analyzeCoverage() {
    const revision = preparation.value?.revision
    if (!revision || !canAnalyzeCoverage.value) return
    await run('coverage', async (session) => {
      const response = await api.analyzeVoiceCoverage(session.id, revision, session.signal)
      if (session.isCurrent()) apply(response)
    })
  }
  async function cancelBuild() {
    await run('cancel-build', async (session) => {
      const response = await api.cancelVoiceBuild(session.id, session.signal)
      if (session.isCurrent()) callbacks.profile(response)
    })
  }
  async function chooseModel(event: Event) {
    if (!(event.target instanceof HTMLSelectElement)) return
    const id = event.target.value
    await run('model', async (session) => {
      const response = await api.selectVoiceModel(session.id, id, session.signal)
      if (session.isCurrent()) callbacks.profile(response)
    })
  }
  watch(selectedIds, () => {
    cleanedIds.value = cleanedIds.value.filter((id) => selectedIds.value.includes(id) && segments.value.some((segment) => segment.id === id && segment.has_cleaned))
    if (!references.value.some((reference) => reference.id === referenceId.value && selectedIds.value.includes(reference.segment_id))) referenceId.value = ''
  })
  watch(canResume, (available) => { if (!available) resume.value = false })
  watch(() => voice().recordings, (recordings) => {
    const previous = sources.value
    sources.value = recordings.map((recording) => previous.find((source) => source.filename === recording.filename) ?? { filename: recording.filename, enabled: true, kind: 'song' })
  }, { immediate: true })
  watch(() => voice().id, () => {
    actionToken++
    poll.stop()
    preparation.value = null
    selectedIds.value = []; cleanedIds.value = []; referenceId.value = ''; action.value = ''; error.value = ''; loading.value = true
    singerConfirmed.value = false
    poll.start()
  })
  onMounted(() => {
    poll.start()
    const session = capture()
    const token = actionToken
    void api.voiceSeparationOptions(session.signal).then((options) => { if (session.isCurrent() && token === actionToken) separationOptions.value = options }).catch((cause: unknown) => { if (session.isCurrent() && token === actionToken) error.value = errorText(cause) })
  })
  onBeforeUnmount(() => { actionToken++; poll.stop() })

  return { preparation, sources, quality, separationOptions, clean, durationMinutes, singerConfirmed, selectedIds, cleanedIds, referenceId, mode, resume, action, error, loading, preparing, building, busy, segments, references, models, canResume, selectedSeconds, selectionDirty, optionsDirty, validSelection, budgetDirty, budgetValid, withinBudget, canBuild, needsSeparation, canPrepare, canAnalyzeCoverage, message, prepare, cancelPreparation, reload, saveSelection, build, analyzeCoverage, cancelBuild, chooseModel }
}

export type VoicePreparationState = UnwrapNestedRefs<ReturnType<typeof useVoicePreparation>>

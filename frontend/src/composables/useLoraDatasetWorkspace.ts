import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../api/loraDataset'
import { ApiError } from '../api/http'
import type { LoraDatasetEntry, LoraDatasetIssue, LoraDatasetOperationResponse, LoraWorkspace, LoraWorkspaceForm, UpdateLoraDatasetSampleRequest } from '../api/generated'
import type { useLoraTrainingStore } from '../stores/loraTraining'
import { parseDatasetSample } from '../api/aceStepTraining'

export function useLoraDatasetWorkspace(store: ReturnType<typeof useLoraTrainingStore>, form: Required<LoraWorkspaceForm>, running: Readonly<Ref<boolean>>) {
  const { t } = useI18n()
  const datasets = ref<LoraDatasetEntry[]>([]), selectedId = ref<string | null>(null)
  const busy = ref(false), ready = ref(false), error = ref(''), issues = ref<LoraDatasetIssue[]>([]), saved = ref(false), notice = ref('')
  const selected = computed(() => datasets.value.find(item => item.id === selectedId.value && !item.trashed))
  const disabled = computed(() => busy.value || !ready.value || (running.value && store.nativeBusy))
  let workspace: LoraWorkspace = { revision: 0, form: {} }, alive = true, generation = 0
  let timer: ReturnType<typeof setTimeout> | undefined, saving: Promise<boolean> = Promise.resolve(true)
  const controller = new AbortController()
  function message(cause: unknown): string {
    const code = cause instanceof ApiError ? cause.message : ''
    if (code.includes('revision') || code.includes('conflict')) return t('loraRecovery.conflict')
    if (code.includes('busy') || code.includes('native_unavailable')) return t('loraRecovery.busy')
    if (code.includes('unsafe')) return t('loraRecovery.unsafe')
    if (code.includes('name')) return t('loraRecovery.nameConflict')
    if (code.includes('preprocess') || code.includes('tensor')) return t('loraRecovery.reuseMismatch')
    return t('loraRecovery.libraryError')
  }
  function replace(entry: LoraDatasetEntry) { if (alive) datasets.value = [entry, ...datasets.value.filter(item => item.id !== entry.id)] }
  function apply(response: LoraDatasetOperationResponse, token = generation, restoreMetadata = true) {
    if (!alive || token !== generation) return
    replace(response.dataset)
    selectedId.value = response.dataset.id
    store.samples = response.samples.map(parseDatasetSample)
    store.datasetPath = response.dataset.dataset_path
    form.audio_dir = response.dataset.audio_dir
    if (restoreMetadata && response.metadata) Object.assign(form, response.metadata)
    saved.value = true
    notice.value = response.message === 'dataset_sources_changed' ? t('loraRecovery.sourcesChanged') : response.message === 'dataset_source_history_unavailable' ? t('loraRecovery.sourceHistory') : ''
  }
  async function refresh() {
    const token = generation, previous = datasets.value
    const response = await api.listLoraDatasets(controller.signal)
    if (!alive || token !== generation || datasets.value !== previous) return
    datasets.value = response.datasets; issues.value = response.issues ?? []
  }
  async function syncWorkspace() {
    const token = generation
    if (!alive) return
    await saving
    if (!alive || token !== generation) return
    try {
      const current = await api.getLoraWorkspace(controller.signal)
      if (alive && token === generation) workspace = current
    } catch (cause) { if (alive && token === generation) error.value = message(cause) }
  }
  function persist(flush = false): Promise<boolean> {
    if (!ready.value || !alive) return Promise.resolve(false)
    if (timer) { clearTimeout(timer); timer = undefined }
    const token = generation, entry = selected.value, context = store.currentRecoveryContext, settings = { ...form }, selectedDatasetId = selectedId.value
    const task = (id: string, output = '') => entry && id ? { task_id: id, dataset_id: entry.id, dataset_path: entry.dataset_path, output_dir: output } : null
    saving = saving.then(async () => {
      if ((!alive && !flush) || token !== generation) return false
      try {
        const next = await api.saveLoraWorkspace({ ...workspace, selected_dataset_id: selectedDatasetId, form: settings, training_tensor_dir: context.trainingTensorDir || settings.tensor_dir, auto_label_task: task(context.autoLabelTaskId ?? ''), preprocess_task: task(context.preprocessTaskId ?? '', context.preprocessOutputDir ?? '') }, flush ? undefined : controller.signal, flush)
        if (token !== generation) return false
        workspace = next
        return alive || flush
      } catch (cause) { if (alive && token === generation) error.value = message(cause); return false }
    })
    return saving
  }
  async function action(work: () => Promise<void>) {
    if (disabled.value || !alive) return
    busy.value = true; error.value = ''; saved.value = false
    try { await saving; if (!alive) return; await work() } catch (cause) { if (alive) error.value = message(cause) }
    finally { if (alive) busy.value = false }
  }
  async function select(id: string) {
    return action(async () => {
      const entry = datasets.value.find(item => item.id === id && !item.trashed && item.available !== false)
      if (!entry) return
      ++generation
      store.stopBackgroundTasks()
      store.restoreRecoveryContext({ datasetPath: entry.dataset_path })
      store.samples = []; selectedId.value = id; form.audio_dir = entry.audio_dir
      form.output_dir = ''; form.tensor_dir = ''; form.skip_existing = false
      workspace = { ...workspace, auto_label_task: null, preprocess_task: null }
      const token = generation
      if (running.value) apply(await api.loadLoraDataset(entry.id, entry.revision, controller.signal), token)
      if (!alive || token !== generation) return
      await persist()
      if (!alive || token !== generation) return
      if (running.value) await store.startBackgroundTasks()
    })
  }
  async function create(name: string) {
    let created: LoraDatasetEntry | undefined
    await action(async () => { created = await api.createLoraDataset(name, controller.signal); if (alive) replace(created) })
    if (created && alive) await select(created.id)
  }
  async function rename(name: string) {
    await action(async () => { const entry = selected.value; if (entry) replace(await api.renameLoraDataset(entry.id, name, entry.revision, controller.signal)) })
  }
  async function trash() {
    await action(async () => {
      const entry = selected.value
      if (!entry) return
      replace(await api.trashLoraDataset(entry.id, entry.revision, controller.signal))
      if (!alive) return
      selectedId.value = null; store.samples = []; store.restoreRecoveryContext({ datasetPath: '' })
      await syncWorkspace()
      await persist()
    })
  }
  async function restore(id: string) {
    await action(async () => { const entry = datasets.value.find(item => item.id === id); if (entry) replace(await api.restoreLoraDataset(entry.id, entry.revision, controller.signal)) })
  }
  async function removeRecords(ids: string[]) {
    const requested = new Set(ids), token = generation
    const records = datasets.value.filter(item => requested.has(item.id) && item.trashed).map(({ id, revision }) => ({ id, revision }))
    if (!records.length || requested.size !== ids.length || records.length !== ids.length) return
    await action(async () => {
      if (token !== generation) return
      const response = await api.removeLoraDatasetRecords(records, controller.signal)
      if (!alive || token !== generation) return
      const removed = new Set(response.removed_ids.filter(id => requested.has(id)))
      datasets.value = datasets.value.filter(item => !item.trashed || !removed.has(item.id))
      await syncWorkspace()
    })
  }
  async function saveSnapshot(): Promise<boolean> {
    let success = false
    await action(async () => {
      const entry = selected.value, token = generation
      if (!entry) return
      apply(await api.saveLoraDataset(entry.id, { revision: entry.revision, custom_tag: form.custom_tag, tag_position: form.tag_position, all_instrumental: form.all_instrumental, genre_ratio: form.genre_ratio }, controller.signal), token)
      if (alive && token === generation) success = await persist()
    })
    return success
  }
  async function updateSample(index: number, request: Omit<UpdateLoraDatasetSampleRequest, 'revision'>) {
    let success = false
    await action(async () => {
      const entry = selected.value, token = generation
      if (!entry) return
      const response = await api.updateLoraDatasetSample(entry.id, index, { ...request, revision: entry.revision }, controller.signal)
      if (!alive || token !== generation) return
      const updated = response.samples.find(item => item.index === index), row = store.samples.findIndex(item => item.index === index)
      if (!updated || row < 0) throw new TypeError('Missing updated sample')
      replace(response.dataset)
      // Replacing the whole list would discard drafts in other sample rows.
      store.samples.splice(row, 1, parseDatasetSample(updated))
      saved.value = true
      success = true
    })
    return success
  }
  async function upload(files: File[]) {
    let result: Awaited<ReturnType<typeof api.uploadDatasetFiles>> | undefined
    await action(async () => {
      const entry = selected.value, token = generation
      if (!entry) return
      const response = await api.uploadDatasetFiles(entry.name, files, entry.id, entry.revision, controller.signal)
      if (!alive || token !== generation) return
      form.audio_dir = response.audio_dir
      apply(await api.scanLoraDataset(entry.id, { revision: entry.revision, audio_dir: response.audio_dir, custom_tag: form.custom_tag, tag_position: form.tag_position, all_instrumental: form.all_instrumental }, controller.signal), token)
      if (!alive || token !== generation) return
      await persist()
      result = response
    })
    return result
  }
  async function scan() {
    await action(async () => {
      const entry = selected.value, token = generation
      if (!entry) return
      apply(await api.scanLoraDataset(entry.id, { revision: entry.revision, audio_dir: form.audio_dir, custom_tag: form.custom_tag, tag_position: form.tag_position, all_instrumental: form.all_instrumental }, controller.signal), token)
      if (!alive || token !== generation) return
      await persist()
    })
  }
  async function initialize() {
    const token = ++generation
    busy.value = true; ready.value = false; error.value = ''
    try {
      const catalog = await api.listLoraDatasets(controller.signal)
      if (!alive || token !== generation) return
      const restored = await api.getLoraWorkspace(controller.signal)
      if (!alive || token !== generation) return
      datasets.value = catalog.datasets; issues.value = catalog.issues ?? []; workspace = restored
      selectedId.value = restored.selected_dataset_id ?? null
      Object.assign(form, restored.form)
      const entry = selected.value
      const path = entry?.dataset_path ?? store.datasetPath
      const label = restored.auto_label_task?.dataset_id === entry?.id && restored.auto_label_task?.dataset_path === path ? restored.auto_label_task : null
      const preprocess = restored.preprocess_task?.dataset_id === entry?.id && restored.preprocess_task?.dataset_path === path ? restored.preprocess_task : null
      const newer = (label?.task_id && label.task_id !== store.autoLabelTaskId) || (preprocess?.task_id && preprocess.task_id !== store.preprocessTaskId)
      const retained = !newer && store.datasetPath === path && (store.autoLabelRunning || store.preprocessRunning || store.autoLabelStarting || store.preprocessStarting || store.trainingStarting || store.training?.is_training)
      // The browser can revisit before an accepted submission has been saved.
      if (!retained) store.restoreRecoveryContext({ datasetPath: path, autoLabelTaskId: label?.task_id, preprocessTaskId: preprocess?.task_id, preprocessOutputDir: preprocess?.output_dir, trainingTensorDir: restored.training_tensor_dir || form.tensor_dir })
      if (running.value) await store.startBackgroundTasks()
      if (!alive || token !== generation) return
      if (entry && running.value && !store.nativeBusy) apply(await api.loadLoraDataset(entry.id, entry.revision, controller.signal), token, false)
      if (!alive || token !== generation) return
      ready.value = true
    } catch (cause) { if (alive && token === generation) { error.value = message(cause); void store.startBackgroundTasks() } }
    finally { if (alive && token === generation) busy.value = false }
  }
  watch([() => ({ ...form }), () => store.currentRecoveryContext], () => {
    if (!ready.value || !alive) return
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => { void persist() }, 400)
  }, { deep: true })
  watch(running, value => { if (value && ready.value && !busy.value) void initialize(); else if (!value) store.stopBackgroundTasks() })
  watch(() => store.nativeBusy, value => { if (!value && running.value && ready.value && !busy.value && selected.value && selected.value.sample_count > 0 && !store.samples.length) void select(selected.value.id) })
  watch(() => store.autoLabelRunning, (value, previous) => { if (previous && !value && alive && ready.value) void refresh().catch(() => { error.value = t('loraRecovery.libraryError') }) })
  const flush = () => { if (timer && ready.value) void persist(true) }
  window.addEventListener('pagehide', flush)
  onBeforeUnmount(() => { flush(); alive = false; controller.abort(); if (timer) clearTimeout(timer); window.removeEventListener('pagehide', flush) })
  return { datasets, selectedId, selected, busy, ready, disabled, error, issues, saved, notice, refresh, syncWorkspace, initialize, persist, select, create, rename, trash, restore, removeRecords, saveSnapshot, updateSample, upload, scan, apply }
}

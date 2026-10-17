import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiError } from '../api/http'
import * as api from '../api/mashups'
import type { MashupCreateRequest, MashupGeneratePlanRequest, MashupJob, MashupJobRequest, MashupPlacement, MashupState, MashupTiming } from '../api/generated'
import { createPollingLoop } from '../composables/polling'

interface Snapshot { state?: MashupState; jobs?: MashupJob[] }
export const useMashupStore = defineStore('music-mashups', () => {
  const document = ref<MashupState | null>(null), documents = ref<MashupState[]>([]), jobs = ref<MashupJob[]>([])
  const busy = ref(false), disconnected = ref(false), error = ref('')
  const active = computed(() => document.value?.status === 'preparing' || jobs.value.some(item => ['queued', 'running', 'cancelling', 'saving'].includes(item.status)))
  let generation = 0, observing = false, controller: AbortController | undefined
  async function read(id: string, signal: AbortSignal): Promise<Snapshot> { const [state, result] = await Promise.all([api.getMashup(id, signal), api.listMashupJobs(id, signal)]); return { state, jobs: result.jobs } }
  function commit(snapshot: Snapshot) {
    if (snapshot.state) {
      document.value = snapshot.state
      const index = documents.value.findIndex(item => item.id === snapshot.state?.id)
      if (index < 0) documents.value.unshift(snapshot.state); else documents.value[index] = snapshot.state
    }
    if (snapshot.jobs) jobs.value = snapshot.jobs
  }
  const poll = createPollingLoop(async context => {
    const id = document.value?.id
    if (!observing || !id) return false
    try { const snapshot = await read(id, context.signal); if (!context.isCurrent() || !observing || document.value?.id !== id) return false; if ((snapshot.state?.revision ?? -1) >= (document.value?.revision ?? 0)) commit(snapshot); disconnected.value = false }
    catch { if (context.isCurrent() && observing) disconnected.value = true }
    return disconnected.value || active.value
  }, 2500)
  function stop() { observing = false; ++generation; controller?.abort(); controller = undefined; busy.value = false; poll.stop() }
  async function run(work: (signal: AbortSignal) => Promise<Snapshot>) {
    if (busy.value || !observing) return
    poll.stop(); const token = ++generation, request = new AbortController(); controller = request; busy.value = true; error.value = ''
    try { const result = await work(request.signal); if (observing && token === generation && !request.signal.aborted) { commit(result); disconnected.value = false } }
    catch (cause) { if (observing && token === generation && !request.signal.aborted) { error.value = cause instanceof ApiError && cause.message === 'mashup_revision_conflict' ? 'conflict' : 'actionFailed'; disconnected.value = !(cause instanceof ApiError) || cause.status >= 500 } }
    finally { if (observing && token === generation) { controller = undefined; busy.value = false; if (active.value || disconnected.value) poll.start(false) } }
  }
  function start(id?: string) {
    stop(); observing = true; document.value = null; jobs.value = []
    return run(async signal => { const result = await api.listMashups(signal); if (signal.aborted) return {}; documents.value = result.documents; const selected = id ?? result.documents[0]?.id; return selected ? read(selected, signal) : {} })
  }
  function load(id: string) { return run(signal => read(id, signal)) }
  function refresh() { return document.value ? load(document.value.id) : start() }
  function create(request: MashupCreateRequest) { return run(async signal => { const state = await api.createMashup(request, signal); return signal.aborted ? {} : { state, jobs: (await api.listMashupJobs(state.id, signal)).jobs } }) }
  function timing(source: 'a' | 'b', timing: MashupTiming) { const selected = document.value; return selected ? run(async signal => ({ state: await api.updateMashupTiming(selected.id, { expected_revision: selected.revision, source, timing }, signal) })) : undefined }
  function generate(settings: Omit<MashupGeneratePlanRequest, 'expected_revision'>) { const selected = document.value; return selected ? run(async signal => ({ state: await api.generateMashupPlan(selected.id, { ...settings, expected_revision: selected.revision }, signal) })) : undefined }
  function savePlacements(placements: MashupPlacement[]) { const selected = document.value; return selected ? run(async signal => ({ state: await api.editMashupPlan(selected.id, { expected_revision: selected.revision, placements }, signal) })) : undefined }
  function render(request: MashupJobRequest) { const selected = document.value; return selected ? run(async signal => { const job = await api.startMashupRender(selected.id, request, signal); return { jobs: [job, ...jobs.value.filter(item => item.id !== job.id)] } }) : undefined }
  function cancelPreparation() { const selected = document.value; return selected ? run(async signal => ({ state: await api.cancelMashup(selected.id, signal) })) : undefined }
  function retryPreparation() { const selected = document.value; return selected ? run(async signal => ({ state: await api.retryMashupPreparation(selected.id, signal) })) : undefined }
  function jobAction(id: string, action: 'cancel' | 'save') { return run(async signal => { const job = await (action === 'cancel' ? api.cancelMashupJob(id, signal) : api.retryMashupSave(id, signal)); return { jobs: [job, ...jobs.value.filter(item => item.id !== job.id)] } }) }
  function newDraft() { if (!busy.value && !active.value) { poll.stop(); document.value = null; jobs.value = []; error.value = ''; disconnected.value = false } }
  return { document, documents, jobs, busy, disconnected, error, active, start, stop, load, refresh, create, timing, generate, savePlacements, render, cancelPreparation, retryPreparation, jobAction, newDraft }
})

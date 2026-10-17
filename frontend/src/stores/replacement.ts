import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import * as api from '../api/replacement'
import { ApiError, isDefiniteClientError } from '../api/http'
import { isRecord } from '../api/nativeValidation'
import { parseReplacementActionRequest, parseReplacementRenderRequest } from '../api/generated'
import type { AlignmentEditRequest, ReplacementActionRequest, ReplacementCapabilities, ReplacementMixSettings, ReplacementPlanResponse, ReplacementPlanSummary, ReplacementPreviewWindow, ReplacementRenderRequest } from '../api/generated'
import { createPollingLoop } from '../composables/polling'

type PendingAction = { kind: 'create'; planId: string; body: api.ReplacementRequest }
  | { kind: 'prepare' | 'retry'; planId: string; body: ReplacementActionRequest }
  | { kind: 'preview' | 'render' | 'remix'; planId: string; body: ReplacementRenderRequest }
const pendingKey = 'openfabric.replacement-action.v1'
function readPending(): PendingAction | null {
  try {
    const saved = sessionStorage.getItem(pendingKey)
    if (!saved || saved.length > 131072) return null
    const value: unknown = JSON.parse(saved)
    if (!isRecord(value) || Object.keys(value).length !== 3 || typeof value.planId !== 'string' || !/^[a-f0-9]{32}$/.test(value.planId)) return null
    if (value.kind === 'create') return { kind: value.kind, planId: value.planId, body: api.replacementRequest(value.body) }
    if (value.kind === 'prepare' || value.kind === 'retry') return { kind: value.kind, planId: value.planId, body: parseReplacementActionRequest(value.body) }
    if (value.kind === 'preview' || value.kind === 'render' || value.kind === 'remix') return { kind: value.kind, planId: value.planId, body: parseReplacementRenderRequest(value.body) }
  } catch { /* An unavailable or malformed browser retry hint never starts work. */ }
  return null
}

export const useReplacementStore = defineStore('music-replacement', () => {
  const plan = ref<ReplacementPlanResponse | null>(null), plans = ref<ReplacementPlanSummary[]>([])
  const capabilities = ref<ReplacementCapabilities | null>(null)
  const busy = ref(false), disconnected = ref(false), error = ref('')
  const pendingAction = ref<PendingAction | null>(readPending())
  function remember(value: PendingAction | null) {
    pendingAction.value = value
    try { if (value) sessionStorage.setItem(pendingKey, JSON.stringify(value)); else sessionStorage.removeItem(pendingKey) } catch { /* Backend jobs remain durable when browser storage is unavailable. */ }
  }
  const active = computed(() => !!plan.value && (!!plan.value.active_attempt_id || plan.value.status === 'queued' || plan.value.status === 'running'))
  let generation = 0, observing = false, controller: AbortController | undefined
  const poll = createPollingLoop(async context => {
    const id = plan.value?.id
    if (!observing || !id) return false
    try {
      const result = await api.getReplacementPlan(id, context.signal)
      if (!context.isCurrent() || !observing || plan.value?.id !== id) return false
      if ((result.revision ?? 1) >= (plan.value.revision ?? 1)) plan.value = result
      disconnected.value = false
    } catch { if (context.isCurrent() && observing) disconnected.value = true }
    return disconnected.value || active.value
  }, 2500)
  function stop() { observing = false; generation++; controller?.abort(); controller = undefined; busy.value = false; poll.stop() }
  async function run(work: (signal: AbortSignal) => Promise<ReplacementPlanResponse | null>) {
    if (busy.value || !observing) return
    poll.stop(); const token = ++generation, request = new AbortController()
    controller = request; busy.value = true; error.value = ''
    try {
      const result = await work(request.signal)
      if (observing && token === generation && !request.signal.aborted) { if (result) plan.value = result; disconnected.value = false }
    } catch (cause) {
      if (observing && token === generation && !request.signal.aborted) {
        error.value = cause instanceof ApiError && cause.message === 'replacement_revision_conflict' ? 'conflict' : 'actionFailed'
        disconnected.value = !isDefiniteClientError(cause)
      }
    } finally {
      if (observing && token === generation) { controller = undefined; busy.value = false; if (active.value || disconnected.value) poll.start(false) }
    }
  }
  function start(id?: string) {
    stop(); observing = true; plan.value = null
    return run(async signal => {
      const [listed, available] = await Promise.all([api.listReplacementPlans(undefined, signal), api.replacementCapabilities(signal)])
      if (signal.aborted) return null
      if (!signal.aborted) { plans.value = listed.plans ?? []; capabilities.value = available }
      const selected = id ?? listed.plans?.[0]?.id
      return selected ? api.getReplacementPlan(selected, signal) : null
    })
  }
  function load(id: string) { return run(signal => api.getReplacementPlan(id, signal)) }
  function refresh() { return plan.value ? load(plan.value.id) : start() }
  function create(request: api.ReplacementRequest) {
    if (busy.value || !observing || pendingAction.value) return
    remember({ kind: 'create', planId: crypto.randomUUID().replaceAll('-', ''), body: api.replacementRequest(JSON.parse(JSON.stringify(request))) })
    return resumePending()
  }
  function update(request: api.ReplacementRequest) { const selected = plan.value; return selected && !pendingAction.value ? run(signal => api.updateReplacementPlan(selected.id, { expected_revision: selected.revision ?? 1, request }, signal)) : undefined }
  function saveMix(mix: ReplacementMixSettings) { const selected = plan.value; return selected && !pendingAction.value ? run(signal => api.editReplacementMix(selected.id, { expected_revision: selected.revision ?? 1, mix }, signal)) : undefined }
  function saveAlignment(edit: Omit<AlignmentEditRequest, 'expected_revision'>) { const selected = plan.value; return selected && !pendingAction.value ? run(signal => api.editReplacementAlignment(selected.id, { ...edit, expected_revision: selected.revision ?? 1 }, signal)) : undefined }
  function prepare() { return beginAction('prepare') }
  function retry() { return beginAction('retry') }
  function beginAction(kind: 'prepare' | 'retry') {
    const selected = plan.value
    if (!selected || busy.value || !observing || pendingAction.value || active.value || disconnected.value) return
    remember({ kind, planId: selected.id, body: { expected_revision: selected.revision ?? 1, request_id: crypto.randomUUID().replaceAll('-', '') } })
    return resumePending()
  }
  function resumePending() {
    const saved = pendingAction.value
    if (!saved || busy.value || !observing) return
    return run(async signal => {
      try {
        const result = saved.kind === 'create' ? await api.createReplacementPlan(saved.body, signal, saved.planId)
          : saved.kind === 'prepare' ? await api.prepareReplacementPlan(saved.planId, saved.body, signal)
          : saved.kind === 'retry' ? await api.retryReplacementPlan(saved.planId, saved.body, signal)
          : await api.renderReplacement(saved.planId, saved.kind, parseReplacementRenderRequest(saved.body), signal)
        if (!signal.aborted) {
          remember(null)
          if (saved.kind === 'create') plans.value = [{ id: result.id, revision: result.revision ?? 1, title: result.request.title,
            mode: result.request.mode, status: result.status ?? 'draft', phase: result.phase ?? 'inspecting', updated_at: result.updated_at }, ...plans.value.filter(item => item.id !== result.id)]
        }
        return result
      } catch (cause) {
        if (!signal.aborted && isDefiniteClientError(cause)) remember(null)
        throw cause
      }
    })
  }
  function cancel() { const selected = plan.value; return selected ? run(signal => api.cancelReplacementPlan(selected.id, signal)) : undefined }
  function render(mode: 'preview' | 'render' | 'remix', mix: ReplacementMixSettings, preview?: ReplacementPreviewWindow) {
    const selected = plan.value
    if (!selected || busy.value || !observing || pendingAction.value || active.value || disconnected.value) return
    const body = parseReplacementRenderRequest({ expected_revision: selected.revision ?? 1, request_id: crypto.randomUUID().replaceAll('-', ''), mix, ...(preview ? { preview_window: preview } : {}) })
    remember({ kind: mode, planId: selected.id, body })
    return resumePending()
  }
  function newDraft() { if (!busy.value && !active.value && !pendingAction.value) { poll.stop(); plan.value = null; error.value = ''; disconnected.value = false } }
  return { plan, plans, capabilities, busy, disconnected, error, active, pendingAction, resumePending, start, stop, load, refresh, create, update, saveMix, saveAlignment, prepare, retry, cancel, render, newDraft }
})

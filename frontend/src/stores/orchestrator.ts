import { acceptHMRUpdate, defineStore } from 'pinia'
import * as api from '../api/orchestrator'
import type { ModelId, ModelRuntimeStatus, OrchestratorStatus } from '../types'
import { createPollingLoop, type PollContext, type PollingLoop } from '../composables/polling'

const FAST_POLL_MS = 1500
const SLOW_POLL_MS = 8000
const loops = new WeakMap<object, PollingLoop>()

interface StatusEntry {
  id: ModelId
  label: string
  status: ModelRuntimeStatus
  error: string | null
}

export const useOrchestratorStore = defineStore('orchestrator', {
  state: () => ({
    activeModel: null as ModelId | null,
    statuses: {} as Record<string, StatusEntry>,
    switching: false,
    switchError: null as string | null,
  }),
  getters: {
    isBusy(state): boolean {
      return Object.values(state.statuses).some((m) => m.status === 'starting' || m.status === 'stopping')
    },
  },
  actions: {
    _applySnapshot(snapshot: OrchestratorStatus) {
      this.activeModel = snapshot.active_model
      this.statuses = snapshot.models
    },
    async refresh(context?: PollContext) {
      try {
        const snapshot = await api.getStatus(context?.signal)
        if (!context || context.isCurrent()) this._applySnapshot(snapshot)
      } catch {
        // Transient network hiccup (e.g. backend restarting) - next poll retries.
      }
    },
    startPolling() {
      let loop = loops.get(this)
      if (!loop) {
        loop = createPollingLoop((context) => this.refresh(context), () => this.isBusy ? FAST_POLL_MS : SLOW_POLL_MS)
        loops.set(this, loop)
      }
      loop.start()
    },
    stopPolling() {
      loops.get(this)?.stop()
    },
    async switchModel(model: ModelId) {
      this.switching = true
      this.switchError = null
      try {
        this._applySnapshot(await api.switchModel(model))
      } catch (err) {
        this.switchError = err instanceof Error ? err.message : String(err)
        await this.refresh()
        throw err
      } finally {
        this.switching = false
      }
    },
    async stopActive() {
      this._applySnapshot(await api.stopActive())
    },
  },
})

if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useOrchestratorStore, import.meta.hot))
}

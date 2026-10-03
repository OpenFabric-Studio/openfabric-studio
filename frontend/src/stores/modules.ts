import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as api from '../api/modules'
import type { ModuleInfo, ModuleInventory, ModuleInstallJob } from '../api/generated'
import { createPollingLoop, type PollContext } from '../composables/polling'

export type ModuleId = ModuleInfo['id']
export type DisplayModuleState = ModuleInfo['state'] | 'unknown'

export const useModulesStore = defineStore('modules', () => {
  const inventory = ref<ModuleInventory | null>(null)
  const jobs = ref<ModuleInstallJob[]>([])
  const offline = ref(false)
  const jobsOffline = ref(false)
  const loading = ref(false)
  let generation = 0
  let request: AbortController | null = null
  const busy = computed(() => jobs.value.some(job => job.state === 'queued' || job.state === 'running'))
  async function refresh(context?: PollContext) {
    const token = ++generation
    request?.abort(); const controller = new AbortController(); request = controller
    const abort = () => controller.abort()
    context?.signal.addEventListener('abort', abort, { once: true })
    const current = () => token === generation && !controller.signal.aborted && (!context || context.isCurrent())
    loading.value = !inventory.value
    const results = await Promise.allSettled([api.getModules(controller.signal), api.listModuleJobs(controller.signal)])
    if (current()) {
      const status = results[0], operations = results[1]
      if (status.status === 'fulfilled') { inventory.value = status.value; offline.value = false } else offline.value = true
      if (operations.status === 'fulfilled') { jobs.value = operations.value; jobsOffline.value = false } else jobsOffline.value = true
      loading.value = false; request = null
    }
    context?.signal.removeEventListener('abort', abort)
  }
  const loop = createPollingLoop(refresh, () => busy.value ? 2000 : 15000)
  function startPolling() { loop.start() }
  function stopPolling() { generation++; request?.abort(); request = null; loading.value = false; loop.stop() }
  function acceptJob(job: ModuleInstallJob) {
    // A poll started before this mutation must not replace its newer result.
    generation++; request?.abort(); request = null; loading.value = false
    jobs.value = [job, ...jobs.value.filter(item => item.id !== job.id)]
  }
  function stateOf(id: ModuleId): DisplayModuleState {
    return offline.value ? 'unknown' : inventory.value?.modules.find(module => module.id === id)?.state ?? 'unknown'
  }
  return { inventory, jobs, offline, jobsOffline, loading, busy, refresh, startPolling, stopPolling, acceptJob, stateOf }
})

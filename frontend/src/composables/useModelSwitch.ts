import { useRouter } from 'vue-router'
import { useOrchestratorStore } from '../stores/orchestrator'
import type { ModelId } from '../types'

export const MODEL_ROUTES: Record<ModelId, string> = {
  ace_step: 'ace-step',
  yue2: 'yue2',
}

export const MODEL_LABELS: Record<ModelId, string> = {
  ace_step: 'ACE-Step 1.5',
  yue2: 'YuE2-3B',
}

/** Navigates to the model's page and (unless already running) tells the backend to switch to it. */
export function useModelSwitch() {
  const router = useRouter()
  const orchestrator = useOrchestratorStore()

  async function selectModel(id: ModelId): Promise<void> {
    const routeName = MODEL_ROUTES[id]
    if (router.currentRoute.value.name !== routeName) await router.push({ name: routeName })
    const status = orchestrator.statuses[id]?.status ?? 'stopped'
    if (orchestrator.activeModel === id && status === 'running') return
    await orchestrator.switchModel(id)
  }

  return { selectModel }
}

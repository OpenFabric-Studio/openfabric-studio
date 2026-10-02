<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { MODEL_LABELS, useModelSwitch } from '../../composables/useModelSwitch'
import { useOrchestratorStore } from '../../stores/orchestrator'
import type { ModelId, ModelRuntimeStatus } from '../../types'

const props = defineProps<{ modelId: ModelId; status: ModelRuntimeStatus; error: string | null }>()
const { t } = useI18n()
const { selectModel } = useModelSwitch()
const orchestrator = useOrchestratorStore()
const pending = ref(false), failed = ref(false)
const busy = computed(() => pending.value || orchestrator.switching || orchestrator.isBusy)
let alive = true, generation = 0
onBeforeUnmount(() => { alive = false; ++generation })

async function start() {
  if (busy.value) return
  const request = ++generation
  pending.value = true; failed.value = false
  try {
    await selectModel(props.modelId, () => alive && request === generation)
  } catch {
    if (alive && request === generation) failed.value = true
  } finally {
    if (alive && request === generation) pending.value = false
  }
}
</script>

<template>
  <div class="mx-auto max-w-xl rounded-xl border border-border bg-panel p-8 text-center">
    <p v-if="status === 'starting'" class="text-sm text-text-dim">
      <span class="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2 border-accent1 border-t-transparent align-middle"></span>
      {{ t('offlineBanner.starting', { model: MODEL_LABELS[modelId] }) }}
    </p>
    <p v-else-if="status === 'stopping'" class="text-sm text-text-dim">{{ t('offlineBanner.stopping', { model: MODEL_LABELS[modelId] }) }}</p>
    <template v-else>
      <p class="text-sm text-text-dim">{{ t('offlineBanner.notRunning', { model: MODEL_LABELS[modelId] }) }}</p>
      <p v-if="status === 'error' || failed" role="alert" class="mt-3 rounded-lg bg-status-failed/10 p-3 text-left text-xs text-status-failed">{{ t('appNavigation.switchFailed') }}</p>
      <button type="button" :disabled="busy" class="accent-gradient mt-4 min-h-11 rounded-lg px-4 py-2 text-sm font-medium text-white disabled:cursor-wait disabled:opacity-50" @click="start">{{ t('offlineBanner.start', { model: MODEL_LABELS[modelId] }) }}</button>
    </template>
  </div>
</template>

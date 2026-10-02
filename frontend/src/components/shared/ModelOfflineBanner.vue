<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { MODEL_LABELS, useModelSwitch } from '../../composables/useModelSwitch'
import type { ModelId, ModelRuntimeStatus } from '../../types'

const props = defineProps<{ modelId: ModelId; status: ModelRuntimeStatus; error: string | null }>()
const { t } = useI18n()
const { selectModel } = useModelSwitch()

async function start() {
  try {
    await selectModel(props.modelId)
  } catch {
    // orchestrator.switchError is already surfaced in the header.
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
      <p v-if="status === 'error' && error" class="mt-3 whitespace-pre-line rounded-lg bg-status-failed/10 p-3 text-left text-xs text-status-failed">{{ error }}</p>
      <button type="button" class="accent-gradient mt-4 rounded-lg px-4 py-2 text-sm font-medium text-white" @click="start">{{ t('offlineBanner.start', { model: MODEL_LABELS[modelId] }) }}</button>
    </template>
  </div>
</template>

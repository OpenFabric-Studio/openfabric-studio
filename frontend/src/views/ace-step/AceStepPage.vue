<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useOrchestratorStore } from '../../stores/orchestrator'
import { useAceStepStore } from '../../stores/aceStep'
import * as trainingApi from '../../api/aceStepTraining'
import ModelOfflineBanner from '../../components/shared/ModelOfflineBanner.vue'
import GenerateForm from './GenerateForm.vue'
import ResultsFeed from './ResultsFeed.vue'
import { createPollingLoop } from '../../composables/polling'
import GeneratorPanel, { type GeneratorPanelMode } from '../../components/shared/GeneratorPanel.vue'

const orchestrator = useOrchestratorStore()
const store = useAceStepStore()
const { t } = useI18n()

const modelStatus = computed(() => orchestrator.statuses.ace_step?.status ?? 'stopped')
const modelError = computed(() => orchestrator.statuses.ace_step?.error ?? null)
const isRunning = computed(() => modelStatus.value === 'running')
const generatorMode = ref<GeneratorPanelMode>('docked')

watch(
  isRunning,
  (running) => {
    if (running) void store.loadInventory()
  },
  { immediate: true },
)

onMounted(() => {
  void store.loadHistory()
  store.startBackgroundTasks()
})
onBeforeUnmount(() => store.stopBackgroundTasks())

// Training and generation share the same GPU/model process, so while a LoRA
// training run is active (started from the /ace-step/lora tab, possibly in
// another browser tab) native generation is disabled while independent voice
// replacement remains available. Unknown initial status must not select it.
const isTraining = ref(false)
const trainingLoop = createPollingLoop(async (context) => {
  if (!isRunning.value) return
  try {
    const status = await trainingApi.trainingStatus(context.signal)
    if (context.isCurrent()) isTraining.value = status.is_training
  } catch {
    // Leave last known value - a transient failure shouldn't flip the banner.
  }
}, 10000)
onMounted(() => trainingLoop.start())
onBeforeUnmount(() => trainingLoop.stop())
</script>

<template>
  <div class="space-y-6">
    <ModelOfflineBanner v-if="!isRunning" model-id="ace_step" :status="modelStatus" :error="modelError" />
    <div v-else-if="isTraining" class="rounded-xl border border-status-queued/40 bg-status-queued/10 p-6 text-center text-sm text-text-dim">
      {{ t('acePage.trainingActive') }}
      <RouterLink to="/ace-step/lora" class="text-accent1 hover:underline">{{ t('acePage.openTrainingPage') }}</RouterLink>
    </div>
    <div class="grid grid-cols-1 gap-6" :class="{ 'lg:grid-cols-[480px_minmax(0,1fr)]': generatorMode === 'docked' }" data-generator-workspace>
      <GeneratorPanel v-model="generatorMode">
        <GenerateForm :generation-available="orchestrator.statuses.ace_step ? isRunning && !isTraining : null" />
      </GeneratorPanel>
      <ResultsFeed />
    </div>
  </div>
</template>

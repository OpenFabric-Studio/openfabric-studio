<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { musicLoraCapabilities } from '../../api/musicLora'
import type { MusicLoraCapabilities } from '../../api/generated'

const { t } = useI18n()
const capabilities = ref<MusicLoraCapabilities['engines']>([]), error = ref(false)
const controller = new AbortController()
const workflow = ['dataset', 'captions', 'preprocess', 'train', 'export']
const controls = ['dataset', 'autoLabel', 'preprocess', 'rank', 'alpha', 'dropout', 'learningRate', 'epochs', 'batch', 'accumulation', 'saveCadence', 'seed', 'precision', 'checkpointing', 'export']
const models = [
  { engine: 'ace_step', route: 'music-lora-ace-step' },
  { engine: 'stable_audio3', route: 'music-lora-stable-audio3' },
  { engine: 'yue2', route: 'music-lora-yue2' },
]
onMounted(async () => {
  try { const value = await musicLoraCapabilities(controller.signal); if (!controller.signal.aborted) capabilities.value = value.engines }
  catch { if (!controller.signal.aborted) error.value = true }
})
onBeforeUnmount(() => { controller.abort() })
</script>

<template>
  <div class="space-y-6">
    <section class="space-y-3 rounded-xl border border-border bg-panel p-5"><h3 class="text-lg font-semibold text-text">{{ t('loraWorkspace.startTitle') }}</h3><p class="max-w-4xl text-sm leading-relaxed text-text-dim">{{ t('loraWorkspace.startBody') }}</p></section>
    <section class="space-y-3"><h3 class="text-lg font-semibold text-text">{{ t('loraWorkspace.workflowTitle') }}</h3><ol class="grid gap-3 md:grid-cols-2"><li v-for="step in workflow" :key="step" class="space-y-2 rounded-xl border border-border bg-panel p-5"><h4 class="font-medium text-text">{{ t(`loraWorkspace.workflow.${step}.title`) }}</h4><p class="text-sm leading-relaxed text-text-dim">{{ t(`loraWorkspace.workflow.${step}.body`) }}</p></li></ol></section>
    <section class="space-y-3"><h3 class="text-lg font-semibold text-text">{{ t('loraWorkspace.modelsTitle') }}</h3><p v-if="error" role="alert" class="text-sm text-status-queued">{{ t('loraWorkspace.capabilityError') }}</p><div class="grid gap-3 md:grid-cols-2"><article v-for="model in models" :key="model.engine" class="flex flex-col items-start gap-3 rounded-xl border border-border bg-panel p-5"><h4 class="font-semibold text-text">{{ t(`musicLora.engines.${model.engine}`) }}</h4><p class="text-sm leading-relaxed text-text-dim">{{ t(`loraWorkspace.models.${model.engine}`) }}</p><p v-for="capability in capabilities.filter(value => value.engine === model.engine)" :key="capability.engine" class="text-xs text-text-dim">{{ t('musicLora.loading') }}: {{ t(capability.loading ? 'musicLora.supported' : 'musicLora.unsupported') }} · {{ t('musicLora.training') }}: {{ t(capability.training ? 'musicLora.supported' : 'musicLora.unsupported') }}</p><RouterLink :to="{ name: model.route }" class="mt-auto inline-flex min-h-11 items-center rounded-lg border border-border px-4 py-2 text-sm text-accent2 focus-visible:outline-2 focus-visible:outline-accent2">{{ t('loraWorkspace.openModel', { model: t(`musicLora.engines.${model.engine}`) }) }}</RouterLink></article></div></section>
    <section class="space-y-3 rounded-xl border border-border bg-panel p-5"><h3 class="text-lg font-semibold text-text">{{ t('loraWorkspace.controlsTitle') }}</h3><p class="text-sm leading-relaxed text-text-dim">{{ t('loraWorkspace.controlsIntro') }}</p><dl class="grid gap-x-6 gap-y-4 md:grid-cols-2"><div v-for="control in controls" :key="control" class="space-y-1"><dt class="text-sm font-medium text-text">{{ t(`loraWorkspace.controls.${control}.title`) }}</dt><dd class="text-sm leading-relaxed text-text-dim">{{ t(`loraWorkspace.controls.${control}.body`) }}</dd></div></dl></section>
    <section class="space-y-3 rounded-xl border border-border bg-panel p-5"><h3 class="text-lg font-semibold text-text">{{ t('loraWorkspace.recoveryTitle') }}</h3><p class="text-sm leading-relaxed text-text-dim">{{ t('loraWorkspace.recoveryBody') }}</p><p class="text-sm leading-relaxed text-text-dim">{{ t('loraWorkspace.crashBody') }}</p></section>
  </div>
</template>

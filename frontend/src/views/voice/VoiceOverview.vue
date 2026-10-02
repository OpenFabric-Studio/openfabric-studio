<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { VoiceProfileResponse } from '../../api/contracts'
import WaveformPlayer from '../../components/shared/WaveformPlayer.vue'
import { shortVoiceId } from './voiceLabels'
const props = defineProps<{ voice: VoiceProfileResponse; active: boolean; busy: boolean }>()
const emit = defineEmits<{ model: [event: Event]; compare: []; use: []; stop: []; prepare: [] }>()
const { t } = useI18n()
const model = computed(() => props.voice.models?.find(item => item.id === props.voice.active_model_id))
const referenceSrc = computed(() => `/api/voices/${encodeURIComponent(props.voice.id)}/reference/published?model=${encodeURIComponent(props.voice.active_model_id ?? '')}`)
</script>
<template>
  <div class="space-y-6 py-2">
    <div class="rounded-xl border border-status-done/25 bg-status-done/5 p-4">
      <p class="text-xs font-medium text-status-done">{{ t('singingWorkspace.published') }}</p>
      <h3 class="mt-2 text-xl font-semibold text-text">{{ active ? t('voiceClone.usingThis') : t('voiceClone.ready') }}</h3>
      <p class="mt-2 text-sm text-text-dim">{{ t('singingWorkspace.publishedHint') }}</p>
      <div class="mt-4 flex flex-wrap gap-3"><button type="button" class="min-h-11 rounded-lg bg-accent1 px-4 py-2 text-sm text-white" @click="emit('compare')">{{ t('singingWorkspace.test') }}</button><button v-if="!active" type="button" :disabled="busy" class="min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text disabled:opacity-50" @click="emit('use')">{{ t('voiceClone.useThis') }}</button><button v-else type="button" class="min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text" @click="emit('stop')">{{ t('voiceClone.stopUsing') }}</button></div>
    </div>
    <div class="grid gap-5 xl:grid-cols-2">
      <div class="space-y-3 rounded-xl border border-border p-4"><h3 class="font-medium text-text">{{ t('singingWorkspace.model') }}</h3><label v-if="voice.models?.length" class="block space-y-2 text-sm"><span class="text-text-dim">{{ t('voiceClone.review.activeModel') }}</span><select :value="voice.active_model_id" :disabled="busy" :aria-label="t('voiceClone.review.activeModel')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-text" @change="emit('model', $event)"><option v-for="choice in voice.models" :key="choice.id" :value="choice.id">{{ choice.kind === 'base' ? t('voiceClone.review.referenceOnly') : t('voiceClone.review.trainedSteps', { steps: choice.steps }) }} · {{ shortVoiceId(choice.id) }}</option></select></label><p v-else class="break-words text-sm text-text">{{ t('singingWorkspace.legacyModel') }} · {{ shortVoiceId(voice.active_model_id ?? '') }}</p><p class="text-sm text-text-dim">{{ model ? (model.kind === 'base' ? t('voiceClone.review.referenceOnly') : t('voiceClone.review.trainedSteps', { steps: model.steps })) : t('voiceClone.review.trainedSteps', { steps: voice.trained_steps }) }}</p><p class="text-xs text-text-dim">{{ t('singingWorkspace.modelHint') }}</p><p v-if="busy" class="text-xs text-text-dim">{{ t('singingWorkspace.modelBusy') }}</p></div>
      <div class="min-w-0 space-y-3 rounded-xl border border-border p-4"><h3 class="font-medium text-text">{{ t('singingWorkspace.reference') }}</h3><WaveformPlayer :key="referenceSrc" :src="referenceSrc" /><p class="text-xs text-text-dim">{{ t('singingWorkspace.referenceHint') }}</p></div>
    </div>
    <div class="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4"><p class="text-sm text-text-dim">{{ t('singingWorkspace.improveHint') }}</p><button type="button" class="min-h-11 rounded-lg border border-border px-3 py-2 text-sm text-text" @click="emit('prepare')">{{ t('singingWorkspace.reviewSources') }}</button></div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { VoiceProfile } from '../../api/voices'
import type { VoicePreparationState } from './useVoicePreparation'
import { shortVoiceId } from './voiceLabels'
defineProps<{ state: VoicePreparationState; voice: VoiceProfile }>()
const { t } = useI18n()
</script>

<template>
  <section class="space-y-4">
    <h3 class="text-base font-semibold text-text">{{ t('voiceClone.review.buildTitle') }}</h3>
    <p class="text-sm text-text-dim">{{ t('voiceClone.workspace.buildIntro') }}</p>
    <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.review.mode') }}</span><select v-model="state.mode" :disabled="state.busy" :aria-label="t('voiceClone.review.mode')" class="w-full rounded-lg border border-border bg-panel-2 p-2"><option value="0">{{ t('voiceClone.review.referenceOnly') }}</option><option v-for="steps in [200, 500, 1000]" :key="steps" :value="String(steps)">{{ t('voiceClone.review.trainSteps', { steps }) }}</option><option value="compare" :disabled="state.resume">{{ t('voiceClone.review.compareCheckpoints') }}</option></select></label>
    <p class="text-sm text-text-dim">{{ t('voiceClone.review.buildHint') }}</p>
    <label class="flex items-center gap-2 text-sm text-text"><input v-model="state.resume" type="checkbox" :disabled="state.busy || !state.canResume" />{{ t('voiceClone.review.resume') }}</label><p class="text-sm text-text-dim">{{ t('voiceClone.review.resumeHint') }}</p>
    <button type="button" :disabled="!state.canBuild" class="rounded-lg bg-accent1 px-4 py-2 text-sm text-white disabled:opacity-50" @click="state.build">{{ state.action === 'build' || state.building ? t('voiceClone.building') : t('voiceClone.build') }}</button>
    <p v-if="!state.canBuild && !state.busy" class="text-sm text-text-dim">{{ t('voiceClone.review.prepareBeforeBuild') }}</p>
    <label v-if="state.models.length" class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.review.activeModel') }}</span><select :value="voice.active_model_id" :disabled="state.busy" :aria-label="t('voiceClone.review.activeModel')" class="w-full rounded-lg border border-border bg-panel-2 p-2" @change="state.chooseModel"><option v-for="model in state.models" :key="model.id" :value="model.id">{{ model.kind === 'base' ? `${t('voiceClone.review.referenceOnly')} · ${shortVoiceId(model.id)}` : `${t('voiceClone.review.trainedSteps', { steps: model.steps })} · ${shortVoiceId(model.id)}` }}</option></select></label>
  </section>
</template>

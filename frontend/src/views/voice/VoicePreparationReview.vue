<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { voiceErrorText, type VoiceProfile } from '../../api/voices'
import type { VoicePreparationResponse } from '../../api/contracts'
import { useVoicePreparation } from './useVoicePreparation'
import type { VoiceReviewState, VoiceWorkspaceStep } from './voiceWorkspace'
import VoiceFilesPanel from './VoiceFilesPanel.vue'
import VoiceSamplesPanel from './VoiceSamplesPanel.vue'
import VoiceBuildPanel from './VoiceBuildPanel.vue'
import VoiceCoveragePanel from './VoiceCoveragePanel.vue'

const props = defineProps<{ voice: VoiceProfile; disabled?: boolean; step: VoiceWorkspaceStep }>()
const emit = defineEmits<{ profile: [voice: VoiceProfile]; preparation: [preparation: VoicePreparationResponse]; state: [state: VoiceReviewState]; navigate: [step: VoiceWorkspaceStep] }>()
const { t } = useI18n()
const state = reactive(useVoicePreparation(() => props.voice, () => !!props.disabled, {
  profile: (profile) => emit('profile', profile), preparation: (response) => emit('preparation', response),
}))
watch(() => ({ loading: state.loading, preparing: state.preparing, optionsDirty: state.optionsDirty, selectionDirty: state.selectionDirty, selectedCount: state.selectedIds.length, referenceReady: !!state.referenceId, validSelection: state.validSelection, canBuild: state.canBuild, canPrepare: state.canPrepare, canAnalyzeCoverage: state.canAnalyzeCoverage, action: state.action, error: state.error }), (value) => emit('state', value), { immediate: true })
defineExpose({ cancelPreparation: state.cancelPreparation, prepare: state.prepare, analyzeCoverage: state.analyzeCoverage, cancelBuild: state.cancelBuild, build: state.build })
</script>

<template>
  <div class="space-y-4">
    <p v-if="state.loading" class="text-sm text-text-dim">{{ t('common.loading') }}</p>
    <div v-if="(state.selectionDirty || state.optionsDirty && state.preparation?.revision) && step !== 'files'" class="rounded-lg border border-accent1/40 bg-panel-2 p-3 text-sm text-text"><p>{{ t(state.optionsDirty ? 'voiceClone.workspace.optionsDirty' : 'voiceClone.workspace.selectionDirty') }}</p><button type="button" class="mt-2 rounded-lg border border-border px-3 py-2 text-sm" @click="emit('navigate', state.optionsDirty ? 'files' : 'samples')">{{ t(state.optionsDirty ? 'voiceClone.workspace.reviewFiles' : 'voiceClone.workspace.reviewSamples') }}</button></div>
    <VoiceFilesPanel v-if="step === 'files'" :state="state" :voice="voice" @navigate="emit('navigate', 'samples')" />
    <VoiceSamplesPanel v-else-if="step === 'samples'" :state="state" :voice="voice" />
    <VoiceCoveragePanel v-else-if="step === 'coverage'" :state="state" />
    <VoiceBuildPanel v-else-if="step === 'build'" :state="state" :voice="voice" />
    <details v-if="state.preparation?.sources?.length && step === 'files'" class="text-sm text-text-dim"><summary class="cursor-pointer">{{ t('voiceClone.workspace.sourceReports') }}</summary><ul class="mt-2 space-y-2"><li v-for="source in state.preparation.sources" :key="source.filename">{{ source.filename }} · {{ t('voiceClone.review.sourceReport', { accepted: source.accepted_sec.toFixed(1), rejected: source.rejected_sec.toFixed(1), total: source.duration_sec.toFixed(1) }) }}<span v-if="source.error_code"> · {{ voiceErrorText(source.error_code) }}</span></li></ul></details>
    <details v-if="state.preparation?.warnings?.length && step !== 'compare'" class="text-sm text-text-dim"><summary class="cursor-pointer">{{ t('voiceClone.workspace.screeningLimits') }}</summary><p v-for="warning in state.preparation.warnings" :key="warning" class="mt-2">{{ state.message(warning, 'warning') }}</p></details>
  </div>
</template>

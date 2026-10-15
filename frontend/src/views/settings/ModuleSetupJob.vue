<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useModulesStore, type ModuleId } from '../../stores/modules'
import type { ModuleInstallJob } from '../../api/generated'
import { formatCreated } from '../../composables/formatCreated'

const props = defineProps<{ job: ModuleInstallJob; title: string; working: boolean }>()
const emit = defineEmits<{ control: [action: 'cancel' | 'resume'] }>()
const { t } = useI18n(), store = useModulesStore()
const failedCount = computed(() => props.job.steps.filter(step => step.state === 'failed').length)
const retryable = computed(() => props.job.steps.every(step => step.failure?.retryable !== false))
const hasEvents = computed(() => props.job.steps.some(step => step.events?.length))
const musicWorkspaces: Partial<Record<ModuleId, string>> = { ace_step: '/music/ace-step', yue2: '/music/yue2', heartmula: '/music/heartmula', stable_audio3: '/music/stable-audio3' }
function moduleInfo(id: ModuleId) { return store.inventory?.modules.find(module => module.id === id) }
function jobDate(value: string): string { return formatCreated(Date.parse(value)).full }
function bytes(value: number | null | undefined): string {
  if (value == null) return t('moduleWorkspace.unknownSize')
  if (value < 1000000) return `${value.toLocaleString()} B`
  return value >= 1000000000 ? `${(value / 1000000000).toFixed(1)} GB` : `${(value / 1000000).toFixed(1)} MB`
}
function safeUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined
  try { const url = new URL(value); return url.protocol === 'https:' ? url.href : undefined } catch { return undefined }
}
</script>

<template>
      <article class="space-y-4 rounded-lg border border-border p-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="space-y-1">
            <h4 class="font-semibold text-text">{{ title }}</h4>
            <p :role="job.state === 'failed' ? 'alert' : 'status'" class="text-sm font-semibold" :class="job.state === 'failed' ? 'text-status-failed' : job.state === 'completed' ? 'text-status-done' : 'text-status-queued'">{{ t(`moduleWorkspace.jobs.${job.state}`) }}</p>
            <p class="text-xs text-text-dim">{{ t('moduleWorkspace.activitySummary', { verified: job.steps.filter(item => item.state === 'verified').length, manual: job.steps.filter(item => item.state === 'manual' || item.state === 'skipped').length, total: job.steps.length }) }}</p>
            <p v-if="failedCount" class="text-xs font-semibold text-status-failed">{{ t('moduleWorkspace.failedCount', { count: failedCount }) }}</p>
          </div>
          <dl class="space-y-1 text-xs text-text-dim">
            <div class="flex flex-wrap gap-x-1"><dt>{{ t('moduleWorkspace.started') }}</dt><dd><time :datetime="job.created_at">{{ jobDate(job.created_at) }}</time></dd></div>
            <div class="flex flex-wrap gap-x-1"><dt>{{ t('moduleWorkspace.updated') }}</dt><dd><time :datetime="job.updated_at">{{ jobDate(job.updated_at) }}</time></dd></div>
          </dl>
        </div>
        <p class="text-xs text-text-dim">{{ t(job.download_models ? 'moduleWorkspace.modelsRequested' : 'moduleWorkspace.modelsNotRequested') }}</p>
        <p v-if="job.state === 'completed'" class="text-xs text-text-dim">{{ t('moduleWorkspace.completedHint') }}</p>
        <p v-if="job.current_step != null && job.state === 'running'" class="text-sm text-text">{{ t('moduleWorkspace.currentStep', { current: job.current_step + 1, total: job.steps.length }) }} · {{ job.steps[job.current_step]?.name }}</p>
        <ol class="space-y-3 text-sm">
          <li v-for="(item, index) in job.steps" :key="item.module_id" class="space-y-2 rounded-lg border border-border bg-panel-2/40 p-3">
            <h5 class="font-medium" :class="item.state === 'failed' ? 'text-status-failed' : 'text-text'">{{ index + 1 }}. {{ item.name }} · {{ t(`moduleWorkspace.steps.${item.state}`) }}</h5>
            <p class="text-xs leading-relaxed text-text-dim">{{ item.detail }}</p>
            <div v-if="item.state === 'failed' && item.failure" role="alert" class="space-y-2 border-l-2 border-status-failed pl-3">
              <p class="font-medium text-status-failed">{{ item.failure.summary }}</p>
              <p class="text-sm leading-relaxed text-text">{{ item.failure.recovery }}</p>
            </div>
            <p v-if="item.last_output_at" data-last-output class="text-xs text-text-dim">{{ t('moduleWorkspace.lastOutput') }} <time :datetime="item.last_output_at">{{ jobDate(item.last_output_at) }}</time></p>
            <div v-if="item.download" class="space-y-1 rounded-lg border border-border p-2 text-xs text-text-dim">
              <p class="break-all text-text">{{ item.download.artifact_name }}</p>
              <p>{{ bytes(item.download.bytes_received) }} / {{ bytes(item.download.total_bytes) }} · {{ t('moduleWorkspace.downloadAttempt', { attempt: item.download.attempt, total: item.download.max_attempts }) }}</p>
              <p v-if="job.state === 'running' && item.state === 'running'">{{ item.download.phase === 'retrying' && item.download.retry_after_sec != null ? t('moduleWorkspace.downloadRetry', { seconds: item.download.retry_after_sec }) : t(`moduleWorkspace.downloadPhases.${item.download.phase}`) }}</p>
              <p v-else>{{ t('moduleWorkspace.downloadSaved') }}</p>
            </div>
            <div v-if="['manual', 'skipped'].includes(item.state)" data-manual-instructions class="space-y-3 border-t border-border pt-3">
              <h5 class="font-semibold text-text">{{ t('moduleWorkspace.nextSteps') }}</h5>
              <p class="text-xs text-text-dim">{{ t(store.offline ? 'moduleWorkspace.guidanceOffline' : 'moduleWorkspace.currentGuidance') }}</p>
              <template v-if="moduleInfo(item.module_id)">
                <ol class="list-decimal space-y-3 pl-5">
                  <li v-for="action in moduleInfo(item.module_id)?.actions.filter(action => action.kind !== 'install')" :key="action.label">
                    <p class="font-medium text-text">{{ action.label }}</p><p class="mt-1 text-xs leading-relaxed text-text-dim">{{ action.detail }}</p>
                    <a v-if="safeUrl(action.url)" :href="safeUrl(action.url)" target="_blank" rel="noopener noreferrer" class="inline-block min-h-11 py-3 text-sm text-text underline decoration-accent2">{{ t('moduleWorkspace.openInstructions') }}</a>
                  </li>
                </ol>
                <div v-if="musicWorkspaces[item.module_id]" class="space-y-2">
                  <p class="text-xs leading-relaxed text-text-dim">{{ t('moduleWorkspace.previewInstructions') }}</p>
                  <RouterLink :to="musicWorkspaces[item.module_id] ?? '/music'" class="setup-button inline-flex items-center">{{ t('moduleWorkspace.openWorkspace', { name: item.name }) }}</RouterLink>
                </div>
                <details class="text-xs text-text-dim">
                  <summary class="cursor-pointer py-2 text-text focus-visible:outline-2 focus-visible:outline-accent1">{{ t('moduleWorkspace.currentChecks') }}</summary>
                  <ul class="mt-2 space-y-2"><li v-for="evidence in moduleInfo(item.module_id)?.evidence" :key="evidence.code"><span :class="evidence.verified ? 'text-status-done' : 'text-status-queued'">{{ t(evidence.verified ? 'moduleWorkspace.verified' : 'moduleWorkspace.unverified') }}:</span> {{ evidence.detail }}</li></ul>
                </details>
                <p v-if="!moduleInfo(item.module_id)?.actions.length" class="text-xs text-text-dim">{{ t('moduleWorkspace.noActions') }}</p>
              </template>
              <p v-else class="text-xs text-status-queued">{{ t('moduleWorkspace.guidanceMissing') }}</p>
            </div>
          </li>
        </ol>
        <details data-job-technical class="text-xs text-text-dim">
          <summary class="min-h-11 cursor-pointer py-3 text-text focus-visible:outline-2 focus-visible:outline-accent1">{{ t('moduleWorkspace.technicalDetails') }}</summary>
          <dl class="space-y-2 py-2">
            <div><dt>{{ t('moduleWorkspace.jobId') }}</dt><dd class="break-all font-mono">{{ job.id }}</dd></div>
            <div v-if="job.error_code"><dt>{{ t('moduleWorkspace.errorCode') }}</dt><dd class="break-all font-mono">{{ job.error_code }}</dd></div>
          </dl>
          <ul class="space-y-3">
            <li v-for="item in job.steps" :key="item.module_id" class="space-y-2">
              <p class="font-medium text-text">{{ item.name }}<template v-if="item.failure?.code || item.error_code"> · <code>{{ item.failure?.code ?? item.error_code }}</code></template></p>
              <p v-if="item.failure?.phase">{{ t('moduleWorkspace.failedPhase') }} {{ item.failure.phase }}</p>
              <ol v-if="item.events?.length" class="space-y-2 border-l border-border pl-3">
                <li v-for="(event, index) in item.events" :key="index" data-phase-event><time :datetime="event.at">{{ jobDate(event.at) }}</time> · {{ event.detail }}</li>
              </ol>
            </li>
          </ul>
          <p v-if="!hasEvents" class="py-2">{{ t('moduleWorkspace.noPhaseHistory') }}</p>
        </details>
        <p v-if="job.restart_required" class="text-xs text-status-queued">{{ t('moduleWorkspace.restartHint') }}</p>
        <p v-if="['interrupted', 'cancelled', 'awaiting_manual'].includes(job.state)" class="text-xs leading-relaxed text-text-dim">{{ t('moduleWorkspace.resumeHint') }}</p>
        <p v-if="job.state === 'awaiting_manual'" class="text-sm text-status-queued">{{ t('moduleWorkspace.manualHint') }}</p>
        <div class="flex flex-wrap gap-2">
          <button v-if="job.state === 'queued' || job.state === 'running'" type="button" class="setup-button" :disabled="working" @click="emit('control', 'cancel')">{{ t('moduleWorkspace.cancel') }}</button>
          <button v-if="job.state === 'failed' ? retryable : ['interrupted', 'cancelled', 'awaiting_manual'].includes(job.state)" type="button" class="setup-button" :class="{ 'bg-accent1 text-white': job.state === 'failed' }" :disabled="working || store.busy" @click="emit('control', 'resume')">{{ t(job.state === 'failed' ? 'moduleWorkspace.retry' : 'moduleWorkspace.resume') }}</button>
        </div>
      </article>
</template>

<style scoped>
@reference "../../style.css";
.setup-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

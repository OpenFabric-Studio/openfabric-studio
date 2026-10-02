<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { JobStatus } from '../../types'

defineProps<{ status: JobStatus | 'stopping', label?: string }>()

const { t } = useI18n()

const LABEL_KEYS: Record<JobStatus | 'stopping', string> = {
  stopping: 'generationWorkspace.stopping',
  queued: 'jobStatus.queued',
  running: 'jobStatus.running',
  done: 'jobStatus.done',
  failed: 'jobStatus.failed',
  cancelled: 'jobStatus.cancelled',
}
const COLORS: Record<JobStatus | 'stopping', string> = {
  stopping: 'bg-status-queued/15 text-status-queued',
  queued: 'bg-status-queued/15 text-status-queued',
  running: 'bg-status-running/15 text-status-running',
  done: 'bg-status-done/15 text-status-done',
  failed: 'bg-status-failed/15 text-status-failed',
  cancelled: 'bg-status-cancelled/15 text-status-cancelled',
}
</script>

<template>
  <span class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium" :class="COLORS[status]">
    <span class="h-1.5 w-1.5 rounded-full bg-current"></span>
    {{ label || t(LABEL_KEYS[status]) }}
  </span>
</template>

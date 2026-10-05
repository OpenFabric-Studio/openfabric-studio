<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/audiobookReview'
import type { PauseAnalysisSettings } from '../../api/contracts'
const { t } = useI18n()
const settings = ref<PauseAnalysisSettings | null>(null)
const busy = ref(false), error = ref(''), saved = ref(false)
let alive = true
let controller: AbortController | undefined
onMounted(async () => {
  const request = new AbortController(); controller = request
  try { const result = await api.getPauseSettings(request.signal); if (alive && !request.signal.aborted) settings.value = result }
  catch { if (alive && !request.signal.aborted) error.value = t('audiobookReview.pauseFailed') }
})
async function save() {
  if (!settings.value || busy.value) return
  const request = new AbortController(); controller = request; busy.value = true; error.value = ''; saved.value = false
  try {
    const result = await api.savePauseSettings(settings.value, request.signal)
    if (alive && !request.signal.aborted) { settings.value = result; saved.value = true }
  } catch { if (alive && !request.signal.aborted) error.value = t('audiobookReview.pauseFailed') }
  finally { if (alive && !request.signal.aborted) busy.value = false }
}
onBeforeUnmount(() => { alive = false; controller?.abort() })
</script>
<template>
  <details class="rounded-lg border border-border p-3">
    <summary class="cursor-pointer text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-accent1">{{ t('audiobookReview.pauseTitle') }}</summary>
    <p class="my-3 text-xs text-text-dim">{{ t('audiobookReview.pauseHint') }}</p>
    <div v-if="settings" class="grid gap-3 sm:grid-cols-3">
      <label class="space-y-1 text-xs text-text-dim"><span>{{ t('audiobookReview.energy') }}</span><input v-model.number="settings.energy_ratio" type="number" min="0.01" max="0.5" step="0.01" :disabled="busy" :aria-label="t('audiobookReview.energy')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"></label>
      <label class="space-y-1 text-xs text-text-dim"><span>{{ t('audiobookReview.silence') }}</span><input v-model.number="settings.min_silence_ms" type="number" min="40" max="2000" step="20" :disabled="busy" :aria-label="t('audiobookReview.silence')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"></label>
      <label class="space-y-1 text-xs text-text-dim"><span>{{ t('audiobookReview.padding') }}</span><input v-model.number="settings.padding_ms" type="number" min="0" max="1000" step="20" :disabled="busy" :aria-label="t('audiobookReview.padding')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"></label>
    </div>
    <button v-if="settings" type="button" :disabled="busy" class="mt-3 min-h-11 rounded-lg border border-border px-3 text-xs text-text disabled:opacity-50" @click="save">{{ t('audiobookReview.pauseSave') }}</button>
    <p v-if="error" role="alert" class="mt-2 text-xs text-status-failed">{{ error }}</p>
    <p v-if="saved" role="status" class="mt-2 text-xs text-status-done">{{ t('audiobookReview.pauseSaved') }}</p>
  </details>
</template>

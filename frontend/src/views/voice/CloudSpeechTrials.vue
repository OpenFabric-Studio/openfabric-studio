<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { CloudSpeechTrial } from '../../api/contracts'
import { listCloudTrials } from '../../api/cloudSpeech'
import { speechTrialAudioUrl } from '../../api/voiceProfiles'
import SpeechAudioPreview from './SpeechAudioPreview.vue'

const props = withDefaults(defineProps<{ profileId: string; active?: boolean; disabled?: boolean; refreshKey?: string }>(), { active: true, disabled: false, refreshKey: '' })
const { t } = useI18n()
const trials = ref<CloudSpeechTrial[]>([])
const selectedId = ref('')
const busy = ref(false)
const failed = ref(false)
const selected = computed(() => trials.value.find(trial => trial.id === selectedId.value))
const audioUrl = computed(() => selected.value ? speechTrialAudioUrl(selected.value.id) : null)
let controller: AbortController | null = null
let generation = 0
let alive = true

function cancel() { generation += 1; controller?.abort(); controller = null; busy.value = false }
async function load() {
  cancel()
  if (!props.active || props.disabled) return
  const token = generation, profileId = props.profileId
  controller = new AbortController()
  busy.value = true
  failed.value = false
  try {
    const result = await listCloudTrials(profileId, controller.signal)
    if (!alive || token !== generation || profileId !== props.profileId || !props.active) return
    trials.value = result.filter(trial => trial.profile_id === profileId && trial.audio_url === speechTrialAudioUrl(trial.id))
    if (!trials.value.some(trial => trial.id === selectedId.value)) selectedId.value = trials.value[0]?.id ?? ''
  } catch {
    if (alive && token === generation && props.active) { failed.value = true; trials.value = []; selectedId.value = '' }
  } finally {
    if (alive && token === generation) { busy.value = false; controller = null }
  }
}
watch(() => [props.profileId, props.refreshKey, props.active, props.disabled], (_, previous) => {
  if (!previous || previous[0] !== props.profileId) { trials.value = []; selectedId.value = '' }
  void load()
}, { immediate: true })
onBeforeUnmount(() => { alive = false; cancel() })
</script>
<template>
  <section class="space-y-3 rounded-lg border border-border bg-panel-2 p-3" :aria-label="t('cloudSpeech.recentTrials')">
    <div class="flex items-center justify-between gap-3">
      <h3 class="text-sm font-medium text-text">{{ t('cloudSpeech.recentTrials') }}</h3>
      <button type="button" :disabled="busy || disabled || !active" class="min-h-11 rounded-lg border border-border px-3 text-xs text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" @click="load">{{ t('cloudSpeech.recentRefresh') }}</button>
    </div>
    <p v-if="failed" role="alert" class="text-xs text-status-failed">{{ t('cloudSpeech.recentFailed') }}</p>
    <p v-else-if="!busy && !trials.length" class="text-xs text-text-dim">{{ t('cloudSpeech.recentEmpty') }}</p>
    <label v-if="trials.length" class="block space-y-1">
      <span class="text-xs text-text-dim">{{ t('cloudSpeech.chooseTrial') }}</span>
      <select v-model="selectedId" :disabled="disabled || !active" :aria-label="t('cloudSpeech.chooseTrial')" class="min-h-11 w-full rounded-lg border border-border bg-panel p-2 text-xs text-text">
        <option v-for="trial in trials" :key="trial.id" :value="trial.id">{{ trial.created_at }} · {{ trial.provenance.model }} {{ trial.provenance.voice ?? '' }}</option>
      </select>
    </label>
    <template v-if="selected && audioUrl">
      <SpeechAudioPreview :src="audioUrl" :label="t('cloudSpeech.recentAudio')" :active="active" />
      <a :href="audioUrl" download class="inline-flex min-h-11 items-center text-xs text-accent1 underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('cloudSpeech.recentDownload') }}</a>
      <p class="break-words text-xs text-text-dim">{{ t('cloudSpeech.provenance', { model: selected.provenance.model, receipt: selected.provenance.receipt_id }) }}</p>
      <p v-if="selected.provenance.actual_cost_usd != null" class="text-xs text-text-dim">{{ t('cloudSpeech.cost', { cost: selected.provenance.actual_cost_usd }) }}</p>
    </template>
  </section>
</template>

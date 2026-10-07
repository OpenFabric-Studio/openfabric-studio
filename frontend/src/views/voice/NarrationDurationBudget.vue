<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { durationGuidance } from '../../api/narrationTiming'
import type { NarrationDurationGuidance, NarrationDurationRequest, NarrationDurationPassageSource } from '../../api/contracts'
const props = withDefaults(defineProps<{ profileId: string; language: string; text: string; active?: boolean; refreshKey?: string; passageSource?: NarrationDurationPassageSource }>(), { active: true, refreshKey: '' })
const { t } = useI18n()
const targets = [15, 30, 60, 90] as const
const target = ref<NarrationDurationRequest['target_seconds']>(30)
const result = ref<NarrationDurationGuidance | null>(null), loading = ref(false), failed = ref(false)
let alive = true, generation = 0, timer: ReturnType<typeof setTimeout> | undefined, controller: AbortController | undefined
function stop() { generation++; if (timer !== undefined) clearTimeout(timer); timer = undefined; controller?.abort(); controller = undefined; loading.value = false }
function schedule() {
  stop(); result.value = null; failed.value = false
  if (!props.active || !/^[0-9a-f]{32}$/.test(props.profileId)) return
  const token = generation
  timer = setTimeout(() => { timer = undefined; void load(token) }, 300)
}
async function load(token: number) {
  const request = new AbortController(); controller = request; loading.value = true
  const current = () => alive && token === generation && !request.signal.aborted && props.active
  try {
    const response = await durationGuidance({ profile_id: props.profileId, language: props.language || 'en', text: props.text, target_seconds: target.value,
      ...(props.passageSource ? { passage_source: props.passageSource } : {}) }, request.signal)
    if (current() && response.target_seconds === target.value) result.value = response
  } catch { if (current()) failed.value = true }
  finally { if (current()) { loading.value = false; controller = undefined } }
}
const range = computed(() => result.value?.estimated_min_ms != null && result.value.estimated_max_ms != null ? `${(result.value.estimated_min_ms / 1000).toFixed(1)}–${(result.value.estimated_max_ms / 1000).toFixed(1)}` : '')
watch(() => [props.profileId, props.language, props.text, props.active, props.refreshKey, JSON.stringify(props.passageSource), target.value], schedule, { immediate: true })
onBeforeUnmount(() => { alive = false; stop() })
</script>
<template>
  <div class="space-y-2 rounded-lg border border-border bg-panel-2 p-3" data-duration-budget>
    <label class="flex flex-wrap items-center gap-3 text-xs text-text-dim"><span>{{ t('audiobookReview.durationTarget') }}</span><select v-model.number="target" :aria-label="t('audiobookReview.durationTarget')" class="min-h-11 rounded-lg border border-border bg-panel px-3 text-sm text-text"><option v-for="seconds in targets" :key="seconds" :value="seconds">{{ t('audiobookReview.durationSeconds', { seconds }) }}</option></select></label>
    <p v-if="loading" role="status" class="text-xs text-text-dim">{{ t('audiobookReview.durationLoading') }}</p>
    <template v-else-if="result?.state === 'approximate'">
      <p class="text-xs text-text">{{ t('audiobookReview.durationApproximate', { range, count: result.measurement_count }) }}</p>
      <p class="text-xs text-text-dim">{{ t('audiobookReview.durationCharacters', { count: result.suggested_characters, seconds: target }) }}</p>
    </template>
    <p v-else-if="result" class="text-xs text-text-dim">{{ t(result.reason === 'model_unverified' ? 'audiobookReview.durationModelUnverified' : 'audiobookReview.durationNeedsTakes') }}</p>
    <p v-else-if="failed" role="status" class="text-xs text-text-dim">{{ t('audiobookReview.durationFailed') }}</p>
    <p class="text-xs text-text-dim">{{ t('audiobookReview.durationHint') }}</p>
  </div>
</template>

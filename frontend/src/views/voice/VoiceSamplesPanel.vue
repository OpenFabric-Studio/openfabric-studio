<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { voiceReferenceUrl, voiceSampleUrl, type VoiceProfile } from '../../api/voices'
import WaveformPlayer from '../../components/shared/WaveformPlayer.vue'
import type { VoicePreparationState } from './useVoicePreparation'
import type { VoiceSegment } from '../../api/contracts'
import { isSelectableVoiceSample } from './voiceWorkspace'

const props = defineProps<{ state: VoicePreparationState; voice: VoiceProfile }>()
const { t } = useI18n()
const search = ref('')
const filter = ref('all')
const source = ref('all')
const sort = ref('time')
const page = ref(1)
const pageSize = ref(25)
const expanded = ref(props.state.segments[0]?.id ?? '')
const sourceNames = computed(() => [...new Set(props.state.segments.map((segment) => segment.source_filename))].sort((left, right) => left.localeCompare(right)))
const filtered = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return props.state.segments.filter((segment) => segment.source_filename.toLocaleLowerCase().includes(query)
    && (source.value === 'all' || segment.source_filename === source.value)
    && (filter.value === 'all' || filter.value === 'accepted' && segment.accepted || filter.value === 'rejected' && !segment.accepted || filter.value === 'selected' && props.state.selectedIds.includes(segment.id) || filter.value === 'cleaned' && segment.has_cleaned))
    .sort((left, right) => {
      const fallback = left.source_filename.localeCompare(right.source_filename) || left.start_sec - right.start_sec
      if (sort.value === 'score') return right.score - left.score || fallback
      if (sort.value === 'duration') return right.duration_sec - left.duration_sec || fallback
      if (sort.value === 'level') return right.level_db - left.level_db || fallback
      return fallback
    })
})
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize.value)))
const visible = computed(() => filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
watch([search, filter, source, sort, pageSize], () => { page.value = 1 })
watch(pages, (count) => { page.value = Math.min(page.value, count) })
const visibleAccepted = computed(() => visible.value.filter(isSelectableVoiceSample))
function selectVisible(selected: boolean) {
  if (props.state.busy) return
  const ids = (selected ? visibleAccepted.value : visible.value.filter((segment) => segment.accepted)).map((segment) => segment.id)
  props.state.selectedIds = selected ? [...new Set([...props.state.selectedIds, ...ids])] : props.state.selectedIds.filter((id) => !ids.includes(id))
}
function selectSample(segment: VoiceSegment, event: Event) {
  if (!(event.target instanceof HTMLInputElement) || props.state.busy) return
  if (event.target.checked && isSelectableVoiceSample(segment)) props.state.selectedIds = [...new Set([...props.state.selectedIds, segment.id])]
  else props.state.selectedIds = props.state.selectedIds.filter((id) => id !== segment.id)
}
function cleanVisible(cleaned: boolean) {
  if (props.state.busy) return
  const ids = visibleAccepted.value.filter((segment) => segment.has_cleaned && props.state.selectedIds.includes(segment.id)).map((segment) => segment.id)
  props.state.cleanedIds = cleaned ? [...new Set([...props.state.cleanedIds, ...ids])] : props.state.cleanedIds.filter((id) => !ids.includes(id))
}
function title(filename: string, start: number, end: number) { return `${filename} · ${start.toFixed(1)}–${end.toFixed(1)} ${t('common.secondsUnit')}` }
</script>

<template>
  <section class="space-y-4">
    <div><h3 class="text-base font-semibold text-text">{{ t('voiceClone.review.segments') }}</h3><p class="mt-1 text-sm text-text-dim">{{ t('voiceClone.workspace.samplesIntro') }}</p></div>
    <p v-if="!state.segments.length" class="rounded-lg bg-panel-2 p-4 text-sm text-text-dim">{{ t('voiceClone.workspace.noSamples') }}</p>
    <template v-else>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.workspace.searchSamples') }}</span><input v-model="search" type="search" :aria-label="t('voiceClone.workspace.searchSamples')" class="w-full rounded-lg border border-border bg-panel-2 p-2" /></label>
        <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.workspace.sampleStatus') }}</span><select v-model="filter" :aria-label="t('voiceClone.workspace.sampleStatus')" class="w-full rounded-lg border border-border bg-panel-2 p-2"><option value="all">{{ t('voiceClone.workspace.allSamples') }}</option><option value="accepted">{{ t('voiceClone.review.accepted') }}</option><option value="rejected">{{ t('voiceClone.review.rejected') }}</option><option value="selected">{{ t('voiceClone.workspace.selected') }}</option><option value="cleaned">{{ t('voiceClone.workspace.withCleanup') }}</option></select></label>
        <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.workspace.source') }}</span><select v-model="source" :aria-label="t('voiceClone.workspace.source')" class="w-full rounded-lg border border-border bg-panel-2 p-2"><option value="all">{{ t('voiceClone.workspace.allSources') }}</option><option v-for="filename in sourceNames" :key="filename" :value="filename">{{ filename }}</option></select></label>
        <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.workspace.sort') }}</span><select v-model="sort" :aria-label="t('voiceClone.workspace.sortSamples')" class="w-full rounded-lg border border-border bg-panel-2 p-2"><option value="time">{{ t('voiceClone.workspace.sourceTime') }}</option><option value="score">{{ t('voiceClone.workspace.highestScreening') }}</option><option value="duration">{{ t('voiceClone.workspace.longestFirst') }}</option><option value="level">{{ t('voiceClone.workspace.loudestFirst') }}</option></select></label>
      </div>
      <p class="text-sm text-text-dim" role="status">{{ t('voiceClone.workspace.sampleCount', { visible: visible.length, total: state.segments.length, selected: state.selectedIds.length, seconds: state.selectedSeconds.toFixed(1) }) }}</p>
      <p class="text-sm text-text-dim">{{ t('voiceClone.workspace.selectionBudget', { minutes: state.budgetValid ? state.durationMinutes.toFixed(1) : '—' }) }}</p>
      <p v-if="!state.budgetValid" role="alert" class="text-sm text-status-failed">{{ t('voiceClone.workspace.invalidBudget') }}</p>
      <p v-else-if="!state.withinBudget" role="alert" class="text-sm text-status-failed">{{ t('voiceClone.workspace.overBudget', { minutes: state.durationMinutes.toFixed(1) }) }}</p>
      <p v-if="state.selectedIds.length > 1000" role="alert" class="text-sm text-status-failed">{{ t('voiceClone.workspace.tooManySamples') }}</p>
      <div class="flex flex-wrap items-center gap-3"><label class="flex items-center gap-2 text-sm text-text"><span>{{ t('voiceClone.workspace.pageSize') }}</span><select v-model.number="pageSize" class="rounded-lg border border-border bg-panel-2 p-2"><option v-for="size in [25, 50, 100]" :key="size" :value="size">{{ size }}</option></select></label><button type="button" :disabled="page === 1" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="page--">{{ t('voiceClone.workspace.previousPage') }}</button><span class="text-sm text-text-dim" role="status">{{ t('voiceClone.workspace.page', { page, pages, matches: filtered.length }) }}</span><button type="button" :disabled="page === pages" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="page++">{{ t('voiceClone.workspace.nextPage') }}</button></div>
      <div class="flex flex-wrap gap-2"><button type="button" :disabled="state.busy || !visibleAccepted.length" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="selectVisible(true)">{{ t('voiceClone.workspace.selectVisible') }}</button><button type="button" :disabled="state.busy || !visible.some((segment) => segment.accepted && state.selectedIds.includes(segment.id))" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="selectVisible(false)">{{ t('voiceClone.workspace.clearVisible') }}</button><button type="button" :disabled="state.busy || !visibleAccepted.some((segment) => segment.has_cleaned && state.selectedIds.includes(segment.id))" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="cleanVisible(true)">{{ t('voiceClone.workspace.cleanVisible') }}</button><button type="button" :disabled="state.busy || !visibleAccepted.some((segment) => segment.has_cleaned && state.selectedIds.includes(segment.id))" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="cleanVisible(false)">{{ t('voiceClone.workspace.originalVisible') }}</button></div>
      <p class="text-sm text-text-dim">{{ t('voiceClone.workspace.bulkHint') }}</p>
      <div class="space-y-2">
        <article v-for="segment in visible" :key="segment.id" class="space-y-3 rounded-lg border border-border bg-panel-2 p-3">
          <div class="flex flex-wrap items-start justify-between gap-3"><label class="flex min-w-0 flex-1 items-start gap-2 text-sm text-text"><input :checked="state.selectedIds.includes(segment.id)" type="checkbox" :value="segment.id" :disabled="state.busy || !isSelectableVoiceSample(segment) && !state.selectedIds.includes(segment.id)" @change="selectSample(segment, $event)" :aria-label="t('voiceClone.review.includeSegment', { id: segment.id })" class="mt-1" /><span class="break-all">{{ title(segment.source_filename, segment.start_sec, segment.end_sec) }}<span class="block text-sm" :class="segment.accepted ? 'text-status-done' : 'text-text-dim'">{{ t(segment.accepted ? 'voiceClone.review.accepted' : 'voiceClone.review.rejected') }} · {{ t(state.cleanedIds.includes(segment.id) ? 'voiceClone.review.cleaned' : 'voiceClone.review.original') }}</span></span></label><button type="button" :aria-label="t('voiceClone.workspace.auditionSample', { sample: title(segment.source_filename, segment.start_sec, segment.end_sec) })" :aria-expanded="expanded === segment.id" class="rounded-lg border border-border px-3 py-2 text-sm text-text" @click="expanded = expanded === segment.id ? '' : segment.id">{{ t(expanded === segment.id ? 'voiceClone.workspace.hideAudition' : 'voiceClone.workspace.audition') }}</button></div>
          <p v-if="segment.accepted && !isSelectableVoiceSample(segment)" class="text-sm text-status-failed">{{ t('voiceClone.workspace.unsupportedClipDuration') }}</p>
          <p v-for="reason in segment.reasons" :key="reason" class="text-sm text-text-dim">{{ state.message(reason, 'reason') }}</p>
          <div v-if="expanded === segment.id" class="space-y-3">
            <div class="grid gap-3 sm:grid-cols-2"><div class="space-y-1"><p class="text-sm text-text-dim">{{ t('voiceClone.review.original') }}</p><WaveformPlayer :src="voiceSampleUrl(voice.id, segment.id, 'original', state.preparation?.revision)" /></div><div v-if="segment.has_cleaned" class="space-y-1"><p class="text-sm text-text-dim">{{ t('voiceClone.review.cleaned') }}</p><WaveformPlayer :src="voiceSampleUrl(voice.id, segment.id, 'cleaned', state.preparation?.revision)" /><label class="flex items-center gap-2 text-sm text-text"><input v-model="state.cleanedIds" type="checkbox" :value="segment.id" :disabled="state.busy || !state.selectedIds.includes(segment.id)" :aria-label="t('voiceClone.review.useCleaned', { id: segment.id })" />{{ t('voiceClone.review.useCleanedLabel') }}</label></div></div>
            <details class="text-sm text-text-dim"><summary class="cursor-pointer">{{ t('voiceClone.workspace.measurements') }}</summary><p class="mt-2">{{ t('voiceClone.review.measurements', { level: segment.level_db.toFixed(1), peak: segment.peak.toFixed(3), periodicity: segment.periodicity.toFixed(2), score: segment.score.toFixed(2), clipped: (segment.clipped_fraction * 100).toFixed(2) }) }}</p><p class="mt-2">{{ t('voiceClone.review.measurementHint') }}</p></details>
          </div>
        </article>
        <p v-if="!visible.length" class="text-sm text-text-dim">{{ t('voiceClone.workspace.noMatches') }}</p>
      </div>
      <div class="space-y-3 border-t border-border pt-4"><h4 class="text-sm font-medium text-text">{{ t('voiceClone.review.reference') }}</h4><p class="text-sm text-text-dim">{{ t('voiceClone.workspace.referenceHint') }}</p>
        <div v-for="reference in state.references" :key="reference.id" class="space-y-2 rounded-lg bg-panel-2 p-3"><label class="flex items-center gap-2 text-sm text-text"><input v-model="state.referenceId" type="radio" :name="`reference-${voice.id}`" :value="reference.id" :disabled="state.busy || !state.selectedIds.includes(reference.segment_id)" />{{ title(reference.source_filename, reference.start_sec, reference.end_sec) }}</label><WaveformPlayer :src="voiceReferenceUrl(voice.id, reference.id, state.preparation?.revision)" /></div>
        <p class="text-sm text-text-dim">{{ t('voiceClone.review.selectedSeconds', { seconds: state.selectedSeconds.toFixed(1) }) }}</p><button type="button" :disabled="state.busy || !state.selectionDirty || !state.validSelection || !state.referenceId" class="rounded-lg bg-accent1 px-4 py-2 text-sm text-white disabled:opacity-50" @click="state.saveSelection">{{ t('voiceClone.review.save') }}</button><p v-if="state.selectionDirty" class="text-sm text-text-dim">{{ t('voiceClone.review.saveBeforeBuild') }}</p>
      </div>
    </template>
  </section>
</template>

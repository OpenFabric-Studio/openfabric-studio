<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { parseAlignmentEditRequest, parseAlignmentResult } from '../../api/generated'
import type { AlignmentEditRequest, AlignmentResult } from '../../api/generated'

const props = defineProps<{ alignment: AlignmentResult; reviewed: readonly string[]; disabled?: boolean; stale?: boolean }>()
const emit = defineEmits<{ save: [edit: Omit<AlignmentEditRequest, 'expected_revision'>]; dirty: [value: boolean]; stale: [value: boolean] }>()
const { t } = useI18n()
const draft = ref(parseAlignmentResult(JSON.parse(JSON.stringify(props.alignment)))), reviewed = ref<string[]>([...props.reviewed]), invalid = ref(false)
const diagram = ref<SVGSVGElement | null>(null), selected = ref<number | null>(null)
const incomingKey = computed(() => JSON.stringify([props.alignment, props.reviewed])), baselineKey = ref(incomingKey.value), locallyStale = ref(false)
const dirty = computed(() => JSON.stringify([draft.value, reviewed.value]) !== baselineKey.value)
watch(dirty, value => emit('dirty', value), { immediate: true, flush: 'sync' })
watch(locallyStale, value => emit('stale', value), { flush: 'sync' })
let drag: number | null = null
const sourceEnd = computed(() => Math.max(1, ...(draft.value.anchors ?? []).map(item => item.source_sec), ...(draft.value.phrases ?? []).map(item => item.prepared_end_sec ?? 0)))
const targetEnd = computed(() => Math.max(1, ...(draft.value.anchors ?? []).map(item => item.target_sec), ...(draft.value.phrases ?? []).map(item => item.reference_end_sec ?? 0)))
const points = computed(() => (draft.value.anchors ?? []).map(point => `${point.source_sec / sourceEnd.value * 600},${200 - point.target_sec / targetEnd.value * 200}`).join(' '))
function reset() { baselineKey.value = incomingKey.value; draft.value = parseAlignmentResult(JSON.parse(JSON.stringify(props.alignment))); reviewed.value = [...props.reviewed]; locallyStale.value = false; invalid.value = false; selected.value = null; drag = null }
function reviewRevision() {
  const anchors = draft.value.anchors?.map(anchor => ({ ...anchor })) ?? []
  const prior = new Map(draft.value.phrases?.map(phrase => [phrase.phrase_id, JSON.stringify(phrase)]))
  const unchanged = new Set(props.alignment.phrases?.filter(phrase => prior.get(phrase.phrase_id) === JSON.stringify(phrase)).map(phrase => phrase.phrase_id))
  reviewed.value = reviewed.value.filter(id => unchanged.has(id))
  baselineKey.value = incomingKey.value
  draft.value = parseAlignmentResult(JSON.parse(JSON.stringify({ ...props.alignment, anchors })))
  locallyStale.value = false
}
watch(incomingKey, () => { if (incomingKey.value === baselineKey.value) return; if (dirty.value) locallyStale.value = true; else reset() })
defineExpose({ reviewRevision, reset })
function check() {
  const anchors = draft.value.anchors ?? [], request = draft.value.request
  if (anchors.length < 2) return false
  const min = request.min_duration_ratio ?? .75, max = request.max_duration_ratio ?? 1.5
  return anchors.every((point, index) => {
    if (!Number.isFinite(point.source_sec) || !Number.isFinite(point.target_sec)) return false
    const prior = anchors[index - 1]
    return !prior || point.source_sec > prior.source_sec && point.target_sec > prior.target_sec && (point.target_sec - prior.target_sec) / (point.source_sec - prior.source_sec) >= min - 1e-12 && (point.target_sec - prior.target_sec) / (point.source_sec - prior.source_sec) <= max + 1e-12
  })
}
function save() {
  if (props.disabled || props.stale || locallyStale.value) return
  try {
    if (!check()) throw new TypeError('Invalid alignment')
    const result = parseAlignmentEditRequest({ expected_revision: 1, alignment: draft.value, reviewed_phrase_ids: reviewed.value })
    invalid.value = false; emit('save', { alignment: result.alignment, reviewed_phrase_ids: result.reviewed_phrase_ids })
  } catch { invalid.value = true }
}
function nudge(event: KeyboardEvent, index: number) {
  if (props.disabled) return
  if (event.key === 'Escape') { event.preventDefault(); reset(); return }
  if (event.key === 'Enter') { event.preventDefault(); save(); return }
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return
  const point = draft.value.anchors?.[index]
  if (!point) return
  event.preventDefault(); const amount = event.shiftKey ? .1 : .01
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') point.source_sec = Math.max(0, Math.round((point.source_sec + (event.key === 'ArrowLeft' ? -amount : amount)) * 1000) / 1000)
  else point.target_sec = Math.max(0, Math.round((point.target_sec + (event.key === 'ArrowDown' ? -amount : amount)) * 1000) / 1000)
}
function startDrag(event: PointerEvent, index: number) {
  if (props.disabled || !(event.currentTarget instanceof SVGCircleElement)) return
  event.preventDefault(); drag = index; selected.value = index; event.currentTarget.setPointerCapture(event.pointerId)
}
function moveDrag(event: PointerEvent) {
  const point = drag === null ? undefined : draft.value.anchors?.[drag], bounds = diagram.value?.getBoundingClientRect()
  if (!point || !bounds?.width || !bounds.height || props.disabled) return
  const source = sourceEnd.value, target = targetEnd.value
  point.source_sec = Math.max(0, Math.min(source, (event.clientX - bounds.left) / bounds.width * source))
  point.target_sec = Math.max(0, Math.min(target, (bounds.bottom - event.clientY) / bounds.height * target))
}
function addAnchor() {
  if (props.disabled) return
  draft.value.anchors ??= []
  const anchors = draft.value.anchors
  if (!anchors.length) { anchors.push({ source_sec: 0, target_sec: 0 }, { source_sec: sourceEnd.value, target_sec: targetEnd.value }); return }
  if (anchors.length >= 1000) return
  let at = 1, gap = 0
  for (let index = 1; index < anchors.length; index++) { const left = anchors[index - 1], right = anchors[index]; if (left && right && right.source_sec - left.source_sec > gap) { gap = right.source_sec - left.source_sec; at = index } }
  const left = anchors[at - 1], right = anchors[at]
  if (left && right) anchors.splice(at, 0, { source_sec: (left.source_sec + right.source_sec) / 2, target_sec: (left.target_sec + right.target_sec) / 2 })
}
function review(id: string, event: Event) { if (event.target instanceof HTMLInputElement) reviewed.value = event.target.checked ? [...new Set([...reviewed.value, id])] : reviewed.value.filter(value => value !== id) }
</script>
<template>
  <section class="space-y-4 rounded-xl border border-border p-4">
    <h3 class="font-semibold">{{ t('replacement.alignment') }}</h3><p class="text-sm text-text-dim">{{ t('replacement.anchorHelp') }}</p>
    <svg ref="diagram" viewBox="0 0 600 200" preserveAspectRatio="none" class="h-52 w-full touch-none rounded-lg border border-border bg-panel-2" role="group" :aria-label="t('replacement.anchorDiagram')" @pointermove="moveDrag" @pointerup="drag = null" @pointercancel="drag = null">
      <polyline :points="points" fill="none" stroke="currentColor" class="text-accent2" stroke-width="2" vector-effect="non-scaling-stroke" />
      <circle v-for="(point,index) in draft.anchors ?? []" :key="index" :cx="point.source_sec / sourceEnd * 600" :cy="200 - point.target_sec / targetEnd * 200" r="6" class="cursor-move fill-accent2 stroke-panel focus:stroke-white" role="button" :tabindex="disabled ? -1 : 0" :aria-label="t('replacement.anchorLabel',{index:index + 1,source:point.source_sec.toFixed(3),target:point.target_sec.toFixed(3)})" @focus="selected = index" @keydown="nudge($event,index)" @pointerdown="startDrag($event,index)" />
    </svg>
    <fieldset :disabled="disabled" class="space-y-3"><div class="max-h-72 space-y-2 overflow-y-auto"><div v-for="(point,index) in draft.anchors ?? []" :key="index" class="grid gap-2 rounded-lg border p-2 sm:grid-cols-3" :class="selected === index ? 'border-accent2' : 'border-border'"><label class="alignment-label"><span>{{ t('replacement.sourceAnchor',{index:index + 1}) }}</span><input v-model.number="point.source_sec" type="number" min="0" max="7200" step="0.001" class="alignment-input"></label><label class="alignment-label"><span>{{ t('replacement.targetAnchor') }}</span><input v-model.number="point.target_sec" type="number" min="0" max="7200" step="0.001" class="alignment-input"></label><button type="button" class="alignment-button self-end" :disabled="(draft.anchors?.length ?? 0) <= 2" @click="draft.anchors?.splice(index,1)">{{ t('replacement.removeAnchor') }}</button></div></div><button type="button" class="alignment-button" :disabled="(draft.anchors?.length ?? 0) >= 1000" @click="addAnchor">{{ t('replacement.addAnchor') }}</button>
      <h4 class="font-medium">{{ t('replacement.phraseReview') }}</h4><p class="text-xs text-text-dim">{{ t('replacement.reviewHelp') }}</p><div class="max-h-80 space-y-2 overflow-y-auto"><article v-for="phrase in draft.phrases ?? []" :key="phrase.phrase_id" class="space-y-2 rounded-lg border border-border p-3 text-sm"><p>{{ t(`replacement.phraseReasons.${phrase.reason}`) }} · {{ phrase.reference_start_sec?.toFixed(2) ?? '—' }}–{{ phrase.reference_end_sec?.toFixed(2) ?? '—' }} s</p><p class="text-xs text-text-dim">{{ phrase.confidence == null ? t('replacement.confidenceUnknown') : t('replacement.confidence',{value:phrase.confidence.toFixed(2)}) }}</p><label class="flex min-h-11 items-center gap-3"><input type="checkbox" :checked="reviewed.includes(phrase.phrase_id)" @change="review(phrase.phrase_id,$event)">{{ t('replacement.reviewPhrase') }}</label></article></div>
      <p v-if="invalid" role="alert" class="text-sm text-status-failed">{{ t('replacement.invalidAnchors') }}</p><p v-if="locallyStale" role="status" class="text-sm text-status-warning">{{ t('replacement.draftConflict') }}</p><div class="flex flex-wrap gap-3"><button type="button" class="alignment-button" :disabled="stale || locallyStale" @click="save">{{ t('replacement.saveAlignment') }}</button><button type="button" class="alignment-button" @click="reset">{{ t('replacement.resetAlignment') }}</button></div>
    </fieldset>
  </section>
</template>
<style scoped>
@reference '../../style.css';
.alignment-label { @apply flex min-w-0 flex-col gap-1.5 text-xs text-text-dim; }
.alignment-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text; }
.alignment-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent2 disabled:opacity-50; }
</style>

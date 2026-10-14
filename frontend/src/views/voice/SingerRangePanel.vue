<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/singerRanges'
import { ApiError } from '../../api/http'
import type { PlannedNote, PlannedRangeRequest, RangeAssessment, SingerRangeProfile, SingerRangeRequest } from '../../api/contracts'

const props = withDefaults(defineProps<{ artistId: string; active?: boolean }>(), { active: true })
const { t, te } = useI18n()
const id = useId()
const profile = ref<SingerRangeProfile | null>(null), assessment = ref<RangeAssessment | null>(null)
const pending = ref(false), loaded = ref(false), error = ref(''), invalidField = ref(''), notice = ref('')
const errorSummary = ref<HTMLElement | null>(null)
const low = ref<string | number>(''), high = ref<string | number>(''), extended = ref(false)
const extendedLow = ref<string | number>(''), extendedHigh = ref<string | number>('')
const policy = ref<NonNullable<SingerRangeRequest['policy']>>('warn'), chosenKey = ref('')
const operator = ref(''), operatorDeclaration = ref(''), artistDeclaration = ref('')
const plannedMidi = ref<string | number>(60), start = ref<string | number>(0), end = ref<string | number>(1)
const assessedNotes = ref<PlannedNote[]>([]), approvalEvidence = ref(''), approvalDeclaration = ref('')
const outOfRangeNotes = computed(() => assessment.value?.out_of_range_notes ?? [])
const policies = ['warn', 'block', 'needs_approval'] as const
const fieldClass = 'min-h-11 w-full rounded-lg border border-border bg-panel px-3 text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1'
let alive = true, generation = 0
let controller: AbortController | undefined

function number(value: string | number): number | undefined {
  if (typeof value === 'string' && !value.trim()) return undefined
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : undefined
}
function midi(value: string | number): number | undefined {
  const parsed = number(value)
  return parsed !== undefined && Number.isInteger(parsed) && parsed >= 0 && parsed <= 127 ? parsed : undefined
}
function noteName(value: string | number): string {
  const note = midi(value)
  const names = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B']
  return note === undefined ? '—' : `${names[note % 12] ?? ''}${Math.floor(note / 12) - 1}`
}
function current(token: number, signal: AbortSignal): boolean {
  return alive && props.active && token === generation && !signal.aborted
}
async function showError(message: string, field = ''): Promise<void> {
  const token = generation
  error.value = message; invalidField.value = field
  await nextTick()
  if (alive && props.active && token === generation) errorSummary.value?.focus()
}
function errorText(cause: unknown): string {
  const key = `singerRange.errors.${cause instanceof ApiError ? cause.message : ''}`
  return te(key) ? t(key) : t('singerRange.errors.unknown')
}
function warningText(code: string): string {
  const key = `singerRange.warnings.${code}`
  return te(key) ? t(key) : t('singerRange.warnings.unknown')
}
function populate(saved: SingerRangeProfile | null): void {
  profile.value = saved
  low.value = saved?.comfortable_low_midi ?? ''; high.value = saved?.comfortable_high_midi ?? ''
  extended.value = saved?.extended_low_midi != null && saved.extended_high_midi != null
  extendedLow.value = saved?.extended_low_midi ?? ''; extendedHigh.value = saved?.extended_high_midi ?? ''
  policy.value = saved?.policy ?? 'warn'; chosenKey.value = saved?.chosen_key ?? ''
  operator.value = saved?.operator_label ?? ''; operatorDeclaration.value = saved?.operator_declaration ?? ''
  artistDeclaration.value = saved?.artist_confirmation_declaration ?? ''
}
async function reload(): Promise<void> {
  if (!alive || !props.active || pending.value || !props.artistId) return
  const token = ++generation
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  pending.value = true; loaded.value = false; error.value = ''; invalidField.value = ''; notice.value = ''
  assessment.value = null; assessedNotes.value = []
  try {
    const response = await api.getSingerRange(props.artistId, signal)
    if (current(token, signal)) { populate(response); loaded.value = true }
  } catch (cause) { if (current(token, signal)) await showError(errorText(cause)) }
  finally { if (current(token, signal)) pending.value = false }
}
async function perform<T>(request: (signal: AbortSignal) => Promise<T>, accept: (response: T) => void): Promise<void> {
  if (!alive || !props.active || pending.value || !loaded.value) return
  const token = ++generation
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  pending.value = true; error.value = ''; invalidField.value = ''; notice.value = ''
  try {
    const response = await request(signal)
    if (current(token, signal)) accept(response)
  } catch (cause) { if (current(token, signal)) await showError(errorText(cause)) }
  finally { if (current(token, signal)) pending.value = false }
}
async function save(): Promise<void> {
  if (!loaded.value || pending.value) return
  const lower = midi(low.value), upper = midi(high.value)
  if (lower === undefined || upper === undefined || lower > upper) return showError(t('singerRange.invalidBounds'), 'comfortable-low')
  const lowerExtended = extended.value ? midi(extendedLow.value) : null
  const upperExtended = extended.value ? midi(extendedHigh.value) : null
  if (extended.value && (lowerExtended === undefined || upperExtended === undefined || lowerExtended === null || upperExtended === null || lowerExtended > lower || upperExtended < upper)) {
    return showError(t('singerRange.invalidExtended'), 'extended-low')
  }
  if (!operator.value.trim() || operatorDeclaration.value.trim().length < 20 || artistDeclaration.value.trim().length < 20) {
    return showError(t('singerRange.declarationRequired'), !operator.value.trim() ? 'operator' : operatorDeclaration.value.trim().length < 20 ? 'operator-declaration' : 'artist-declaration')
  }
  const request: SingerRangeRequest = { comfortable_low_midi: lower, comfortable_high_midi: upper,
    extended_low_midi: lowerExtended, extended_high_midi: upperExtended, policy: policy.value, chosen_key: chosenKey.value.trim(),
    operator_label: operator.value.trim(), operator_declaration: operatorDeclaration.value.trim(), artist_confirmation_declaration: artistDeclaration.value.trim() }
  await perform(signal => api.saveSingerRange(props.artistId, request, signal), response => {
    populate(response); assessment.value = null; assessedNotes.value = []; notice.value = t('singerRange.saved')
  })
}
function acceptAssessment(response: RangeAssessment, notes: PlannedNote[]): void {
  assessment.value = response; assessedNotes.value = notes; notice.value = t('singerRange.assessmentDone')
}
async function assess(): Promise<void> {
  if (!loaded.value || pending.value) return
  const note = midi(plannedMidi.value), from = number(start.value), to = number(end.value)
  if (note === undefined || from === undefined || to === undefined || from < 0 || from >= to || to > 7200) {
    return showError(t('singerRange.invalidNote'), 'planned-note')
  }
  const notes: PlannedNote[] = [{ midi_note: note, start_sec: from, end_sec: to }]
  await perform(signal => api.assessSingerRange(props.artistId, { notes }, signal), response => acceptAssessment(response, notes))
}
const canApprove = computed(() => assessment.value?.decision === 'needs_approval' && assessment.value.analysis_status === 'observed'
  && assessment.value.profile_version_id === profile.value?.id && !!operator.value.trim()
  && !!approvalEvidence.value.trim() && approvalDeclaration.value.trim().length >= 20 && assessedNotes.value.length > 0)
async function approve(): Promise<void> {
  const saved = assessment.value
  if (!canApprove.value || !saved?.profile_version_id) return
  const request: PlannedRangeRequest = { notes: [...assessedNotes.value], approval: {
    profile_version_id: saved.profile_version_id, assessment_sha256: saved.assessment_sha256,
    operator_label: operator.value.trim(), evidence_ref: approvalEvidence.value.trim(), artist_confirmation_declaration: approvalDeclaration.value.trim(),
  } }
  await perform(signal => api.assessSingerRange(props.artistId, request, signal), response => acceptAssessment(response, request.notes))
}
function focusInvalid(): void { document.getElementById(`${id}-${invalidField.value}`)?.focus() }
watch([plannedMidi, start, end], () => { assessment.value = null; assessedNotes.value = []; approvalEvidence.value = ''; approvalDeclaration.value = '' })
watch(() => [props.artistId, props.active] as const, (values, previous) => {
  generation++; controller?.abort(); pending.value = false; loaded.value = false; assessment.value = null; assessedNotes.value = []
  error.value = ''; invalidField.value = ''; notice.value = ''; approvalEvidence.value = ''; approvalDeclaration.value = ''
  if (values[0] !== previous?.[0]) { populate(null); plannedMidi.value = 60; start.value = 0; end.value = 1 }
  if (props.active) void reload()
}, { immediate: true })
onBeforeUnmount(() => { alive = false; generation++; controller?.abort() })
</script>

<template>
  <section v-if="active" class="space-y-4 rounded-lg border border-border bg-panel-2 p-3" :aria-label="t('singerRange.title')" :aria-busy="pending">
    <h4 class="font-medium text-text">{{ t('singerRange.title') }}</h4>
    <p class="text-sm text-text-dim">{{ t('singerRange.intro') }}</p>
    <p class="text-sm text-text-dim">{{ t('singerRange.observationHint') }}</p>
    <p class="text-sm text-text-dim">{{ t('singerRange.actorHint') }}</p>
    <div v-if="error" ref="errorSummary" role="alert" tabindex="-1" class="space-y-1 rounded-lg border border-status-failed p-3 text-sm text-status-failed">
      <p class="font-medium">{{ t('singerRange.errorTitle') }}</p><p>{{ error }}</p>
      <a v-if="invalidField" :href="`#${id}-${invalidField}`" class="inline-block min-h-11 underline" @click.prevent="focusInvalid">{{ t('singerRange.goToField') }}</a>
    </div>
    <p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>
    <p v-if="pending" role="status" class="text-sm text-text-dim">{{ t('singerRange.loading') }}</p>
    <p v-if="profile" class="break-words text-sm text-text">{{ t('singerRange.version') }} {{ profile.revision }} · {{ profile.id }}</p>
    <p v-else-if="loaded" class="text-sm text-text-dim">{{ t('singerRange.empty') }}</p>
    <button type="button" :disabled="pending" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="reload">{{ t('singerRange.refresh') }}</button>
    <form novalidate class="space-y-3" @submit.prevent="save">
      <fieldset :disabled="pending || !loaded" class="space-y-3">
        <p class="text-sm text-text-dim">{{ t('singerRange.noteHint') }}</p>
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="space-y-1 text-sm text-text"><span>{{ t('singerRange.comfortableLow') }} · {{ noteName(low) }}</span><input :id="`${id}-comfortable-low`" v-model="low" type="number" min="0" max="127" step="1" :aria-label="t('singerRange.comfortableLow')" :aria-invalid="invalidField === 'comfortable-low'" :aria-describedby="invalidField === 'comfortable-low' ? `${id}-comfortable-error` : undefined" :class="fieldClass" /></label>
          <label class="space-y-1 text-sm text-text"><span>{{ t('singerRange.comfortableHigh') }} · {{ noteName(high) }}</span><input v-model="high" type="number" min="0" max="127" step="1" :aria-label="t('singerRange.comfortableHigh')" :aria-invalid="invalidField === 'comfortable-low'" :aria-describedby="invalidField === 'comfortable-low' ? `${id}-comfortable-error` : undefined" :class="fieldClass" /></label>
        </div>
        <p v-if="invalidField === 'comfortable-low'" :id="`${id}-comfortable-error`" class="text-sm text-status-failed">{{ error }}</p>
        <label class="flex min-h-11 items-center gap-2 text-sm text-text"><input v-model="extended" type="checkbox" />{{ t('singerRange.useExtended') }}</label>
        <div v-if="extended" class="grid gap-3 sm:grid-cols-2">
          <label class="space-y-1 text-sm text-text"><span>{{ t('singerRange.extendedLow') }} · {{ noteName(extendedLow) }}</span><input :id="`${id}-extended-low`" v-model="extendedLow" type="number" min="0" max="127" step="1" :aria-label="t('singerRange.extendedLow')" :aria-invalid="invalidField === 'extended-low'" :class="fieldClass" /></label>
          <label class="space-y-1 text-sm text-text"><span>{{ t('singerRange.extendedHigh') }} · {{ noteName(extendedHigh) }}</span><input v-model="extendedHigh" type="number" min="0" max="127" step="1" :aria-label="t('singerRange.extendedHigh')" :aria-invalid="invalidField === 'extended-low'" :class="fieldClass" /></label>
        </div>
        <p v-if="invalidField === 'extended-low'" class="text-sm text-status-failed">{{ error }}</p>
        <label class="block space-y-1 text-sm text-text"><span>{{ t('singerRange.policy') }}</span><select v-model="policy" :aria-label="t('singerRange.policy')" :class="fieldClass"><option v-for="option in policies" :key="option" :value="option">{{ t(`singerRange.policies.${option}`) }}</option></select></label>
        <label class="block space-y-1 text-sm text-text"><span>{{ t('singerRange.chosenKey') }}</span><input v-model="chosenKey" maxlength="60" :aria-label="t('singerRange.chosenKey')" :class="fieldClass" /></label>
        <p class="text-sm text-text-dim">{{ t('singerRange.keyHint') }}</p>
        <label class="block space-y-1 text-sm text-text"><span>{{ t('singerRange.operator') }}</span><input :id="`${id}-operator`" v-model="operator" maxlength="120" :aria-label="t('singerRange.operator')" :aria-invalid="invalidField === 'operator'" :class="fieldClass" /></label>
        <label class="block space-y-1 text-sm text-text"><span>{{ t('singerRange.operatorDeclaration') }}</span><textarea :id="`${id}-operator-declaration`" v-model="operatorDeclaration" rows="2" maxlength="2000" :aria-label="t('singerRange.operatorDeclaration')" :aria-invalid="invalidField === 'operator-declaration'" :class="fieldClass" /></label>
        <label class="block space-y-1 text-sm text-text"><span>{{ t('singerRange.artistDeclaration') }}</span><textarea :id="`${id}-artist-declaration`" v-model="artistDeclaration" rows="2" maxlength="2000" :aria-label="t('singerRange.artistDeclaration')" :aria-invalid="invalidField === 'artist-declaration'" :class="fieldClass" /></label>
        <p v-if="invalidField === 'operator' || invalidField.endsWith('-declaration')" class="text-sm text-status-failed">{{ error }}</p>
        <p class="text-sm text-text-dim">{{ t('singerRange.immutable') }}</p>
        <button type="submit" :disabled="pending || !loaded" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50">{{ t('singerRange.save') }}</button>
      </fieldset>
    </form>
    <form novalidate class="space-y-3" @submit.prevent="assess">
      <h5 class="text-sm font-medium text-text">{{ t('singerRange.plannedTitle') }}</h5><p class="text-sm text-text-dim">{{ t('singerRange.plannedHint') }}</p>
      <fieldset :disabled="pending || !loaded" class="grid gap-3 sm:grid-cols-3">
        <label class="space-y-1 text-sm text-text"><span>{{ t('singerRange.plannedNote') }} · {{ noteName(plannedMidi) }}</span><input :id="`${id}-planned-note`" v-model="plannedMidi" type="number" min="0" max="127" step="1" :aria-label="t('singerRange.plannedNote')" :aria-invalid="invalidField === 'planned-note'" :class="fieldClass" /></label>
        <label class="space-y-1 text-sm text-text"><span>{{ t('singerRange.start') }}</span><input v-model="start" type="number" min="0" max="7200" step="0.01" :aria-label="t('singerRange.start')" :aria-invalid="invalidField === 'planned-note'" :class="fieldClass" /></label>
        <label class="space-y-1 text-sm text-text"><span>{{ t('singerRange.end') }}</span><input v-model="end" type="number" min="0" max="7200" step="0.01" :aria-label="t('singerRange.end')" :aria-invalid="invalidField === 'planned-note'" :class="fieldClass" /></label>
        <button type="submit" :disabled="pending || !loaded" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50">{{ t('singerRange.assess') }}</button>
      </fieldset>
      <p v-if="invalidField === 'planned-note'" class="text-sm text-status-failed">{{ error }}</p>
    </form>
    <div v-if="assessment" class="space-y-2 rounded-lg border border-border p-3 text-sm text-text" role="status">
      <p class="font-medium">{{ t(`singerRange.decisions.${assessment.decision}`) }}</p><p>{{ t(`singerRange.analysis.${assessment.analysis_status}`) }}</p>
      <p v-if="outOfRangeNotes.length">{{ t('singerRange.outOfRange') }}: {{ outOfRangeNotes.map(note => `${noteName(note.midi_note)} (${note.midi_note})`).join(', ') }}</p>
      <ul class="space-y-1 text-text-dim"><li v-for="warning in assessment.warnings" :key="warning">{{ warningText(warning) }}</li></ul>
      <form v-if="assessment.decision === 'needs_approval' && assessment.analysis_status === 'observed'" class="space-y-3" @submit.prevent="approve">
        <p class="text-text-dim">{{ t('singerRange.approvalHint') }}</p>
        <fieldset :disabled="pending" class="space-y-3">
          <label class="block space-y-1"><span>{{ t('singerRange.approvalEvidence') }}</span><input v-model="approvalEvidence" maxlength="500" :aria-label="t('singerRange.approvalEvidence')" :class="fieldClass" /></label>
          <label class="block space-y-1"><span>{{ t('singerRange.approvalDeclaration') }}</span><textarea v-model="approvalDeclaration" rows="2" maxlength="2000" :aria-label="t('singerRange.approvalDeclaration')" :class="fieldClass" /></label>
          <button type="submit" :disabled="!canApprove" class="min-h-11 rounded-lg border border-border px-3 disabled:opacity-50">{{ t('singerRange.approve') }}</button>
        </fieldset>
      </form>
    </div>
  </section>
</template>

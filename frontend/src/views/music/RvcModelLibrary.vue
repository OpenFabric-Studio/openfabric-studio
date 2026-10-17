<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { isDefiniteClientError } from '../../api/http'
import { importRvcModel, listRvcModels, removeRvcModel } from '../../api/rvcModels'
import { parseRvcModelImport } from '../../api/generated'
import type { ModuleLicenseDeclaration, RvcModelImport, RvcRegisteredModel } from '../../api/generated'
const props = defineProps<{ modelValue: string; voices: { id: string; name: string }[]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; valid: [value: boolean] }>()
const { t } = useI18n(), models = ref<RvcRegisteredModel[]>([]), busy = ref(false), failed = ref(false)
const voice = ref(''), label = ref(''), evidence = ref(''), rights = ref(''), url = ref(''), licenseName = ref(''), knownLicense = ref(false)
const restriction = ref<ModuleLicenseDeclaration['restriction']>('conditional'), speaker = ref(0), indexRate = ref(0)
const trusted = ref(false), executable = ref(false), terms = ref(false), checkpoint = ref<File>(), index = ref<File>()
const requestId = ref(crypto.randomUUID().replaceAll('-', '')), reviewDate = new Date().toISOString().slice(0, 10)
const pending = ref<{ metadata: RvcModelImport; checkpoint: File; index?: File } | null>(null), removeReview = ref(false)
let alive = true, generation = 0, controller: AbortController | undefined
const selected = computed(() => models.value.find(item => item.id === props.modelValue))
watch(() => selected.value?.ready === true && !selected.value.blocked_reasons?.length, value => emit('valid', value), { immediate: true })
const formKey = computed(() => [voice.value, label.value, evidence.value, rights.value, url.value, licenseName.value, knownLicense.value, restriction.value, speaker.value, indexRate.value, trusted.value, executable.value, terms.value, checkpoint.value, index.value])
watch(formKey, () => { if (!pending.value) requestId.value = crypto.randomUUID().replaceAll('-', '') })
const metadata = computed(() => {
  const weightsLicense = { component_id: 'registered-rvc-weights', name: label.value.trim() || 'RVC weights', scope: 'model', status: knownLicense.value ? 'declared' : 'unknown', declared_license: knownLicense.value ? licenseName.value.trim() : null, source_url: url.value.trim() || null, notes: evidence.value.trim(), reviewed_at: reviewDate, restriction: knownLicense.value ? restriction.value : 'unknown', redistribution: 'unknown', copyleft: 'unknown', evidence: knownLicense.value ? [{ kind: 'publisher_terms', source_url: url.value.trim(), observed_at: reviewDate, reviewer: 'Local operator declaration' }] : [] }
  try { return parseRvcModelImport({ request_id: requestId.value, voice_id: voice.value, label: label.value.trim(), evidence_ref: evidence.value.trim(), rights_declaration: rights.value.trim(), source_url: url.value.trim() || null, weights_license: weightsLicense, trusted_local_checkpoint: trusted.value, pickle_execution_acknowledged: executable.value, declared_terms_acknowledged: terms.value, speaker_id: speaker.value, index_rate: index.value ? indexRate.value : 0 }) } catch { return null }
})
const filesValid = computed(() => !!checkpoint.value && /\.pth$/i.test(checkpoint.value.name) && checkpoint.value.size > 0 && (!index.value || /\.index$/i.test(index.value.name) && index.value.size > 0) && checkpoint.value.size + (index.value?.size ?? 0) <= 256 * 1024 ** 2)
function file(event: Event, kind: 'checkpoint' | 'index') { if (!(event.target instanceof HTMLInputElement) || pending.value || busy.value || props.disabled) return; const selected = event.target.files?.[0]; if (kind === 'checkpoint') checkpoint.value = selected; else { index.value = selected; if (!selected) indexRate.value = 0 } }
function choose(event: Event) { if (event.target instanceof HTMLSelectElement && !props.disabled && !busy.value) { removeReview.value = false; emit('update:modelValue', event.target.value) } }
async function reload() {
  if (busy.value || props.disabled) return
  const token = ++generation, request = new AbortController(); controller?.abort(); controller = request; busy.value = true; failed.value = false
  try { const result = await listRvcModels(request.signal); if (alive && token === generation && !request.signal.aborted) models.value = result.models ?? [] }
  catch { if (alive && token === generation && !request.signal.aborted) failed.value = true }
  finally { if (alive && token === generation) { busy.value = false; controller = undefined } }
}
async function submit() {
  if (props.disabled || busy.value || !pending.value && (!metadata.value || !filesValid.value || !checkpoint.value)) return
  if (!pending.value && metadata.value && checkpoint.value) pending.value = { metadata: metadata.value, checkpoint: checkpoint.value, ...(index.value ? { index: index.value } : {}) }
  const saved = pending.value; if (!saved) return
  const token = ++generation, request = new AbortController(); controller?.abort(); controller = request; busy.value = true; failed.value = false
  try {
    const result = await importRvcModel(saved.metadata, saved.checkpoint, saved.index, request.signal)
    if (alive && token === generation && !request.signal.aborted) { models.value = [result, ...models.value.filter(item => item.id !== result.id)]; emit('update:modelValue', result.id); pending.value = null; checkpoint.value = index.value = undefined; trusted.value = executable.value = terms.value = false; requestId.value = crypto.randomUUID().replaceAll('-', '') }
  } catch (cause) { if (alive && token === generation && !request.signal.aborted) { failed.value = true; if (isDefiniteClientError(cause)) { pending.value = null; requestId.value = crypto.randomUUID().replaceAll('-', '') } } }
  finally { if (alive && token === generation) { busy.value = false; controller = undefined } }
}
async function remove() {
  if (!selected.value || !removeReview.value || busy.value || props.disabled) return
  const id = selected.value.id, token = ++generation, request = new AbortController(); controller = request; busy.value = true; failed.value = false
  try { await removeRvcModel(id, request.signal); if (alive && token === generation && !request.signal.aborted) { models.value = models.value.filter(item => item.id !== id); emit('update:modelValue', ''); removeReview.value = false } }
  catch { if (alive && token === generation && !request.signal.aborted) failed.value = true }
  finally { if (alive && token === generation) { busy.value = false; controller = undefined } }
}
onMounted(() => { void reload() })
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort() })
</script>
<template>
  <section class="space-y-4" :aria-busy="busy" :aria-label="t('replacement.rvc.title')">
    <label class="rvc-label">{{ t('replacement.rvc.selected') }}<select :value="modelValue" class="rvc-input" :disabled="disabled || busy" @change="choose"><option value="">{{ t('replacement.rvc.choose') }}</option><option v-for="model in models" :key="model.id" :value="model.id">{{ model.label }} · {{ t(model.ready && !model.blocked_reasons?.length ? 'replacement.available' : 'replacement.unavailable') }}</option><option v-if="modelValue && !selected" :value="modelValue" disabled>{{ t('replacement.savedVoiceUnavailable') }}</option></select></label>
    <template v-if="selected"><p class="break-all text-xs text-text-dim">{{ selected.checkpoint_sha256 }}</p><p class="text-xs text-text-dim">{{ t('replacement.rvc.observed') }}</p></template>
    <div class="flex flex-wrap gap-3"><button type="button" class="rvc-button" :disabled="disabled || busy" @click="reload">{{ t('replacement.rvc.refresh') }}</button><button v-if="selected" type="button" class="rvc-button" :disabled="disabled || busy" @click="removeReview = !removeReview">{{ t('replacement.rvc.remove') }}</button></div>
    <div v-if="removeReview" class="space-y-2 rounded-lg border border-border p-3"><p class="text-sm text-text-dim">{{ t('replacement.rvc.removeHelp') }}</p><button type="button" class="rvc-button" :disabled="disabled || busy" @click="remove">{{ t('replacement.rvc.confirmRemove') }}</button></div>
    <p v-if="failed" role="alert" class="text-sm text-status-failed">{{ t('replacement.rvc.failed') }}</p><p v-if="pending" role="status" class="text-sm text-status-warning">{{ t('replacement.rvc.pending') }}</p>
    <details class="rounded-lg border border-border p-3"><summary class="min-h-11 cursor-pointer py-2 text-sm">{{ t('replacement.rvc.import') }}</summary>
      <form class="mt-3 space-y-4" @submit.prevent="submit"><p class="text-sm leading-relaxed text-status-warning">{{ t('replacement.rvc.trustHelp') }}</p><fieldset class="space-y-4" :disabled="disabled || busy || !!pending">
        <label class="rvc-label">{{ t('replacement.rvc.profile') }}<select v-model="voice" name="voice" class="rvc-input"><option value="">{{ t('replacement.chooseVoice') }}</option><option v-for="item in voices" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
        <label class="rvc-label">{{ t('replacement.rvc.label') }}<input v-model="label" name="label" maxlength="160" class="rvc-input" /></label><label class="rvc-label">{{ t('replacement.rvc.checkpoint') }}<input name="checkpoint" type="file" accept=".pth" class="rvc-input" @change="file($event, 'checkpoint')" /></label><p v-if="checkpoint" class="break-all text-sm text-text-dim">{{ checkpoint.name }} · {{ (checkpoint.size / 1024 ** 2).toFixed(2) }} MiB</p><label class="rvc-label">{{ t('replacement.rvc.index') }}<input type="file" accept=".index" class="rvc-input" @change="file($event, 'index')" /></label><p v-if="index" class="break-all text-sm text-text-dim">{{ index.name }} · {{ (index.size / 1024 ** 2).toFixed(2) }} MiB</p>
        <div class="grid gap-3 sm:grid-cols-2"><label class="rvc-label">{{ t('replacement.rvc.speaker') }}<input v-model.number="speaker" type="number" min="0" max="1023" step="1" class="rvc-input" /></label><label v-if="index" class="rvc-label">{{ t('replacement.rvc.indexRate') }}<input v-model.number="indexRate" type="number" min="0" max="1" step="0.01" class="rvc-input" /></label></div>
        <label class="rvc-label">{{ t('replacement.rvc.evidence') }}<input v-model="evidence" name="evidence" maxlength="1000" class="rvc-input" /></label><label class="rvc-label">{{ t('replacement.rvc.rights') }}<textarea v-model="rights" name="rights" minlength="16" maxlength="2000" rows="3" class="rvc-input" /></label><label class="rvc-label">{{ t('replacement.rvc.url') }}<input v-model="url" type="url" maxlength="500" class="rvc-input" /></label>
        <label class="flex min-h-11 items-center gap-3 text-sm"><input v-model="knownLicense" type="checkbox" class="size-5 accent-accent1" />{{ t('replacement.rvc.knownLicense') }}</label><template v-if="knownLicense"><label class="rvc-label">{{ t('replacement.rvc.license') }}<input v-model="licenseName" maxlength="120" class="rvc-input" /></label><label class="rvc-label">{{ t('replacement.rvc.restriction') }}<select v-model="restriction" class="rvc-input"><option value="conditional">{{ t('replacement.rvc.conditional') }}</option><option value="noncommercial">{{ t('replacement.rvc.noncommercial') }}</option><option value="unknown">{{ t('replacement.unknown') }}</option><option value="unrestricted_declared">{{ t('replacement.rvc.unrestricted') }}</option></select></label></template>
        <label class="flex min-h-11 items-start gap-3 text-sm"><input v-model="trusted" data-trust type="checkbox" class="mt-1 size-5 shrink-0 accent-accent1" />{{ t('replacement.rvc.trusted') }}</label><label class="flex min-h-11 items-start gap-3 text-sm"><input v-model="executable" data-trust type="checkbox" class="mt-1 size-5 shrink-0 accent-accent1" />{{ t('replacement.rvc.executable') }}</label><label class="flex min-h-11 items-start gap-3 text-sm"><input v-model="terms" data-trust type="checkbox" class="mt-1 size-5 shrink-0 accent-accent1" />{{ t('replacement.rvc.terms') }}</label>
      </fieldset><button type="submit" class="rvc-button" :disabled="disabled || busy || !pending && (!metadata || !filesValid)">{{ t(pending ? 'replacement.rvc.retry' : 'replacement.rvc.register') }}</button></form>
    </details>
  </section>
</template>
<style scoped>
@reference "../../style.css";
.rvc-label { @apply flex min-w-0 flex-col gap-2 text-sm text-text-dim; }
.rvc-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
.rvc-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

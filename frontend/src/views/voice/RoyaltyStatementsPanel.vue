<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/royalties'
import { ApiError } from '../../api/http'
import type { Agreement, AgreementCreate, CsvImportRequest, ImportPreview, Release, RoyaltyStatement } from '../../api/contracts'

const props = defineProps<{ artistId: string; active: boolean }>()
const { t, te } = useI18n()
const agreements = ref<Agreement[]>([]), releases = ref<Release[]>([]), selectedId = ref('')
const statement = ref<RoyaltyStatement | null>(null), preview = ref<ImportPreview | null>(null)
const reviewedRequest = ref<CsvImportRequest | null>(null)
const loaded = ref(false), pending = ref(false), reading = ref(false), error = ref(''), notice = ref('')
const percentage = ref(''), basis = ref<AgreementCreate['basis'] | ''>(''), currency = ref('')
const categories = ref(''), noDeductions = ref(false), start = ref(''), end = ref(''), openEnded = ref(false)
const evidence = ref(''), version = ref(''), distributor = ref(''), csvText = ref(''), fileName = ref('')
const mapping = reactive({ release_id: '', period: '', amount: '', currency: '', row_id: '', deduction_category: '' })
const mappingKeys = ['release_id', 'period', 'amount', 'currency', 'row_id', 'deduction_category'] as const
const paymentAmount = ref(''), paymentDate = ref(''), paymentEvidence = ref(''), paymentKey = ref(''), paymentConfirmed = ref(false)
const publicId = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/
const positiveAmount = /^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$/
const validRate = /^(?:100(?:\.0{1,6})?|(?:[0-9]|[1-9][0-9])(?:\.[0-9]{1,6})?)$/
let alive = true, generation = 0, fileGeneration = 0
let controller: AbortController | undefined

const selected = computed(() => agreements.value.find(agreement => agreement.id === selectedId.value))
const eligibleReleases = computed(() => releases.value.filter(release => release.eligible
  && agreements.value.some(agreement => agreement.id === release.agreement_id)))
const permittedCategories = computed(() => noDeductions.value ? [] : categories.value.split(',').map(value => value.trim()).filter(Boolean))
const canCreate = computed(() => loaded.value && !pending.value && validRate.test(percentage.value) && basis.value !== ''
  && /^[A-Z]{3}$/.test(currency.value) && !!start.value && (openEnded.value || !!end.value && end.value >= start.value)
  && (noDeductions.value || permittedCategories.value.length > 0)
  && permittedCategories.value.every(value => /^[a-z][a-z0-9_]{0,63}$/.test(value))
  && new Set(permittedCategories.value).size === permittedCategories.value.length
  && !!evidence.value.trim() && publicId.test(version.value))
const reportRequest = computed<CsvImportRequest | null>(() => {
  if (!publicId.test(distributor.value) || !csvText.value || !mapping.release_id.trim() || !mapping.period.trim()
    || !mapping.amount.trim() || !mapping.currency.trim()) return null
  const columns = Object.values(mapping).map(value => value.trim()).filter(Boolean)
  if (new Set(columns).size !== columns.length) return null
  return { distributor: distributor.value, csv_text: csvText.value,
    mapping: { release_id: mapping.release_id.trim(), period: mapping.period.trim(), amount: mapping.amount.trim(),
      currency: mapping.currency.trim(), row_id: mapping.row_id.trim() || null, deduction_category: mapping.deduction_category.trim() || null } }
})
const signature = computed(() => JSON.stringify(reportRequest.value))
const foreignRows = computed(() => preview.value?.rows.some(row => !agreements.value.some(agreement => agreement.id === row.agreement_id)) ?? false)
const canPreview = computed(() => loaded.value && !pending.value && !reading.value && reportRequest.value !== null)
const canCommit = computed(() => loaded.value && !pending.value && preview.value !== null && reviewedRequest.value !== null
  && JSON.stringify(reviewedRequest.value) === signature.value && !foreignRows.value
  && (preview.value.errors.length === 0 && preview.value.valid_row_count > 0
    || preview.value.duplicate_content && !!preview.value.existing_import_id))
const canRecordPayment = computed(() => loaded.value && !pending.value && !!selected.value && paymentConfirmed.value
  && positiveAmount.test(paymentAmount.value) && /[1-9]/.test(paymentAmount.value)
  && !!paymentDate.value && !!paymentEvidence.value.trim() && publicId.test(paymentKey.value))

function valid(token: number, signal: AbortSignal): boolean {
  return alive && props.active && generation === token && !signal.aborted
}
function message(code: string): string {
  const key = `royalties.errors.${code}`
  return te(key) ? t(key) : t('royalties.errors.unknown')
}
async function perform<T>(request: (signal: AbortSignal) => Promise<T>, accept: (result: T) => void) {
  if (!alive || !props.active || !loaded.value || pending.value) return
  controller?.abort(); controller = new AbortController()
  const signal = controller.signal, token = generation
  pending.value = true; error.value = ''; notice.value = ''
  try {
    const result = await request(signal)
    if (valid(token, signal)) accept(result)
  } catch (cause) { if (valid(token, signal)) error.value = message(cause instanceof ApiError ? cause.message : '') }
  finally { if (valid(token, signal)) pending.value = false }
}
async function reload() {
  if (!alive || !props.active || pending.value) return
  const token = ++generation, artistId = props.artistId
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  pending.value = true; loaded.value = false; error.value = ''; notice.value = ''
  preview.value = null; reviewedRequest.value = null
  try {
    const [allAgreements, allReleases] = await Promise.all([api.listAgreements(signal), api.listReleases(signal)])
    if (!valid(token, signal) || props.artistId !== artistId) return
    const owned = allAgreements.filter(agreement => agreement.artist_id === artistId)
    const chosen = owned.find(agreement => agreement.id === selectedId.value)?.id ?? owned[0]?.id ?? ''
    const savedStatement = chosen ? await api.getStatement(chosen, signal) : null
    if (!valid(token, signal) || props.artistId !== artistId) return
    agreements.value = owned; releases.value = allReleases; selectedId.value = chosen
    statement.value = savedStatement; loaded.value = true
  } catch (cause) { if (valid(token, signal)) error.value = message(cause instanceof ApiError ? cause.message : '') }
  finally { if (valid(token, signal)) pending.value = false }
}
function createAgreement() {
  if (!canCreate.value || basis.value === '') return
  const request: AgreementCreate = { artist_id: props.artistId, percentage: percentage.value, basis: basis.value,
    currency: currency.value, permitted_deduction_categories: permittedCategories.value,
    eligibility_start: start.value, eligibility_end: openEnded.value ? null : end.value,
    evidence_ref: evidence.value.trim(), evidence_version: version.value }
  void perform(async signal => {
    const agreement = await api.createAgreement(request, signal)
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
    return { agreement, statement: await api.getStatement(agreement.id, signal) }
  }, result => {
    agreements.value = [result.agreement, ...agreements.value.filter(agreement => agreement.id !== result.agreement.id)]
    selectedId.value = result.agreement.id; statement.value = result.statement; notice.value = t('royalties.saved')
  })
}
async function chooseCsv(event: Event) {
  if (!(event.target instanceof HTMLInputElement)) return
  const file = event.target.files?.[0], token = ++fileGeneration, session = generation
  csvText.value = ''; fileName.value = ''; error.value = ''; notice.value = ''
  if (!file) return
  if (file.size > 1_000_000) { error.value = t('royalties.errors.report_too_large'); return }
  reading.value = true
  try {
    const text = await file.text()
    if (!alive || !props.active || session !== generation || token !== fileGeneration) return
    if (new TextEncoder().encode(text).length > 1_000_000) { error.value = t('royalties.errors.report_too_large'); return }
    csvText.value = text; fileName.value = file.name
  } catch { if (alive && props.active && session === generation && token === fileGeneration) error.value = t('royalties.errors.unknown') }
  finally { if (session === generation && token === fileGeneration) reading.value = false }
}
function previewReport() {
  const request = reportRequest.value, reviewedSignature = signature.value
  if (!canPreview.value || !request) return
  preview.value = null; reviewedRequest.value = null
  void perform(signal => api.previewImport(request, signal), result => {
    if (signature.value !== reviewedSignature) return
    preview.value = result; reviewedRequest.value = request
  })
}
function commitReport() {
  const reviewed = preview.value, request = reviewedRequest.value, agreementId = selectedId.value
  if (!canCommit.value || !reviewed || !request) return
  void perform(async signal => {
    await api.commitImport({ request, preview_hash: reviewed.preview_hash }, signal)
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
    return agreementId ? api.getStatement(agreementId, signal) : null
  }, result => { statement.value = result; preview.value = null; reviewedRequest.value = null; notice.value = t('royalties.saved') })
}
function recordPayment() {
  const agreement = selected.value
  if (!canRecordPayment.value || !agreement) return
  void perform(async signal => {
    await api.recordPayment({ agreement_id: agreement.id, amount: paymentAmount.value, currency: agreement.currency,
      occurred_on: paymentDate.value, evidence_ref: paymentEvidence.value.trim(), idempotency_key: paymentKey.value }, signal)
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
    return api.getStatement(agreement.id, signal)
  }, result => { statement.value = result; paymentConfirmed.value = false; notice.value = t('royalties.saved') })
}
function downloadStatement() {
  const saved = statement.value
  if (!alive || !props.active || !saved || saved.artist_id !== props.artistId) return
  const url = URL.createObjectURL(new Blob([JSON.stringify(saved, null, 2) + '\n'], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url; link.download = `royalty-statement-${saved.agreement_id}.json`
  document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url)
}
watch(signature, () => { preview.value = null; reviewedRequest.value = null })
function chooseAgreement() {
  const value = selectedId.value
  if (pending.value || !loaded.value) return
  statement.value = null; paymentConfirmed.value = false
  if (value) void perform(signal => api.getStatement(value, signal), result => { statement.value = result })
}
watch(() => [props.artistId, props.active] as const, (values, previous) => {
  generation++; fileGeneration++; controller?.abort(); pending.value = false; reading.value = false
  loaded.value = false; statement.value = null; preview.value = null; reviewedRequest.value = null
  if (values[0] !== previous?.[0]) {
    agreements.value = []; releases.value = []; selectedId.value = ''; error.value = ''; notice.value = ''
    percentage.value = ''; basis.value = ''; currency.value = ''; categories.value = ''; noDeductions.value = false
    start.value = ''; end.value = ''; openEnded.value = false; evidence.value = ''; version.value = ''
    distributor.value = ''; csvText.value = ''; fileName.value = ''
    for (const key of mappingKeys) mapping[key] = ''
    paymentAmount.value = ''; paymentDate.value = ''; paymentEvidence.value = ''; paymentKey.value = ''; paymentConfirmed.value = false
  }
  if (props.active) void reload()
}, { immediate: true })
onBeforeUnmount(() => { alive = false; generation++; fileGeneration++; controller?.abort() })
</script>

<template>
  <section v-if="active" class="space-y-4 rounded-xl border border-border bg-panel-2 p-4" :aria-label="t('royalties.title')">
    <h3 class="text-base font-semibold text-text">{{ t('royalties.title') }}</h3>
    <p class="text-sm text-text-dim">{{ t('royalties.intro') }}</p>
    <p class="text-sm text-text-dim">{{ t('royalties.actorHint') }}</p>
    <p class="text-sm text-status-queued">{{ t('royalties.releaseGate') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="notice" role="status" class="text-sm text-text">{{ notice }}</p>
    <p v-if="pending && !loaded" role="status" class="text-sm text-text-dim">{{ t('royalties.loading') }}</p>
    <button type="button" :disabled="pending" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="reload">{{ t('royalties.refresh') }}</button>
    <label class="block space-y-1 text-sm text-text"><span>{{ t('royalties.agreement') }}</span><select v-model="selectedId" :disabled="pending || !loaded" :aria-label="t('royalties.agreement')" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" @change="chooseAgreement"><option value="">{{ t('royalties.chooseAgreement') }}</option><option v-for="agreement in agreements" :key="agreement.id" :value="agreement.id">{{ agreement.evidence_version }} · {{ agreement.percentage }}% · {{ agreement.currency }} · {{ t(`royalties.${agreement.basis}`) }}</option></select></label>
    <p v-if="loaded && !agreements.length" class="text-sm text-text-dim">{{ t('royalties.noAgreements') }}</p>

    <details class="rounded-lg border border-border p-3"><summary class="min-h-11 cursor-pointer text-sm font-medium text-text">{{ t('royalties.newAgreement') }}</summary>
      <form class="space-y-3" @submit.prevent="createAgreement"><fieldset :disabled="pending || !loaded" class="space-y-3 text-sm text-text">
        <p class="text-text-dim">{{ t('royalties.termsHint') }}</p>
        <div class="grid gap-3 sm:grid-cols-3">
          <label class="block space-y-1"><span>{{ t('royalties.percentage') }}</span><input v-model="percentage" :aria-label="t('royalties.percentage')" inputmode="decimal" maxlength="10" required class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
          <label class="block space-y-1"><span>{{ t('royalties.basis') }}</span><select v-model="basis" :aria-label="t('royalties.basis')" required class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option value="">{{ t('royalties.chooseBasis') }}</option><option value="gross">{{ t('royalties.gross') }}</option><option value="contract_net">{{ t('royalties.contract_net') }}</option></select></label>
          <label class="block space-y-1"><span>{{ t('royalties.currency') }}</span><input v-model="currency" :aria-label="t('royalties.currency')" maxlength="3" pattern="[A-Z]{3}" required class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
        </div>
        <label class="block space-y-1"><span>{{ t('royalties.deductions') }}</span><input v-model="categories" :disabled="noDeductions" :aria-label="t('royalties.deductions')" maxlength="2000" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
        <label class="flex min-h-11 items-center gap-2"><input v-model="noDeductions" type="checkbox" />{{ t('royalties.noDeductions') }}</label>
        <div class="grid gap-3 sm:grid-cols-2"><label class="block space-y-1"><span>{{ t('royalties.start') }}</span><input v-model="start" :aria-label="t('royalties.start')" type="date" required class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><label class="block space-y-1"><span>{{ t('royalties.end') }}</span><input v-model="end" :aria-label="t('royalties.end')" type="date" :disabled="openEnded" :required="!openEnded" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label></div>
        <label class="flex min-h-11 items-center gap-2"><input v-model="openEnded" type="checkbox" />{{ t('royalties.openEnded') }}</label>
        <label class="block space-y-1"><span>{{ t('royalties.evidence') }}</span><input v-model="evidence" :aria-label="t('royalties.evidence')" maxlength="500" required class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
        <label class="block space-y-1"><span>{{ t('royalties.version') }}</span><input v-model="version" :aria-label="t('royalties.version')" maxlength="120" required class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
        <button type="submit" :disabled="!canCreate" class="min-h-11 rounded-lg border border-border px-3 disabled:opacity-50">{{ t('royalties.createAgreement') }}</button>
      </fieldset></form>
    </details>

    <div class="space-y-2 text-sm text-text"><h4 class="font-medium">{{ t('royalties.releaseIds') }}</h4><p v-if="!eligibleReleases.length" class="text-text-dim">{{ t('royalties.noReleases') }}</p><ul v-else class="space-y-1"><li v-for="release in eligibleReleases" :key="release.id" class="break-all">{{ release.platform }} · {{ release.master_id }} · <code>{{ release.id }}</code></li></ul><p class="text-text-dim">{{ t('royalties.releaseApi') }}</p></div>

    <details open class="rounded-lg border border-border p-3"><summary class="min-h-11 cursor-pointer text-sm font-medium text-text">{{ t('royalties.importTitle') }}</summary>
      <fieldset :disabled="pending || !loaded" class="space-y-3 text-sm text-text">
        <label class="block space-y-1"><span>{{ t('royalties.distributor') }}</span><input v-model="distributor" :aria-label="t('royalties.distributor')" maxlength="120" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
        <label class="block space-y-1"><span>{{ t('royalties.report') }}</span><input type="file" accept=".csv,text/csv" :aria-label="t('royalties.report')" class="min-h-11 w-full" @change="chooseCsv" /></label><p v-if="fileName" class="text-text-dim">{{ fileName }}</p>
        <p class="text-text-dim">{{ t('royalties.mappingHint') }}</p>
        <div class="grid gap-3 sm:grid-cols-2"><label v-for="key in mappingKeys" :key="key" class="block space-y-1"><span>{{ t(`royalties.mapping.${key}`) }}</span><input v-model="mapping[key]" :aria-label="t(`royalties.mapping.${key}`)" :placeholder="t(`royalties.mapping.${key}`)" maxlength="120" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label></div>
        <button type="button" :disabled="!canPreview" class="min-h-11 rounded-lg border border-border px-3 disabled:opacity-50" @click="previewReport">{{ t('royalties.preview') }}</button>
      </fieldset>
      <div v-if="preview" class="mt-3 space-y-2 text-sm text-text" aria-live="polite">
        <p>{{ t('royalties.previewCounts', { valid: preview.valid_row_count, total: preview.input_row_count }) }}</p>
        <p v-if="preview.duplicate_content" class="text-status-queued">{{ t('royalties.duplicate') }}</p>
        <p v-if="foreignRows" class="text-status-failed">{{ t('royalties.errors.other_artist') }}</p>
        <ul v-if="preview.errors.length" class="max-h-48 space-y-1 overflow-auto text-status-failed"><li v-for="(issue, index) in preview.errors" :key="index">{{ t('royalties.rowError', { row: issue.row_number, error: message(issue.code) }) }}</li></ul>
        <div class="overflow-x-auto"><table v-if="preview.totals.length" class="w-full border-collapse text-left"><thead><tr><th class="p-2">{{ t('royalties.agreementId') }}</th><th class="p-2">{{ t('royalties.revenue') }}</th><th class="p-2">{{ t('royalties.deductionTotal') }}</th><th class="p-2">{{ t('royalties.baseTotal') }}</th><th class="p-2">{{ t('royalties.royaltyTotal') }}</th></tr></thead><tbody><tr v-for="total in preview.totals" :key="total.agreement_id" class="border-t border-border"><td class="break-all p-2">{{ total.agreement_id }}</td><td class="p-2">{{ total.revenue }} {{ total.currency }}</td><td class="p-2">{{ total.deductions }} {{ total.currency }}</td><td class="p-2">{{ total.royalty_base }} {{ total.currency }}</td><td class="p-2">{{ total.royalty_due }} {{ total.currency }}</td></tr></tbody></table></div>
      </div>
      <button type="button" :disabled="!canCommit" class="mt-3 min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="commitReport">{{ t('royalties.commit') }}</button>
    </details>

    <div class="space-y-3 text-sm text-text"><h4 class="font-medium">{{ t('royalties.statementTitle') }}</h4><p class="text-text-dim">{{ t('royalties.statementHint') }}</p><p v-if="!statement" class="text-text-dim">{{ t('royalties.noStatement') }}</p><dl v-else class="grid grid-cols-2 gap-2"><dt>{{ t('royalties.royaltyTotal') }}</dt><dd>{{ statement.royalty_accrued }} {{ statement.currency }}</dd><dt>{{ t('royalties.adjustments') }}</dt><dd>{{ statement.adjustments_total }} {{ statement.currency }}</dd><dt>{{ t('royalties.payments') }}</dt><dd>{{ statement.payments_total }} {{ statement.currency }}</dd><dt>{{ t('royalties.balance') }}</dt><dd>{{ statement.balance_due }} {{ statement.currency }}</dd></dl><button type="button" :disabled="!statement || pending" class="min-h-11 rounded-lg border border-border px-3 disabled:opacity-50" @click="downloadStatement">{{ t('royalties.download') }}</button></div>

    <details class="rounded-lg border border-border p-3"><summary class="min-h-11 cursor-pointer text-sm font-medium text-text">{{ t('royalties.paymentTitle') }}</summary><form @submit.prevent="recordPayment"><fieldset :disabled="pending || !selected" class="space-y-3 text-sm text-text"><p class="text-text-dim">{{ t('royalties.paymentHint') }}</p><p v-if="selected">{{ selected.evidence_version }} · {{ selected.currency }}</p><label class="block space-y-1"><span>{{ t('royalties.paymentAmount') }}</span><input v-model="paymentAmount" :aria-label="t('royalties.paymentAmount')" inputmode="decimal" maxlength="20" required class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><label class="block space-y-1"><span>{{ t('royalties.paymentDate') }}</span><input v-model="paymentDate" :aria-label="t('royalties.paymentDate')" type="date" required class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><label class="block space-y-1"><span>{{ t('royalties.paymentEvidence') }}</span><input v-model="paymentEvidence" :aria-label="t('royalties.paymentEvidence')" maxlength="500" required class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><label class="block space-y-1"><span>{{ t('royalties.paymentKey') }}</span><input v-model="paymentKey" :aria-label="t('royalties.paymentKey')" maxlength="120" required class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><label class="flex min-h-11 items-center gap-2"><input v-model="paymentConfirmed" type="checkbox" />{{ t('royalties.paymentConfirmed') }}</label><button type="submit" :disabled="!canRecordPayment" class="min-h-11 rounded-lg border border-border px-3 disabled:opacity-50">{{ t('royalties.recordPayment') }}</button></fieldset></form></details>
  </section>
</template>

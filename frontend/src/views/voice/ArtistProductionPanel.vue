<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/artistProduction'
import { ApiError } from '../../api/http'
import type { VoiceProfile } from '../../api/voices'
import type { ArtistProfile, ArtistConsentVersion, ArtistConsentRequest, SourceAsset, SourceDeclaration, AmendSourceRequest, ProductionRun, RightsReviewRequest, ReleaseReviewRequest } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'
import WaveformPlayer from '../../components/shared/WaveformPlayer.vue'
import SingerRangePanel from './SingerRangePanel.vue'
import RoyaltyStatementsPanel from './RoyaltyStatementsPanel.vue'
import RecordingQualityPanel from './RecordingQualityPanel.vue'
import type { RecordingLabels } from '../../api/contracts'

const props = withDefaults(defineProps<{ voice: VoiceProfile; active?: boolean }>(), { active: true })
const emit = defineEmits<{ labels: [labels: RecordingLabels] }>()
const { t, te } = useI18n()
const artist = ref<ArtistProfile | null>(null), consents = ref<ArtistConsentVersion[]>([])
const assets = ref<SourceAsset[]>([]), runs = ref<ProductionRun[]>([])
const pending = ref(false), loaded = ref(false), error = ref(''), notice = ref('')
const artistName = ref(''), operator = ref(''), declaration = ref(''), evidence = ref(''), evidenceHash = ref('')
const allowedOperations = ref<NonNullable<ArtistConsentRequest['allowed_operations']>>([])
const allowedUses = ref<NonNullable<ArtistConsentRequest['allowed_uses']>>([])
const territories = ref(''), authorizedOperators = ref(''), starts = ref(''), ends = ref('')
const sourceFile = ref<File | null>(null), sourceRole = ref<'guide_vocal' | 'accompaniment'>('guide_vocal')
const sourceOrigin = ref<NonNullable<SourceDeclaration['origin']>>('unknown')
const provider = ref(''), rightsHolder = ref(''), acquisition = ref(''), sourceEvidence = ref(''), sourceScope = ref(''), terms = ref('')
const sourceUses = ref<NonNullable<SourceDeclaration['permitted_uses']>>([])
const sourceReviewer = ref(''), reviewEvidence = ref(''), sourceReviewStatus = ref<RightsReviewRequest['status']>('unknown')
const amendmentAssetId = ref(''), amendmentReviewer = ref(''), amendmentDeclaration = ref<SourceDeclaration | null>(null)
const guideId = ref(''), accompanimentId = ref(''), modelId = ref(props.voice.active_model_id ?? '')
const title = ref(''), chosenKey = ref(''), isolated = ref(false), singerConfirmed = ref(false)
const releaseEvidence = ref(''), releaseNote = ref('')
const guideRangeEvidence = ref(''), guideRangeDeclaration = ref('')
const composition = ref<ReleaseReviewRequest['composition_rights']>('unknown'), master = ref<ReleaseReviewRequest['master_rights']>('unknown')
const voiceRights = ref<ReleaseReviewRequest['voice_rights']>('unknown'), dependency = ref<ReleaseReviewRequest['dependency_rights']>('unknown')
const operations = ['train', 'convert', 'export'] as const
const uses = ['evaluation', 'production', 'commercial'] as const
const sourceUseOptions = ['evaluation', 'production', 'release'] as const
const origins = ['human_recorded', 'licensed_third_party', 'ai_generated', 'unknown'] as const
const reviewStatuses = ['unknown', 'approved', 'restricted'] as const
let alive = true, generation = 0
let controller: AbortController | undefined
const currentConsent = computed(() => [...consents.value].sort((left, right) => right.revision - left.revision)[0])
const operatorRestricted = computed(() => (currentConsent.value?.authorized_operators?.length ?? 0) > 0)
const permissionRecorded = computed(() => {
  const consent = currentConsent.value
  return consent?.status === 'active' && consent.allowed_operations?.includes('convert') && consent.allowed_uses?.includes('production')
    && !operatorRestricted.value
    && (!consent.starts_at || Date.parse(consent.starts_at) <= Date.now()) && (!consent.ends_at || Date.parse(consent.ends_at) > Date.now())
})
const canDeclare = computed(() => loaded.value && !!artist.value && !pending.value && !!operator.value.trim() && declaration.value.trim().length >= 20)
const canAppend = computed(() => canDeclare.value && !!evidence.value.trim() && allowedOperations.value.length > 0 && allowedUses.value.length > 0)
const modelChoices = computed(() => props.voice.models ?? [])
function currentReviewStatus(asset: SourceAsset): RightsReviewRequest['status'] {
  const review = asset.reviews?.at(-1), revisionId = asset.declaration_revisions?.at(-1)?.id ?? asset.id
  return review?.declaration_revision_id === revisionId ? review.status : 'unknown'
}
function eligible(asset: SourceAsset): boolean {
  return !asset.reference_only && ['human_recorded', 'licensed_third_party'].includes(asset.origin)
    && !!asset.declaration?.permitted_uses?.includes('production') && !asset.declaration.blocked_uses?.includes('production')
    && currentReviewStatus(asset) === 'approved'
}
const guides = computed(() => assets.value.filter(asset => asset.role === 'guide_vocal'))
const accompaniments = computed(() => assets.value.filter(asset => asset.role === 'accompaniment'))
const canRender = computed(() => loaded.value && !pending.value && permissionRecorded.value && !!title.value.trim()
  && modelChoices.value.some(model => model.id === modelId.value) && guideId.value !== accompanimentId.value
  && guides.value.some(asset => asset.id === guideId.value && eligible(asset))
  && accompaniments.value.some(asset => asset.id === accompanimentId.value && eligible(asset)) && isolated.value && singerConfirmed.value)
function errorText(cause: unknown): string {
  const key = `artistProduction.errors.${cause instanceof ApiError ? cause.message : ''}`
  return te(key) ? t(key) : t('artistProduction.errors.unknown')
}
function valid(token: number, signal: AbortSignal): boolean { return alive && props.active && generation === token && !signal.aborted }
function upsertRun(run: ProductionRun) { runs.value = [run, ...runs.value.filter(item => item.id !== run.id)] }
const poll = createPollingLoop(async context => {
  const token = generation, voiceId = props.voice.id
  try {
    const response = await api.listProductionRuns(context.signal)
    if (!context.isCurrent() || !valid(token, context.signal) || voiceId !== props.voice.id) return false
    runs.value = response.filter(run => run.request.voice_id === voiceId)
  } catch (cause) {
    if (context.isCurrent() && valid(token, context.signal)) error.value = errorText(cause)
    return false
  }
  return runs.value.some(run => run.status === 'queued' || run.status === 'running')
}, 2000)
function schedule() {
  if (alive && props.active && runs.value.some(run => run.status === 'queued' || run.status === 'running')) poll.start(false)
}
async function perform<T>(request: (signal: AbortSignal) => Promise<T>, accept: (response: T) => void) {
  if (pending.value || !alive || !props.active) return
  poll.stop(); controller?.abort(); controller = new AbortController()
  const signal = controller.signal, token = generation
  pending.value = true; error.value = ''; notice.value = ''
  try {
    const response = await request(signal)
    if (valid(token, signal)) { accept(response); notice.value = t('artistProduction.saved') }
  } catch (cause) { if (valid(token, signal)) error.value = errorText(cause) }
  finally { if (valid(token, signal)) { pending.value = false; schedule() } }
}
async function reload() {
  if (!alive || !props.active || pending.value) return
  closeAmendment()
  const token = ++generation, voiceId = props.voice.id
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  poll.stop(); pending.value = true; loaded.value = false; error.value = ''; notice.value = ''
  try {
    const [profiles, sourceAssets, savedRuns] = await Promise.all([api.listArtists(signal), api.listSourceAssets(signal), api.listProductionRuns(signal)])
    if (!valid(token, signal) || voiceId !== props.voice.id) return
    const selected = profiles.find(profile => profile.voice_id === voiceId) ?? null
    const versions = selected ? await api.listArtistConsents(selected.id, signal) : []
    if (!valid(token, signal) || voiceId !== props.voice.id) return
    artist.value = selected; consents.value = versions; assets.value = sourceAssets
    runs.value = savedRuns.filter(run => run.request.voice_id === voiceId); loaded.value = true
  } catch (cause) { if (valid(token, signal)) error.value = errorText(cause) }
  finally { if (valid(token, signal)) { pending.value = false; schedule() } }
}
function createProfile() {
  if (!artistName.value.trim() || !loaded.value) return
  void perform(signal => api.createArtist({ name: artistName.value.trim(), voice_id: props.voice.id }, signal), profile => { artist.value = profile; consents.value = []; artistName.value = '' })
}
function timestamp(value: string): string | null { return value ? new Date(value).toISOString() : null }
function operatorLabels(value: string): string[] {
  return [...new Set(value.split(',').map(label => label.normalize('NFKC').trim().replace(/\s+/g, ' ')).filter(Boolean))]
}
function appendConsent(status: 'active' | 'revoked') {
  const profile = artist.value
  if (!profile || !(status === 'active' ? canAppend.value : canDeclare.value)) return
  let body: ArtistConsentRequest
  try {
    body = { status, signed_evidence_ref: evidence.value.trim() || null, signed_evidence_sha256: evidenceHash.value.trim() || null,
      operator_label: operator.value.trim(), human_declaration: declaration.value.trim(), allowed_operations: [...allowedOperations.value],
      allowed_uses: [...allowedUses.value], authorized_operators: operatorLabels(authorizedOperators.value),
      territories: territories.value.split(',').map(value => value.trim()).filter(Boolean), starts_at: timestamp(starts.value), ends_at: timestamp(ends.value) }
  } catch { error.value = t('artistProduction.errors.unknown'); return }
  void perform(signal => api.appendArtistConsent(profile.id, body, signal), version => {
    consents.value = [version, ...consents.value]; artist.value = { ...profile, current_consent_version_id: version.id }
  })
}
function chooseSourceFile(event: Event) {
  if (event.target instanceof HTMLInputElement) sourceFile.value = event.target.files?.[0] ?? null
}
function upload() {
  const file = sourceFile.value
  if (!file || !loaded.value) return
  const body: SourceDeclaration = { role: sourceRole.value, origin: sourceOrigin.value, original_filename: file.name,
    provider: provider.value.trim() || 'unknown', rights_holder: rightsHolder.value.trim(), acquisition_method: acquisition.value.trim(),
    evidence_ref: sourceEvidence.value.trim(), permission_scope: sourceScope.value.trim(), provider_terms_version: terms.value.trim(), permitted_uses: [...sourceUses.value] }
  void perform(signal => api.uploadSourceAsset(file, body, signal), asset => { assets.value = [asset, ...assets.value.filter(item => item.id !== asset.id)]; sourceFile.value = null })
}
function reviewAsset(asset: SourceAsset) {
  if (!sourceReviewer.value.trim() || !reviewEvidence.value.trim() || !loaded.value) return
  const body: RightsReviewRequest = { status: sourceReviewStatus.value, reviewer_label: sourceReviewer.value.trim(), evidence_ref: reviewEvidence.value.trim(),
    declaration_revision_id: asset.declaration_revisions?.at(-1)?.id ?? asset.id }
  void perform(signal => api.reviewSourceAsset(asset.id, body, signal), reviewed => { assets.value = assets.value.map(item => item.id === reviewed.id ? reviewed : item) })
}
function closeAmendment() {
  amendmentAssetId.value = ''; amendmentReviewer.value = ''; amendmentDeclaration.value = null
}
function beginAmendment(asset: SourceAsset) {
  if (!loaded.value || pending.value || !asset.declaration) return
  amendmentAssetId.value = asset.id; amendmentReviewer.value = ''
  amendmentDeclaration.value = { ...asset.declaration, permitted_uses: [...asset.declaration.permitted_uses ?? []], blocked_uses: [...asset.declaration.blocked_uses ?? []] }
}
function amendAsset(asset: SourceAsset) {
  const original = asset.declaration, draft = amendmentDeclaration.value
  if (!loaded.value || asset.id !== amendmentAssetId.value || !original || !draft || !amendmentReviewer.value.trim()) return
  const body: AmendSourceRequest = { reviewer_label: amendmentReviewer.value.trim(), declaration: {
    ...draft, role: original.role, origin: asset.origin, provider: asset.provider,
    original_filename: asset.declaration_revisions?.[0]?.declaration.original_filename ?? original.original_filename,
    permitted_uses: [...draft.permitted_uses ?? []], blocked_uses: [...draft.blocked_uses ?? []],
  } }
  void perform(signal => api.amendSourceEvidence(asset.id, body, signal), amended => {
    if (amended.id !== asset.id) throw new ApiError('unknown', 502)
    assets.value = assets.value.map(item => item.id === asset.id ? amended : item); closeAmendment()
  })
}
function render() {
  const profile = artist.value, consent = currentConsent.value
  if (!canRender.value || !profile || !consent) return
  const request = { artist_id: profile.id, consent_version_id: consent.id, voice_id: props.voice.id, model_revision_id: modelId.value,
    guide_asset_id: guideId.value, accompaniment_asset_id: accompanimentId.value, title: title.value.trim(), guide_singer_confirmed: singerConfirmed.value, guide_isolated: isolated.value, chosen_key: chosenKey.value.trim() }
  void perform(signal => api.startCleanRender(request, signal), upsertRun)
}
function reviewRun(run: ProductionRun) {
  if (run.status !== 'done' || !operator.value.trim() || !releaseEvidence.value.trim()) return
  const request: ReleaseReviewRequest = { reviewer_label: operator.value.trim(), evidence_ref: releaseEvidence.value.trim(), composition_rights: composition.value, master_rights: master.value, voice_rights: voiceRights.value, dependency_rights: dependency.value, note: releaseNote.value.trim() }
  void perform(signal => api.reviewRelease(run.id, request, signal), upsertRun)
}
function approveGuideRange(run: ProductionRun) {
  const assessment = run.range_assessment
  const profileId = assessment?.profile_version_id
  if (assessment?.analysis_status !== 'observed' || assessment.decision !== 'needs_approval' || !profileId
      || !operator.value.trim() || !guideRangeEvidence.value.trim() || guideRangeDeclaration.value.trim().length < 20) return
  void perform(signal => api.startCleanRender({ ...run.request, range_approval: {
    profile_version_id: profileId, assessment_sha256: assessment.assessment_sha256,
    evidence_ref: guideRangeEvidence.value.trim(), operator_label: operator.value.trim(),
    artist_confirmation_declaration: guideRangeDeclaration.value.trim(),
  } }, signal), upsertRun)
}
function rangeWarning(value: string): string {
  const key = `singerRange.warnings.${value}`
  return te(key) ? t(key) : t('singerRange.warnings.unknown')
}
function audioUrl(run: ProductionRun): string | null {
  return run.status === 'done' && run.track_id && run.audio_version_id ? `/api/tracks/${run.track_id}/versions/${run.audio_version_id}/audio` : null
}
watch(() => [props.voice.id, props.active] as const, (values, previous) => {
  generation++; controller?.abort(); poll.stop(); pending.value = false
  closeAmendment()
  if (values[0] !== previous?.[0]) {
    artist.value = null; consents.value = []; assets.value = []; runs.value = []; loaded.value = false
    artistName.value = ''; operator.value = ''; declaration.value = ''; evidence.value = ''; evidenceHash.value = ''
    allowedOperations.value = []; allowedUses.value = []; territories.value = ''; authorizedOperators.value = ''; starts.value = ''; ends.value = ''
    sourceFile.value = null; guideId.value = ''; accompanimentId.value = ''; modelId.value = props.voice.active_model_id ?? ''
    title.value = ''; chosenKey.value = ''; isolated.value = false; singerConfirmed.value = false
    provider.value = ''; rightsHolder.value = ''; acquisition.value = ''; sourceEvidence.value = ''; sourceScope.value = ''; terms.value = ''; sourceUses.value = []
    sourceReviewer.value = ''; reviewEvidence.value = ''; sourceReviewStatus.value = 'unknown'; sourceOrigin.value = 'unknown'
    releaseEvidence.value = ''; releaseNote.value = ''; composition.value = 'unknown'; master.value = 'unknown'; voiceRights.value = 'unknown'; dependency.value = 'unknown'
    notice.value = ''; error.value = ''
  }
  if (props.active) void reload()
}, { immediate: true })
onBeforeUnmount(() => { alive = false; generation++; controller?.abort(); poll.stop() })
</script>

<template>
  <section v-if="active" class="space-y-4 rounded-xl border border-border bg-panel-2 p-4" :aria-label="t('artistProduction.title')">
    <h3 class="text-base font-semibold text-text">{{ t('artistProduction.title') }}</h3>
    <p class="text-sm text-text-dim">{{ t('artistProduction.intro') }}</p>
    <p class="text-sm text-text-dim">{{ t('artistProduction.actorHint') }}</p>
    <p v-if="!currentConsent || currentConsent.status !== 'active'" class="text-sm text-status-queued">{{ t('artistProduction.legacy') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p><p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>
    <p v-if="pending && !loaded" role="status" class="text-sm text-text-dim">{{ t('artistProduction.loading') }}</p>
    <button type="button" :disabled="pending" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="reload">{{ t('artistProduction.refresh') }}</button>
    <div v-if="artist" class="text-sm text-text"><p>{{ t('artistProduction.profile') }}: {{ artist.name }}</p><p>{{ t(`artistProduction.status.${currentConsent?.status ?? 'needs_review'}`) }}</p></div>
    <div v-if="operatorRestricted" class="space-y-1 text-sm text-status-queued"><p class="break-words">{{ t('artistProduction.operatorRestriction', { operators: currentConsent?.authorized_operators?.join(', ') }) }}</p><p>{{ t('artistProduction.operatorAuthenticationRequired') }}</p></div>
    <form v-else class="space-y-3" @submit.prevent="createProfile"><label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.artistName') }}</span><input v-model="artistName" :aria-label="t('artistProduction.artistName')" maxlength="120" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><button type="submit" :disabled="pending || !loaded || !artistName.trim()" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50">{{ t('artistProduction.create') }}</button></form>
    <details class="rounded-lg border border-border p-3"><summary class="min-h-11 cursor-pointer text-sm font-medium text-text">{{ t('artistProduction.permission') }}</summary>
      <fieldset :disabled="pending" class="mt-3 space-y-3"><label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.operator') }}</span><input v-model="operator" :aria-label="t('artistProduction.operator')" maxlength="120" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.declaration') }}</span><textarea v-model="declaration" :aria-label="t('artistProduction.declaration')" maxlength="2000" rows="3" class="w-full rounded-lg border border-border bg-panel p-3" /></label>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.evidence') }}</span><input v-model="evidence" :aria-label="t('artistProduction.evidence')" maxlength="2048" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.evidenceHash') }}</span><input v-model="evidenceHash" :aria-label="t('artistProduction.evidenceHash')" maxlength="64" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
      <fieldset><legend class="text-sm text-text">{{ t('artistProduction.operations') }}</legend><label v-for="operation in operations" :key="operation" class="mr-4 inline-flex min-h-11 items-center gap-2 text-sm text-text"><input v-model="allowedOperations" type="checkbox" :value="operation" />{{ t(`artistProduction.operation.${operation}`) }}</label></fieldset>
      <fieldset><legend class="text-sm text-text">{{ t('artistProduction.uses') }}</legend><label v-for="use in uses" :key="use" class="mr-4 inline-flex min-h-11 items-center gap-2 text-sm text-text"><input v-model="allowedUses" type="checkbox" :value="use" />{{ t(`artistProduction.use.${use}`) }}</label></fieldset>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.authorizedOperators') }}</span><input v-model="authorizedOperators" :aria-label="t('artistProduction.authorizedOperators')" :aria-describedby="`artist-production-operator-hint-${voice.id}`" maxlength="12200" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
      <p :id="`artist-production-operator-hint-${voice.id}`" class="text-sm text-text-dim">{{ t('artistProduction.operatorScopeHint') }}</p><p class="text-sm text-text-dim">{{ t('artistProduction.operatorRecordingHint') }}</p>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.territories') }}</span><input v-model="territories" :aria-label="t('artistProduction.territories')" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
      <div class="grid gap-3 sm:grid-cols-2"><label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.starts') }}</span><input v-model="starts" type="datetime-local" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.ends') }}</span><input v-model="ends" type="datetime-local" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label></div>
      <p class="text-sm text-text-dim">{{ t('artistProduction.scopeHint') }}</p><div class="flex flex-wrap gap-3"><button type="button" :disabled="!canAppend" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="appendConsent('active')">{{ t('artistProduction.append') }}</button><button type="button" :disabled="!canDeclare" class="min-h-11 rounded-lg border border-status-failed/40 px-3 text-sm text-status-failed disabled:opacity-50" @click="appendConsent('revoked')">{{ t('artistProduction.revoke') }}</button></div></fieldset>
      <ul class="mt-3 space-y-2 text-sm text-text-dim"><li v-for="version in consents" :key="version.id" class="break-words">#{{ version.revision }} · {{ t(`artistProduction.status.${version.status ?? 'needs_review'}`) }} · {{ version.signed_evidence_ref || '—' }}</li></ul>
    </details>
    <details class="rounded-lg border border-border p-3"><summary class="min-h-11 cursor-pointer text-sm font-medium text-text">{{ t('artistProduction.script') }}</summary><p class="text-sm text-text-dim">{{ t('artistProduction.scriptHint') }}</p></details>
    <details class="rounded-lg border border-border p-3"><summary class="min-h-11 cursor-pointer text-sm font-medium text-text">{{ t('artistProduction.sources') }}</summary><p class="text-sm text-text-dim">{{ t('artistProduction.sourceHint') }}</p><p class="text-sm text-text-dim">{{ t('artistProduction.amendmentHint') }}</p>
      <fieldset :disabled="pending" class="mt-3 space-y-3"><div class="grid gap-3 sm:grid-cols-2"><label class="space-y-1 text-sm text-text"><span>{{ t('artistProduction.role') }}</span><select v-model="sourceRole" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option v-for="role in ['guide_vocal', 'accompaniment']" :key="role" :value="role">{{ t(`artistProduction.roles.${role}`) }}</option></select></label><label class="space-y-1 text-sm text-text"><span>{{ t('artistProduction.origin') }}</span><select v-model="sourceOrigin" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option v-for="origin in origins" :key="origin" :value="origin">{{ t(`artistProduction.origins.${origin}`) }}</option></select></label></div>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.audio') }}</span><input type="file" accept=".wav,.mp3,.flac,.ogg,.opus,.m4a" :aria-label="t('artistProduction.audio')" class="block min-h-11 max-w-full text-sm" @change="chooseSourceFile" /></label>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.provider') }}</span><input v-model="provider" maxlength="120" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.rightsHolder') }}</span><input v-model="rightsHolder" maxlength="300" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.acquisition') }}</span><input v-model="acquisition" maxlength="300" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.sourceEvidence') }}</span><input v-model="sourceEvidence" maxlength="500" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.sourceScope') }}</span><textarea v-model="sourceScope" maxlength="1000" rows="2" class="w-full rounded-lg border border-border bg-panel p-3" /></label><label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.terms') }}</span><input v-model="terms" maxlength="300" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
      <fieldset><legend class="text-sm text-text">{{ t('artistProduction.sourceUses') }}</legend><label v-for="use in sourceUseOptions" :key="use" class="mr-4 inline-flex min-h-11 items-center gap-2 text-sm text-text"><input v-model="sourceUses" type="checkbox" :value="use" />{{ t(`artistProduction.sourceUse.${use}`) }}</label></fieldset><button type="button" :disabled="!loaded || !sourceFile" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="upload">{{ t('artistProduction.upload') }}</button>
      <div class="grid gap-3 sm:grid-cols-2"><label class="space-y-1 text-sm text-text"><span>{{ t('artistProduction.sourceReviewer') }}</span><input v-model="sourceReviewer" :aria-label="t('artistProduction.sourceReviewer')" maxlength="200" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><label class="space-y-1 text-sm text-text"><span>{{ t('artistProduction.reviewEvidence') }}</span><input v-model="reviewEvidence" :aria-label="t('artistProduction.reviewEvidence')" maxlength="500" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label></div><label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.reviewStatus') }}</span><select v-model="sourceReviewStatus" :aria-label="t('artistProduction.reviewStatus')" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option v-for="status in reviewStatuses" :key="status" :value="status">{{ t(`artistProduction.rightsStatus.${status}`) }}</option></select></label>
      <ul class="space-y-3"><li v-for="asset in assets" :key="asset.id" class="space-y-3 rounded-lg border border-border p-3 text-sm text-text">
        <p class="break-words">{{ asset.declaration?.original_filename || asset.id }} · {{ t(`artistProduction.origins.${asset.origin}`) }} · {{ asset.provider }} · {{ asset.role }}</p>
        <p v-if="asset.reference_only" class="text-status-queued">{{ t('artistProduction.referenceOnly') }}</p>
        <p data-source-review-status>{{ t(`artistProduction.rightsStatus.${currentReviewStatus(asset)}`) }}</p>
        <p v-if="asset.declaration_revisions?.length" class="text-text-dim">{{ t('artistProduction.sourceRevisionCount', { count: asset.declaration_revisions.length }) }}</p>
        <ul v-if="asset.declaration_revisions?.length" class="space-y-1 text-text-dim"><li v-for="revision in asset.declaration_revisions" :key="revision.id" class="break-words">{{ revision.created_at }} · {{ revision.reviewer_label }} · {{ revision.declaration.evidence_ref || '—' }}</li></ul>
        <div class="flex flex-wrap gap-3">
          <button type="button" :disabled="!loaded || !sourceReviewer.trim() || !reviewEvidence.trim()" class="min-h-11 rounded-lg border border-border px-3 disabled:opacity-50" @click="reviewAsset(asset)">{{ t('artistProduction.review') }}</button>
          <button v-if="asset.declaration" type="button" :disabled="!loaded" class="min-h-11 rounded-lg border border-border px-3 disabled:opacity-50" @click="beginAmendment(asset)">{{ t('artistProduction.amendSource') }}</button>
        </div>
        <div v-if="amendmentAssetId === asset.id && amendmentDeclaration" data-source-revision class="space-y-3 border-t border-border pt-3">
          <p class="text-text-dim">{{ t('artistProduction.immutableSourceHint') }}</p>
          <label class="block space-y-1"><span>{{ t('artistProduction.amendmentReviewer') }}</span><input v-model="amendmentReviewer" :aria-label="t('artistProduction.amendmentReviewer')" maxlength="200" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
          <label class="block space-y-1"><span>{{ t('artistProduction.amendmentRightsHolder') }}</span><input v-model="amendmentDeclaration.rights_holder" :aria-label="t('artistProduction.amendmentRightsHolder')" maxlength="300" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
          <label class="block space-y-1"><span>{{ t('artistProduction.amendmentAcquisition') }}</span><input v-model="amendmentDeclaration.acquisition_method" :aria-label="t('artistProduction.amendmentAcquisition')" maxlength="300" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
          <label class="block space-y-1"><span>{{ t('artistProduction.amendmentEvidence') }}</span><input v-model="amendmentDeclaration.evidence_ref" :aria-label="t('artistProduction.amendmentEvidence')" maxlength="500" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
          <label class="block space-y-1"><span>{{ t('artistProduction.amendmentScope') }}</span><textarea v-model="amendmentDeclaration.permission_scope" :aria-label="t('artistProduction.amendmentScope')" maxlength="1000" rows="2" class="w-full rounded-lg border border-border bg-panel p-3" /></label>
          <label class="block space-y-1"><span>{{ t('artistProduction.amendmentTerms') }}</span><input v-model="amendmentDeclaration.provider_terms_version" :aria-label="t('artistProduction.amendmentTerms')" maxlength="300" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
          <fieldset><legend>{{ t('artistProduction.amendmentPermittedUses') }}</legend><label v-for="use in sourceUseOptions" :key="use" class="mr-4 inline-flex min-h-11 items-center gap-2"><input v-model="amendmentDeclaration.permitted_uses" type="checkbox" :value="use" />{{ t(`artistProduction.sourceUse.${use}`) }}</label></fieldset>
          <fieldset><legend>{{ t('artistProduction.amendmentBlockedUses') }}</legend><label v-for="use in sourceUseOptions" :key="use" class="mr-4 inline-flex min-h-11 items-center gap-2"><input v-model="amendmentDeclaration.blocked_uses" type="checkbox" :value="use" />{{ t(`artistProduction.sourceUse.${use}`) }}</label></fieldset>
          <div class="flex flex-wrap gap-3"><button type="button" :disabled="!amendmentReviewer.trim()" class="min-h-11 rounded-lg border border-border px-3 disabled:opacity-50" @click="amendAsset(asset)">{{ t('artistProduction.appendSourceRevision') }}</button><button type="button" class="min-h-11 rounded-lg border border-border px-3" @click="closeAmendment">{{ t('artistProduction.cancelSourceRevision') }}</button></div>
        </div>
      </li></ul></fieldset>
    </details>
    <div class="space-y-3"><h4 class="font-medium text-text">{{ t('artistProduction.clean') }}</h4><fieldset :disabled="pending" class="space-y-3"><div class="grid gap-3 sm:grid-cols-2"><label class="space-y-1 text-sm text-text"><span>{{ t('artistProduction.guide') }}</span><select v-model="guideId" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option value="">{{ t('artistProduction.choose') }}</option><option v-for="asset in guides" :key="asset.id" :value="asset.id" :disabled="!eligible(asset)">{{ asset.declaration?.original_filename || asset.id }}</option></select></label><label class="space-y-1 text-sm text-text"><span>{{ t('artistProduction.accompaniment') }}</span><select v-model="accompanimentId" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option value="">{{ t('artistProduction.choose') }}</option><option v-for="asset in accompaniments" :key="asset.id" :value="asset.id" :disabled="!eligible(asset)">{{ asset.declaration?.original_filename || asset.id }}</option></select></label></div>
    <label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.model') }}</span><select v-model="modelId" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option v-for="model in modelChoices" :key="model.id" :value="model.id">{{ model.id }} · {{ model.steps }}</option></select></label><label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.renderTitle') }}</span><input v-model="title" maxlength="300" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><label class="block space-y-1 text-sm text-text"><span>{{ t('artistProduction.chosenKey') }}</span><input v-model="chosenKey" maxlength="40" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
    <label class="flex min-h-11 items-center gap-2 text-sm text-text"><input v-model="isolated" type="checkbox" />{{ t('artistProduction.isolated') }}</label><label class="flex min-h-11 items-center gap-2 text-sm text-text"><input v-model="singerConfirmed" type="checkbox" />{{ t('artistProduction.singer') }}</label></fieldset><button type="button" :disabled="!canRender" class="min-h-11 rounded-lg bg-accent1 px-4 text-sm text-white disabled:opacity-50" @click="render">{{ t('artistProduction.render') }}</button><p v-if="!canRender" class="text-sm text-text-dim">{{ t('artistProduction.blocked') }}</p></div>
    <div class="space-y-3"><h4 class="font-medium text-text">{{ t('artistProduction.runs') }}</h4><p class="text-sm text-text-dim">{{ t('artistProduction.technical') }}</p><article v-for="run in runs" :key="run.id" class="space-y-2 rounded-lg border border-border p-3 text-sm text-text"><p>{{ run.request.title }} · {{ t(`jobStatus.${run.status}`) }}</p><p v-if="run.error_code" role="alert" class="text-status-failed">{{ errorText(new ApiError(run.error_code, 409)) }}</p><p>{{ t(run.gate?.approved_for_release ? 'artistProduction.releaseApproved' : 'artistProduction.releaseBlocked') }}</p><p v-if="!run.model.dependency_inventory_complete" class="text-status-queued">{{ t('artistProduction.dependencyUnknown') }}</p><ul class="space-y-1 text-text-dim"><li v-for="blocker in run.gate?.blockers" :key="blocker">{{ errorText(new ApiError(blocker, 409)) }}</li></ul><div v-if="audioUrl(run)" role="group" :aria-label="t('artistProduction.result')"><WaveformPlayer :src="audioUrl(run) ?? ''" /></div>
    <details v-if="run.status === 'done'"><summary class="min-h-11 cursor-pointer">{{ t('artistProduction.releaseReview') }}</summary><p class="text-text-dim">{{ t('artistProduction.releaseHint') }}</p><fieldset :disabled="pending" class="space-y-3"><label class="block space-y-1"><span>{{ t('artistProduction.releaseEvidence') }}</span><input v-model="releaseEvidence" maxlength="500" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label><div class="grid gap-3 sm:grid-cols-2"><label class="space-y-1"><span>{{ t('artistProduction.composition') }}</span><select v-model="composition" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option v-for="status in reviewStatuses" :key="status" :value="status">{{ t(`artistProduction.rightsStatus.${status}`) }}</option></select></label><label class="space-y-1"><span>{{ t('artistProduction.master') }}</span><select v-model="master" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option v-for="status in reviewStatuses" :key="status" :value="status">{{ t(`artistProduction.rightsStatus.${status}`) }}</option></select></label><label class="space-y-1"><span>{{ t('artistProduction.voiceRights') }}</span><select v-model="voiceRights" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option v-for="status in reviewStatuses" :key="status" :value="status">{{ t(`artistProduction.rightsStatus.${status}`) }}</option></select></label><label class="space-y-1"><span>{{ t('artistProduction.dependency') }}</span><select v-model="dependency" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3"><option v-for="status in reviewStatuses" :key="status" :value="status">{{ t(`artistProduction.rightsStatus.${status}`) }}</option></select></label></div><label class="block space-y-1"><span>{{ t('artistProduction.releaseNote') }}</span><textarea v-model="releaseNote" maxlength="2000" rows="2" class="w-full rounded-lg border border-border bg-panel p-3" /></label><button type="button" :disabled="!operator.trim() || !releaseEvidence.trim()" class="min-h-11 rounded-lg border border-border px-3 disabled:opacity-50" @click="reviewRun(run)">{{ t('artistProduction.releaseReview') }}</button></fieldset></details></article></div>
    <SingerRangePanel v-if="artist" :key="`range-${artist.id}`" :artist-id="artist.id" :active="active" />
    <RecordingQualityPanel :key="`recording-qc-${voice.id}`" :voice="voice" :active="active" @labels="emit('labels', $event)" />
    <div v-for="run in runs.filter(item => item.range_assessment)" :key="`range-assessment-${run.id}`" class="space-y-3 rounded-lg border border-border p-3 text-sm">
      <h4 class="font-medium">{{ run.request.title }} · {{ t('artistProduction.guideRangeAssessment') }}</h4>
      <p>{{ run.range_assessment?.analysis_status }} · {{ run.range_assessment?.decision }}</p>
      <ul class="space-y-1 text-text-dim"><li v-for="warning in run.range_assessment?.warnings" :key="warning">{{ rangeWarning(warning) }}</li></ul>
      <p v-for="note in run.range_assessment?.out_of_range_notes" :key="`${note.start_sec}-${note.midi_note}`">MIDI {{ note.midi_note }} · {{ note.start_sec }}–{{ note.end_sec }} s · {{ note.timing_basis }}</p>
      <div v-if="run.range_assessment?.decision === 'needs_approval' && run.range_assessment.analysis_status === 'observed'" class="space-y-2">
        <label class="block space-y-1"><span>{{ t('artistProduction.guideRangeEvidence') }}</span><input v-model="guideRangeEvidence" maxlength="500" class="min-h-11 w-full rounded-lg border border-border bg-panel px-3" /></label>
        <label class="block space-y-1"><span>{{ t('artistProduction.guideRangeConfirmation') }}</span><textarea v-model="guideRangeDeclaration" maxlength="2000" class="w-full rounded-lg border border-border bg-panel p-3" /></label>
        <button type="button" :disabled="pending || !operator.trim() || !guideRangeEvidence.trim() || guideRangeDeclaration.trim().length < 20" class="min-h-11 rounded-lg border border-border px-3 disabled:opacity-50" @click="approveGuideRange(run)">{{ t('artistProduction.guideRangeApprove') }}</button>
      </div>
    </div>
    <RoyaltyStatementsPanel v-if="artist" :key="`statements-${artist.id}`" :artist-id="artist.id" :active="active" />
    <p class="text-sm text-text-dim">{{ t('artistProduction.retention') }}</p>
  </section>
</template>

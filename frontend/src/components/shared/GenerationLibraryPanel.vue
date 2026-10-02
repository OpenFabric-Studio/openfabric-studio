<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import * as api from '../../api/generationLibrary'
import type { GenerationEngine, GenerationSnapshot, GenerationHistoryEntry, GenerationPreset } from '../../api/generationLibrary'
import { ApiError } from '../../api/http'
import { legacyPresetImports, historyResultSettings } from '../../composables/generationSnapshots'
import { editingGenerationPreset, reuseGenerationSettings } from '../../composables/generationDrafts'
import { trackAudioUrl } from '../../api/tracks'
import HelpModal from './HelpModal.vue'
import TrackAudioVersions from './TrackAudioVersions.vue'

const props = defineProps<{ engine?: GenerationEngine; currentSettings?: GenerationSnapshot }>()
const emit = defineEmits<{ reused: [] }>()
const { t } = useI18n()
const router = useRouter()
const tab = ref<'history' | 'presets'>('history')
const searchLabel = computed(() => t(tab.value === 'presets' ? 'generationWorkspace.presetSearch' : 'generationWorkspace.search'))
const filterEngine = ref<GenerationEngine | ''>(props.engine ?? '')
const search = ref('')
const offset = ref(0)
const total = ref(0)
const presetTotal = ref(0)
const history = ref<GenerationHistoryEntry[]>([])
const presets = ref<GenerationPreset[]>([])
const selectedHistory = ref<GenerationHistoryEntry | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const name = ref('')
type NameAction = { kind: 'create'; settings: GenerationSnapshot; historyId: string | null } | { kind: 'rename' | 'duplicate'; preset: GenerationPreset }
const nameAction = ref<NameAction | null>(null)
const deleteAction = ref<GenerationPreset | null>(null)
const editing = computed(() => editingGenerationPreset.value?.engine === props.currentSettings?.engine ? editingGenerationPreset.value : null)
let alive = true
let generation = 0
let controller: AbortController | null = null
let mutationController: AbortController | null = null
function failure(cause: unknown, fallback = 'saveFailed'): string {
  if (cause instanceof ApiError && cause.message === 'generation_preset_name_exists') return t('generationWorkspace.nameExists')
  if (cause instanceof ApiError && cause.message === 'generation_revision_conflict') return t('generationWorkspace.conflict')
  return t(`generationWorkspace.${fallback}`)
}
async function load(refreshEditor = false) {
  const token = ++generation
  controller?.abort(); const request = new AbortController(); controller = request
  loading.value = true; error.value = ''
  const filter = { engine: filterEngine.value || undefined, search: search.value, offset: offset.value }
  const edited = refreshEditor ? editing.value : null
  const results = await Promise.allSettled([api.listHistory(filter, request.signal), api.listPresets(filter, request.signal), edited ? api.getPreset(edited.id, request.signal) : Promise.resolve(null)])
  if (!alive || token !== generation) return
  const [historyResult, presetResult, editingResult] = results
  if (historyResult?.status === 'fulfilled') { history.value = historyResult.value.data; total.value = historyResult.value.total }
  if (presetResult?.status === 'fulfilled') { presets.value = presetResult.value.data; presetTotal.value = presetResult.value.total }
  if (edited && editingGenerationPreset.value?.id === edited.id) {
    if (editingResult.status === 'fulfilled' && editingResult.value) editingGenerationPreset.value = editingResult.value
    else if (editingResult.status === 'rejected' && editingResult.reason instanceof ApiError && editingResult.reason.status === 404) { editingGenerationPreset.value = null; notice.value = t('generationWorkspace.presetMissing') }
  }
  if (results.some(result => result.status === 'rejected')) error.value = t('generationWorkspace.failed')
  loading.value = false; controller = null
}
function reload() { void load(true) }
async function migrateAndLoad() {
  const request = new AbortController(); mutationController = request
  try {
    const imports = legacyPresetImports(localStorage.getItem('acestep_presets'), localStorage.getItem('yue2_presets'))
    for (let offset = 0; offset < imports.length && alive && !request.signal.aborted; offset += 1000) {
      await api.importPresets({ presets: imports.slice(offset, offset + 1000) }, request.signal)
    }
  } catch (cause) {
    if (alive && !request.signal.aborted) notice.value = failure(cause)
  } finally {
    if (mutationController === request) mutationController = null
  }
  if (alive && !request.signal.aborted) await load()
}
function reuse(settings: GenerationSnapshot, preset: GenerationPreset | null = null) {
  reuseGenerationSettings(settings, preset)
  emit('reused')
  if (props.engine !== settings.engine) void router.push(settings.engine === 'ace_step' ? '/ace-step' : '/yue2')
}
function create(settings: GenerationSnapshot, historyId: string | null = null, suggested = '') {
  name.value = suggested.slice(0, 200); nameAction.value = { kind: 'create', settings, historyId }
}
function named(kind: 'rename' | 'duplicate', preset: GenerationPreset) {
  name.value = kind === 'rename' ? preset.name : `${preset.name.slice(0, 193)} (copy)`; nameAction.value = { kind, preset }
}
async function mutate(action: (signal: AbortSignal) => Promise<unknown>): Promise<boolean> {
  if (saving.value) return false
  saving.value = true; error.value = ''; notice.value = ''
  const request = new AbortController(); mutationController = request
  try {
    await action(request.signal)
    if (!alive || request.signal.aborted) return false
    notice.value = t('generationWorkspace.saved')
    await load(); return alive
  } catch (cause) { if (alive && !request.signal.aborted) error.value = failure(cause); return false }
  finally { if (alive && mutationController === request) { saving.value = false; mutationController = null } }
}
async function saveNamed() {
  const action = nameAction.value; const label = name.value.trim()
  if (!action || !label || label.length > 200) return
  const succeeded = await mutate(signal => action.kind === 'create'
    ? api.createPreset({ name: label, settings: action.settings, source_history_id: action.historyId }, signal)
    : action.kind === 'rename'
      ? api.updatePreset(action.preset.id, { name: label, settings: action.preset.settings, revision: action.preset.revision, source_history_id: action.preset.source_history_id }, signal)
      : api.duplicatePreset(action.preset.id, { name: label, revision: action.preset.revision }, signal))
  if (succeeded) nameAction.value = null
}
async function updateCurrent() {
  const preset = editing.value; const settings = props.currentSettings
  if (!preset || !settings) return
  const succeeded = await mutate(async signal => {
    const updated = await api.updatePreset(preset.id, { name: preset.name, settings, revision: preset.revision, source_history_id: preset.source_history_id }, signal)
    if (alive && !signal.aborted) editingGenerationPreset.value = updated
  })
  if (succeeded) tab.value = 'presets'
}
async function confirmDelete() {
  const preset = deleteAction.value
  if (!preset) return
  if (await mutate(signal => api.deletePreset(preset.id, preset.revision, signal))) {
    deleteAction.value = null
    if (editingGenerationPreset.value?.id === preset.id) editingGenerationPreset.value = null
  }
}
function needsReference(settings: GenerationSnapshot): boolean {
  return settings.engine === 'ace_step' ? settings.useRefAudio || settings.styleReferenceRequiresReupload : settings.referenceRequiresReupload
}
function engineName(engine: GenerationEngine): string { return engine === 'ace_step' ? 'ACE-Step' : 'YuE' }
watch([tab, search, filterEngine], () => { if (offset.value !== 0) offset.value = 0; else void load() })
watch(offset, () => { void load() })
onMounted(() => { void migrateAndLoad() })
onBeforeUnmount(() => { alive = false; generation++; controller?.abort(); mutationController?.abort() })
</script>

<template>
  <section class="space-y-3" :aria-busy="loading || saving">
    <div class="flex flex-wrap items-center gap-2">
      <button type="button" class="library-button" :aria-pressed="tab === 'history'" @click="tab = 'history'">{{ t('generationWorkspace.history') }}</button>
      <button type="button" class="library-button" :aria-pressed="tab === 'presets'" @click="tab = 'presets'">{{ t('generationWorkspace.presets') }}</button>
      <button type="button" class="library-button ml-auto" :disabled="loading || saving" @click="reload">{{ t('generationWorkspace.reload') }}</button>
    </div>
    <div v-if="currentSettings" class="flex flex-wrap gap-2">
      <button type="button" class="library-button" :disabled="saving" @click="create(currentSettings)">{{ t('generationWorkspace.saveCurrent') }}</button>
      <template v-if="editing">
        <button type="button" class="library-button" :disabled="saving" @click="updateCurrent">{{ t('generationWorkspace.updateCurrent', { name: editing.name }) }}</button>
        <button type="button" class="library-button" :disabled="saving" @click="editingGenerationPreset = null">{{ t('generationWorkspace.cancelEdit') }}</button>
      </template>
    </div>
    <div class="flex gap-2">
      <input v-model="search" type="search" maxlength="500" :aria-label="searchLabel" :placeholder="searchLabel" class="min-w-0 flex-1 rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" />
      <select v-if="!engine" v-model="filterEngine" :aria-label="t('generationWorkspace.engine')" class="rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"><option value="">{{ t('generationWorkspace.allEngines') }}</option><option value="ace_step">ACE-Step</option><option value="yue2">YuE</option></select>
    </div>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="notice" role="status" class="text-xs text-text-dim">{{ notice }}</p>
    <p v-if="loading" role="status" class="text-xs text-text-dim">{{ t('generationWorkspace.loading') }}</p>
    <div v-if="tab === 'history'" class="space-y-2">
      <article v-for="entry in history" :key="entry.id" class="space-y-2 rounded-lg border border-border bg-panel-2 p-3">
        <p class="text-sm font-medium text-text">{{ entry.title || engineName(entry.engine) }} <span class="text-xs font-normal text-text-dim">{{ engineName(entry.engine) }} · {{ entry.created_at }}</span></p>
        <p class="line-clamp-2 whitespace-pre-line text-xs text-text-dim">{{ entry.lyrics }}</p>
        <p v-if="entry.reference_requires_reupload || needsReference(entry.settings)" class="text-xs text-status-queued">{{ t('generationWorkspace.reupload') }}</p>
        <p v-if="entry.track_id === null" class="text-xs text-text-dim">{{ t('generationWorkspace.unavailableAudio') }}</p>
        <div class="flex flex-wrap gap-2">
          <button v-if="entry.track_id !== null" type="button" class="library-button" @click="selectedHistory = entry">{{ t('generationWorkspace.preview') }}</button>
          <button type="button" class="library-button" @click="reuse(historyResultSettings(entry))">{{ t('generationWorkspace.reuse') }}</button>
          <button type="button" class="library-button" :disabled="saving" @click="create(historyResultSettings(entry), entry.id, entry.title)">{{ t('generationWorkspace.favorite') }}</button>
        </div>
      </article>
      <p v-if="!loading && !history.length" class="text-sm text-text-dim">{{ t('generationWorkspace.empty') }}</p>
      <div v-if="total" class="flex flex-wrap items-center gap-2 text-xs text-text-dim">
        <span>{{ t('generationWorkspace.results', { total, start: offset + 1, end: Math.min(offset + history.length, total) }) }}</span>
        <button type="button" class="library-button" :disabled="loading || offset === 0" @click="offset = Math.max(0, offset - 100)">{{ t('generationWorkspace.previous') }}</button>
        <button type="button" class="library-button" :disabled="loading || offset + history.length >= total" @click="offset += 100">{{ t('generationWorkspace.next') }}</button>
      </div>
    </div>
    <div v-else class="space-y-2">
      <article v-for="preset in presets" :key="preset.id" class="space-y-2 rounded-lg border border-border bg-panel-2 p-3">
        <p class="text-sm font-medium text-text">{{ preset.name }} <span class="text-xs font-normal text-text-dim">{{ engineName(preset.engine) }}</span></p>
        <p v-if="needsReference(preset.settings)" class="text-xs text-status-queued">{{ t('generationWorkspace.reupload') }}</p>
        <p v-if="preset.settings.engine === 'ace_step' && preset.settings.loraRequiresReselection" class="text-xs text-status-queued">{{ t('generationWorkspace.reselectLora') }}</p>
        <div class="flex flex-wrap gap-2">
          <button type="button" class="library-button" @click="reuse(preset.settings)">{{ t('generationWorkspace.reuse') }}</button>
          <button type="button" class="library-button" @click="reuse(preset.settings, preset)">{{ t('generationWorkspace.edit') }}</button>
          <button type="button" class="library-button" :disabled="saving" @click="named('rename', preset)">{{ t('generationWorkspace.rename') }}</button>
          <button type="button" class="library-button" :disabled="saving" @click="named('duplicate', preset)">{{ t('generationWorkspace.duplicate') }}</button>
          <button type="button" class="library-button text-status-failed" :disabled="saving" @click="deleteAction = preset">{{ t('generationWorkspace.delete') }}</button>
        </div>
      </article>
      <p v-if="!loading && !presets.length" class="text-sm text-text-dim">{{ t('generationWorkspace.empty') }}</p>
      <div v-if="presetTotal" class="flex flex-wrap items-center gap-2 text-xs text-text-dim">
        <span>{{ t('generationWorkspace.results', { total: presetTotal, start: offset + 1, end: Math.min(offset + presets.length, presetTotal) }) }}</span>
        <button type="button" class="library-button" :disabled="loading || offset === 0" @click="offset = Math.max(0, offset - 100)">{{ t('generationWorkspace.previous') }}</button>
        <button type="button" class="library-button" :disabled="loading || offset + presets.length >= presetTotal" @click="offset += 100">{{ t('generationWorkspace.next') }}</button>
      </div>
    </div>
    <HelpModal :open="!!selectedHistory" :title="t('generationWorkspace.preview')" @close="selectedHistory = null">
      <TrackAudioVersions v-if="selectedHistory?.track_id != null" :track-id="selectedHistory.track_id" :fallback-audio-url="trackAudioUrl(selectedHistory.track_id)" />
      <pre class="whitespace-pre-wrap text-xs">{{ selectedHistory?.lyrics }}</pre>
    </HelpModal>
    <HelpModal :open="!!nameAction" :title="t('generationWorkspace.name')" @close="!saving && (nameAction = null)">
      <form class="space-y-3" @submit.prevent="saveNamed">
        <input v-model="name" maxlength="200" required :aria-label="t('generationWorkspace.name')" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" />
        <button type="submit" class="library-button" :disabled="saving || !name.trim()">{{ t('generationWorkspace.save') }}</button>
      </form>
    </HelpModal>
    <HelpModal :open="!!deleteAction" :title="t('generationWorkspace.confirmDelete', { name: deleteAction?.name })" @close="!saving && (deleteAction = null)">
      <p>{{ t('generationWorkspace.deleteHint') }}</p>
      <button type="button" class="library-button text-status-failed" :disabled="saving" @click="confirmDelete">{{ t('generationWorkspace.confirm') }}</button>
      <button type="button" class="library-button ml-2" :disabled="saving" @click="deleteAction = null">{{ t('generationWorkspace.cancel') }}</button>
    </HelpModal>
  </section>
</template>
<style scoped>
@reference "../../style.css";
.library-button { @apply rounded-lg border border-border px-2.5 py-1.5 text-xs text-text hover:bg-panel-2 disabled:opacity-50; }
.library-button[aria-pressed="true"] { @apply border-accent1 text-accent1; }
</style>

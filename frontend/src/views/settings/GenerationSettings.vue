<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/generationLibrary'
import type { GenerationLibrarySettings } from '../../api/generationLibrary'
import { ApiError } from '../../api/http'
import { completionNotifications, enableCompletionNotifications, notificationCapability, markGenerationsRead } from '../../composables/completionNotifications'
import HelpModal from '../../components/shared/HelpModal.vue'
import GenerationLibraryPanel from '../../components/shared/GenerationLibraryPanel.vue'
import ReferencePreparationButton from '../../components/shared/ReferencePreparationButton.vue'
const { t } = useI18n()
const saved = ref<GenerationLibrarySettings | null>(null)
const limit = ref(100)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const open = ref(false)
const notificationBusy = ref(false)
const notificationSupported = ref<boolean | null>(null)
const valid = computed(() => Number.isInteger(limit.value) && limit.value >= 1 && limit.value <= 10000)
const dirty = computed(() => !!saved.value && saved.value.history_limit !== limit.value)
let alive = true
let token = 0
let draftRevision = 0
let controller: AbortController | null = null
watch(limit, () => { draftRevision++ }, { flush: 'sync' })
async function load() {
  if (saving.value) return
  const generation = ++token; const initialDraft = draftRevision; const keepDraft = dirty.value
  controller?.abort(); const request = new AbortController(); controller = request
  loading.value = true; error.value = ''
  try {
    const response = await api.getSettings(request.signal)
    if (!alive || generation !== token) return
    saved.value = response
    if (!keepDraft && initialDraft === draftRevision) limit.value = response.history_limit
  } catch { if (alive && generation === token) error.value = t('generationWorkspace.failed') }
  finally { if (alive && generation === token) { loading.value = false; controller = null } }
}
async function save() {
  if (!saved.value || !valid.value || !dirty.value || saving.value || loading.value) return
  const generation = ++token; const initialDraft = draftRevision
  const request = new AbortController(); controller = request; saving.value = true; error.value = ''; notice.value = ''
  try {
    const response = await api.updateSettings({ history_limit: limit.value, revision: saved.value.revision }, request.signal)
    if (!alive || generation !== token) return
    saved.value = response
    if (initialDraft === draftRevision) limit.value = response.history_limit
    notice.value = t('generationWorkspace.saved')
  } catch (cause) { if (alive && generation === token) error.value = t(cause instanceof ApiError && cause.message === 'generation_revision_conflict' ? 'generationWorkspace.conflict' : 'generationWorkspace.saveFailed') }
  finally { if (alive && generation === token) { saving.value = false; controller = null } }
}
async function notifications(event: Event) {
  if (!(event.target instanceof HTMLInputElement) || notificationBusy.value) return
  const enabled = event.target.checked; notificationBusy.value = true
  try { await enableCompletionNotifications(enabled) } finally { if (alive) notificationBusy.value = false }
}
onMounted(() => { void load(); void notificationCapability().then(supported => { if (alive) notificationSupported.value = supported }) })
onBeforeUnmount(() => { alive = false; token++; controller?.abort() })
</script>
<template>
  <section class="space-y-4 rounded-xl border border-border bg-panel p-5" :aria-busy="loading || saving">
    <h2 class="text-lg font-semibold text-text">{{ t('generationWorkspace.title') }}</h2>
    <p class="text-sm text-text-dim">{{ t('generationWorkspace.metadataOnly') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>
    <form v-if="saved" class="flex flex-wrap items-end gap-3" @submit.prevent="save">
      <label class="block space-y-1 text-sm text-text">
        <span>{{ t('generationWorkspace.limit') }}</span>
        <input v-model.number="limit" type="number" min="1" max="10000" step="1" :aria-label="t('generationWorkspace.limit')" class="block rounded-lg border border-border bg-panel-2 p-2 text-text" />
        <span class="block text-xs text-text-dim">{{ t('generationWorkspace.limitHint') }}</span>
      </label>
      <button type="submit" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" :disabled="loading || saving || !valid || !dirty">{{ t('generationWorkspace.save') }}</button>
    </form>
    <button type="button" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" :disabled="loading || saving" @click="load">{{ t('generationWorkspace.reload') }}</button>
    <button type="button" class="ml-2 rounded-lg border border-border px-3 py-2 text-sm text-text" @click="open = true">{{ t('generationWorkspace.open') }}</button>
    <ReferencePreparationButton />
    <div class="space-y-2 border-t border-border pt-4">
      <h3 class="font-medium text-text">{{ t('generationWorkspace.notifications') }}</h3>
      <label class="flex items-center gap-2 text-sm text-text-dim"><input type="checkbox" :checked="completionNotifications.enabled" :disabled="notificationBusy || notificationSupported === false" @change="notifications" />{{ t('generationWorkspace.enableNotifications') }}</label>
      <p v-if="notificationSupported === false" class="text-xs text-text-dim">{{ t('generationWorkspace.notificationUnsupported') }}</p>
      <p v-if="completionNotifications.status === 'denied'" role="status" class="text-xs text-status-queued">{{ t('generationWorkspace.notificationDenied') }}</p>
      <p v-if="completionNotifications.unread.length" class="text-xs text-accent1">{{ t('generationWorkspace.unread', { count: completionNotifications.unread.length }) }} <button type="button" class="underline" @click="markGenerationsRead()">{{ t('generationWorkspace.markRead') }}</button></p>
    </div>
    <HelpModal :open="open" :title="t('generationWorkspace.title')" @close="open = false"><GenerationLibraryPanel v-if="open" /></HelpModal>
  </section>
</template>

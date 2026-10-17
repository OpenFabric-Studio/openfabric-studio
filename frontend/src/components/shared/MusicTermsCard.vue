<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { acceptMusicTerms, getMusicTerms, updateMusicTermsSettings } from '../../api/musicTerms'
import { ApiError } from '../../api/http'
import type { MusicTermsSnapshot } from '../../api/generated'

const props = defineProps<{ engine: MusicTermsSnapshot['engine']; operation: MusicTermsSnapshot['operation'] }>()
const emit = defineEmits<{ blocked: [value: boolean] }>()
const { t } = useI18n()
const snapshot = shallowRef<MusicTermsSnapshot | null>(null)
const loading = ref(false), saving = ref(false), reviewed = ref(false), allowNoncommercial = ref(false)
const error = ref<'offline' | 'saveFailed' | 'changed' | ''>('')
const requiresNoncommercial = computed(() => snapshot.value?.requirements.some(row => row.restriction === 'noncommercial') ?? false)
const needsNoncommercialChoice = computed(() => requiresNoncommercial.value && !snapshot.value?.noncommercial_enabled)
const blocked = computed(() => snapshot.value ? !snapshot.value.allowed : false)
const canDisable = computed(() => props.operation === 'setup' && snapshot.value?.noncommercial_enabled)
let active = true, generation = 0
let controller: AbortController | null = null

watch(blocked, value => emit('blocked', value), { immediate: true })

async function load(reset = false, changed = false) {
  const token = ++generation
  controller?.abort(); controller = new AbortController()
  loading.value = true; saving.value = false; reviewed.value = false; allowNoncommercial.value = false
  error.value = changed ? 'changed' : ''
  if (reset) snapshot.value = null
  try {
    const result = await getMusicTerms(props.engine, props.operation, controller.signal)
    if (!active || token !== generation) return
    snapshot.value = result
  } catch { if (active && token === generation) error.value = 'offline' }
  finally { if (active && token === generation) loading.value = false }
}

async function accept() {
  const current = snapshot.value
  if (!current || !blocked.value || loading.value || saving.value || !reviewed.value || (needsNoncommercialChoice.value && !allowNoncommercial.value)) return
  const token = ++generation
  controller?.abort(); controller = new AbortController(); saving.value = true; error.value = ''
  try {
    const result = await acceptMusicTerms({ engine: props.engine, operation: props.operation, expected_revision: current.revision, terms_digests: current.requirements.map(row => row.terms_digest), allow_noncommercial: needsNoncommercialChoice.value && allowNoncommercial.value }, controller.signal)
    if (!active || token !== generation) return
    if (result.engine !== props.engine || result.operation !== props.operation) { error.value = 'saveFailed'; return }
    snapshot.value = result; reviewed.value = false; allowNoncommercial.value = false
  } catch (cause) {
    if (!active || token !== generation) return
    if (cause instanceof ApiError && cause.status === 409) await load(false, true)
    else error.value = 'saveFailed'
  } finally { if (active && token === generation) saving.value = false }
}

async function disableNoncommercial() {
  const current = snapshot.value
  if (!current || !canDisable.value || loading.value || saving.value) return
  const token = ++generation
  controller?.abort(); controller = new AbortController(); saving.value = true; error.value = ''
  try {
    const result = await updateMusicTermsSettings({ expected_revision: current.revision, noncommercial_enabled: false }, controller.signal)
    if (!active || token !== generation) return
    snapshot.value = { ...current, ...result, allowed: current.allowed && !requiresNoncommercial.value }
    await load()
  } catch (cause) {
    if (!active || token !== generation) return
    if (cause instanceof ApiError && cause.status === 409) await load(false, true)
    else error.value = 'saveFailed'
  } finally { if (active && token === generation) saving.value = false }
}

watch(() => [props.engine, props.operation], () => { void load(true) }, { immediate: true })
onBeforeUnmount(() => { active = false; ++generation; controller?.abort() })
</script>

<template>
  <section v-if="loading || error || snapshot?.requirements.length || canDisable" class="space-y-3 rounded-lg border border-border bg-panel-2 p-3" :aria-label="t('musicTerms.title')" :aria-busy="loading || saving">
    <h3 class="text-sm font-semibold text-text">{{ t('musicTerms.title') }} · {{ t(`musicTerms.operations.${operation}`) }}</h3>
    <p class="text-xs text-text-dim">{{ t('musicTerms.hint') }}</p>
    <p v-if="loading" role="status" class="text-xs text-text-dim">{{ t('musicTerms.loading') }}</p>
    <p v-if="error" role="status" class="text-xs text-status-queued">
      {{ t(`musicTerms.${error}`) }}
      <button v-if="error !== 'changed'" type="button" class="min-h-11 px-2 text-text underline focus-visible:outline-2 focus-visible:outline-accent1" :disabled="loading || saving" @click="load()">{{ t('musicTerms.retry') }}</button>
    </p>
    <ul v-if="snapshot?.requirements.length" class="space-y-3">
      <li v-for="row in snapshot.requirements" :key="row.component_id" class="space-y-1 text-xs">
        <p class="font-medium text-text">{{ row.name }} · {{ t(`moduleWorkspace.licenseScopes.${row.scope}`) }}</p>
        <span class="inline-block rounded-full border border-border px-2 py-1 text-status-queued">{{ t(`musicTerms.restrictions.${row.restriction}`) }}</span>
        <span v-if="row.acknowledged" class="ml-2 text-text-dim">{{ t('musicTerms.acknowledged') }}</span>
        <a :href="row.source_url" target="_blank" rel="noopener noreferrer" class="block min-h-11 py-3 text-text underline decoration-accent2 focus-visible:outline-2 focus-visible:outline-accent1">{{ t('musicTerms.source') }}</a>
      </li>
    </ul>
    <div v-if="snapshot && blocked" class="space-y-2">
      <label class="flex min-h-11 items-center gap-2 text-sm text-text"><input v-model="reviewed" type="checkbox" :disabled="loading || saving" class="h-4 w-4 accent-accent1" />{{ t('musicTerms.review') }}</label>
      <template v-if="needsNoncommercialChoice">
        <label class="flex min-h-11 items-center gap-2 text-sm text-text"><input v-model="allowNoncommercial" type="checkbox" :disabled="loading || saving" class="h-4 w-4 accent-accent1" />{{ t('musicTerms.enableNoncommercial') }}</label>
        <p class="text-xs text-text-dim">{{ t('musicTerms.noncommercialHint') }}</p>
      </template>
      <button type="button" class="min-h-11 rounded-lg border border-border px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="loading || saving || !reviewed || (needsNoncommercialChoice && !allowNoncommercial)" @click="accept">{{ saving ? t('musicTerms.saving') : t('musicTerms.accept') }}</button>
    </div>
    <p v-else-if="snapshot?.requirements.length" role="status" class="text-xs text-text-dim">{{ t('musicTerms.accepted') }}</p>
    <button v-if="snapshot?.allowed && snapshot.requirements.length" type="button" class="min-h-11 rounded-lg border border-border px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="loading || saving" @click="load()">{{ t('musicTerms.refresh') }}</button>
    <button v-if="canDisable" type="button" class="min-h-11 rounded-lg border border-border px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="loading || saving" @click="disableNoncommercial">{{ t('musicTerms.disableNoncommercial') }}</button>
  </section>
</template>

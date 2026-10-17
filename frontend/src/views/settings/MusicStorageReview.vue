<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/musicArtifacts'
import type { MusicArtifactCleanupPlan, MusicArtifactCleanupReceipt, MusicArtifactGroup, MusicArtifactInventory } from '../../api/generated'

const { t } = useI18n()
const open = ref(false)
const inventory = ref<MusicArtifactInventory | null>(null)
const plan = ref<MusicArtifactCleanupPlan | null>(null)
const receipt = ref<MusicArtifactCleanupReceipt | null>(null)
const selected = ref<string[]>([])
const reviewed = ref(false)
const busy = ref(false)
const uncertain = ref(false)
const error = ref('')
let active = true
let operation = 0
let controller: AbortController | null = null
const savedPlans = computed(() => [...new Set(inventory.value?.groups.flatMap(group => (group.dependencies ?? []).filter(item => item.kind === 'cleanup_plan').map(item => item.id)) ?? [])].filter(id => /^[a-f0-9]{32}$/.test(id)))
const removable = computed(() => plan.value?.items.filter(item => item.disposition === 'eligible') ?? [])
const bytes = computed(() => removable.value.reduce((sum, item) => sum + (item.bytes ?? 0), 0))
const canConfirm = computed(() => !!plan.value && removable.value.length > 0 && reviewed.value && !busy.value && !uncertain.value && plan.value.status !== 'executing' && plan.value.status !== 'completed' && receipt.value?.status !== 'completed')
function size(value: number | null | undefined): string {
  if (value == null) return t('musicArtifacts.unknownSize')
  return `${(value / 1024 ** 2).toLocaleString(undefined, { maximumFractionDigits: 1 })} MiB`
}
function analysisId(group: MusicArtifactGroup): string | undefined {
  return group.kind === 'analysis_workspace' && group.disposition === 'review' ? group.dependencies?.find(item => item.kind === 'analysis_job')?.id : undefined
}
function begin() {
  controller?.abort(); controller = new AbortController()
  busy.value = true; error.value = ''
  return { token: ++operation, signal: controller.signal }
}
function current(token: number) { return active && open.value && token === operation }
function finish(token: number) { if (current(token)) { busy.value = false; controller = null } }
async function refresh() {
  if (busy.value) return
  const { token, signal } = begin()
  try {
    const result = await api.getMusicArtifacts(signal)
    if (!current(token)) return
    inventory.value = result
    const valid = new Set(result.groups.map(analysisId).filter(id => id !== undefined))
    selected.value = selected.value.filter(id => valid.has(id))
  } catch { if (current(token)) error.value = t('musicArtifacts.loadFailed') }
  finally { finish(token) }
}
async function prepare() {
  if (busy.value || !selected.value.length || selected.value.length > 20) return
  const { token, signal } = begin()
  try {
    const result = await api.createMusicCleanupPlan({ analysis_job_ids: [...selected.value] }, signal)
    if (!current(token)) return
    plan.value = result; receipt.value = null; reviewed.value = false; uncertain.value = false
  } catch { if (current(token)) error.value = t('musicArtifacts.reviewFailed') }
  finally { finish(token) }
}
async function reconnect(id: string) {
  if (busy.value) return
  const { token, signal } = begin()
  reviewed.value = false
  try {
    const result = await api.getMusicCleanupPlan(id, signal)
    if (!current(token)) return
    plan.value = result; receipt.value = null; uncertain.value = result.status === 'executing'
    try {
      const saved = await api.getMusicCleanupReceipt(id, signal)
      if (!current(token)) return
      if (saved.plan_sha256 !== result.plan_sha256) throw new TypeError('Changed cleanup plan')
      receipt.value = saved; uncertain.value = false
    } catch { if (current(token) && result.status !== 'pending') uncertain.value = true }
  } catch { if (current(token)) error.value = t('musicArtifacts.actionFailed') }
  finally { finish(token) }
}
async function confirm() {
  if (!canConfirm.value || !plan.value) return
  const submitted = plan.value
  const { token, signal } = begin()
  reviewed.value = false
  try {
    const result = await api.confirmMusicCleanupPlan(submitted.id, { plan_sha256: submitted.plan_sha256, confirm_cleanup: true }, signal)
    if (!current(token)) return
    receipt.value = result; uncertain.value = false
    plan.value = { ...submitted, status: result.status === 'completed' ? 'completed' : 'partial' }
    selected.value = []
  } catch { if (current(token)) { uncertain.value = true; error.value = t('musicArtifacts.actionFailed') } }
  finally { finish(token) }
}
function toggle() {
  open.value = !open.value
  if (open.value) void refresh()
  else { operation++; controller?.abort(); controller = null; busy.value = false; reviewed.value = false }
}
onBeforeUnmount(() => { active = false; operation++; controller?.abort() })
</script>

<template>
  <section class="rounded-xl border border-border bg-panel p-5">
    <button type="button" class="storage-button text-left font-semibold" :aria-expanded="open" aria-controls="music-storage-review" @click="toggle">{{ t('musicArtifacts.title') }}</button>
    <div v-if="open" id="music-storage-review" class="mt-3 space-y-4" :aria-busy="busy">
      <p class="text-sm text-text-dim">{{ t('musicArtifacts.intro') }}</p>
      <button type="button" class="storage-button" :disabled="busy" @click="refresh">{{ t('musicArtifacts.refresh') }}</button>
      <p v-if="busy" role="status" class="text-sm text-text-dim">{{ t('musicArtifacts.loading') }}</p>
      <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
      <p v-if="inventory && !inventory.scan_complete" class="text-sm text-status-warning">{{ t('musicArtifacts.incomplete') }}</p>
      <p v-if="inventory && !inventory.groups.length" class="text-sm text-text-dim">{{ t('musicArtifacts.empty') }}</p>
      <ul v-if="inventory" class="max-h-96 space-y-2 overflow-y-auto">
        <li v-for="group in inventory.groups" :key="group.id" class="rounded-lg border border-border p-3 text-sm">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <label v-if="analysisId(group)" class="flex min-h-11 cursor-pointer items-center gap-3">
              <input v-model="selected" type="checkbox" :value="analysisId(group)" :aria-label="t('musicArtifacts.select', { id: group.id })" :disabled="busy || (selected.length >= 20 && !selected.includes(analysisId(group) ?? ''))">
              <span class="break-all">{{ group.root_alias }} / {{ group.relative_path ?? group.id }}</span>
            </label>
            <span v-else class="break-all">{{ group.root_alias }} / {{ group.relative_path ?? group.id }}</span>
            <span>{{ group.size_complete ? size(group.size_bytes) : group.size_bytes == null ? size(null) : t('musicArtifacts.partialSize', { size: size(group.size_bytes) }) }} · {{ t(`musicArtifacts.${group.disposition}`) }}</span>
          </div>
          <p v-for="reason in group.reasons" :key="reason" class="mt-1 text-text-dim">{{ t('musicArtifacts.reason', { reason: t(`musicArtifacts.reasons.${reason}`) }) }}</p>
        </li>
      </ul>
      <div v-if="inventory" class="flex flex-wrap items-center gap-3">
        <span class="text-sm text-text-dim">{{ t('musicArtifacts.selected', { count: selected.length }) }}</span>
        <button type="button" class="storage-button" :disabled="busy || !selected.length || selected.length > 20" @click="prepare">{{ t('musicArtifacts.prepare') }}</button>
      </div>
      <div v-if="savedPlans.length" class="flex flex-wrap gap-2">
        <button v-for="id in savedPlans" :key="id" type="button" class="storage-button" :disabled="busy" @click="reconnect(id)">{{ t('musicArtifacts.recover', { id: id.slice(0, 8) }) }}</button>
      </div>
      <section v-if="plan" class="space-y-3 rounded-lg border border-status-warning/50 p-4" aria-labelledby="music-cleanup-heading">
        <h3 id="music-cleanup-heading" class="font-semibold">{{ t('musicArtifacts.reviewTitle') }}</h3>
        <p class="text-sm text-text-dim">{{ t('musicArtifacts.planId', { id: plan.id.slice(0, 8) }) }}</p>
        <ul class="space-y-2 text-sm">
          <li v-for="item in plan.items" :key="item.job_id" class="rounded border border-border p-3">
            <p>{{ item.job_id.slice(0, 8) }} · {{ t(item.disposition === 'eligible' ? 'musicArtifacts.eligible' : 'musicArtifacts.keep') }} · {{ size(item.bytes) }}</p>
            <p v-if="item.source_sha256" class="text-text-dim">{{ t('musicArtifacts.source', { hash: item.source_sha256.slice(0, 12) }) }}</p>
            <p v-for="reason in item.reasons" :key="reason" class="text-text-dim">{{ t('musicArtifacts.reasons.' + reason) }}</p>
          </li>
        </ul>
        <p class="text-sm">{{ t('musicArtifacts.planBytes', { size: size(bytes) }) }}</p>
        <p class="text-sm text-status-warning">{{ t('musicArtifacts.warning') }}</p>
        <p v-if="uncertain || plan.status === 'executing'" role="status" class="text-sm">{{ t('musicArtifacts.pending') }}</p>
        <div v-if="receipt" role="status" class="space-y-2 text-sm">
          <p>{{ t(receipt.status === 'completed' ? 'musicArtifacts.completed' : receipt.status === 'partial' ? 'musicArtifacts.partial' : 'musicArtifacts.receiptBlocked') }}</p>
          <p v-for="item in receipt.items" :key="item.job_id">{{ item.job_id.slice(0, 8) }} · {{ item.status === 'removed' ? t('musicArtifacts.removed', { size: size(item.removed_bytes) }) : t(item.status === 'pending' ? 'musicArtifacts.pendingItem' : 'musicArtifacts.kept') }}</p>
        </div>
        <label v-if="plan.status !== 'completed'" class="flex min-h-11 cursor-pointer items-center gap-3 text-sm">
          <input v-model="reviewed" type="checkbox" :disabled="busy || uncertain || plan.status === 'executing'">{{ t('musicArtifacts.confirmLabel') }}
        </label>
        <div class="flex flex-wrap gap-3">
          <button v-if="plan.status !== 'completed'" type="button" class="storage-button" :disabled="!canConfirm" @click="confirm">{{ t(plan.status === 'partial' ? 'musicArtifacts.resume' : 'musicArtifacts.confirm') }}</button>
          <button type="button" class="storage-button" :disabled="busy" @click="reconnect(plan.id)">{{ t('musicArtifacts.reconnect') }}</button>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
@reference '../../style.css';
.storage-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text hover:border-accent2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent2 disabled:cursor-not-allowed disabled:opacity-50; }
</style>

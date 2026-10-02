<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { voiceErrorText, type VoiceProfile } from '../../api/voices'
import type { VoicePreparationState } from './useVoicePreparation'

const props = defineProps<{ state: VoicePreparationState; voice: VoiceProfile }>()
const emit = defineEmits<{ navigate: [] }>()
const { t } = useI18n()
const search = ref('')
const filter = ref('all')
const kind = ref('all')
const sort = ref('name')
const customDuration = ref(![15, 30, 60].includes(props.state.durationMinutes))
const visible = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return props.state.sources.filter((source) => source.filename.toLocaleLowerCase().includes(query)
    && (filter.value === 'all' || source.enabled === (filter.value === 'enabled'))
    && (kind.value === 'all' || source.kind === kind.value))
    .sort((left, right) => sort.value === 'size' ? bytes(right.filename) - bytes(left.filename) || left.filename.localeCompare(right.filename) : left.filename.localeCompare(right.filename))
})
const enabledCount = computed(() => props.state.sources.filter((source) => source.enabled).length)
function bytes(filename: string) { return props.voice.recordings.find((recording) => recording.filename === filename)?.bytes ?? 0 }
function changeVisible(enabled: boolean) {
  if (props.state.busy) return
  for (const source of visible.value) source.enabled = enabled
  props.state.singerConfirmed = false
}
function setKind(value: 'song' | 'vocal') {
  if (props.state.busy) return
  for (const source of visible.value) source.kind = value
}
function durationPreset(event: Event) {
  if (!(event.target instanceof HTMLSelectElement)) return
  customDuration.value = event.target.value === 'custom'
  if (!customDuration.value) props.state.durationMinutes = Number(event.target.value)
}
</script>

<template>
  <section class="space-y-4">
    <div><h3 class="text-base font-semibold text-text">{{ t('voiceClone.review.title') }}</h3><p class="mt-1 text-sm text-text-dim">{{ t('voiceClone.workspace.filesIntro') }}</p></div>
    <div class="grid gap-3 sm:grid-cols-2">
      <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.workspace.searchFiles') }}</span><input v-model="search" type="search" :aria-label="t('voiceClone.workspace.searchFiles')" class="w-full rounded-lg border border-border bg-panel-2 p-2" /></label>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.workspace.sort') }}</span><select v-model="sort" :aria-label="t('voiceClone.workspace.sortFiles')" class="w-full rounded-lg border border-border bg-panel-2 p-2"><option value="name">{{ t('voiceClone.workspace.filename') }}</option><option value="size">{{ t('voiceClone.workspace.largestFirst') }}</option></select></label>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.workspace.fileStatus') }}</span><select v-model="filter" :aria-label="t('voiceClone.workspace.fileStatus')" class="w-full rounded-lg border border-border bg-panel-2 p-2"><option value="all">{{ t('voiceClone.workspace.allFiles') }}</option><option value="enabled">{{ t('voiceClone.workspace.enabled') }}</option><option value="excluded">{{ t('voiceClone.workspace.excluded') }}</option></select></label>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.workspace.inputKind') }}</span><select v-model="kind" :aria-label="t('voiceClone.workspace.inputKind')" class="w-full rounded-lg border border-border bg-panel-2 p-2"><option value="all">{{ t('voiceClone.workspace.allTypes') }}</option><option value="song">{{ t('voiceClone.review.song') }}</option><option value="vocal">{{ t('voiceClone.review.vocal') }}</option></select></label>
    </div>
    <p class="text-sm text-text-dim" role="status">{{ t('voiceClone.workspace.filesCount', { visible: visible.length, total: state.sources.length, enabled: enabledCount }) }}</p>
    <div class="flex flex-wrap gap-2"><button type="button" :disabled="state.busy || !visible.length" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="changeVisible(true)">{{ t('voiceClone.workspace.enableVisible') }}</button><button type="button" :disabled="state.busy || !visible.length" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="changeVisible(false)">{{ t('voiceClone.workspace.excludeVisible') }}</button><button type="button" :disabled="state.busy || !visible.length" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="setKind('song')">{{ t('voiceClone.workspace.visibleSongs') }}</button><button type="button" :disabled="state.busy || !visible.length" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="setKind('vocal')">{{ t('voiceClone.workspace.visibleVocals') }}</button></div>
    <fieldset :disabled="state.busy" class="space-y-3"><legend class="mb-2 text-sm font-medium text-text">{{ t('voiceClone.review.sources') }}</legend>
      <div v-for="source in visible" :key="source.filename" class="flex flex-wrap items-center gap-3 rounded-lg bg-panel-2 p-3">
        <label class="flex min-w-0 flex-1 items-center gap-2 text-sm text-text"><input v-model="source.enabled" type="checkbox" @change="state.singerConfirmed = false" /><span class="break-all">{{ source.filename }}</span></label><span class="text-xs text-text-dim">{{ (bytes(source.filename) / 1024 / 1024).toFixed(1) }} MiB</span>
        <select v-model="source.kind" :aria-label="t('voiceClone.review.inputType', { filename: source.filename })" class="rounded-lg border border-border bg-panel p-2 text-sm text-text"><option value="song">{{ t('voiceClone.review.song') }}</option><option value="vocal">{{ t('voiceClone.review.vocal') }}</option></select>
      </div>
      <p v-if="!visible.length" class="text-sm text-text-dim">{{ t('voiceClone.workspace.noMatches') }}</p>
      <p class="text-sm text-text-dim">{{ t('voiceClone.review.sourceHint') }}</p>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.review.separation') }}</span><select v-model="state.quality" :disabled="!state.needsSeparation" :aria-label="t('voiceClone.review.separation')" class="w-full rounded-lg border border-border bg-panel-2 p-2"><option v-for="option in state.separationOptions" :key="option.id" :value="option.id" :disabled="!option.available">{{ t(`voiceClone.review.quality.${option.id}`) }}{{ option.available ? '' : ` — ${t('voiceClone.review.unavailable')}` }}</option></select></label>
      <p class="text-sm text-text-dim">{{ t('voiceClone.review.separationHint') }}</p>
      <template v-if="state.needsSeparation"><p v-for="option in state.separationOptions.filter((item) => !item.available)" :key="option.id" class="text-sm text-text-dim">{{ t(`voiceClone.review.quality.${option.id}`) }} · {{ voiceErrorText(option.reason ?? 'separation_unavailable') }}</p></template>
      <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.workspace.duration') }}</span><select :value="customDuration ? 'custom' : String(state.durationMinutes)" :aria-label="t('voiceClone.workspace.duration')" class="w-full rounded-lg border border-border bg-panel-2 p-2" @change="durationPreset"><option v-for="minutes in [15, 30, 60]" :key="minutes" :value="String(minutes)">{{ t('voiceClone.workspace.minutes', { minutes }) }}</option><option value="custom">{{ t('voiceClone.workspace.customDuration') }}</option></select></label>
      <label v-if="customDuration" class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.workspace.customMinutes') }}</span><input v-model.number="state.durationMinutes" type="number" min="1" max="60" step="1" :aria-label="t('voiceClone.workspace.customMinutes')" class="w-full rounded-lg border border-border bg-panel-2 p-2" /></label>
      <p class="text-sm text-text-dim">{{ t('voiceClone.workspace.durationHint') }}</p>
      <div v-if="state.budgetDirty && state.preparation?.revision" class="rounded-lg border border-accent1/40 bg-panel-2 p-3 text-sm text-text"><p>{{ t('voiceClone.workspace.budgetDirty') }}</p><button type="button" class="mt-2 rounded-lg border border-border px-3 py-2" @click="emit('navigate')">{{ t('voiceClone.workspace.reviewSamples') }}</button></div>
      <label class="flex items-start gap-2 text-sm text-text"><input v-model="state.clean" type="checkbox" class="mt-1" /><span>{{ t('voiceClone.review.cleanup') }}<span class="block text-sm text-text-dim">{{ t('voiceClone.review.cleanupHint') }}</span></span></label>
      <label class="flex items-start gap-2 text-sm text-text"><input v-model="state.singerConfirmed" type="checkbox" class="mt-1" :aria-label="t('voiceClone.review.singer')" /><span>{{ t('voiceClone.review.singer') }}</span></label>
    </fieldset>
    <div class="flex flex-wrap items-center gap-3"><button type="button" :disabled="!state.canPrepare" class="rounded-lg bg-accent1 px-4 py-2 text-sm text-white disabled:opacity-50" @click="state.prepare">{{ t('voiceClone.review.prepare') }}</button><button type="button" :disabled="state.busy" class="rounded-lg border border-border px-3 py-2 text-sm text-text-dim disabled:opacity-50" @click="state.reload">{{ t('voiceClone.review.reload') }}</button></div>
  </section>
</template>

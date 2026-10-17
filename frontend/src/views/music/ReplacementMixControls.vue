<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ReplacementMixSettings, SourceAsset, VoiceMixSettings } from '../../api/generated'
import { listSourceAssets } from '../../api/artistProduction'
import VoiceMixControls from '../../components/shared/VoiceMixControls.vue'
import LoudnessControls from '../../components/shared/LoudnessControls.vue'
const props = defineProps<{ modelValue: ReplacementMixSettings; disabled?: boolean; irSupported?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: ReplacementMixSettings]; valid: [value: boolean] }>()
const { t } = useI18n()
const vocalValid = ref(true), assets = ref<SourceAsset[]>([]), busy = ref(false), failed = ref(false)
let alive = true, controller: AbortController | undefined
const vocal = computed<VoiceMixSettings>(() => ({ schema_version: props.modelValue.schema_version ?? 1, vocal_level: props.modelValue.vocal_level ?? 'match_original', manual_gain_db: props.modelValue.manual_gain_db ?? 0, eq_bands: props.modelValue.eq_bands ?? [], compression: props.modelValue.compression ?? null, loudness: { profile: 'off' } }))
const valid = computed(() => vocalValid.value && (props.modelValue.space !== 'owned_ir' || props.irSupported === true && !!props.modelValue.ir_asset_id && (props.modelValue.wet ?? 0) > 0 && (props.modelValue.wet ?? 0) <= .25) && (props.modelValue.master_level !== 'custom' || props.modelValue.loudness?.profile !== 'off'))
watch(valid, value => emit('valid', value), { immediate: true })
function update(patch: Partial<ReplacementMixSettings>) { if (!props.disabled) emit('update:modelValue', { ...props.modelValue, ...patch }) }
function updateVocal(value: VoiceMixSettings | null) { if (value) { const { loudness: _loudness, ...rest } = value; update(rest) } }
function master(event: Event) {
  if (!(event.target instanceof HTMLSelectElement)) return
  const choice = event.target.value
  if (choice === 'off' || choice === 'match_original' || choice === 'custom') update({ master_level: choice, loudness: choice === 'custom' ? { profile: 'music' } : { profile: 'off' } })
}
async function loadAssets() {
  if (props.disabled || busy.value) return
  controller?.abort(); const request = new AbortController(); controller = request; busy.value = true; failed.value = false
  try {
    const result = await listSourceAssets(request.signal)
    if (alive && !request.signal.aborted) assets.value = result.filter(item => item.role === 'reference')
  } catch { if (alive && !request.signal.aborted) failed.value = true }
  finally { if (alive && !request.signal.aborted) { busy.value = false; controller = undefined } }
}
function space(event: Event) {
  if (!(event.target instanceof HTMLSelectElement)) return
  if (event.target.value === 'owned_ir') { update({ space: 'owned_ir', ir_asset_id: null, wet: .1 }); void loadAssets() }
  else update({ space: 'off', ir_asset_id: null, wet: 0 })
}
function ir(event: Event) { if (event.target instanceof HTMLSelectElement) update({ ir_asset_id: event.target.value || null }) }
function wet(event: Event) { if (event.target instanceof HTMLInputElement) update({ wet: event.target.valueAsNumber }) }
onBeforeUnmount(() => { alive = false; controller?.abort() })
</script>
<template>
  <div class="space-y-4">
    <VoiceMixControls :model-value="vocal" :disabled="disabled" always-enabled hide-loudness :max-makeup="3" @update:model-value="updateVocal" @valid="vocalValid = $event" />
    <fieldset :disabled="disabled" class="space-y-4 rounded-lg border border-border p-3"><label class="mix-label"><span>{{ t('replacement.masterLevel') }}</span><select :value="modelValue.master_level ?? 'off'" class="mix-input" @change="master"><option value="off">{{ t('replacement.masterOff') }}</option><option value="match_original">{{ t('replacement.masterMatch') }}</option><option value="custom">{{ t('replacement.masterCustom') }}</option></select></label>
      <LoudnessControls v-if="modelValue.master_level === 'custom'" :model-value="modelValue.loudness ?? {profile:'music'}" @update:model-value="update({loudness:$event,master_level:$event.profile === 'off' ? 'off' : 'custom'})" />
      <label class="mix-label"><span>{{ t('replacement.space') }}</span><select :value="modelValue.space ?? 'off'" class="mix-input" @change="space"><option value="off">{{ t('replacement.spaceOff') }}</option><option value="owned_ir" :disabled="!irSupported">{{ t('replacement.spaceIr') }}</option></select></label>
      <div v-if="modelValue.space === 'owned_ir'" class="space-y-3"><p class="text-xs text-text-dim">{{ t('replacement.irHelp') }}</p><label class="mix-label"><span>{{ t('replacement.irAsset') }}</span><select :value="modelValue.ir_asset_id ?? ''" class="mix-input" @change="ir"><option value="">{{ t('replacement.chooseIr') }}</option><option v-for="asset in assets" :key="asset.id" :value="asset.id">{{ asset.declaration?.original_filename || asset.id }}</option></select></label><label class="mix-label"><span>{{ t('replacement.wet') }}</span><input :value="modelValue.wet ?? .1" type="number" min="0.001" max="0.25" step="0.01" class="mix-input" @input="wet"></label><button type="button" :disabled="busy" class="mix-button" @click="loadAssets">{{ t('replacement.refreshAssets') }}</button><RouterLink to="/voice-clone" class="inline-flex min-h-11 items-center text-sm text-accent2 underline">{{ t('replacement.manageAssets') }}</RouterLink><p v-if="failed" role="alert" class="text-xs text-status-failed">{{ t('replacement.assetsFailed') }}</p></div>
    </fieldset>
  </div>
</template>
<style scoped>
@reference '../../style.css';
.mix-label { @apply flex min-w-0 flex-col gap-1.5 text-xs text-text-dim; }
.mix-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text; }
.mix-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text disabled:opacity-50; }
</style>

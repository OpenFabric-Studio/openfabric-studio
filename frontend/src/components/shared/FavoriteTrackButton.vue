<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTrackFavoritesStore } from '../../stores/trackFavorites'

const props = defineProps<{ trackId: number; label: string }>()
const { t } = useI18n()
const favorites = useTrackFavoritesStore()
const pressed = computed(() => favorites.isFavorite(props.trackId))
const pending = computed(() => favorites.isPending(props.trackId))
const known = computed(() => favorites.hasTrack(props.trackId))
const buttonLabel = computed(() => t(pressed.value ? 'trackFavorites.remove' : 'trackFavorites.add', { name: props.label }))
let controller: AbortController | undefined
function hydrate() { void favorites.ensureTrack(props.trackId) }
async function toggle() {
  if (pending.value || !known.value) return
  controller?.abort()
  controller = new AbortController()
  await favorites.toggle(props.trackId, controller.signal)
}
watch(() => props.trackId, () => { controller?.abort(); controller = undefined; hydrate() })
onMounted(hydrate)
onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <span class="inline-flex flex-wrap items-center gap-1.5">
    <button type="button" :aria-label="buttonLabel" :title="buttonLabel" :aria-pressed="pressed" :aria-busy="pending"
      :disabled="pending || !known" class="inline-flex min-h-9 min-w-9 items-center justify-center rounded-lg border border-border px-2 text-xl transition-colors hover:border-accent1 focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50"
      :class="pressed ? 'text-accent1' : 'text-text-dim'" @click="toggle"><span aria-hidden="true">{{ pressed ? '★' : '☆' }}</span></button>
    <span v-if="pending" class="text-xs text-text-dim" role="status">{{ t('trackFavorites.saving') }}</span>
    <span v-else-if="!known && favorites.loading" class="text-xs text-text-dim" role="status">{{ t('trackFavorites.loading') }}</span>
    <span v-if="favorites.errorFor(trackId)" class="text-xs text-status-failed" role="alert">{{ t('trackFavorites.saveFailed') }}</span>
    <span v-else-if="!known && favorites.loadError" class="text-xs text-status-failed" role="alert">{{ t('trackFavorites.loadFailed') }} <button type="button" class="text-accent1 underline" @click="hydrate">{{ t('trackFavorites.retry') }}</button></span>
  </span>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, computed } from 'vue'
import { useDialogA11y } from '../../composables/useDialogA11y'
import { useI18n } from 'vue-i18n'
import * as tracksApi from '../../api/tracks'
import type { SavedTrack } from '../../api/tracks'

const { t } = useI18n()
const props = withDefaults(defineProps<{ trackOnly?: boolean; selectedTrackIds?: readonly number[] }>(), { trackOnly: false, selectedTrackIds: () => [] })

const emit = defineEmits<{
  pick: [payload: { sourceUrl: string; sourceLabel: string; trackId?: number }]
  close: []
}>()

const tracks = ref<SavedTrack[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const dialog = ref<HTMLElement | null>(null)
const controller = new AbortController()
let active = true
useDialogA11y(dialog, () => active, () => emit('close'))
onBeforeUnmount(() => { active = false; controller.abort() })

onMounted(async () => {
  try {
    const loaded = await tracksApi.listTracks(undefined, controller.signal)
    if (active) tracks.value = loaded
  } catch {
    if (active) error.value = t('upstreamWorkspace.libraryFailed')
  } finally {
    if (active) loading.value = false
  }
})

const STEM_LABELS = computed<Record<string, string>>(() => ({
  vocals: t('library.stems.vocals'),
  drums: t('library.stems.drums'),
  bass: t('library.stems.bass'),
  other: t('library.stems.other'),
}))

function pick(sourceUrl: string, sourceLabel: string, trackId?: number) {
  if (active && !(props.trackOnly && trackId !== undefined && props.selectedTrackIds.includes(trackId))) emit('pick', { sourceUrl, sourceLabel, ...(trackId === undefined ? {} : { trackId }) })
}
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

async function onFileSelected(event: Event) {
  const input = event.target
  if (!(input instanceof HTMLInputElement)) return
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true
  error.value = null
  try {
    const uploaded = await tracksApi.uploadTrack(file, undefined, controller.signal)
    if (!active) return
    tracks.value.unshift(uploaded)
    pick(uploaded.audio_url, uploaded.title || file.name, uploaded.id)
  } catch {
    if (active) error.value = t('upstreamWorkspace.uploadFailed')
  } finally {
    if (active) { uploading.value = false; if (fileInput.value) fileInput.value.value = '' }
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" @mousedown.self="emit('close')">
      <div ref="dialog" role="dialog" aria-modal="true" :aria-label="t('library.title')" tabindex="-1" class="flex max-h-[80vh] w-full max-w-lg flex-col gap-3 overflow-y-auto rounded-xl bg-panel p-4">
        <div class="flex items-center justify-between">
          <p class="text-sm font-medium text-text">{{ t('library.title') }}</p>
          <button type="button" class="text-text-dim hover:text-status-failed" :aria-label="t('common.close')" :title="t('common.close')" @click="emit('close')">✕</button>
        </div>

        <div class="flex items-center justify-between border-b border-border/60 pb-2">
          <span class="text-xs text-text-dim">{{ t('library.orUpload') }}</span>
          <input
            ref="fileInput"
            type="file"
            accept="audio/wav,audio/mp3,audio/mpeg,audio/flac,audio/ogg,audio/aac"
            class="hidden"
            @change="onFileSelected"
          />
          <button
            type="button"
            class="rounded-lg border border-accent/60 bg-accent/10 px-2.5 py-1 text-xs font-medium text-accent hover:bg-accent/20 disabled:opacity-50"
            :disabled="uploading"
            @click="fileInput?.click()"
          >
            {{ uploading ? t('common.loading') : t('library.uploadFile') }}
          </button>
        </div>

        <p v-if="loading" class="text-xs text-text-dim">{{ t('common.loading') }}</p>
        <p v-else-if="error" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ error }}</p>
        <p v-else-if="tracks.length === 0" class="text-xs text-text-dim">{{ t('library.noTracks') }}</p>

        <div v-else class="space-y-2">
          <div v-for="trk in tracks" :key="trk.id" class="rounded-lg border border-border bg-panel-2 p-2">
            <p class="truncate text-xs font-medium text-text">
              <span v-if="trk.short_id" class="tabular-nums text-text-dim">{{ trk.short_id }} · </span>{{ trk.title || t('library.untitled') }}
            </p>
            <div class="mt-1 flex flex-wrap gap-1.5">
              <button
                type="button"
                class="rounded font-medium disabled:cursor-default"
                :class="[props.trackOnly ? 'min-h-11 px-3 py-2 text-xs' : 'px-2 py-1 text-[11px]', props.trackOnly && props.selectedTrackIds.includes(trk.id) ? 'border border-status-done/40 bg-status-done/10 text-status-done' : 'accent-gradient text-white']"
                :disabled="props.trackOnly && props.selectedTrackIds.includes(trk.id)"
                @click="pick(trk.audio_url, trk.title || t('library.trackFallback'), trk.id)"
              >
                {{ t(props.trackOnly ? props.selectedTrackIds.includes(trk.id) ? 'library.alreadyAdded' : 'library.addToTraining' : 'library.fullMix') }}
              </button>
              <button
                v-for="name in props.trackOnly ? [] : Object.keys(trk.stems || {})"
                :key="name"
                type="button"
                class="rounded border border-border px-2 py-1 text-[11px] text-text"
                @click="pick(trk.stems![name], `${trk.title || t('library.trackFallback')} — ${STEM_LABELS[name] || name}`)"
              >
                {{ STEM_LABELS[name] || name }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

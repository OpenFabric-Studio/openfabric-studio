<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getArtistSettings, saveArtistSettings } from '../../api/artistSettings'
const { t } = useI18n()
const artist = ref(''), album = ref('')
const savedArtist = ref<string | null>(null), savedAlbum = ref('')
const loading = ref(false), saving = ref(false), error = ref(''), notice = ref('')
let active = true, generation = 0, revision = 0
let controller: AbortController | null = null
watch([artist, album], () => { revision++ }, { flush: 'sync' })
const dirty = computed(() => savedArtist.value !== null && (artist.value !== savedArtist.value || album.value !== savedAlbum.value))
async function load() {
  if (loading.value || saving.value) return
  const token = ++generation, initialRevision = revision
  controller?.abort(); controller = new AbortController(); loading.value = true; error.value = ''
  try {
    const result = await getArtistSettings(controller.signal)
    if (!active || token !== generation) return
    savedArtist.value = result.artist
    savedAlbum.value = result.album ?? ''
    if (revision === initialRevision) { artist.value = savedArtist.value; album.value = savedAlbum.value }
  } catch { if (active && token === generation) error.value = t('upstreamWorkspace.artistLoadFailed') }
  finally { if (active && token === generation) loading.value = false }
}
async function save() {
  if (!dirty.value || saving.value || loading.value || artist.value.length > 120 || album.value.length > 120) return
  const token = ++generation, initialRevision = revision, submittedArtist = artist.value, submittedAlbum = album.value
  controller?.abort(); controller = new AbortController(); saving.value = true; error.value = ''; notice.value = ''
  try {
    const result = await saveArtistSettings(submittedArtist, submittedAlbum, controller.signal)
    if (!active || token !== generation) return
    savedArtist.value = result.artist
    savedAlbum.value = result.album ?? ''
    if (revision === initialRevision) { artist.value = savedArtist.value; album.value = savedAlbum.value }
    notice.value = t('upstreamWorkspace.artistSaved')
  } catch { if (active && token === generation) error.value = t('upstreamWorkspace.artistSaveFailed') }
  finally { if (active && token === generation) saving.value = false }
}
onMounted(() => { void load() })
onBeforeUnmount(() => { active = false; ++generation; controller?.abort() })
</script>
<template>
  <section class="space-y-3 rounded-xl border border-border bg-panel p-5" :aria-label="t('upstreamWorkspace.artistTitle')">
    <h2 class="text-lg font-semibold text-text">{{ t('upstreamWorkspace.artistTitle') }}</h2>
    <p class="text-sm text-text-dim">{{ t('upstreamWorkspace.artistHint') }}</p>
    <p v-if="loading" role="status" class="text-sm text-text-dim">{{ t('upstreamWorkspace.artistLoading') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }} <button v-if="savedArtist === null" type="button" class="ml-2 text-accent1 underline" :disabled="loading" @click="load">{{ t('upstreamWorkspace.retry') }}</button></p>
    <form v-if="savedArtist !== null" class="space-y-3" @submit.prevent="save">
      <label class="block space-y-1 text-sm text-text-dim"><span>{{ t('upstreamWorkspace.artistLabel') }}</span><input v-model="artist" type="text" maxlength="120" :aria-label="t('upstreamWorkspace.artistLabel')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-text focus-visible:outline-2 focus-visible:outline-accent1" /></label>
      <label class="block space-y-1 text-sm text-text-dim"><span>{{ t('upstreamWorkspace.albumLabel') }}</span><input v-model="album" type="text" maxlength="120" :aria-label="t('upstreamWorkspace.albumLabel')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-text focus-visible:outline-2 focus-visible:outline-accent1" :placeholder="t('upstreamWorkspace.albumHint')" /></label>
      <p v-if="dirty" class="text-xs text-status-queued">{{ t('upstreamWorkspace.artistDirty') }}</p>
      <button type="submit" :disabled="saving || loading || !dirty || artist.length > 120 || album.length > 120" class="min-h-11 rounded-lg border border-border bg-panel-2 px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50">{{ saving ? t('common.loading') : t('upstreamWorkspace.artistSave') }}</button>
    </form>
    <p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>
  </section>
</template>

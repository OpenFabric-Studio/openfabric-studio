<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError, apiFetch, apiJson } from '../../api/http'
import { parseLibraryStatusResponse, parseLibraryPickResponse } from '../../api/contracts'
import type { LibraryStatusResponse } from '../../api/contracts'

const { t } = useI18n()
const info = ref<LibraryStatusResponse | null>(null)
const path = ref('')
const saving = ref(false)
const picking = ref(false)
const error = ref('')
const notice = ref('')

function errorText(err: unknown): string {
  const code = err instanceof ApiError ? err.message : ''
  const key = `dataFolder.err.${code}`
  const message = String(t(key))
  if (code && message !== key) return message
  return err instanceof Error ? err.message : String(t('dataFolder.err.unknown'))
}

async function load() {
  try {
    const row = await apiFetch('/api/settings/library', undefined, parseLibraryStatusResponse)
    info.value = row
    path.value = row.pending_data_dir || row.data_dir
    if (row.error) error.value = errorText(new ApiError(row.error, 400))
  } catch (err) {
    error.value = errorText(err)
  }
}

async function pick() {
  if (picking.value) return
  picking.value = true
  error.value = ''
  try {
    const row = await apiJson('/api/settings/library/pick', {}, 'POST', parseLibraryPickResponse)
    if (row.path) path.value = row.path
  } catch (err) {
    if (err instanceof ApiError && err.message === 'cancelled') return
    error.value = errorText(err)
  } finally {
    picking.value = false
  }
}

async function save() {
  if (saving.value) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const row = await apiJson('/api/settings/library', { data_dir: path.value }, 'PUT', parseLibraryStatusResponse)
    info.value = row
    notice.value = row.restart_required ? t('dataFolder.restart') : t('dataFolder.saved')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="mt-16 space-y-4 rounded-xl border border-border bg-panel p-5 text-left">
    <div>
      <h2 class="text-lg font-semibold text-text">{{ t('dataFolder.title') }}</h2>
      <p class="mt-2 text-sm text-text-dim">{{ t('dataFolder.intro') }}</p>
    </div>
    <form class="space-y-3" @submit.prevent="save">
      <label class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('dataFolder.pathLabel') }}</span>
        <span class="flex gap-2">
          <input
            v-model="path"
            type="text"
            spellcheck="false"
            class="min-w-0 flex-1 rounded-lg border border-border bg-panel-2 p-2 font-mono text-sm text-text"
          />
          <button
            v-if="info?.can_pick"
            type="button"
            class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50"
            :disabled="picking || saving"
            @click="pick"
          >
            {{ picking ? t('dataFolder.choosing') : t('dataFolder.choose') }}
          </button>
        </span>
      </label>
      <button
        type="submit"
        class="rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        :disabled="saving || !path.trim()"
      >
        {{ saving ? t('dataFolder.saving') : t('dataFolder.save') }}
      </button>
    </form>
    <p v-if="info?.restart_required" class="text-sm text-text">
      {{ t('dataFolder.pending', { path: info.pending_data_dir }) }}
    </p>
    <p v-if="notice" class="text-sm text-status-done">{{ notice }}</p>
    <p v-if="error" class="break-all text-sm text-status-failed">{{ error }}</p>
    <ul v-if="info" class="space-y-1 text-sm text-text-dim">
      <li v-for="folder in info.folders" :key="folder.path" class="text-xs">
        <span class="font-mono text-text [overflow-wrap:anywhere]">{{ info.data_dir }}/{{ folder.path }}</span>
        <span class="text-text-dim"> — {{ t(`dataFolder.${folder.key}`) }}</span>
      </li>
    </ul>
    <p class="text-xs text-text-dim">{{ t('dataFolder.engines') }}</p>
  </section>
</template>

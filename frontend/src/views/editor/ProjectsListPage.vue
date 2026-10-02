<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import * as projectsApi from '../../api/projects'
import type { ProjectSummary } from '../../api/projects'

const { t, locale } = useI18n()

const projects = ref<ProjectSummary[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  try {
    projects.value = await projectsApi.listProjects()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function remove(id: number): Promise<void> {
  if (!window.confirm(t('projectsPage.confirmDelete'))) return
  await projectsApi.deleteProject(id)
  await load()
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(locale.value === 'ru' ? 'ru-RU' : 'en-US')
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-4 p-4">
    <div class="flex items-center justify-between">
      <h1 class="text-lg font-semibold text-text">{{ t('projectsPage.title') }}</h1>
      <router-link
        to="/editor/new"
        class="accent-gradient rounded-lg px-3 py-1.5 text-sm font-medium text-white"
      >
        {{ t('projectsPage.newProject') }}
      </router-link>
    </div>

    <p v-if="loading" class="text-xs text-text-dim">{{ t('common.loading') }}</p>
    <p v-else-if="error" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ error }}</p>
    <p v-else-if="projects.length === 0" class="rounded-xl border border-dashed border-border p-8 text-center text-sm text-text-dim">
      {{ t('projectsPage.empty') }}
    </p>

    <div v-else class="space-y-2">
      <div v-for="p in projects" :key="p.id" class="flex items-center justify-between rounded-xl border border-border bg-panel p-3">
        <router-link :to="`/editor/${p.id}`" class="min-w-0">
          <p class="truncate text-sm font-medium text-text">{{ p.name }}</p>
          <p class="text-xs text-text-dim">{{ t('projectsPage.modified', { date: formatDate(p.updated_at) }) }}</p>
        </router-link>
        <button type="button" class="shrink-0 text-text-dim hover:text-status-failed" :title="t('common.delete')" @click="remove(p.id)">✕</button>
      </div>
    </div>
  </div>
</template>

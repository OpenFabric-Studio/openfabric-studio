<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { useOrchestratorStore } from '../../stores/orchestrator'
import { MODEL_LABELS, MODEL_ROUTES, useModelSwitch } from '../../composables/useModelSwitch'
import { setLocale, currentLocale, type LocaleCode } from '../../i18n'
import type { ModelId, ModelRuntimeStatus } from '../../types'
import HelpModal from './HelpModal.vue'

const orchestrator = useOrchestratorStore()
const route = useRoute()
const { selectModel } = useModelSwitch()
const { t } = useI18n()
const headerElement = ref<HTMLElement | null>(null)
const helpOpen = ref(false)
let headerObserver: ResizeObserver | undefined
function measureHeader() {
  const element = headerElement.value
  const height = element && window.getComputedStyle(element).position === 'sticky' ? element.getBoundingClientRect().height : 0
  document.documentElement.style.setProperty('--app-header-height', `${height}px`)
}
onMounted(() => {
  measureHeader()
  if (typeof ResizeObserver !== 'undefined' && headerElement.value) { headerObserver = new ResizeObserver(measureHeader); headerObserver.observe(headerElement.value) }
  window.addEventListener('resize', measureHeader)
})
onBeforeUnmount(() => { headerObserver?.disconnect(); window.removeEventListener('resize', measureHeader); document.documentElement.style.removeProperty('--app-header-height') })

const LOCALES: { code: LocaleCode; label: string }[] = [
  { code: 'ru', label: 'Русский' },
  { code: 'en', label: 'English' },
]

function onLocaleChange(e: Event) {
  if (!(e.target instanceof HTMLSelectElement)) return
  const value = e.target.value
  const choice = LOCALES.find((locale) => locale.code === value)
  if (choice) setLocale(choice.code)
}

const MODEL_IDS: ModelId[] = ['ace_step', 'yue2']

function statusOf(id: ModelId): ModelRuntimeStatus {
  return orchestrator.statuses[id]?.status ?? 'stopped'
}

const LED_CLASSES: Record<ModelRuntimeStatus, string> = {
  stopped: 'bg-gray-500',
  starting: 'bg-status-queued animate-pulse',
  running: 'bg-status-done',
  stopping: 'bg-status-queued animate-pulse',
  error: 'bg-status-failed',
}

const STATUS_LABEL_KEYS: Record<ModelRuntimeStatus, string> = {
  stopped: 'modelStatus.stopped',
  starting: 'modelStatus.starting',
  running: 'modelStatus.running',
  stopping: 'modelStatus.stopping',
  error: 'modelStatus.error',
}

async function onSelect(id: ModelId) {
  try {
    await selectModel(id)
  } catch {
    // orchestrator.switchError already holds the message, rendered below.
  }
}
</script>

<template>
  <header ref="headerElement" class="top-0 z-40 border-b border-border bg-bg/90 backdrop-blur sm:sticky">
    <div class="mx-auto flex w-full max-w-7xl flex-wrap items-center gap-4 px-4 py-3 sm:px-6">
      <router-link to="/" class="flex items-center gap-2 text-text">
        <span class="accent-gradient flex h-6 w-6 shrink-0 items-center justify-center rounded-md">
          <svg viewBox="0 0 32 32" width="16" height="16" aria-hidden="true">
            <text x="16" y="23" text-anchor="middle" font-family="Inter, system-ui, sans-serif" font-weight="800" font-size="21" fill="white">R</text>
          </svg>
        </span>
        <span class="flex flex-col leading-tight">
          <span class="text-lg font-semibold">Remiqora</span>
          <span class="text-[10px] text-text-dim">{{ t('header.tagline') }}</span>
        </span>
      </router-link>

      <nav class="ml-auto flex flex-wrap gap-2">
        <button type="button" class="rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text-dim hover:text-text focus-visible:outline-2 focus-visible:outline-accent1" @click="helpOpen = true">{{ t('common.help') }}</button>
        <router-link
          to="/settings"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.path.startsWith('/settings') ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
        >
          {{ t('header.settings') }}
        </router-link>
        <router-link
          to="/editor"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.path.startsWith('/editor') ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
        >
          {{ t('header.editor') }}
        </router-link>
        <router-link
          to="/voice-clone"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.path.startsWith('/voice-clone') ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
        >
          {{ t('header.voiceClone') }}
        </router-link>
        <router-link
          to="/video"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.path.startsWith('/video') ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
        >
          {{ t('header.video') }}
        </router-link>
        <router-link
          v-if="statusOf('ace_step') === 'running'"
          to="/ace-step/lora"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.path.startsWith('/ace-step/lora') ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
        >
          {{ t('header.lora') }}
        </router-link>
        <button
          v-for="id in MODEL_IDS"
          :key="id"
          type="button"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.name === MODEL_ROUTES[id] ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
          @click="onSelect(id)"
        >
          <span class="h-2 w-2 rounded-full" :class="LED_CLASSES[statusOf(id)]"></span>
          <span>{{ MODEL_LABELS[id] }}</span>
          <span class="text-xs text-text-dim">{{ t(STATUS_LABEL_KEYS[statusOf(id)]) }}</span>
        </button>
        <select
          :aria-label="t('common.language')"
          class="rounded-lg border border-border bg-panel-2 px-2 py-2 text-sm text-text"
          :value="currentLocale()"
          @change="onLocaleChange"
        >
          <option v-for="loc in LOCALES" :key="loc.code" :value="loc.code">{{ loc.label }}</option>
        </select>
      </nav>
    </div>
    <p v-if="orchestrator.switchError" class="border-t border-status-failed/30 bg-status-failed/10 px-4 py-2 text-xs whitespace-pre-line text-status-failed sm:px-6">
      {{ orchestrator.switchError }}
    </p>
  </header>
  <Teleport to="body">
    <HelpModal :open="helpOpen" :title="t('upstreamWorkspace.helpTitle')" @close="helpOpen = false">
      <p>{{ t('upstreamWorkspace.helpGeneration') }}</p>
      <p>{{ t('upstreamWorkspace.helpVersions') }}</p>
      <p>{{ t('upstreamWorkspace.helpVoice') }}</p>
      <p>{{ t('upstreamWorkspace.helpVideo') }}</p>
      <p>{{ t('upstreamWorkspace.helpSettings') }}</p>
    </HelpModal>
  </Teleport>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useOrchestratorStore } from '../../stores/orchestrator'
import { MODEL_LABELS } from '../../composables/useModelSwitch'
import type { ModelId } from '../../types'
import { ENGINE_STATUS_CLASSES, ENGINE_STATUS_KEYS, type DisplayEngineStatus } from './enginePresentation'
import AppIcon from './AppIcon.vue'
import AppTooltip from './AppTooltip.vue'

const props = defineProps<{ mobile: boolean; navigationOpen: boolean }>()
const emit = defineEmits<{ toggleNavigation: [] }>()
const orchestrator = useOrchestratorStore(), { t } = useI18n()
const headerElement = ref<HTMLElement | null>(null)
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
const MODEL_IDS: ModelId[] = ['ace_step', 'yue2']
function statusOf(id: ModelId): DisplayEngineStatus { return orchestrator.statuses[id]?.status ?? 'unknown' }
function statusLabel(id: ModelId) { return t('appNavigation.engineStatus', { model: MODEL_LABELS[id], status: t(ENGINE_STATUS_KEYS[statusOf(id)]) }) }
</script>

<template>
  <header ref="headerElement" class="sticky top-0 z-30 h-11 shrink-0 border-b border-border bg-bg/95 px-3 backdrop-blur sm:px-5">
    <div class="flex h-full items-center justify-between">
      <button v-if="props.mobile" type="button" class="flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-text-dim hover:text-text focus-visible:outline-2 focus-visible:outline-accent2" :aria-label="t('appNavigation.open')" :aria-expanded="props.navigationOpen" aria-controls="app-navigation" @click="emit('toggleNavigation')"><AppIcon name="menu" /></button>
      <div class="ml-auto flex items-center" role="group" :aria-label="t('appNavigation.status')">
        <AppTooltip v-for="id in MODEL_IDS" :key="id" v-slot="{ describedBy }" :text="statusLabel(id)" :enabled="!props.mobile || !props.navigationOpen" side="bottom" press>
          <button type="button" class="flex h-11 w-11 items-center justify-center rounded-md text-text-dim hover:bg-panel-2 hover:text-text focus-visible:outline-2 focus-visible:outline-accent2" :aria-label="statusLabel(id)" :aria-describedby="describedBy">
            <span class="relative"><AppIcon :name="id" /><span class="absolute -right-1 -bottom-1 h-2 w-2 rounded-full ring-2 ring-bg" :class="ENGINE_STATUS_CLASSES[statusOf(id)]" /></span>
          </button>
        </AppTooltip>
        <AppTooltip v-if="orchestrator.switchError" v-slot="{ describedBy }" :text="t('appNavigation.switchFailed')" :enabled="!props.mobile || !props.navigationOpen" side="bottom" press>
          <button type="button" class="flex h-11 w-11 items-center justify-center rounded-md text-status-failed hover:bg-panel-2 focus-visible:outline-2 focus-visible:outline-accent2" :aria-label="t('appNavigation.switchFailed')" :aria-describedby="describedBy"><AppIcon name="warning" /></button>
        </AppTooltip>
        <span class="sr-only" role="status">{{ orchestrator.switchError ? t('appNavigation.switchFailed') : '' }}</span>
      </div>
    </div>
  </header>
</template>

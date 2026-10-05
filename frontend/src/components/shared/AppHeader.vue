<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useOrchestratorStore } from '../../stores/orchestrator'
import { useModulesStore, type ModuleId, type DisplayModuleState } from '../../stores/modules'
import type { AppIconName } from './appIcons'
import { RouterLink } from 'vue-router'
import AppIcon from './AppIcon.vue'
import { useProvidersStore } from '../../stores/providers'
import AppTooltip from './AppTooltip.vue'

const props = defineProps<{ mobile: boolean; navigationOpen: boolean }>()
const emit = defineEmits<{ toggleNavigation: [] }>()
const orchestrator = useOrchestratorStore(), { t } = useI18n()
const modules = useModulesStore()
const providers = useProvidersStore()
const headerElement = ref<HTMLElement | null>(null)
let headerObserver: ResizeObserver | undefined
function measureHeader() {
  const element = headerElement.value
  const height = element && window.getComputedStyle(element).position === 'sticky' ? element.getBoundingClientRect().height : 0
  document.documentElement.style.setProperty('--app-header-height', `${height}px`)
}
function revealStatus(event: FocusEvent) {
  if (event.target instanceof HTMLElement) event.target.scrollIntoView({ block: 'nearest', inline: 'nearest' })
}
onMounted(() => {
  providers.startPolling()
  measureHeader()
  if (typeof ResizeObserver !== 'undefined' && headerElement.value) { headerObserver = new ResizeObserver(measureHeader); headerObserver.observe(headerElement.value) }
  window.addEventListener('resize', measureHeader)
})
onBeforeUnmount(() => { providers.stopPolling(); headerObserver?.disconnect(); window.removeEventListener('resize', measureHeader); document.documentElement.style.removeProperty('--app-header-height') })
const indicators: { id: ModuleId; icon: AppIconName; label: string }[] = [
  { id: 'ace_step', icon: 'ace_step', label: 'ace_step' }, { id: 'yue2', icon: 'yue2', label: 'yue2' },
  { id: 'speech', icon: 'speech', label: 'speech' }, { id: 'singing', icon: 'singing', label: 'singing' },
  { id: 'video', icon: 'video', label: 'video' }, { id: 'media', icon: 'tools', label: 'tools' },
]
const statusClasses: Record<DisplayModuleState, string> = { ready: 'bg-status-done', installed: 'bg-accent2', partial: 'bg-status-queued', missing: 'bg-text-dim', unsupported: 'bg-text-dim/40', unknown: 'bg-text-dim/40' }
function statusLabel(id: ModuleId, label: string) { return t('moduleWorkspace.header.tooltip', { name: t(`moduleWorkspace.header.${label}`), status: t(`moduleWorkspace.states.${modules.stateOf(id)}`) }) }
</script>

<template>
  <header ref="headerElement" class="sticky top-0 z-30 h-11 shrink-0 border-b border-border bg-bg/95 px-1 backdrop-blur sm:px-5">
    <div class="flex h-full items-center justify-between">
      <button v-if="props.mobile" type="button" class="flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-text-dim hover:text-text focus-visible:outline-2 focus-visible:outline-accent2" :aria-label="t('appNavigation.open')" :aria-expanded="props.navigationOpen" aria-controls="app-navigation" @click="emit('toggleNavigation')"><AppIcon name="menu" /></button>
      <div class="ml-auto flex min-w-0 items-center overflow-x-auto [&>span]:shrink-0" role="group" :aria-label="t('appNavigation.status')" @focusin="revealStatus">
        <AppTooltip v-for="item in indicators" :key="item.id" v-slot="{ describedBy }" :text="statusLabel(item.id, item.label)" :enabled="!props.mobile || !props.navigationOpen" side="bottom" press>
          <RouterLink :to="{ name: 'settings', hash: `#module-${item.id}` }" class="flex h-11 w-11 items-center justify-center rounded-md text-text-dim hover:bg-panel-2 hover:text-text focus-visible:outline-2 focus-visible:outline-accent2" :aria-label="statusLabel(item.id, item.label)" :aria-describedby="describedBy">
            <span class="relative"><AppIcon :name="item.icon" /><span class="absolute -right-1 -bottom-1 h-2 w-2 rounded-full ring-2 ring-bg" :class="statusClasses[modules.stateOf(item.id)]" /></span>
          </RouterLink>
        </AppTooltip>
        <AppTooltip v-slot="{ describedBy }" :text="t(`cloudProviders.header.${providers.state}`)" :enabled="!props.mobile || !props.navigationOpen" side="bottom" press><RouterLink to="/settings#providers" class="flex h-11 w-11 items-center justify-center rounded-md text-text-dim hover:bg-panel-2 hover:text-text focus-visible:outline-2 focus-visible:outline-accent2" :aria-label="t(`cloudProviders.header.${providers.state}`)" :aria-describedby="describedBy"><span class="relative"><AppIcon name="cloud" /><span class="absolute -right-1 -bottom-1 h-2 w-2 rounded-full ring-2 ring-bg" :class="providers.state === 'configured' ? 'bg-accent2' : providers.state === 'missing_key' ? 'bg-status-queued' : 'bg-text-dim/40'" /></span></RouterLink></AppTooltip>
        <AppTooltip v-if="orchestrator.switchError && !props.mobile" v-slot="{ describedBy }" :text="t('appNavigation.switchFailed')" :enabled="!props.mobile || !props.navigationOpen" side="bottom" press>
          <button type="button" class="flex h-11 w-11 items-center justify-center rounded-md text-status-failed hover:bg-panel-2 focus-visible:outline-2 focus-visible:outline-accent2" :aria-label="t('appNavigation.switchFailed')" :aria-describedby="describedBy"><AppIcon name="warning" /></button>
        </AppTooltip>
        <span class="sr-only" role="status">{{ orchestrator.switchError ? t('appNavigation.switchFailed') : '' }}</span>
      </div>
    </div>
  </header>
</template>

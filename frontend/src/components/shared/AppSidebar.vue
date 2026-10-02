<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useOrchestratorStore } from '../../stores/orchestrator'
import type { AppIconName } from './appIcons'
import AppIcon from './AppIcon.vue'
import AppTooltip from './AppTooltip.vue'
import HelpModal from './HelpModal.vue'

const props = defineProps<{ collapsed: boolean; mobile: boolean; active: boolean }>()
const emit = defineEmits<{ toggle: []; navigate: [] }>()
const { t } = useI18n(), route = useRoute(), router = useRouter()
const orchestrator = useOrchestratorStore()
const helpOpen = ref(false)
const trainingAvailable = computed(() => orchestrator.statuses.ace_step?.status === 'running')

interface RouteItem { path: string; key: string; icon: AppIconName }
interface NavigationGroup { key: string; items: RouteItem[] }
const groups: NavigationGroup[] = [
  { key: 'workspace', items: [
    { path: '/', key: 'appNavigation.home', icon: 'home' },
    { path: '/editor', key: 'header.editor', icon: 'editor' },
  ] },
  { key: 'create', items: [
    { path: '/music', key: 'appNavigation.music', icon: 'yue2' },
    { path: '/audiobooks', key: 'appNavigation.audiobook', icon: 'audiobook' },
    { path: '/video', key: 'header.video', icon: 'video' },
  ] },
]
function activePath(path: string) { return path === '/' ? route.path === '/' : route.path === path || route.path.startsWith(`${path}/`) }
function openTraining() {
  if (!trainingAvailable.value) return
  emit('navigate'); void router.push({ name: 'ace-step-lora' })
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col" :data-collapsed="props.collapsed">
    <div class="navigation-brand" :class="props.collapsed ? 'flex-col gap-1' : 'gap-2'">
      <span class="accent-gradient flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-sm font-bold text-white" aria-hidden="true">O</span>
      <span v-if="!props.collapsed" class="min-w-0 flex-1 text-sm font-semibold leading-tight text-text">OpenFabric<span class="block text-xs font-normal text-text-dim">Studio</span></span>
      <AppTooltip v-slot="{ describedBy }" :text="t(props.mobile ? 'appNavigation.close' : props.collapsed ? 'appNavigation.expand' : 'appNavigation.collapse')" :enabled="props.active && !props.mobile">
        <button type="button" data-navigation-toggle class="navigation-toggle" :aria-label="t(props.mobile ? 'appNavigation.close' : props.collapsed ? 'appNavigation.expand' : 'appNavigation.collapse')" :aria-expanded="props.mobile ? true : !props.collapsed" :aria-describedby="describedBy" aria-controls="app-navigation" @click="emit('toggle')"><AppIcon :name="props.mobile ? 'close' : props.collapsed ? 'expand' : 'collapse'" /></button>
      </AppTooltip>
    </div>

    <nav id="app-navigation" class="min-h-0 flex-1 overflow-y-auto px-2 pb-3" :aria-label="t('appNavigation.main')">
      <section v-for="group in groups" :key="group.key" class="navigation-group" :aria-label="t(`appNavigation.${group.key}`)">
        <h2 class="navigation-heading" :class="props.collapsed ? 'sr-only' : ''">{{ t(`appNavigation.${group.key}`) }}</h2>
        <AppTooltip v-for="item in group.items" :key="item.path" v-slot="{ describedBy }" :text="t(item.key)" :enabled="props.active && props.collapsed">
          <router-link :to="item.path" class="navigation-item" :aria-label="t(item.key)" :aria-current="activePath(item.path) ? 'page' : undefined" :aria-describedby="describedBy" @click="emit('navigate')"><AppIcon :name="item.icon" /><span :class="props.collapsed ? 'sr-only' : ''">{{ t(item.key) }}</span></router-link>
        </AppTooltip>
      </section>
      <section class="navigation-group" :aria-label="t('appNavigation.tools')">
        <h2 class="navigation-heading" :class="props.collapsed ? 'sr-only' : ''">{{ t('appNavigation.tools') }}</h2>
        <AppTooltip v-slot="{ describedBy }" :text="trainingAvailable ? t('appNavigation.training') : t('appNavigation.startTraining')" :enabled="props.active && (props.collapsed || !trainingAvailable)" :press="!trainingAvailable">
          <button type="button" class="navigation-item w-full" :aria-label="t('appNavigation.training')" :aria-disabled="!trainingAvailable" :aria-current="route.name === 'ace-step-lora' ? 'page' : undefined" :aria-describedby="describedBy" @click="openTraining"><AppIcon name="training" /><span :class="props.collapsed ? 'sr-only' : ''">{{ t('appNavigation.training') }}</span></button>
        </AppTooltip>
        <AppTooltip v-slot="{ describedBy }" :text="t('header.voiceClone')" :enabled="props.active && props.collapsed">
          <router-link to="/voice-clone" class="navigation-item" :aria-label="t('header.voiceClone')" :aria-current="activePath('/voice-clone') ? 'page' : undefined" :aria-describedby="describedBy" @click="emit('navigate')"><AppIcon name="voice" /><span :class="props.collapsed ? 'sr-only' : ''">{{ t('header.voiceClone') }}</span></router-link>
        </AppTooltip>
      </section>
    </nav>

    <div class="shrink-0 border-t border-border px-2 py-3">
      <AppTooltip v-slot="{ describedBy }" :text="t('header.settings')" :enabled="props.active && props.collapsed">
        <router-link to="/settings" class="navigation-item" :aria-label="t('header.settings')" :aria-current="activePath('/settings') ? 'page' : undefined" :aria-describedby="describedBy" @click="emit('navigate')"><AppIcon name="settings" /><span :class="props.collapsed ? 'sr-only' : ''">{{ t('header.settings') }}</span></router-link>
      </AppTooltip>
      <AppTooltip v-slot="{ describedBy }" :text="t('common.help')" :enabled="props.active && props.collapsed">
        <button type="button" class="navigation-item w-full" :aria-label="t('common.help')" :aria-describedby="describedBy" @click="helpOpen = true"><AppIcon name="help" /><span :class="props.collapsed ? 'sr-only' : ''">{{ t('common.help') }}</span></button>
      </AppTooltip>
    </div>
  </div>
  <Teleport to="body">
    <HelpModal :open="helpOpen" :title="t('upstreamWorkspace.helpTitle')" @close="helpOpen = false">
      <p>{{ t('upstreamWorkspace.helpGeneration') }}</p><p>{{ t('upstreamWorkspace.helpVersions') }}</p>
      <p>{{ t('upstreamWorkspace.helpVoice') }}</p><p>{{ t('upstreamWorkspace.helpVideo') }}</p><p>{{ t('upstreamWorkspace.helpSettings') }}</p>
    </HelpModal>
  </Teleport>
</template>

<style scoped>
.navigation-brand { display: flex; align-items: center; flex-shrink: 0; padding: 12px 10px; }
.navigation-toggle { display: flex; align-items: center; justify-content: center; width: 44px; height: 44px; border-radius: 8px; color: var(--color-text-dim); }
.navigation-group { padding-top: 12px; }
.navigation-heading { margin: 0 12px 6px; font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--color-text-dim); }
.navigation-item { display: flex; align-items: center; gap: 12px; min-height: 44px; margin-bottom: 4px; padding: 10px 12px; border-radius: 8px; color: var(--color-text-dim); font-size: 13px; text-align: left; transition: background-color .15s, color .15s; }
.navigation-item:hover, .navigation-toggle:hover { background: var(--color-panel-2); color: var(--color-text); }
.navigation-item[aria-current="page"] { background: color-mix(in srgb, var(--color-accent1) 18%, transparent); color: var(--color-text); box-shadow: inset 3px 0 var(--color-accent2); }
.navigation-item:disabled, .navigation-item[aria-disabled="true"] { color: var(--color-text-dim); }
.navigation-item:focus-visible, .navigation-toggle:focus-visible { outline: 2px solid var(--color-accent2); outline-offset: -2px; }
[data-collapsed="true"] .navigation-group + .navigation-group { margin-top: 8px; border-top: 1px solid var(--color-border); }
[data-collapsed="true"] .navigation-item { justify-content: center; padding: 10px 0; }
@media (prefers-reduced-motion: reduce) { .navigation-item { transition: none; } }
</style>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

const { t } = useI18n(), route = useRoute(), router = useRouter()
const tablist = ref<HTMLElement | null>(null)
const tabs = [
  { name: 'music-lora', label: 'loraWorkspace.overview' },
  { name: 'music-lora-ace-step', label: 'musicLora.engines.ace_step' },
  { name: 'music-lora-stable-audio3', label: 'musicLora.engines.stable_audio3' },
  { name: 'music-lora-yue2', label: 'musicLora.engines.yue2' },
  { name: 'music-lora-heartmula', label: 'musicLora.engines.heartmula' },
]
async function navigateTab(event: KeyboardEvent, index: number) {
  let target: number
  switch (event.key) {
    case 'ArrowRight': target = (index + 1) % tabs.length; break
    case 'ArrowLeft': target = (index - 1 + tabs.length) % tabs.length; break
    case 'Home': target = 0; break
    case 'End': target = tabs.length - 1; break
    default: return
  }
  event.preventDefault()
  const tab = tabs[target]
  if (!tab) return
  await router.push({ name: tab.name }); await nextTick()
  tablist.value?.querySelectorAll<HTMLElement>('[role="tab"]')[target]?.focus()
}
</script>

<template>
  <div class="min-w-0 space-y-5">
    <header class="space-y-2"><h2 class="text-xl font-semibold text-text">{{ t('loraWorkspace.title') }}</h2><p class="max-w-3xl text-sm leading-relaxed text-text-dim">{{ t('loraWorkspace.intro') }}</p></header>
    <div ref="tablist" role="tablist" :aria-label="t('loraWorkspace.tabs')" class="flex flex-wrap gap-2 border-b border-border pb-4">
      <RouterLink v-for="(tab, index) in tabs" :id="`lora-tab-${tab.name}`" :key="tab.name" :to="{ name: tab.name }" role="tab" :aria-selected="route.name === tab.name" :aria-controls="`lora-panel-${tab.name}`" :tabindex="route.name === tab.name ? 0 : -1" class="inline-flex min-h-11 items-center rounded-lg border px-4 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent2" :class="route.name === tab.name ? 'border-accent2 bg-accent1/15 text-text' : 'border-border bg-panel text-text-dim hover:bg-panel-2 hover:text-text'" @keydown="navigateTab($event, index)">{{ t(tab.label) }}</RouterLink>
    </div>
    <section :id="`lora-panel-${String(route.name)}`" role="tabpanel" :aria-labelledby="`lora-tab-${String(route.name)}`" tabindex="0" class="min-w-0 rounded-lg focus-visible:outline-2 focus-visible:outline-accent2">
      <RouterView v-slot="{ Component, route: childRoute }"><KeepAlive><component :is="Component" :key="String(childRoute.name)" /></KeepAlive></RouterView>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink, useRoute } from 'vue-router'
import { useOrchestratorStore } from '../../stores/orchestrator'
import { MODEL_LABELS, MODEL_ROUTES, useModelSwitch } from '../../composables/useModelSwitch'
import { ENGINE_STATUS_CLASSES, ENGINE_STATUS_KEYS, type DisplayEngineStatus } from '../../components/shared/enginePresentation'
import AppIcon from '../../components/shared/AppIcon.vue'
import type { ModelId } from '../../types'

const { t } = useI18n(), route = useRoute()
const orchestrator = useOrchestratorStore(), { selectModel } = useModelSwitch()
const models: ModelId[] = ['ace_step', 'yue2']
const selected = computed(() => models.find(id => route.name === MODEL_ROUTES[id]))
const selecting = ref<ModelId | null>(null), navigationFailed = ref(false)
const busy = computed(() => selecting.value !== null || orchestrator.switching || orchestrator.isBusy)
let alive = true, generation = 0
onBeforeUnmount(() => { alive = false; ++generation })
watch(() => route.name, name => {
  if (selecting.value !== null && name !== MODEL_ROUTES[selecting.value]) { ++generation; selecting.value = null }
})
function statusOf(id: ModelId): DisplayEngineStatus { return orchestrator.statuses[id]?.status ?? 'unknown' }
async function select(id: ModelId) {
  if (busy.value) return
  const request = ++generation
  selecting.value = id; navigationFailed.value = false
  try { await selectModel(id, () => alive && request === generation) }
  catch { if (alive && request === generation) navigationFailed.value = true }
  finally { if (alive && request === generation) selecting.value = null }
}
</script>

<template>
  <div class="min-w-0 space-y-5">
    <header class="space-y-3 border-b border-border pb-5">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div><h1 class="text-2xl font-semibold text-text">{{ t('musicWorkspace.title') }}</h1><p class="mt-2 text-sm text-text-dim">{{ t('musicWorkspace.intro') }}</p></div>
        <div role="group" :aria-label="t('musicWorkspace.model')" aria-describedby="music-switch-hint" class="flex min-w-0 flex-wrap gap-2">
          <button v-for="id in models" :key="id" type="button" :data-model="id" :aria-label="MODEL_LABELS[id]" :aria-pressed="selected === id" :disabled="busy" class="flex min-h-11 items-center gap-3 rounded-xl border px-4 py-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent2 disabled:cursor-wait disabled:opacity-60" :class="selected === id ? 'border-accent2 bg-accent1/15 text-text' : 'border-border bg-panel text-text-dim hover:bg-panel-2 hover:text-text'" @click="select(id)">
            <AppIcon :name="id" />
            <span><span class="block text-sm font-medium">{{ MODEL_LABELS[id] }}</span><span class="mt-1 flex items-center gap-1.5 text-xs text-text-dim"><span aria-hidden="true" class="h-1.5 w-1.5 rounded-full" :class="ENGINE_STATUS_CLASSES[statusOf(id)]" />{{ t(ENGINE_STATUS_KEYS[statusOf(id)]) }}</span></span>
          </button>
          <RouterLink to="/music/openrouter" data-model="openrouter" :aria-pressed="route.name === 'openrouter-music'" class="flex min-h-11 items-center gap-3 rounded-xl border px-4 py-3 text-sm font-medium focus-visible:outline-2 focus-visible:outline-accent2" :class="route.name === 'openrouter-music' ? 'border-accent2 bg-accent1/15 text-text' : 'border-border bg-panel text-text-dim'"><AppIcon name="yue2" /><span>{{ t('cloudMusic.title') }}<span class="mt-1 block text-xs text-text-dim">OpenRouter · {{ t('cloudMusic.experimental') }}</span></span></RouterLink>
        </div>
      </div>
      <p id="music-switch-hint" class="text-xs leading-relaxed text-text-dim">{{ t('musicWorkspace.switchHint') }}</p>
      <p v-if="busy" role="status" class="text-sm text-text-dim">{{ t('musicWorkspace.switching') }}</p>
      <p v-if="navigationFailed || orchestrator.switchError" role="alert" class="text-sm text-status-failed">{{ t('appNavigation.switchFailed') }}</p>
    </header>
    <router-view />
  </div>
</template>

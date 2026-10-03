<script setup lang="ts">
import { onBeforeUnmount, onMounted, watchEffect } from 'vue'
import { useI18n } from 'vue-i18n'
import { useOrchestratorStore } from './stores/orchestrator'
import { useModulesStore } from './stores/modules'
import AppShell from './components/shared/AppShell.vue'
import { completionNotifications, startCompletionPreferenceSync, stopCompletionPreferenceSync } from './composables/completionNotifications'

const orchestrator = useOrchestratorStore()
const modules = useModulesStore()
const { t } = useI18n()

watchEffect(() => {
  document.title = `${completionNotifications.unread.length ? `(${completionNotifications.unread.length}) ` : ''}OpenFabric Studio — ${t('header.tagline')}`
})

onMounted(() => { startCompletionPreferenceSync(); orchestrator.startPolling(); modules.startPolling() })
onBeforeUnmount(() => { stopCompletionPreferenceSync(); orchestrator.stopPolling(); modules.stopPolling() })
</script>

<template>
  <AppShell>
    <router-view v-slot="{ Component }">
      <Transition name="fade" mode="out-in">
        <component :is="Component" />
      </Transition>
    </router-view>
  </AppShell>
</template>

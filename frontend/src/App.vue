<script setup lang="ts">
import { onBeforeUnmount, onMounted, watchEffect } from 'vue'
import { useI18n } from 'vue-i18n'
import { useOrchestratorStore } from './stores/orchestrator'
import AppHeader from './components/shared/AppHeader.vue'
import AppFooter from './components/shared/AppFooter.vue'
import { completionNotifications, startCompletionPreferenceSync, stopCompletionPreferenceSync } from './composables/completionNotifications'

const orchestrator = useOrchestratorStore()
const { t } = useI18n()

watchEffect(() => {
  document.title = `${completionNotifications.unread.length ? `(${completionNotifications.unread.length}) ` : ''}OpenFabric Studio — ${t('header.tagline')}`
})

onMounted(() => { startCompletionPreferenceSync(); orchestrator.startPolling() })
onBeforeUnmount(() => { stopCompletionPreferenceSync(); orchestrator.stopPolling() })
</script>

<template>
  <AppHeader />
  <main class="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
    <router-view v-slot="{ Component }">
      <Transition name="fade" mode="out-in">
        <component :is="Component" />
      </Transition>
    </router-view>
  </main>
  <AppFooter />
</template>

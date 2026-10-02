<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import HelpModal from './HelpModal.vue'
const { t } = useI18n()
const open = ref(false)
const links = [
  { key: 'appLicense', url: 'https://github.com/OpenFabric-Studio/openfabric-studio/blob/main/LICENSE' },
  { key: 'vendorNotice', url: 'https://github.com/OpenFabric-Studio/openfabric-studio/blob/main/backend/app/fixtures/COPYING.seed-vc' },
  { key: 'projectNotices', url: 'https://github.com/OpenFabric-Studio/openfabric-studio/blob/main/NOTICE' },
  { key: 'upstream', url: 'https://github.com/inikolax/remiqora' },
  { key: 'sourceFork', url: 'https://github.com/mchosc/remiqora' },
]
</script>
<template>
  <footer class="border-t border-border/60">
    <div class="mx-auto flex w-full max-w-7xl flex-wrap items-center gap-3 px-4 py-5 text-xs text-text-dim sm:px-6">
      <span class="mr-auto">{{ t('upstreamWorkspace.attribution') }}</span>
      <span>{{ t('upstreamWorkspace.local') }}</span>
      <button type="button" class="min-h-9 px-2 text-accent1 hover:underline focus-visible:outline-2 focus-visible:outline-accent1" @click="open = true">{{ t('upstreamWorkspace.notices') }}</button>
    </div>
  </footer>
  <Teleport to="body">
    <HelpModal :open="open" :title="t('upstreamWorkspace.noticesTitle')" @close="open = false">
      <p>{{ t('upstreamWorkspace.noticesText') }}</p>
      <ul class="space-y-2"><li v-for="link in links" :key="link.key"><a :href="link.url" target="_blank" rel="noopener" class="text-accent1 hover:underline">{{ t(`upstreamWorkspace.${link.key}`) }}</a></li></ul>
    </HelpModal>
  </Teleport>
</template>

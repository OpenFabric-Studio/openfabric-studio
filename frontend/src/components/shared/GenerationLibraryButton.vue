<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { GenerationSnapshot } from '../../api/generationLibrary'
import { completionNotifications, markGenerationsRead } from '../../composables/completionNotifications'
import { editingGenerationPreset } from '../../composables/generationDrafts'
import HelpModal from './HelpModal.vue'
import GenerationLibraryPanel from './GenerationLibraryPanel.vue'
const props = defineProps<{ settings: GenerationSnapshot }>()
const unread = computed(() => completionNotifications.unread.filter(id => id.startsWith(props.settings.engine === 'ace_step' ? 'ace:' : 'yue:')).length)
const { t } = useI18n()
const open = ref(false)
</script>
<template>
  <button type="button" class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text hover:bg-panel-2" @click="open = true; markGenerationsRead(settings.engine)">{{ t('generationWorkspace.open') }} <span v-if="unread" class="ml-1 text-accent1">({{ unread }})</span></button>
  <p v-if="editingGenerationPreset?.engine === settings.engine" class="text-xs text-accent1">{{ t('generationWorkspace.updateCurrent', { name: editingGenerationPreset.name }) }}</p>
  <HelpModal :open="open" :title="t('generationWorkspace.title')" @close="open = false">
    <GenerationLibraryPanel v-if="open" :engine="settings.engine" :current-settings="settings" @reused="open = false" />
  </HelpModal>
</template>

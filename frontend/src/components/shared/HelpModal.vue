<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDialogA11y } from '../../composables/useDialogA11y'

const props = defineProps<{ open: boolean; title: string }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const dialog = ref<HTMLElement | null>(null)
useDialogA11y(dialog, () => props.open, () => emit('close'))
</script>

<template>
  <div v-if="open" class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 sm:items-center" @click.self="emit('close')">
    <div ref="dialog" role="dialog" aria-modal="true" :aria-label="title" tabindex="-1" class="my-8 w-full max-w-2xl rounded-xl border border-border bg-panel p-5 shadow-2xl">
      <div class="mb-4 flex items-center justify-between">
        <h3 class="text-lg font-semibold text-text">{{ title }}</h3>
        <button type="button" class="rounded-full p-1 text-text-dim hover:bg-panel-2 hover:text-text focus-visible:outline-2 focus-visible:outline-accent1" :aria-label="t('common.close')" @click="emit('close')">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </div>
      <div class="max-h-[70vh] space-y-3 overflow-y-auto text-sm leading-relaxed text-text-dim">
        <slot />
      </div>
    </div>
  </div>
</template>

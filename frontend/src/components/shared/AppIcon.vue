<script setup lang="ts">
import { APP_ICON_PATHS, type AppIconName } from './appIcons'
defineProps<{ name: AppIconName }>()
</script>
<template>
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" class="h-5 w-5 shrink-0" aria-hidden="true" focusable="false">
    <path :d="APP_ICON_PATHS[name]" />
  </svg>
</template>

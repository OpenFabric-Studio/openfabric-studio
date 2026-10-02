<script setup lang="ts">
withDefaults(defineProps<{ summary: string; open?: boolean }>(), { open: false })
</script>

<template>
  <details class="rounded-lg border border-border bg-panel-2/50 p-3" :open="open">
    <summary class="cursor-pointer select-none text-sm font-medium text-text-dim">{{ summary }}</summary>
    <div class="mt-3 space-y-3">
      <slot />
    </div>
  </details>
</template>

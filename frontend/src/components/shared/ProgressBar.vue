<script setup lang="ts">
defineProps<{ value?: number | null }>()
</script>

<template>
  <div class="h-2 w-full overflow-hidden rounded-full bg-panel-2">
    <div v-if="value == null" class="progress-indeterminate h-full w-1/3 accent-gradient"></div>
    <div v-else class="h-full accent-gradient transition-[width] duration-300" :style="{ width: Math.min(100, Math.max(0, value)) + '%' }"></div>
  </div>
</template>

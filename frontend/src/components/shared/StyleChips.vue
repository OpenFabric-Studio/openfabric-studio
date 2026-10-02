<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ text?: string; max?: number }>()
defineEmits<{ more: [] }>()
const tags = computed(() => { const parts = (props.text || '').split(',').map(part => part.trim()).filter(Boolean); return parts.length >= 2 && parts.every(part => part.length <= 40) ? parts : [] })
const shown = computed(() => props.max === undefined ? tags.value : tags.value.slice(0, props.max))
</script>
<template>
 <ul v-if="tags.length" class="flex flex-wrap gap-1.5"><li v-for="(tag, i) in shown" :key="i" class="rounded-full border border-accent1/25 bg-accent1/10 px-2.5 py-1 text-xs leading-4 text-text">{{ tag }}</li><li v-if="shown.length < tags.length"><button type="button" class="min-h-7 rounded-full border border-dashed border-border px-2.5 text-xs text-text-dim hover:border-accent1" @click="$emit('more')">+{{ tags.length - shown.length }}</button></li></ul>
 <p v-else-if="text" class="leading-relaxed text-text" :class="max === undefined ? '' : 'truncate'" :title="max === undefined ? undefined : text">{{ text }}</p>
</template>

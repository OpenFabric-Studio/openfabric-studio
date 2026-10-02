<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import StyleChips from './StyleChips.vue'
const props = defineProps<{ styleText?: string; lyrics?: string }>()
const { t } = useI18n()
type Segment = { text: string; mark: boolean }
type Row = { kind: 'section'; text: string } | { kind: 'gap' } | { kind: 'line'; segments: Segment[] }
const rows = computed<Row[]>(() => {
 const result: Row[] = []
 for (const raw of (props.lyrics || '').replace(/\r/g, '').split('\n')) {
  const section = /^\s*\[([^\]]+)\]\s*$/.exec(raw)
  if (section?.[1]) result.push({ kind: 'section', text: section[1] })
  else if (!raw.trim()) { if (result.length && result[result.length - 1]?.kind !== 'gap') result.push({ kind: 'gap' }) }
  else result.push({ kind: 'line', segments: raw.trimEnd().split(/(\([^)]*\))/).filter(Boolean).map(text => ({ text, mark: /^\([^)]*\)$/.test(text) })) })
 }
 while (result[result.length - 1]?.kind === 'gap') result.pop()
 return result
})
const copied = ref<'style' | 'lyrics' | null>(null)
const copyFailed = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
let mounted = true
async function copy(part: 'style' | 'lyrics'): Promise<void> {
 const request = ++generation
 copyFailed.value = false
 try {
  await navigator.clipboard.writeText(part === 'style' ? props.styleText || '' : props.lyrics || '')
  if (!mounted || request !== generation) return
  copied.value = part; clearTimeout(timer); timer = setTimeout(() => { copied.value = null }, 1800)
 } catch { if (mounted && request === generation) copyFailed.value = true }
}
onBeforeUnmount(() => { mounted = false; generation++; clearTimeout(timer) })
</script>
<template>
 <div class="space-y-4 rounded-lg border border-border/60 bg-panel-2 p-3 text-xs" data-track-details>
  <section v-if="styleText" class="space-y-2">
   <div class="flex flex-wrap items-center justify-between gap-2"><h4 class="text-sm font-semibold text-accent1">{{ t('upstreamLibrary.style') }}</h4><button type="button" class="min-h-9 rounded-lg border border-border px-3 hover:bg-panel" @click="copy('style')">{{ t(copied === 'style' ? 'upstreamLibrary.copied' : 'upstreamLibrary.copyStyle') }}</button></div>
   <StyleChips :text="styleText" />
  </section>
  <section v-if="rows.length" class="space-y-2">
   <div class="flex flex-wrap items-center justify-between gap-2"><h4 class="text-sm font-semibold text-accent2">{{ t('upstreamLibrary.lyrics') }}</h4><button type="button" class="min-h-9 rounded-lg border border-border px-3 hover:bg-panel" @click="copy('lyrics')">{{ t(copied === 'lyrics' ? 'upstreamLibrary.copied' : 'upstreamLibrary.copyLyrics') }}</button></div>
   <div class="max-h-80 overflow-y-auto rounded-md border-l-2 border-accent2/40 bg-panel/60 px-3 py-2 leading-relaxed">
    <template v-for="(row, index) in rows" :key="index">
     <p v-if="row.kind === 'section'" class="mb-1 mt-3 first:mt-0"><span data-lyrics-section class="inline-block rounded bg-accent2/15 px-2 py-1 font-medium text-accent2">{{ row.text }}</span></p>
     <div v-else-if="row.kind === 'gap'" class="h-2" aria-hidden="true"></div>
     <p v-else class="whitespace-pre-wrap text-text"><template v-for="(segment, i) in row.segments" :key="i"><em v-if="segment.mark" data-performance-mark class="not-italic text-accent1">{{ segment.text }}</em><template v-else>{{ segment.text }}</template></template></p>
    </template>
   </div>
  </section>
  <p v-if="copyFailed" role="alert" class="text-status-failed">{{ t('upstreamLibrary.copyFailed') }}</p>
  <p v-else-if="copied" role="status" class="sr-only">{{ t('upstreamLibrary.copied') }}</p>
 </div>
</template>

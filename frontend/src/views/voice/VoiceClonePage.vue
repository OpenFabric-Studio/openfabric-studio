<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import SingingVoiceWorkspace from './SingingVoiceWorkspace.vue'
import VoiceProfilesPanel from './VoiceProfilesPanel.vue'
import AudiobooksPanel from './AudiobooksPanel.vue'
const voiceModes = ['singing', 'speech'] as const
const modes = [...voiceModes, 'audiobooks'] as const
type VoiceMode = typeof modes[number]
const props = withDefaults(defineProps<{ audiobooksOnly?: boolean }>(), { audiobooksOnly: false })
const { t } = useI18n()
const route = useRoute(), router = useRouter()
const mode = computed<VoiceMode>(() => props.audiobooksOnly || route.query.mode === 'audiobooks' ? 'audiobooks' : voiceModes.find(value => value === route.query.mode) ?? 'singing')
watch(() => [route.name, route.query.mode], () => {
  if (route.name !== 'voice-clone' || route.query.mode !== 'audiobooks') return
  const query = { ...route.query }
  delete query.mode
  void router.replace({ name: 'audiobooks', query, hash: route.hash })
}, { immediate: true })
const visited = reactive<Record<VoiceMode, boolean>>({ singing: false, speech: false, audiobooks: false })
let alive = true
let navigationGeneration = 0
let modeFocusGeneration = 0
onBeforeUnmount(() => { alive = false; navigationGeneration++; modeFocusGeneration++ })
const activity = reactive<Record<VoiceMode, string>>({ singing: '', speech: '', audiobooks: '' })
function focusMode(value: VoiceMode) {
  document.getElementById(value === 'audiobooks' ? 'voice-studio-heading' : `studio-tab-${value}`)?.focus()
}
watch(mode, async (value, previous) => {
  const generation = ++modeFocusGeneration
  visited[value] = true
  const focused = document.activeElement
  const previousPanel = previous ? document.getElementById(`studio-panel-${previous}`) : null
  const leavingWorkspace = focused instanceof HTMLElement && (previousPanel?.contains(focused) || focused.closest('[data-voice-confirmation]'))
  if (leavingWorkspace) {
    await nextTick()
    if (alive && generation === modeFocusGeneration && mode.value === value) focusMode(value)
  }
}, { immediate: true })
async function selectMode(value: VoiceMode, focus = false) {
  const generation = ++navigationGeneration
  const query = { ...route.query }
  if (value === 'audiobooks') {
    delete query.mode
    delete query.voice
    delete query.stage
  }
  else query.mode = value
  await router.push({ name: value === 'audiobooks' ? 'audiobooks' : 'voice-clone', query })
  if (focus) {
    await nextTick()
    if (alive && generation === navigationGeneration && mode.value === value) focusMode(value)
  }
}
function onModeKey(event: KeyboardEvent) {
  let index = voiceModes.findIndex(value => value === mode.value)
  if (event.key === 'ArrowRight') index = (index + 1) % voiceModes.length
  else if (event.key === 'ArrowLeft') index = (index + voiceModes.length - 1) % voiceModes.length
  else if (event.key === 'Home') index = 0
  else if (event.key === 'End') index = voiceModes.length - 1
  else return
  event.preventDefault()
  const value = voiceModes[index]; if (value) void selectMode(value, true)
}
</script>
<template>
  <div class="mx-auto max-w-7xl space-y-5">
    <header><h1 id="voice-studio-heading" tabindex="-1" class="text-2xl font-semibold text-text focus-visible:outline-2 focus-visible:outline-accent1">{{ t(mode === 'audiobooks' ? 'voiceStudio.modes.audiobooks' : 'voiceStudio.title') }}</h1><p class="mt-2 text-sm text-text-dim">{{ t(mode === 'audiobooks' ? 'voiceStudio.descriptions.audiobooks' : 'voiceStudio.intro') }}</p></header>
    <div v-if="mode !== 'audiobooks'" role="tablist" :aria-label="t('voiceStudio.navigation')" class="flex gap-2 border-b border-border pb-3"><button v-for="value in voiceModes" :id="`studio-tab-${value}`" :key="value" role="tab" type="button" :aria-label="t(`voiceStudio.modes.${value}`)" :aria-selected="mode === value" :aria-controls="`studio-panel-${value}`" :tabindex="mode === value ? 0 : -1" class="min-h-11 rounded-lg px-4 py-2 text-sm font-medium transition-colors" :class="mode === value ? 'bg-accent1 text-white' : 'text-text-dim hover:bg-panel-2 hover:text-text'" @click="selectMode(value)" @keydown="onModeKey"><span>{{ t(`voiceStudio.modes.${value}`) }}</span><span v-if="activity[value]" aria-hidden="true" class="ml-2 inline-block h-2 w-2 rounded-full bg-status-queued"></span></button></div>
    <template v-for="value in modes" :key="value"><button v-if="activity[value] && mode !== value" type="button" class="flex min-h-11 w-full flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-panel-2 px-4 py-3 text-left text-sm text-text" @click="selectMode(value, true)"><span role="status">{{ activity[value] }}</span><span class="text-text-dim">{{ t('voiceStudio.viewActivity', { mode: t(`voiceStudio.modes.${value}`) }) }} →</span></button></template>
    <p v-if="mode !== 'audiobooks'" class="text-sm text-text-dim">{{ t(`voiceStudio.descriptions.${mode}`) }}</p>
    <section v-show="mode === 'singing'" id="studio-panel-singing" role="tabpanel" aria-labelledby="studio-tab-singing"><SingingVoiceWorkspace v-if="visited.singing" :active="mode === 'singing'" @activity="activity.singing = $event" /></section>
    <section v-show="mode === 'speech'" id="studio-panel-speech" role="tabpanel" aria-labelledby="studio-tab-speech"><VoiceProfilesPanel v-if="visited.speech" :active="mode === 'speech'" @activity="activity.speech = $event" /></section>
    <section v-show="mode === 'audiobooks'" id="studio-panel-audiobooks" role="region" aria-labelledby="voice-studio-heading"><AudiobooksPanel v-if="visited.audiobooks" :active="mode === 'audiobooks'" @activity="activity.audiobooks = $event" /></section>
  </div>
</template>

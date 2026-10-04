<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as enginesApi from '../../api/localEngines'
import type { LocalEngineCard, LocalEngineId, LocalEngineResult } from '../../api/localEngines'
import SpeechAudioPreview from './SpeechAudioPreview.vue'
import VideoPreviewPlayer from '../video/VideoPreviewPlayer.vue'

const props = withDefaults(defineProps<{ kind: 'speech' | 'singing' | 'picture'; active?: boolean }>(), { active: true })
const { t } = useI18n()
const cards = ref<LocalEngineCard[]>([])
const loading = ref(false)
const loadError = ref(false)
const busy = ref('')
const error = ref('')
const notice = ref('')
const results = ref<Partial<Record<LocalEngineId, LocalEngineResult>>>({})
const kokoroText = ref('')
const kokoroVoice = ref('af_heart')
const chatterText = ref('')
const chatterModel = ref<'original' | 'multilingual'>('original')
const chatterLanguage = ref('en')
const chatterPath = ref('')
const wanPrompt = ref('')
const wanPath = ref('')
const rvcModel = ref('')
const rvcInput = ref('')
let alive = true
let statusController: AbortController | null = null
let runController: AbortController | null = null

const visible = computed((): LocalEngineId[] => {
  if (props.kind === 'speech') return ['kokoro', 'chatterbox']
  if (props.kind === 'singing') return ['rvc']
  return ['wan22']
})
const kokoroVoices = computed(() => cards.value.find(card => card.id === 'kokoro')?.voices ?? [])
const chatterLanguages = computed(() => cards.value.find(card => card.id === 'chatterbox')?.languages ?? [])
function card(id: LocalEngineId) { return cards.value.find(item => item.id === id) }
function media(id: LocalEngineId) {
  const result = results.value[id]
  return result ? enginesApi.localEngineMediaUrl(result) : null
}
function kokoroLang(voice: string): 'a' | 'b' { return voice.startsWith('b') ? 'b' : 'a' }

async function refresh() {
  statusController?.abort()
  const current = new AbortController()
  statusController = current
  loading.value = true
  loadError.value = false
  try {
    const status = await enginesApi.listLocalEngines(current.signal)
    if (!alive || current.signal.aborted) return
    cards.value = status.engines
    if (!kokoroVoices.value.includes(kokoroVoice.value) && kokoroVoices.value[0]) kokoroVoice.value = kokoroVoices.value[0]
    if (chatterLanguages.value.length && !chatterLanguages.value.includes(chatterLanguage.value)) chatterLanguage.value = chatterLanguages.value.includes('en') ? 'en' : chatterLanguages.value[0] ?? 'en'
  } catch {
    if (alive && !current.signal.aborted) loadError.value = true
  } finally {
    if (alive && statusController === current) { loading.value = false; statusController = null }
  }
}

async function stage(file: File | undefined, assign: (path: string) => void) {
  if (!file || busy.value) return
  error.value = ''
  busy.value = 'upload'
  try {
    const path = await enginesApi.uploadLocalInput(file)
    if (!alive) return
    assign(path)
  } catch (err) {
    if (alive) error.value = enginesApi.localEngineError(err) || t('localEngines.statusFailed')
  } finally {
    if (alive) busy.value = ''
  }
}

async function run(id: LocalEngineId, action: (signal: AbortSignal) => Promise<LocalEngineResult>) {
  if (busy.value || !card(id)?.installed) return
  error.value = ''
  notice.value = ''
  const current = new AbortController()
  runController = current
  busy.value = id
  try {
    const result = await action(current.signal)
    if (!alive || current.signal.aborted) return
    results.value = { ...results.value, [id]: result }
    notice.value = result.detail
  } catch (err) {
    if (alive && !current.signal.aborted) error.value = enginesApi.localEngineError(err) || t('localEngines.statusFailed')
  } finally {
    if (alive && runController === current) { busy.value = ''; runController = null }
  }
}

function onKokoro() {
  const text = kokoroText.value.trim()
  if (!text) return
  const voice = kokoroVoice.value
  void run('kokoro', signal => enginesApi.runKokoro({ text, voice, lang: kokoroLang(voice) }, signal))
}
function onChatter() {
  const text = chatterText.value.trim()
  if (!text) return
  const model = chatterModel.value
  const audio = chatterPath.value.trim()
  void run('chatterbox', signal => enginesApi.runChatterbox({
    text, model, language_id: model === 'multilingual' ? chatterLanguage.value : 'en', ...(audio ? { audio_prompt_path: audio } : {}),
  }, signal))
}
function onWan() {
  const prompt = wanPrompt.value.trim()
  if (!prompt) return
  const image = wanPath.value.trim()
  void run('wan22', signal => enginesApi.runWan({ prompt, ...(image ? { image_path: image } : {}) }, signal))
}
function onRvc() {
  const model = rvcModel.value.trim()
  const input = rvcInput.value.trim()
  if (!model || !input) return
  void run('rvc', signal => enginesApi.runRvc({ model_path: model, input_path: input }, signal))
}

watch(() => props.active, active => { if (!active) runController?.abort() })
onMounted(() => { void refresh() })
onBeforeUnmount(() => { alive = false; statusController?.abort(); runController?.abort() })
</script>
<template>
  <section data-local-engines class="space-y-4 rounded-xl border border-border bg-panel p-5" :aria-label="t(`localEngines.${kind}Title`)">
    <div>
      <h2 class="text-lg font-semibold text-text">{{ t(`localEngines.${kind}Title`) }}</h2>
      <p class="mt-1 text-sm text-text-dim">{{ t(`localEngines.${kind}Intro`) }}</p>
    </div>
    <p v-if="loading" role="status" class="text-sm text-text-dim">{{ t('localEngines.statusPending') }}</p>
    <p v-if="loadError" role="alert" class="text-sm text-status-failed">{{ t('localEngines.statusFailed') }}</p>
    <p v-if="error" role="alert" class="rounded-lg border border-status-failed/40 bg-status-failed/10 px-4 py-3 text-sm text-status-failed">{{ error }}</p>
    <p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>

    <form v-if="visible.includes('kokoro')" data-local-engine="kokoro" class="space-y-3 rounded-lg border border-border bg-panel-2 p-4" @submit.prevent="onKokoro">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div><h3 class="text-sm font-semibold text-text">{{ t('localEngines.kokoro.title') }}</h3><p class="mt-1 text-xs text-text-dim">{{ t('localEngines.kokoro.help') }}</p></div>
        <p class="text-xs font-medium" :class="card('kokoro')?.installed ? 'text-status-done' : 'text-status-failed'">{{ card('kokoro') ? (card('kokoro')?.installed ? t('localEngines.installed') : t('localEngines.missing')) : t('localEngines.statusPending') }}</p>
      </div>
      <p v-if="card('kokoro') && !card('kokoro')?.installed" class="text-xs text-text-dim">{{ t('localEngines.setup', { script: card('kokoro')?.setup_script }) }}</p>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.kokoro.text') }}</span><textarea v-model="kokoroText" rows="4" maxlength="4000" :aria-label="t('localEngines.kokoro.text')" class="w-full rounded-lg border border-border bg-panel p-3 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" /></label>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.kokoro.voice') }}</span><select v-model="kokoroVoice" :aria-label="t('localEngines.kokoro.voice')" class="w-full rounded-lg border border-border bg-panel px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1"><option v-for="voice in kokoroVoices" :key="voice" :value="voice">{{ voice }}</option></select></label>
      <button type="submit" class="rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="!!busy || !card('kokoro')?.installed || !kokoroText.trim()">{{ busy === 'kokoro' ? t('localEngines.generating') : t('localEngines.generate') }}</button>
      <SpeechAudioPreview v-if="media('kokoro')" :src="media('kokoro') || ''" :label="t('localEngines.kokoro.result')" :active="active" />
      <p v-else-if="results.kokoro?.output_path" class="text-xs text-text-dim">{{ t('localEngines.playbackMissing') }}</p>
    </form>

    <form v-if="visible.includes('chatterbox')" data-local-engine="chatterbox" class="space-y-3 rounded-lg border border-border bg-panel-2 p-4" @submit.prevent="onChatter">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div><h3 class="text-sm font-semibold text-text">{{ t('localEngines.chatterbox.title') }}</h3><p class="mt-1 text-xs text-text-dim">{{ t('localEngines.chatterbox.help') }}</p></div>
        <p class="text-xs font-medium" :class="card('chatterbox')?.installed ? 'text-status-done' : 'text-status-failed'">{{ card('chatterbox') ? (card('chatterbox')?.installed ? t('localEngines.installed') : t('localEngines.missing')) : t('localEngines.statusPending') }}</p>
      </div>
      <p v-if="card('chatterbox') && !card('chatterbox')?.installed" class="text-xs text-text-dim">{{ t('localEngines.setup', { script: card('chatterbox')?.setup_script }) }}</p>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.chatterbox.text') }}</span><textarea v-model="chatterText" rows="4" maxlength="4000" :aria-label="t('localEngines.chatterbox.text')" class="w-full rounded-lg border border-border bg-panel p-3 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" /></label>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.chatterbox.model') }}</span><select v-model="chatterModel" :aria-label="t('localEngines.chatterbox.model')" class="w-full rounded-lg border border-border bg-panel px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1"><option value="original">{{ t('localEngines.chatterbox.original') }}</option><option value="multilingual">{{ t('localEngines.chatterbox.multilingual') }}</option></select></label>
      <label v-if="chatterModel === 'multilingual'" class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.chatterbox.language') }}</span><select v-model="chatterLanguage" :aria-label="t('localEngines.chatterbox.language')" class="w-full rounded-lg border border-border bg-panel px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1"><option v-for="language in chatterLanguages" :key="language" :value="language">{{ language }}</option></select></label>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.chatterbox.reference') }}</span><input type="file" accept=".wav,.flac,.mp3,audio/wav,audio/flac,audio/mpeg" :aria-label="t('localEngines.chatterbox.reference')" class="block w-full text-sm text-text-dim file:mr-3 file:rounded-lg file:border-0 file:bg-panel file:px-3 file:py-2 file:text-sm file:text-text" @change="stage(($event.target as HTMLInputElement).files?.[0], path => chatterPath = path)" /></label>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.chatterbox.referencePath') }}</span><input v-model="chatterPath" type="text" :aria-label="t('localEngines.chatterbox.referencePath')" class="w-full rounded-lg border border-border bg-panel px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" /></label>
      <button type="submit" class="rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="!!busy || !card('chatterbox')?.installed || !chatterText.trim()">{{ busy === 'chatterbox' ? t('localEngines.generating') : busy === 'upload' ? t('localEngines.upload') : t('localEngines.generate') }}</button>
      <SpeechAudioPreview v-if="media('chatterbox')" :src="media('chatterbox') || ''" :label="t('localEngines.chatterbox.result')" :active="active" />
      <p v-else-if="results.chatterbox?.output_path" class="text-xs text-text-dim">{{ t('localEngines.playbackMissing') }}</p>
    </form>

    <form v-if="visible.includes('wan22')" data-local-engine="wan22" class="space-y-3" @submit.prevent="onWan">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div><h3 class="text-sm font-semibold text-text">{{ t('localEngines.wan.title') }}</h3><p class="mt-1 text-xs text-text-dim">{{ t('localEngines.wan.help') }}</p></div>
        <p class="text-xs font-medium" :class="card('wan22')?.installed ? 'text-status-done' : 'text-status-failed'">{{ card('wan22') ? (card('wan22')?.installed ? t('localEngines.installed') : t('localEngines.missing')) : t('localEngines.statusPending') }}</p>
      </div>
      <p v-if="card('wan22') && !card('wan22')?.installed" class="text-xs text-text-dim">{{ t('localEngines.setup', { script: card('wan22')?.setup_script }) }}</p>
      <p class="text-xs text-text-dim">{{ t('localEngines.wan.variant') }}: ti2v-5b</p>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.wan.prompt') }}</span><textarea v-model="wanPrompt" rows="4" maxlength="2000" :aria-label="t('localEngines.wan.prompt')" class="w-full rounded-lg border border-border bg-panel-2 p-3 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" /></label>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.wan.still') }}</span><input type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp" :aria-label="t('localEngines.wan.still')" class="block w-full text-sm text-text-dim file:mr-3 file:rounded-lg file:border-0 file:bg-panel-2 file:px-3 file:py-2 file:text-sm file:text-text" @change="stage(($event.target as HTMLInputElement).files?.[0], path => wanPath = path)" /></label>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.wan.stillPath') }}</span><input v-model="wanPath" type="text" :aria-label="t('localEngines.wan.stillPath')" class="w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" /></label>
      <button type="submit" class="rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="!!busy || !card('wan22')?.installed || !wanPrompt.trim()">{{ busy === 'wan22' ? t('localEngines.generating') : t('localEngines.generate') }}</button>
      <VideoPreviewPlayer v-if="media('wan22')" :src="media('wan22') || ''" :label="t('localEngines.wan.result')" />
      <p v-else-if="results.wan22?.output_path" class="text-xs text-text-dim">{{ t('localEngines.playbackMissing') }}</p>
    </form>

    <form v-if="visible.includes('rvc')" data-local-engine="rvc" class="space-y-3" @submit.prevent="onRvc">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div><h3 class="text-sm font-semibold text-text">{{ t('localEngines.rvc.title') }}</h3><p class="mt-1 text-xs text-text-dim">{{ t('localEngines.rvc.help') }}</p></div>
        <p class="text-xs font-medium" :class="card('rvc')?.installed ? 'text-status-done' : 'text-status-failed'">{{ card('rvc') ? (card('rvc')?.installed ? t('localEngines.installed') : t('localEngines.missing')) : t('localEngines.statusPending') }}</p>
      </div>
      <p v-if="card('rvc') && !card('rvc')?.installed" class="text-xs text-text-dim">{{ t('localEngines.setup', { script: card('rvc')?.setup_script }) }}</p>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.rvc.model') }}</span><input type="file" accept=".pth" :aria-label="t('localEngines.rvc.model')" class="block w-full text-sm text-text-dim file:mr-3 file:rounded-lg file:border-0 file:bg-panel-2 file:px-3 file:py-2 file:text-sm file:text-text" @change="stage(($event.target as HTMLInputElement).files?.[0], path => rvcModel = path)" /></label>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.rvc.modelPath') }}</span><input v-model="rvcModel" type="text" :aria-label="t('localEngines.rvc.modelPath')" class="w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" /></label>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.rvc.audio') }}</span><input type="file" accept=".wav,.flac,.mp3,audio/wav,audio/flac,audio/mpeg" :aria-label="t('localEngines.rvc.audio')" class="block w-full text-sm text-text-dim file:mr-3 file:rounded-lg file:border-0 file:bg-panel-2 file:px-3 file:py-2 file:text-sm file:text-text" @change="stage(($event.target as HTMLInputElement).files?.[0], path => rvcInput = path)" /></label>
      <label class="block space-y-1"><span class="text-xs text-text-dim">{{ t('localEngines.rvc.audioPath') }}</span><input v-model="rvcInput" type="text" :aria-label="t('localEngines.rvc.audioPath')" class="w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1" /></label>
      <button type="submit" class="rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50" :disabled="!!busy || !card('rvc')?.installed || !rvcModel.trim() || !rvcInput.trim()">{{ busy === 'rvc' ? t('localEngines.generating') : t('localEngines.generate') }}</button>
      <SpeechAudioPreview v-if="media('rvc')" :src="media('rvc') || ''" :label="t('localEngines.rvc.result')" :active="active" />
      <p v-else-if="results.rvc?.output_path" class="text-xs text-text-dim">{{ t('localEngines.playbackMissing') }}</p>
    </form>
  </section>
</template>

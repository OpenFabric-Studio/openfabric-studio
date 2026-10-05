<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import * as provider from '../../api/openrouter'
import * as music from '../../api/cloudMusic'
import type { CloudMusicJob, OpenRouterModel, OpenRouterMusicRequest, OpenRouterQuote, OpenRouterStatus } from '../../api/generated'
const { t } = useI18n()
const warningKeys: Readonly<Record<string,string>> = { experimental_music: 'cloudProviders.warnings.experimental_music', fixed_song_estimate_from_published_page: 'cloudProviders.warnings.fixed_song_estimate_from_published_page' }
function warningText(code: string) { return t(warningKeys[code] ?? 'cloudProviders.warnings.unknown') }
const status=ref<OpenRouterStatus | null>(null)
const models=ref<OpenRouterModel[]>([])
const selected=ref(''), prompt=ref(''), title=ref(''), seed=ref<number | null>(null)
const historyIncomplete=ref(false)
const jobs=ref<CloudMusicJob[]>([]), quote=ref<OpenRouterQuote | null>(null), confirmed=ref(false), busy=ref(false), error=ref('')
const model=computed(()=>models.value.find(m=>m.id===selected.value))
const ready=computed(()=>!!status.value?.enabled && !!status.value?.credential_configured)
const valid=computed(()=>ready.value && !!model.value && prompt.value.trim().length>0 && prompt.value.length<=4000)
let alive=true, generation=0, controller: AbortController | undefined, timer: ReturnType<typeof setTimeout> | undefined
function current(signal: AbortSignal){return alive && !signal.aborted}
function parameters(): OpenRouterMusicRequest {return {model:selected.value,prompt:prompt.value.trim(),seed:model.value?.supports_seed ? seed.value : null}}
watch([selected,prompt,seed],()=>{quote.value=null;confirmed.value=false},{flush:'sync'})
async function action(fn: (signal: AbortSignal)=>Promise<void>, message='cloudMusic.actionFailed'){
  if(!alive || busy.value)return
  const token=++generation, request=new AbortController();controller=request;busy.value=true;error.value=''
  try{await fn(request.signal)}catch{if(alive && token===generation)error.value=t(message)}finally{if(alive && token===generation){busy.value=false;controller=undefined;schedule()}}
}
function applyModels(value: OpenRouterModel[]){models.value=value.filter(m=>m.kind==='music');if(!models.value.some(m=>m.id===selected.value))selected.value=models.value[0]?.id ?? ''}
function schedule(){if(timer)clearTimeout(timer);if(!alive || !jobs.value.some(j=>j.status==='queued'||j.status==='running'))return;timer=setTimeout(()=>{void history()},2500)}
function history(){return action(async signal=>{const response=await music.listCloudMusic(signal);if(current(signal)){jobs.value=response.jobs;historyIncomplete.value=response.history_incomplete ?? false}})}
function refresh(){void action(async signal=>{const response=await provider.refreshProviderCatalog(signal);if(current(signal))applyModels(response.models ?? [])})}
function review(){if(!valid.value)return;const body=parameters();void action(async signal=>{const response=await music.quoteCloudMusic(body,signal);if(current(signal)){quote.value=response;confirmed.value=false}})}
function generate(){if(!valid.value || !quote.value || !confirmed.value)return;if(quote.value.expires_at*1000<=Date.now()){quote.value=null;confirmed.value=false;error.value=t('cloudMusic.expired');return}const body={...parameters(),title:title.value,quote_id:quote.value.id,transfers_confirmed:true};quote.value=null;confirmed.value=false;void action(async signal=>{const response=await music.submitCloudMusic(body,signal);if(current(signal))jobs.value=[response,...jobs.value.filter(j=>j.id!==response.id)]})}
function retrySave(job: CloudMusicJob){void action(async signal=>{const response=await music.retrySaveCloudMusic(job.id,signal);if(current(signal))jobs.value=jobs.value.map(j=>j.id===response.id ? response : j)})}
function cancel(job: CloudMusicJob){void action(async signal=>{const response=await music.cancelCloudMusic(job.id,signal);if(current(signal))jobs.value=jobs.value.map(j=>j.id===response.id ? response : j)})}
onMounted(()=>{void action(async signal=>{const value=await provider.getProviderStatus(signal);if(!current(signal))return;status.value=value;const response=await music.listCloudMusic(signal);if(!current(signal))return;jobs.value=response.jobs;historyIncomplete.value=response.history_incomplete ?? false;if(value.enabled && value.credential_configured){const catalog=await provider.getProviderCatalog(signal);if(current(signal))applyModels(catalog.models ?? [])}},'cloudMusic.loadFailed')})
onBeforeUnmount(()=>{alive=false;generation++;controller?.abort();if(timer)clearTimeout(timer)})
</script>
<template>
  <section class="space-y-5" :aria-busy="busy">
    <div class="rounded-xl border border-accent1/30 bg-accent1/5 p-5"><div class="flex items-center gap-3"><h2 class="text-lg font-semibold text-text">{{ t('cloudMusic.title') }}</h2><span class="rounded-full border border-accent1/30 px-2 py-1 text-xs text-accent2">{{ t('cloudMusic.experimental') }}</span></div><p class="mt-2 text-sm text-text-dim">{{ t('cloudMusic.intro') }}</p><p class="mt-2 text-xs leading-relaxed text-text-dim">{{ t('cloudMusic.limits') }}</p></div>
    <RouterLink v-if="!ready" to="/settings#providers" class="inline-flex min-h-11 items-center text-sm text-accent2 underline">{{ t('cloudMusic.setup') }}</RouterLink>
    <p v-if="historyIncomplete" role="alert" class="text-sm text-status-queued">{{ t('cloudProviders.historyIncomplete') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <form class="space-y-4 rounded-xl border border-border bg-panel p-5" @submit.prevent="review">
      <div class="flex flex-wrap items-end gap-3"><label class="cloud-label flex-1">{{ t('cloudMusic.model') }}<select v-model="selected" class="cloud-input" :disabled="busy || !ready"><option v-for="item in models" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><button class="cloud-button" type="button" :disabled="busy || !ready" @click="refresh">{{ t('cloudMusic.refresh') }}</button></div>
      <p v-if="!models.length" class="text-sm text-text-dim">{{ t('cloudMusic.noModels') }}</p>
      <label class="cloud-label">{{ t('cloudMusic.prompt') }}<textarea v-model="prompt" rows="5" maxlength="4000" class="cloud-input resize-y" :disabled="busy" :placeholder="t('cloudMusic.promptHint')" /></label>
      <label class="cloud-label">{{ t('cloudMusic.titleLabel') }}<input v-model="title" class="cloud-input" maxlength="500" :disabled="busy" /></label>
      <label v-if="model?.supports_seed" class="cloud-label">{{ t('cloudMusic.seed') }}<input v-model.number="seed" type="number" min="0" max="2147483647" class="cloud-input" :disabled="busy" /></label>
      <button type="submit" class="cloud-button" :disabled="busy || !valid">{{ t('cloudProviders.quote') }}</button>
      <div v-if="quote" class="space-y-3 rounded-lg border border-accent1/30 bg-accent1/5 p-4"><p class="font-medium text-text">{{ t('cloudMusic.estimate',{cost:quote.estimated_usd.toFixed(3)}) }}</p><p class="text-xs text-text-dim">{{ t('cloudMusic.estimateHint') }}</p><p v-for="warning in quote.warnings" :key="warning" class="text-xs text-text-dim">{{ warningText(warning) }}</p><label class="flex items-start gap-3 text-sm text-text"><input v-model="confirmed" type="checkbox" :disabled="busy" class="mt-1" />{{ t('cloudProviders.transfer') }}</label></div>
      <button type="button" class="cloud-button bg-accent1 text-white" :disabled="busy || !valid || !quote || !confirmed" @click="generate">{{ t('cloudMusic.generate') }}</button>
    </form>
    <div class="space-y-4"><div class="flex items-center justify-between"><h3 class="font-semibold text-text">{{ t('cloudMusic.history') }}</h3><button class="cloud-button" :disabled="busy" @click="history">{{ t('cloudMusic.refreshJobs') }}</button></div><p v-if="!jobs.length" class="text-sm text-text-dim">{{ t('cloudMusic.empty') }}</p><article v-for="job in jobs" :key="job.id" class="space-y-3 rounded-xl border border-border bg-panel p-5"><div class="flex flex-wrap justify-between gap-3"><div><h4 class="font-medium text-text">{{ job.track?.title || job.title || t('cloudMusic.title') }}</h4><p class="mt-1 text-xs text-text-dim">{{ job.request.model }} · {{ t(`cloudMusic.states.${job.status}`) }}</p></div><button v-if="job.status==='queued'||job.status==='running'" class="cloud-button" :disabled="busy" @click="cancel(job)">{{ t('cloudMusic.cancel') }}</button></div><button v-if="job.can_retry_save" class="cloud-button" :disabled="busy" @click="retrySave(job)">{{ t('cloudMusic.retrySave') }}</button><audio v-if="job.track" :src="job.track.audio_url" controls preload="none" class="w-full" /><p v-if="job.status==='submission_unknown'" role="alert" class="text-sm text-status-queued">{{ t('cloudProviders.unknown') }}</p><p v-if="job.status==='failed'" role="alert" class="text-sm text-status-failed">{{ t('cloudMusic.failed') }}</p><p v-if="job.status==='canceled_tracking'||job.status==='running'" class="text-xs text-text-dim">{{ t('cloudMusic.cancelHint') }}</p></article></div>
  </section>
</template>
<style scoped>
@reference "../../style.css";
.cloud-label { @apply flex flex-col gap-2 text-sm text-text-dim; }
.cloud-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
.cloud-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>

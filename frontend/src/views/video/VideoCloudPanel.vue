<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { resolvedStillId } from './videoWorkspace'
import * as provider from '../../api/openrouter'
import { quoteCloudVideo } from '../../api/videos'
import type { OpenRouterModel, OpenRouterStatus, VideoCloudQuoteResponse, VideoCloudSubmitRequest, VideoProject } from '../../api/contracts'
const props=defineProps<{project: VideoProject;shotId: string;readOnly: boolean;dirty: boolean}>()
const emit=defineEmits<{submit:[projectId: string,body: VideoCloudSubmitRequest];save:[]}>()
const {t}=useI18n()
const models=ref<OpenRouterModel[]>([]),status=ref<OpenRouterStatus | null>(null),nativeDuration=ref(0),offer=ref<VideoCloudQuoteResponse | null>(null)
const transfer=ref(false),trim=ref(false),busy=ref(false),error=ref(''),clock=ref(Date.now())
const shot=computed(()=>props.project.shots?.find(item=>item.id===props.shotId))
const model=computed(()=>{const config=props.project.provider_config;return config?.provider==='openrouter' ? models.value.find(item=>item.id===config.model_id) : undefined})
const durations=computed(()=>(model.value?.supported_durations ?? []).filter(value=>value>=(shot.value?.seconds ?? 4)))
const unsupportedReference=computed(()=>Boolean(shot.value && resolvedStillId(props.project,shot.value) && !model.value?.supported_frame_images?.includes('first_frame')))
const supportedSize=computed(()=>props.project.provider_config?.provider==='openrouter' && model.value?.supported_sizes?.includes(props.project.provider_config.size))
const ready=computed(()=>supportedSize.value && !unsupportedReference.value && status.value?.enabled && status.value?.credential_configured && model.value && durations.value.includes(nativeDuration.value))
const expired=computed(()=>Boolean(offer.value && offer.value.quote.expires_at*1000<=clock.value))
let alive=true,generation=0,controller:AbortController | undefined,timer:ReturnType<typeof setInterval> | undefined
function invalidate(){generation++;controller?.abort();controller=undefined;offer.value=null;transfer.value=false;trim.value=false;busy.value=false;error.value=''}
watch([()=>props.project.id,()=>props.project.revision,()=>props.shotId,()=>JSON.stringify(props.project.provider_config),()=>props.dirty,nativeDuration],invalidate,{flush:'sync'})
watch(durations,value=>{if(!value.includes(nativeDuration.value))nativeDuration.value=value[0] ?? 0},{immediate:true})
async function load(){const token=generation,request=new AbortController();controller=request;busy.value=true
  try{const [catalog,value]=await Promise.all([provider.getProviderCatalog(request.signal),provider.getProviderStatus(request.signal)])
    if(alive && token===generation && !request.signal.aborted){models.value=(catalog.models ?? []).filter(item=>item.kind==='video');status.value=value}}
  catch{if(alive && token===generation)error.value=t('videoCloud.loadFailed')}
  finally{if(alive && token===generation)busy.value=false}
}
async function review(){if(!ready.value || props.dirty || props.readOnly || busy.value || !shot.value)return
  const token=++generation,request=new AbortController();controller=request;busy.value=true;error.value='';offer.value=null;transfer.value=false;trim.value=false
  const body={revision:props.project.revision,shot_id:props.shotId,remote_duration_sec:nativeDuration.value},id=props.project.id
  try{const result=await quoteCloudVideo(id,body,request.signal)
    if(alive && token===generation && !request.signal.aborted && result.project_id===id && result.revision===body.revision && result.shot_id===body.shot_id && result.remote_duration_sec===body.remote_duration_sec)offer.value=result}
  catch{if(alive && token===generation)error.value=t('videoCloud.actionFailed')}
  finally{if(alive && token===generation)busy.value=false}
}
function submit(){const current=offer.value;if(!current || expired.value || !transfer.value || current.trim_required && !trim.value || props.dirty || props.readOnly || busy.value)return
  offer.value=null;transfer.value=false;trim.value=false
  emit('submit',current.project_id,{revision:current.revision,shot_id:current.shot_id,remote_duration_sec:current.remote_duration_sec,quote_id:current.quote.id,transfers_confirmed:true,trim_confirmed:current.trim_required})
}
onMounted(()=>{void load();timer=setInterval(()=>{clock.value=Date.now()},1000)})
onBeforeUnmount(()=>{alive=false;invalidate();if(timer)clearInterval(timer)})
</script>
<template>
  <section class="video-card space-y-4" :aria-busy="busy">
    <h3 class="font-semibold">{{t('videoCloud.title')}}</h3><p class="text-sm text-text-dim">{{t('videoCloud.intro')}}</p>
    <p class="text-xs text-text-dim">{{t('videoCloud.privacy')}} {{t('videoCloud.seedHint')}}</p>
    <RouterLink v-if="!status?.enabled || !status?.credential_configured" to="/settings#providers" class="inline-flex min-h-11 items-center text-accent2 underline">{{t('videoCloud.setup')}}</RouterLink>
    <p v-if="!model" class="text-sm text-text-dim">{{t('videoCloud.noModels')}}</p>
    <p v-if="model && (!supportedSize || !durations.length || unsupportedReference)" class="text-sm text-status-failed">{{t('videoCloud.capabilityMismatch')}}</p>
    <p v-if="model?.supports_seed" class="text-xs text-text-dim">{{t('videoCloud.requestedSeed',{seed:shot?.seed ?? 0})}}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{error}}</p>
    <label>{{t('videoCloud.nativeDuration')}}<select v-model.number="nativeDuration" :disabled="readOnly || busy"><option v-for="value in durations" :key="value" :value="value">{{value}}s</option></select></label>
    <p class="text-sm">{{t('videoCloud.duration',{native:nativeDuration,slot:shot?.seconds ?? 4})}}</p>
    <div v-if="dirty" class="space-y-2"><p class="text-sm text-text-dim">{{t('videoCloud.dirty')}}</p><button type="button" :disabled="readOnly" @click="emit('save')">{{t('videoCloud.save')}}</button></div>
    <button type="button" data-cloud-quote :disabled="!ready || readOnly || dirty || busy" @click="review">{{t('videoCloud.quote')}}</button>
    <div v-if="offer" class="space-y-3 rounded-lg border border-accent1/30 bg-accent1/5 p-4">
      <p class="font-medium">{{t('videoCloud.estimate',{cost:offer.quote.estimated_usd.toFixed(3)})}}</p><p class="text-xs text-text-dim">{{t('videoCloud.estimateHint')}}</p>
      <details><summary>{{t('videoCloud.sentPrompt')}}</summary><p class="mt-2 whitespace-pre-wrap text-sm">{{[project.direction,shot?.prompt].filter(Boolean).join(' ')}}</p></details>
      <p class="text-sm">{{t('videoCloud.transfers',{items:(offer.quote.transfers ?? []).map(item=>t(`videoCloud.${item}`)).join(', ')})}}</p>
      <label class="inline-check"><input v-model="transfer" type="checkbox" :disabled="readOnly || expired">{{t('videoCloud.transfer')}}</label>
      <label v-if="offer.trim_required" class="inline-check"><input v-model="trim" type="checkbox" :disabled="readOnly || expired">{{t('videoCloud.trim',{native:offer.remote_duration_sec,slot:offer.slot_duration_sec})}}</label>
      <p v-if="expired" role="status" class="text-sm text-status-failed">{{t('videoCloud.expired')}}</p>
      <button type="button" data-cloud-submit class="primary" :disabled="readOnly || busy || dirty || expired || !transfer || offer.trim_required && !trim" @click="submit">{{t('videoCloud.submit')}}</button>
    </div>
  </section>
</template>

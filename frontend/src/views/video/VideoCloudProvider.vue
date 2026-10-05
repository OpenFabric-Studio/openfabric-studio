<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import * as provider from '../../api/openrouter'
import type { OpenRouterModel, VideoProject } from '../../api/contracts'
import type { VideoDraft } from './useVideoWorkspace'
const draft=defineModel<VideoDraft>({required:true})
const props=defineProps<{project: VideoProject;readOnly: boolean}>()
const emit=defineEmits<{detachAdapter:[]}>()
const {t}=useI18n()
const models=ref<OpenRouterModel[]>([]),busy=ref(false),error=ref('')
const cloud=computed(()=>draft.value.provider_config?.provider==='openrouter')
const model=computed(()=>{const config=draft.value.provider_config;return config?.provider==='openrouter' ? models.value.find(item=>item.id===config.model_id) : undefined})
function compatibleSizes(item: OpenRouterModel): string[]{return (item.supported_sizes ?? []).filter(size=>{if(props.project.preset!=='reel')return true;const [width,height]=size.split('x').map(Number);return width!==undefined && height!==undefined && width*16===height*9})}
const available=computed(()=>models.value.filter(item=>compatibleSizes(item).length>0))
let alive=true,generation=0,controller:AbortController | undefined
async function load(refresh=false,select=false){if(busy.value || props.readOnly)return;const token=++generation,request=new AbortController();controller=request;busy.value=true;error.value=''
  try{const result=await (refresh ? provider.refreshProviderCatalog(request.signal) : provider.getProviderCatalog(request.signal));if(!alive || token!==generation || request.signal.aborted)return;models.value=(result.models ?? []).filter(item=>item.kind==='video');if(select){const item=available.value[0];if(item)chooseModel(item.id);else error.value=t('videoCloud.noModels')}}
  catch{if(alive && token===generation)error.value=t('videoCloud.loadFailed')}
  finally{if(alive && token===generation)busy.value=false}
}
function local(){if(props.readOnly)return;generation++;controller?.abort();controller=undefined;busy.value=false;error.value='';draft.value.provider_config={provider:'local'}}
function chooseModel(id:string){if(props.readOnly || props.project.character_adapter_id)return;const item=available.value.find(row=>row.id===id),size=item ? compatibleSizes(item)[0] : undefined;if(!item || !size)return
  draft.value.mode='generated';draft.value.provider_config={provider:'openrouter',model_id:item.id,size,generate_audio:false}
}
function modelChanged(event:Event){if(event.target instanceof HTMLSelectElement)chooseModel(event.target.value)}
function sizeChanged(event:Event){if(props.readOnly || !(event.target instanceof HTMLSelectElement) || draft.value.provider_config?.provider!=='openrouter' || !model.value || !compatibleSizes(model.value).includes(event.target.value))return;draft.value.provider_config={...draft.value.provider_config,size:event.target.value}}
watch(()=>props.project.id,()=>{generation++;controller?.abort();busy.value=false;error.value='';if(cloud.value)void load()},{immediate:true})
onBeforeUnmount(()=>{alive=false;generation++;controller?.abort()})
</script>
<template>
  <section class="space-y-3" :aria-busy="busy">
    <h3 class="text-sm font-medium">{{t('videoCloud.provider')}}</h3>
    <div role="group" :aria-label="t('videoCloud.provider')" class="flex flex-wrap gap-2">
      <button type="button" :aria-pressed="!cloud" :disabled="readOnly" @click="local">{{t('videoCloud.local')}}</button>
      <button type="button" data-video-cloud-provider :aria-pressed="cloud" :disabled="readOnly || busy || Boolean(project.character_adapter_id)" @click="load(false,true)">{{t('videoCloud.cloud')}}</button>
    </div>
    <div v-if="project.character_adapter_id" class="space-y-2"><p class="text-xs text-text-dim">{{t('videoCloud.adapter')}}</p><button type="button" :disabled="readOnly" @click="emit('detachAdapter')">{{t('videoCloud.detachAdapter')}}</button></div>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{error}}</p>
    <div v-if="cloud" class="space-y-3">
      <p class="text-sm text-text-dim">{{t('videoCloud.intro')}}</p>
      <label>{{t('videoCloud.model')}}<select :value="draft.provider_config?.provider==='openrouter' ? draft.provider_config.model_id : ''" :disabled="readOnly || busy" data-cloud-model @change="modelChanged"><option v-for="item in available" :key="item.id" :value="item.id">{{item.name}}</option></select></label>
      <label>{{t('videoCloud.size')}}<select :value="draft.provider_config?.provider==='openrouter' ? draft.provider_config.size : ''" :disabled="readOnly || busy" data-cloud-size @change="sizeChanged"><option v-for="size in model ? compatibleSizes(model) : []" :key="size" :value="size">{{size}}</option></select></label>
      <button type="button" :disabled="readOnly || busy" @click="load(true)">{{t('videoCloud.refresh')}}</button>
      <RouterLink to="/settings#providers" class="inline-flex min-h-11 items-center text-accent2 underline">{{t('videoCloud.setup')}}</RouterLink>
      <p class="text-xs text-text-dim">{{t('videoCloud.privacy')}} {{t('videoCloud.seedHint')}}</p>
    </div>
  </section>
</template>

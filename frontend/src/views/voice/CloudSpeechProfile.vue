<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, routerKey } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getProviderCatalog } from '../../api/openrouter'
import { createCloudSpeechProfile } from '../../api/cloudSpeech'
import { patchSpeechVoiceProfile } from '../../api/voiceProfiles'
import type { SpeechVoiceProfile, OpenRouterModel, CloudSpeechConfiguration } from '../../api/contracts'
const props=withDefaults(defineProps<{profile?:SpeechVoiceProfile;disabled?:boolean;active?:boolean}>(),{disabled:false,active:true})
const emit=defineEmits<{saved:[profile:SpeechVoiceProfile]}>()
const {t}=useI18n()
const router=inject(routerKey,undefined)
const expanded=ref(false)
const models=ref<OpenRouterModel[]>([]), modelId=ref(''), voice=ref(''), mode=ref<'preset'|'clone'>('preset'), permission=ref(false), renderer=ref<'local'|'openrouter'>('openrouter'), name=ref(''), saving=ref(false),loading=ref(false),error=ref('')
const model=computed(()=>models.value.find(item=>item.id===modelId.value)),voices=computed(()=>model.value?.supported_voices??[])
let alive=true,generation=0,controller:AbortController|undefined
watch(()=>props.profile,profile=>{generation++;controller?.abort();loading.value=false;saving.value=false;error.value='';renderer.value=profile?.renderer??'openrouter';modelId.value=profile?.cloud?.model??'';voice.value=profile?.cloud?.voice??'';mode.value=profile?.cloud?.clone_reference?'clone':'preset';permission.value=profile?.cloud?.reference_transfer_confirmed??false},{immediate:true})
watch(modelId,()=>{if(!voices.value.includes(voice.value))voice.value=voices.value[0]??'';if(!model.value?.supports_voice_cloning){mode.value='preset';permission.value=false}})
async function load(){
  if(loading.value||!props.active)return
  const token=generation,request=new AbortController();controller?.abort();controller=request;loading.value=true;error.value=''
  try{const result=await getProviderCatalog(request.signal);if(!alive||token!==generation||request.signal.aborted)return;models.value=(result.models??[]).filter(item=>item.kind==='speech');if(!models.value.some(item=>item.id===modelId.value))modelId.value=models.value[0]?.id??'';if(!voices.value.includes(voice.value))voice.value=voices.value[0]??''}
  catch{if(alive&&token===generation&&!request.signal.aborted)error.value=t('cloudSpeech.catalogEmpty')}
  finally{if(alive&&token===generation&&!request.signal.aborted)loading.value=false}
}
function opened(event:Event){if(event.target instanceof HTMLDetailsElement){expanded.value=event.target.open;if(expanded.value)void load()}}
async function save(){
  if(saving.value||props.disabled||!props.active)return
  const profile=props.profile,token=generation,request=new AbortController();controller=request;saving.value=true;error.value=''
  const config:CloudSpeechConfiguration={model:modelId.value,...(mode.value==='clone'?{clone_reference:true,reference_transfer_confirmed:permission.value}:{voice:voice.value})}
  try{
    const result=profile?await patchSpeechVoiceProfile(profile.id,{renderer:renderer.value,...(renderer.value==='openrouter'?{cloud:config}:{})},request.signal):await createCloudSpeechProfile({name:name.value.trim(),model:modelId.value,voice:voice.value},request.signal)
    if(alive&&token===generation&&!request.signal.aborted){emit('saved',result);name.value=''}
  }catch{if(alive&&token===generation&&!request.signal.aborted)error.value=t('cloudSpeech.error')}
  finally{if(alive&&token===generation&&!request.signal.aborted)saving.value=false}
}
onBeforeUnmount(()=>{alive=false;generation++;controller?.abort()})
</script>
<template>
  <details class="rounded-lg border border-border bg-panel p-3" @toggle="opened">
    <summary class="cursor-pointer text-sm font-medium text-text">{{t(profile?'cloudSpeech.renderer':'cloudSpeech.preset')}}<span v-if="profile" class="ml-2 text-xs text-text-dim">{{t(profile.renderer==='openrouter'?'cloudSpeech.cloud':'cloudSpeech.local')}}</span></summary>
    <form v-if="expanded" class="mt-4 space-y-3" @submit.prevent="save">
      <p class="text-xs text-text-dim">{{t('cloudSpeech.transferHelp')}}</p>
      <label v-if="profile" class="block space-y-1"><span class="text-xs text-text-dim">{{t('cloudSpeech.renderer')}}</span><select v-model="renderer" :disabled="saving||disabled" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :aria-label="t('cloudSpeech.renderer')"><option v-if="profile.reference_audio_path" value="local">{{t('cloudSpeech.local')}}</option><option value="openrouter">{{t('cloudSpeech.cloud')}}</option></select></label>
      <label v-else class="block space-y-1"><span class="text-xs text-text-dim">{{t('cloudSpeech.name')}}</span><input v-model="name" required maxlength="120" :disabled="saving||disabled" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :aria-label="t('cloudSpeech.name')"></label>
      <template v-if="renderer==='openrouter'">
        <label class="block space-y-1"><span class="text-xs text-text-dim">{{t('cloudSpeech.model')}}</span><select v-model="modelId" :disabled="saving||disabled||loading" :aria-label="t('cloudSpeech.model')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"><option v-for="item in models" :key="item.id" :value="item.id">{{item.name}}</option></select></label>
        <label v-if="profile?.reference_audio_path&&model?.supports_voice_cloning" class="block space-y-1"><span class="text-xs text-text-dim">{{t('cloudSpeech.voice')}}</span><select v-model="mode" :disabled="saving||disabled" :aria-label="t('cloudSpeech.voice')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"><option value="preset">{{t('cloudSpeech.presetMode')}}</option><option value="clone">{{t('cloudSpeech.clone')}}</option></select></label>
        <label v-if="mode==='preset'" class="block space-y-1"><span class="text-xs text-text-dim">{{t('cloudSpeech.voice')}}</span><select v-model="voice" :disabled="saving||disabled||loading" :aria-label="t('cloudSpeech.voice')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"><option v-for="item in voices" :key="item" :value="item">{{item}}</option></select></label>
        <label v-else class="flex gap-2 text-sm text-text"><input v-model="permission" type="checkbox" :disabled="saving||disabled" :aria-label="t('cloudSpeech.transferPermission')"><span>{{t('cloudSpeech.transferPermission')}}</span></label>
        <p v-if="!models.length&&!loading" class="text-xs text-text-dim">{{t('cloudSpeech.catalogEmpty')}}</p>
        <button type="button" :disabled="loading||saving" class="min-h-11 text-xs text-accent1 underline" @click="load">{{t('cloudSpeech.refresh')}}</button>
        <RouterLink v-if="router" to="/settings#providers" class="ml-4 inline-block min-h-11 py-3 text-xs text-accent1 underline">{{t('cloudSpeech.settings')}}</RouterLink>
        <a v-else href="/settings#providers" class="ml-4 inline-block min-h-11 py-3 text-xs text-accent1 underline">{{t('cloudSpeech.settings')}}</a>
      </template>
      <p v-if="error" role="alert" class="text-sm text-status-failed">{{error}}</p>
      <button type="submit" :disabled="saving||disabled||loading||renderer==='openrouter'&&(!modelId||mode==='preset'&&!voice)||!profile&&!name.trim()" class="min-h-11 rounded-lg bg-accent1 px-3 text-sm text-white disabled:opacity-50">{{t(profile?'cloudSpeech.save':'cloudSpeech.create')}}</button>
    </form>
  </details>
</template>
